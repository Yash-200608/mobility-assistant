"""Emergency SOS path with a mocked Twilio client: no network, no credentials."""
import threading
import time
import unittest
from unittest import mock

import _support as S
import core
import sos
from config import Config

TEST_TWILIO = dict(
    TWILIO_SID="ACtest", TWILIO_TOKEN="test-token",
    TWILIO_PHONE="+15550000001", EMERGENCY_PHONE="+15550000002",
)


def drain(state):
    out = []
    while not state.alert_queue.empty():
        out.append(state.alert_queue.get_nowait()[1])
    return out


class SosTestCase(unittest.TestCase):
    def setUp(self):
        self.state = S.enter(self, S.fresh_state(sos))
        self.clock = S.enter(self, S.fake_time(core, sos, start=1_000.0))
        S.enter(self, mock.patch.multiple(Config, **TEST_TWILIO))
        self.client = S.enter(self, mock.patch.object(sos, "Client"))
        # Run the background send inline so assertions are deterministic; the
        # genuinely-threaded path is covered by AsyncDispatchTests.
        S.enter(self, mock.patch.object(sos, "_run_in_background", lambda fn: fn()))
        self.create = self.client.return_value.messages.create

    def advance(self, seconds):
        self.clock.advance(seconds)


class TriggerSosTests(SosTestCase):
    def test_sends_one_sms_with_location_and_source(self):
        self.state.update_sensor_data({"gps_lat": 28.61, "gps_lng": 77.2})
        self.assertTrue(sos.trigger_sos("hardware"))
        self.create.assert_called_once()
        kwargs = self.create.call_args.kwargs
        self.assertEqual(kwargs["to"], TEST_TWILIO["EMERGENCY_PHONE"])
        self.assertEqual(kwargs["from_"], TEST_TWILIO["TWILIO_PHONE"])
        self.assertIn("28.61,77.2", kwargs["body"])
        self.assertIn("SOS button", kwargs["body"])
        msgs = drain(self.state)
        self.assertIn("requested", msgs[0])
        self.assertTrue(any("emergency contact has been notified" in m for m in msgs), msgs)

    def test_duplicate_triggers_inside_cooldown_send_once(self):
        self.assertTrue(sos.trigger_sos("hardware"))
        drain(self.state)
        for _ in range(5):  # a held button re-fires every frame
            self.advance(0.1)
            self.assertFalse(sos.trigger_sos("hardware"))
        self.create.assert_called_once()
        self.assertTrue(any("sent recently" in m for m in drain(self.state)))

    def test_allowed_again_after_cooldown(self):
        sos.trigger_sos("hardware")
        self.advance(Config.SOS_COOLDOWN_SEC + 0.1)
        self.assertTrue(sos.trigger_sos("hardware"))
        self.assertEqual(self.create.call_count, 2)

    def test_first_sos_right_after_boot_is_not_swallowed(self):
        # time.monotonic() counts from boot on a Pi; uptime of 5 s must not block SOS.
        self.clock.now = 5.0
        self.assertTrue(sos.trigger_sos("hardware"))
        self.create.assert_called_once()

    def test_failed_send_does_not_claim_help_is_coming(self):
        self.create.side_effect = RuntimeError("network unreachable")
        self.assertFalse(sos.trigger_sos("voice", block=True))
        msgs = drain(self.state)
        self.assertTrue(any("could not be sent" in m for m in msgs), msgs)
        self.assertFalse(any("notified" in m for m in msgs), msgs)

    def test_failed_send_can_be_retried_soon_but_not_instantly(self):
        self.create.side_effect = RuntimeError("network unreachable")
        sos.trigger_sos("hardware", block=True)
        self.assertFalse(sos.trigger_sos("hardware", block=True))  # immediate retry is throttled
        self.create.side_effect = None
        self.advance(Config.SOS_RETRY_SEC + 0.1)
        self.assertTrue(sos.trigger_sos("hardware", block=True))  # well before SOS_COOLDOWN_SEC
        self.assertLess(Config.SOS_RETRY_SEC, Config.SOS_COOLDOWN_SEC)

    def test_missing_credentials_is_reported_as_failure_without_calling_twilio(self):
        with mock.patch.object(Config, "TWILIO_TOKEN", None):
            self.assertFalse(sos.trigger_sos("hardware", block=True))
        self.client.assert_not_called()
        self.assertTrue(any("could not be sent" in m for m in drain(self.state)))

    def test_transient_failures_are_retried_then_succeed(self):
        self.create.side_effect = [RuntimeError("503"), RuntimeError("503"), None]
        self.assertTrue(sos.trigger_sos("hardware", block=True))
        self.assertEqual(self.create.call_count, 3)
        self.assertTrue(any("notified" in m for m in drain(self.state)))
        # Delivered, so the full cooldown applies: no immediate duplicate.
        self.assertFalse(sos.trigger_sos("hardware", block=True))

    def test_all_attempts_exhausted_is_reported_once(self):
        self.create.side_effect = RuntimeError("down")
        self.assertFalse(sos.trigger_sos("hardware", block=True))
        self.assertEqual(self.create.call_count, Config.SOS_SEND_ATTEMPTS)
        failures = [m for m in drain(self.state) if "could not be sent" in m]
        self.assertEqual(len(failures), 1)

    def test_retries_back_off_instead_of_hammering(self):
        self.create.side_effect = RuntimeError("down")
        t0 = self.clock.now
        sos.trigger_sos("hardware", block=True)
        self.assertGreaterEqual(self.clock.now - t0, Config.SOS_ATTEMPT_BACKOFF_SEC)

    def test_fall_source_is_labelled_in_the_message(self):
        sos.trigger_sos("fall", block=True)
        self.assertIn("automatic fall detection", self.create.call_args.kwargs["body"])

    def test_sos_alert_is_spoken_even_when_alerts_are_paused(self):
        self.state.set_alerts_enabled(False)
        sos.trigger_sos("hardware")
        self.assertTrue(drain(self.state))

    def test_simultaneous_triggers_from_many_threads_send_exactly_once(self):
        n = 16
        barrier = threading.Barrier(n)
        results = []

        def fire():
            barrier.wait()
            results.append(sos.trigger_sos("hardware", block=True))

        threads = [threading.Thread(target=fire) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(results.count(True), 1)
        self.create.assert_called_once()


class AsyncDispatchTests(unittest.TestCase):
    """The real background thread: a hung network must not stall the caller."""

    def test_trigger_returns_immediately_while_the_send_is_still_in_flight(self):
        release, started = threading.Event(), threading.Event()

        def slow_create(**kwargs):
            started.set()
            release.wait(timeout=5)

        with S.fresh_state(sos) as state, \
                mock.patch.multiple(Config, **TEST_TWILIO), \
                mock.patch.object(sos, "Client") as client:
            client.return_value.messages.create.side_effect = slow_create
            t0 = time.monotonic()
            self.assertTrue(sos.trigger_sos("hardware"))
            self.assertLess(time.monotonic() - t0, 1.0)  # did not wait for the send
            self.assertTrue(started.wait(timeout=2))
            self.assertTrue(any("requested" in m for m in drain(state)))
            release.set()
            for th in threading.enumerate():
                if th.name == "SOSDispatch":
                    th.join(timeout=2)
            self.assertTrue(any("notified" in m for m in drain(state)))


class VoiceSosTests(SosTestCase):
    def test_requires_wake_word_by_default(self):
        self.assertFalse(sos.try_voice_sos_from_transcript("send sos"))
        self.create.assert_not_called()

    def test_wake_word_plus_phrase_triggers(self):
        self.assertTrue(sos.try_voice_sos_from_transcript(f"{Config.WAKE_WORD} send sos please"))
        self.create.assert_called_once()
        self.assertIn("voice command", self.create.call_args.kwargs["body"])

    def test_wake_word_without_phrase_does_not_trigger(self):
        self.assertFalse(sos.try_voice_sos_from_transcript(f"{Config.WAKE_WORD} what is the weather"))
        self.create.assert_not_called()

    def test_empty_transcript_is_ignored(self):
        self.assertFalse(sos.try_voice_sos_from_transcript(""))
        self.create.assert_not_called()

    def test_no_wake_mode_accepts_only_the_stricter_phrase_list(self):
        with mock.patch.object(Config, "SOS_VOICE_REQUIRES_WAKE", False):
            self.assertFalse(sos.try_voice_sos_from_transcript("send sos"))
            self.assertTrue(sos.try_voice_sos_from_transcript("help i've fallen"))
        self.create.assert_called_once()

    def test_repeated_voice_sos_is_throttled(self):
        text = f"{Config.WAKE_WORD} emergency help"
        self.assertTrue(sos.try_voice_sos_from_transcript(text))
        self.assertTrue(sos.try_voice_sos_from_transcript(text))  # utterance consumed...
        self.create.assert_called_once()  # ...but only one SMS goes out


if __name__ == "__main__":
    unittest.main()
