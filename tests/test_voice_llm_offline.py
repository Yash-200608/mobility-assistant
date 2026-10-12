"""Voice routing and the online LLM, proving the offline path never needs a network."""
import socket
import unittest
from unittest import mock

import _support as S
import core
import llm_online
import voice_commands
from config import Config
from core import CircuitBreaker


def drain(state):
    out = []
    while not state.alert_queue.empty():
        out.append(state.alert_queue.get_nowait()[1])
    return out


class FakeGroq:
    """Records calls; returns scripted replies or raises scripted errors."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        msg = type("M", (), {"content": r})
        return type("C", (), {"choices": [type("Ch", (), {"message": msg})]})


class VoiceBase(unittest.TestCase):
    def setUp(self):
        self.state = S.enter(self, S.fresh_state(voice_commands, llm_online))
        self.clock = S.enter(self, S.fake_time(core))
        self.breaker = CircuitBreaker(failure_threshold=4, recovery_timeout=45.0)
        S.enter(self, mock.patch.object(core, "llm_circuit", self.breaker))
        S.enter(self, mock.patch.object(llm_online, "llm_circuit", self.breaker))


class OfflineVoiceTests(VoiceBase):
    def setUp(self):
        super().setUp()
        S.enter(self, mock.patch.object(Config, "GROQ_API_KEY", None))
        # Any attempt to touch the network fails the test loudly.
        S.enter(self, mock.patch.object(
            socket, "create_connection", side_effect=AssertionError("network used while offline")))

    def say(self, text):
        voice_commands.handle_wake_utterance(f"{Config.WAKE_WORD} {text}", None)
        return drain(self.state)

    def test_mode_switching_works_offline(self):
        self.assertIn("stick mode", self.say("stick mode")[0].lower())
        self.assertEqual(self.state.get_mode(), "stick")
        self.say("walker mode")
        self.assertEqual(self.state.get_mode(), "walker")

    def test_pause_and_resume_alerts(self):
        self.say("stop alerts")
        self.assertFalse(self.state.get_alerts_enabled())
        self.say("resume alerts")
        self.assertTrue(self.state.get_alerts_enabled())

    def test_gait_status_reports_when_no_metrics(self):
        self.assertIn("walker mode", self.say("gait status")[0].lower())

    def test_gait_status_reports_live_metrics(self):
        self.state.set_gait_metrics({"pattern": "Active Walking", "symmetry": 91, "cadence": 88})
        msg = self.say("gait status")[0]
        self.assertIn("91", msg)
        self.assertIn("88", msg)

    def test_detailed_question_degrades_gracefully_without_llm(self):
        msgs = self.say("why does my left leg feel weaker than the right one today?")
        self.assertEqual(len(msgs), 1)
        self.assertIn("internet", msgs[0].lower())

    def test_unrecognised_command_gets_help_hint(self):
        self.assertIn("voice help", self.say("banana")[0])

    def test_llm_call_returns_none_without_key_or_network(self):
        self.assertIsNone(llm_online.ask_mobility_llm("hello there", FakeGroq(["x"])))


class OnlineLlmTests(VoiceBase):
    def setUp(self):
        super().setUp()
        S.enter(self, mock.patch.object(Config, "GROQ_API_KEY", "test-key"))
        S.enter(self, mock.patch.object(Config, "ENABLE_ONLINE_LLM", True))
        self.online = S.enter(self, mock.patch.object(llm_online, "is_internet_available", return_value=True))
        S.enter(self, mock.patch.object(voice_commands, "is_internet_available", self.online))

    def ask(self, groq):
        voice_commands.handle_wake_utterance(
            f"{Config.WAKE_WORD} explain how to improve my step symmetry", groq)
        return drain(self.state)

    def test_reply_is_spoken(self):
        g = FakeGroq(["Take smaller, even steps."])
        self.assertEqual(self.ask(g), ["Take smaller, even steps."])

    def test_llm_cooldown_blocks_back_to_back_calls(self):
        g = FakeGroq(["one", "two"])
        self.ask(g)
        self.clock.advance(Config.LLM_COOLDOWN_SEC / 2)
        self.ask(g)
        self.assertEqual(g.calls, 1)
        self.clock.advance(Config.LLM_COOLDOWN_SEC)
        self.ask(g)
        self.assertEqual(g.calls, 2)

    def test_repeated_failures_open_the_breaker_and_stop_calling_the_api(self):
        g = FakeGroq([RuntimeError("503")] * 10)
        for _ in range(4):
            self.clock.advance(Config.LLM_COOLDOWN_SEC + 1)
            self.ask(g)
        self.assertEqual(self.breaker.state, "OPEN")
        calls = g.calls
        self.clock.advance(Config.LLM_COOLDOWN_SEC + 1)
        self.ask(g)
        self.assertEqual(g.calls, calls)  # skipped while open

    def test_breaker_recovers_after_timeout_when_api_is_back(self):
        g = FakeGroq([RuntimeError("503")] * 4 + ["Back online."])
        for _ in range(4):
            self.clock.advance(Config.LLM_COOLDOWN_SEC + 1)
            self.ask(g)
        self.clock.advance(46)
        self.assertEqual(self.ask(g), ["Back online."])
        self.assertEqual(self.breaker.state, "CLOSED")

    def test_offline_while_half_open_does_not_strand_the_breaker(self):
        g = FakeGroq([RuntimeError("timeout")] * 4 + ["Hello again."])
        for _ in range(4):
            self.clock.advance(Config.LLM_COOLDOWN_SEC + 1)
            self.ask(g)
        self.assertEqual(self.breaker.state, "OPEN")

        # Network is down when the retry window opens: the probe must be handed back.
        self.online.return_value = False
        self.clock.advance(46)
        self.ask(g)
        self.clock.advance(Config.LLM_COOLDOWN_SEC + 1)

        # Network returns: the very next question must be answered, not refused forever.
        self.online.return_value = True
        self.assertEqual(self.ask(g), ["Hello again."])

    def test_empty_model_reply_does_not_strand_the_breaker(self):
        g = FakeGroq([RuntimeError("x")] * 4 + ["   ", "Fine now."])
        for _ in range(4):
            self.clock.advance(Config.LLM_COOLDOWN_SEC + 1)
            self.ask(g)
        self.clock.advance(46)
        self.ask(g)  # probe returns blank text
        self.clock.advance(Config.LLM_COOLDOWN_SEC + 1)
        self.assertEqual(self.ask(g), ["Fine now."])

    def test_context_snapshot_contains_mode_and_detections(self):
        self.state.set_mode("stick")
        self.state.set_detections([{"class": "chair"}, {"class": "chair"}, {"class": "person"}])
        ctx = llm_online.mobility_context_snapshot()
        self.assertIn("mode=stick", ctx)
        self.assertIn("chair×2", ctx)
        self.assertIn("person×1", ctx)


class RoutingHeuristicTests(unittest.TestCase):
    def test_short_commands_stay_offline_questions_go_to_llm(self):
        f = voice_commands.should_use_llm_for_remainder
        self.assertFalse(f("gait tips"))
        self.assertFalse(f("stick mode"))
        self.assertTrue(f("what is cadence"))
        self.assertTrue(f("is my walking getting better?"))
        self.assertTrue(f("x" * Config.LLM_VOICE_MIN_CHARS))


if __name__ == "__main__":
    unittest.main()
