"""Orchestration (pipeline.py) and spoken-alert routing (speech.py) without any I/O."""
import csv
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import _support as S
import core
import pipeline
from config import Config
from core import CircuitBreaker
from escalation import FallEscalator
from speech import AlertSpeaker, LatencyRecorder

HZ = 20
IMPACT = S.sensor(pitch=60, roll=5, accel_mag=3.5)
LYING = S.sensor(pitch=80, roll=5, accel_mag=1.0)
UPRIGHT = S.sensor(pitch=2, roll=1, accel_mag=1.0)


def drain(state):
    out = []
    while not state.alert_queue.empty():
        out.append(state.alert_queue.get_nowait()[1])
    return out


class HardwareSafetyTests(unittest.TestCase):
    def setUp(self):
        S.enter(self, mock.patch.object(Config, "FALL_AUTO_SOS", True))
        self.state = core.SystemState()
        self.sos, self.buzz = [], []
        escalator = FallEscalator(state=self.state, sos_fn=lambda s: self.sos.append(s) or True)
        self.safety = pipeline.HardwareSafety(
            state=self.state, escalator=escalator,
            sos_fn=lambda s: self.sos.append(s) or True,
            buzz_fn=lambda p: self.buzz.append(p) or True,
        )
        self.now = 1000.0

    def feed(self, samples):
        results = []
        for s in samples:
            results.append(self.safety.step(s, self.now))
            self.now += 1.0 / HZ
        return results

    def test_sos_button_requests_sos_and_sounds_buzzer(self):
        r = self.feed([S.sensor(sos=1)])[0]
        self.assertTrue(r.sos_requested)
        self.assertEqual(self.sos, ["hardware"])
        self.assertEqual(self.buzz, ["sos"])

    def test_confirmed_fall_arms_escalation_once_and_buzzes_once(self):
        res = self.feed([UPRIGHT] * 10 + [IMPACT] + [LYING] * 60)
        self.assertEqual(sum(r.newly_fallen for r in res), 1)
        self.assertEqual(self.buzz.count("fall"), 1)
        self.assertTrue(self.safety.escalator.pending)
        self.assertEqual(self.sos, [])  # still inside the cancel window

    def test_unanswered_fall_sends_exactly_one_sos_after_the_window(self):
        self.feed([UPRIGHT] * 5 + [IMPACT] + [LYING] * int((Config.FALL_ESCALATION_SEC + 5) * HZ))
        self.assertEqual(self.sos, ["fall"])

    def test_voice_cancel_prevents_the_sos(self):
        self.feed([UPRIGHT] * 5 + [IMPACT] + [LYING] * 40)
        self.assertTrue(self.safety.escalator.try_cancel_from_transcript("i am okay"))
        self.feed([LYING] * int((Config.FALL_ESCALATION_SEC + 5) * HZ))
        self.assertEqual(self.sos, [])

    def test_getting_up_cancels_and_silences_the_buzzer(self):
        self.feed([UPRIGHT] * 5 + [IMPACT] + [LYING] * 40 + [UPRIGHT] * (6 * HZ))
        self.assertEqual(self.sos, [])
        self.assertIn("off", self.buzz)

    def test_stale_feed_still_honours_the_deadline(self):
        self.feed([UPRIGHT] * 5 + [IMPACT] + [LYING] * 40)
        self.assertTrue(self.safety.escalator.pending)
        # Telemetry dies; main() now calls idle_tick instead of step().
        end = self.now + Config.FALL_ESCALATION_SEC + 1
        while self.now < end:
            self.safety.idle_tick(self.now)
            self.now += 0.5
        self.assertEqual(self.sos, ["fall"])

    def test_a_failing_buzzer_never_breaks_the_safety_step(self):
        s = pipeline.HardwareSafety(state=self.state, buzz_fn=mock.Mock(side_effect=OSError("port gone")),
                                    sos_fn=lambda src: True,
                                    escalator=FallEscalator(state=self.state, sos_fn=lambda x: True))
        r = s.step(S.sensor(sos=1), 1.0)
        self.assertTrue(r.sos_requested)

    def test_fall_alert_is_spoken_even_when_alerts_are_paused(self):
        self.state.set_alerts_enabled(False)
        self.feed([UPRIGHT] * 5 + [IMPACT] + [LYING] * 40)
        self.assertTrue(any("Fall detected" in m for m in drain(self.state)))


def landmarks(l_heel_y=0.5, r_heel_y=0.5):
    pts = [SimpleNamespace(x=0.5, y=0.5) for _ in range(33)]
    pts[29].y, pts[30].y = l_heel_y, r_heel_y
    pts[0].y = 0.2      # nose well above hips: upright posture
    pts[23].y = pts[24].y = 0.6
    return pts


class WalkerAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.state = core.SystemState()
        self.rows = []
        self.walker = pipeline.WalkerAnalytics(state=self.state, log_fn=self.rows.append)

    def step(self, lm=None, now=1000.0, **kw):
        return self.walker.step(lm or landmarks(), "walker", "CAMERA/MIC", S.sensor(),
                                hardware_alerts=False, now=now, **kw)

    def test_frame_exposes_metrics_and_updates_shared_state(self):
        f = self.step()
        self.assertIn("pattern", f.metrics)
        self.assertEqual(f.slouch, 0)
        self.assertIsNotNone(self.state.get_gait_metrics())
        self.assertIn("coaching_hints=", self.state.get_rehab_llm_context())

    def test_research_rows_respect_the_logging_interval(self):
        self.step(now=1000.0)
        self.step(now=1000.0 + Config.RESEARCH_LOG_INTERVAL_SEC / 2)
        self.assertEqual(len(self.rows), 1)
        self.step(now=1000.0 + Config.RESEARCH_LOG_INTERVAL_SEC + 0.01)
        self.assertEqual(len(self.rows), 2)

    def test_slouch_alert_is_rate_limited_and_counted(self):
        slumped = landmarks()
        slumped[0].y = 0.59  # nose almost level with hips
        a = self.step(slumped, now=1000.0)
        self.assertIsNotNone(a.posture_alert)
        b = self.step(slumped, now=1005.0)   # inside the 15 s minimum gap
        self.assertIsNone(b.posture_alert)
        c = self.step(slumped, now=1000.0 + pipeline.WalkerAnalytics.SLOUCH_ALERT_MIN_GAP_SEC + 1)
        self.assertIsNotNone(c.posture_alert)
        self.assertEqual(self.walker.rehab.slouch_alerts, 2)

    def test_fall_direction_hint_only_with_trusted_hardware_and_fall_state(self):
        sensors = S.sensor(pitch=40, roll=5)
        no_hw = self.walker.step(landmarks(), "walker", "FALLEN", sensors, hardware_alerts=False, now=1.0)
        self.assertEqual(no_hw.direction_hint, "")
        hw = self.walker.step(landmarks(), "walker", "FALLEN", sensors, hardware_alerts=True, now=2.0)
        self.assertEqual(hw.direction_hint, "forward")


class FakeSpeech:
    def __init__(self, fail=None, label="x"):
        self.calls, self.fail, self.label = [], fail, label

    def __call__(self, text, on_start):
        self.calls.append(text)
        if self.fail:
            raise self.fail
        on_start()


class AlertSpeakerTests(unittest.TestCase):
    def setUp(self):
        self.clock = S.FakeClock(1000.0)
        self.recorder = LatencyRecorder()

    def speaker(self, cloud, local, breaker=None):
        return AlertSpeaker(cloud_speak=cloud, local_speak=local, recorder=self.recorder,
                            breaker=breaker or CircuitBreaker(3, 30.0), clock=self.clock)

    def test_cloud_voice_is_preferred_when_it_works(self):
        cloud, local = FakeSpeech(), FakeSpeech()
        self.assertEqual(self.speaker(cloud, local).handle(self.clock.time(), "hello"), "cloud")
        self.assertEqual(local.calls, [])

    def test_no_internet_falls_back_to_on_device_voice(self):
        cloud = FakeSpeech(fail=ConnectionError("offline"))
        local = FakeSpeech()
        self.assertEqual(self.speaker(cloud, local).handle(self.clock.time(), "Fall detected"), "local")
        self.assertEqual(local.calls, ["Fall detected"])

    def test_missing_api_key_uses_on_device_voice_only(self):
        local = FakeSpeech()
        self.assertEqual(self.speaker(None, local).handle(self.clock.time(), "hi"), "local")

    def test_repeated_cloud_failures_open_the_breaker_so_alerts_are_not_delayed(self):
        cloud, local = FakeSpeech(fail=TimeoutError("slow")), FakeSpeech()
        sp = self.speaker(cloud, local)
        for _ in range(3):
            sp.handle(self.clock.time(), "a")
        calls_before = len(cloud.calls)
        sp.handle(self.clock.time(), "b")
        self.assertEqual(len(cloud.calls), calls_before)   # cloud skipped while open
        self.assertEqual(local.calls[-1], "b")

    def test_cloud_comes_back_after_the_recovery_timeout(self):
        cloud = FakeSpeech(fail=TimeoutError("slow"))
        local = FakeSpeech()
        sp = self.speaker(cloud, local, CircuitBreaker(3, 30.0))
        with mock.patch.object(core, "time", self.clock):
            for _ in range(3):
                sp.handle(self.clock.time(), "a")
            self.clock.advance(31)
            cloud.fail = None
            self.assertEqual(sp.handle(self.clock.time(), "back"), "cloud")

    def test_stale_alerts_are_dropped_not_spoken_late(self):
        cloud = FakeSpeech()
        out = self.speaker(cloud, None).handle(self.clock.time() - 9.0, "old news")
        self.assertEqual(out, "stale")
        self.assertEqual(cloud.calls, [])

    def test_everything_failing_is_reported_not_raised(self):
        sp = self.speaker(FakeSpeech(fail=OSError("x")), FakeSpeech(fail=RuntimeError("no pyttsx3")))
        self.assertEqual(sp.handle(self.clock.time(), "hi"), "silent")


class LatencyRecorderTests(unittest.TestCase):
    def test_records_alert_to_audio_latency_by_route(self):
        r = LatencyRecorder()
        for ms in (100, 200, 300, 400):
            r.record("cloud", 10.0, 10.0 + ms / 1000.0)
        r.record("local", 10.0, 10.05)
        s = r.summary()
        self.assertEqual(s["cloud"]["n"], 4)
        self.assertAlmostEqual(s["cloud"]["median_ms"], 250.0)
        self.assertAlmostEqual(s["cloud"]["max_ms"], 400.0)
        self.assertAlmostEqual(s["local"]["median_ms"], 50.0)

    def test_negative_clock_skew_is_clamped(self):
        self.assertEqual(LatencyRecorder().record("cloud", 10.0, 9.0), 0.0)

    def test_csv_output_has_header_and_rows(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "alert_latency.csv")
            r = LatencyRecorder(csv_path=path)
            r.record("cloud", 1.0, 1.25, "hello there")
            r.record("local", 2.0, 2.10, "hi")
            with open(path, newline="", encoding="utf-8") as fp:
                rows = list(csv.DictReader(fp))
        self.assertEqual([x["route"] for x in rows], ["cloud", "local"])
        self.assertEqual(rows[0]["latency_ms"], "250")

    def test_unwritable_csv_path_disables_logging_without_raising(self):
        with tempfile.TemporaryDirectory() as d:
            blocker = os.path.join(d, "file")
            open(blocker, "w").close()
            r = LatencyRecorder(csv_path=os.path.join(blocker, "x.csv"))  # parent is a file
            r.record("cloud", 1.0, 1.1)
            r.record("cloud", 1.0, 1.1)
            self.assertEqual(r.summary()["cloud"]["n"], 2)


if __name__ == "__main__":
    unittest.main()
