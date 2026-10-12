"""Automatic SOS after a fall: cancel window, voice cancel, recovery cancel."""
import unittest
from unittest import mock

import _support as S
import core
from config import Config
from escalation import FallEscalator


def drain(state):
    out = []
    while not state.alert_queue.empty():
        out.append(state.alert_queue.get_nowait()[1])
    return out


class EscalationTests(unittest.TestCase):
    def setUp(self):
        S.enter(self, mock.patch.object(Config, "FALL_AUTO_SOS", True))
        self.state = core.SystemState()
        self.sent = []
        self.esc = FallEscalator(state=self.state, sos_fn=lambda src: self.sent.append(src) or True)
        self.T = 100.0
        self.W = Config.FALL_ESCALATION_SEC

    def test_flag_off_means_no_arming_and_no_sos(self):
        with mock.patch.object(Config, "FALL_AUTO_SOS", False):
            self.esc.on_fall_confirmed(self.T)
            self.assertIsNone(self.esc.tick(self.T + 1000, "FALLEN"))
        self.assertEqual(self.sent, [])
        self.assertFalse(self.esc.pending)

    def test_no_sos_before_the_window_closes_then_exactly_one(self):
        self.esc.on_fall_confirmed(self.T)
        self.assertTrue(self.esc.pending)
        self.assertIn("okay", drain(self.state)[0])
        self.assertIsNone(self.esc.tick(self.T + self.W - 0.1, "FALLEN"))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.esc.tick(self.T + self.W, "FALLEN"), "sent")
        self.assertIsNone(self.esc.tick(self.T + self.W + 5, "FALLEN"))
        self.assertEqual(self.sent, ["fall"])

    def test_voice_cancel_stops_the_sos(self):
        self.esc.on_fall_confirmed(self.T)
        self.assertTrue(self.esc.try_cancel_from_transcript("hey i'm okay thanks"))
        self.assertIsNone(self.esc.tick(self.T + self.W + 1, "FALLEN"))
        self.assertEqual(self.sent, [])
        self.assertTrue(any("will not send" in m for m in drain(self.state)))

    def test_unrelated_speech_does_not_cancel(self):
        self.esc.on_fall_confirmed(self.T)
        self.assertFalse(self.esc.try_cancel_from_transcript("what is the weather"))
        self.assertTrue(self.esc.pending)

    def test_cancel_phrases_ignored_when_nothing_is_pending(self):
        self.assertFalse(self.esc.try_cancel_from_transcript("i'm okay"))

    def test_standing_back_up_cancels(self):
        self.esc.on_fall_confirmed(self.T)
        self.assertEqual(self.esc.tick(self.T + 5, "RECOVERING"), "cancelled")
        self.assertIsNone(self.esc.tick(self.T + self.W + 1, "UPRIGHT"))
        self.assertEqual(self.sent, [])

    def test_repeated_confirmation_does_not_extend_or_duplicate(self):
        self.esc.on_fall_confirmed(self.T)
        first_deadline = self.esc.deadline
        self.esc.on_fall_confirmed(self.T + 10)
        self.assertEqual(self.esc.deadline, first_deadline)
        self.assertEqual(len(drain(self.state)), 1)

    def test_rearms_for_a_later_separate_fall(self):
        self.esc.on_fall_confirmed(self.T)
        self.esc.tick(self.T + self.W, "FALLEN")           # sent
        self.esc.tick(self.T + self.W + 60, "UPRIGHT")     # up again -> IDLE
        self.esc.on_fall_confirmed(self.T + 500)
        self.assertEqual(self.esc.tick(self.T + 500 + self.W, "FALLEN"), "sent")
        self.assertEqual(self.sent, ["fall", "fall"])

    def test_voice_router_cancels_without_a_wake_word(self):
        import voice_commands
        shared = voice_commands.fall_escalator
        with mock.patch.object(shared, "_state_obj", self.state), \
                mock.patch.object(shared, "_sos_fn", lambda s: True):
            shared.on_fall_confirmed(self.T)
            self.assertTrue(shared.try_cancel_from_transcript("im fine"))
            self.assertFalse(shared.pending)


if __name__ == "__main__":
    unittest.main()
