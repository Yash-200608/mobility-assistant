"""Offline gait / mode voice commands; online LLM only for longer or explicitly detailed questions."""

import random
from typing import Optional

from groq import Groq

from config import Config
from core import global_state
from llm_online import ask_mobility_llm, is_internet_available

GAIT_TIPS = [
    "Lift your toes slightly as you swing each foot forward, then land heel first, softly.",
    "Keep your head level and look about three meters ahead, not at your feet.",
    "Take slightly shorter steps if you feel unsteady; slower is steadier.",
    "Try to match left and right step length; even rhythm helps symmetry.",
    "Pause at a counter or firm chair if you need a short reset before continuing.",
]

_DETAIL_CUES = (
    "explain",
    "describe",
    "tell me more",
    "tell me about",
    "why ",
    "why?",
    "what is",
    "what are",
    "how does",
    "how do",
    "difference",
    "compare",
    "detailed",
    "in detail",
    "meaning of",
    "example",
    "science",
    "research",
    "article",
)


def _gait_status_message() -> str:
    m = global_state.get_gait_metrics()
    if not m:
        return (
            "Switch to walker mode and stand in view of the camera for live gait numbers."
        )
    return (
        f"Gait snapshot. Pattern: {m.get('pattern', 'unknown')}. "
        f"Symmetry about {m.get('symmetry', 0)} percent. "
        f"Cadence about {m.get('cadence', 0)} steps per minute."
    )


def should_use_llm_for_remainder(remainder: str) -> bool:
    r = remainder.strip()
    if len(r) >= Config.LLM_VOICE_MIN_CHARS:
        return True
    if "?" in r:
        return True
    if any(cue in r for cue in _DETAIL_CUES):
        return True
    return False


def _try_offline_mode_and_gait(text_l: str) -> bool:
    if "walker mode" in text_l:
        global_state.set_mode("walker")
        global_state.queue_alert("Switched to walker mode.", force=True)
        return True
    if "stick mode" in text_l:
        global_state.set_mode("stick")
        global_state.queue_alert("Switched to stick mode.", force=True)
        return True
    if any(p in text_l for p in ("gait status", "walking status", "how is my gait", "my gait")):
        global_state.queue_alert(_gait_status_message(), force=True)
        return True
    if any(p in text_l for p in ("gait tips", "gait tip", "walking tips", "training tip")):
        global_state.queue_alert(random.choice(GAIT_TIPS), force=True)
        return True
    if "symmetry" in text_l and ("tip" in text_l or "help" in text_l):
        global_state.queue_alert(
            "For symmetry, aim for equal step length and time on each foot. "
            "Walker mode shows a symmetry score on screen.",
            force=True,
        )
        return True
    if "cadence" in text_l and ("tip" in text_l or "help" in text_l):
        global_state.queue_alert(
            "Cadence is steps per minute. In walker mode we estimate it when your heels cross the virtual floor line.",
            force=True,
        )
        return True
    if any(p in text_l for p in ("heel strike", "heel to toe", "foot placement")):
        global_state.queue_alert(
            "Practice soft heel-first contact, then roll through the foot. Slow down if placement feels rushed.",
            force=True,
        )
        return True
    if any(p in text_l for p in ("virtual floor", "floor line")):
        global_state.queue_alert(
            "The purple virtual floor line on screen is a guide. When each heel passes below it, we count a step for cadence.",
            force=True,
        )
        return True
    if any(p in text_l for p in ("what mode", "current mode", "which mode")):
        global_state.queue_alert(f"You are in {global_state.get_mode()} mode.", force=True)
        return True
    if any(p in text_l for p in ("voice help", "what can you say", "help commands", "command list")):
        global_state.queue_alert(
            "Say walker or stick mode, stop or resume alerts, gait status or gait tips, "
            "or with the wake word say send SOS or emergency help to contact your emergency number. "
            "Long questions need internet.",
            force=True,
        )
        return True
    return False


def _invoke_llm(remainder: str, groq_client: Groq) -> None:
    if not global_state.llm_cooldown_elapsed(Config.LLM_COOLDOWN_SEC):
        return
    global_state.mark_llm_called()
    reply = ask_mobility_llm(remainder, groq_client)
    if reply:
        global_state.queue_alert(reply, force=True)
    elif not is_internet_available():
        global_state.queue_alert(
            "I need an internet connection for detailed answers. Try gait tips or gait status offline.",
            force=True,
        )


def handle_wake_utterance(text: str, groq_client: Optional[Groq]) -> None:
    """Process speech that already contains the wake word (lowercased transcript)."""
    text_l = text.lower().strip()
    wake = Config.WAKE_WORD.lower()
    remainder = text_l.replace(wake, " ")
    remainder = " ".join(remainder.split()).strip()

    if "stop alerts" in text_l:
        global_state.set_alerts_enabled(False)
        global_state.queue_alert("Alerts paused.", force=True)
        return
    if "resume alerts" in text_l:
        global_state.set_alerts_enabled(True)
        global_state.queue_alert("Alerts resumed.", force=True)
        return

    if remainder and should_use_llm_for_remainder(remainder):
        if Config.enable_online_llm() and groq_client:
            _invoke_llm(remainder, groq_client)
        else:
            global_state.queue_alert(
                "Detailed answers need internet and your cloud assistant setup. "
                "Try gait status or gait tips for offline coaching.",
                force=True,
            )
        return

    if _try_offline_mode_and_gait(text_l):
        return

    if remainder:
        global_state.queue_alert(
            "Say gait status, gait tips, or voice help for offline coaching. "
            "For detailed questions, connect to the internet and ask a longer question.",
            force=True,
        )
