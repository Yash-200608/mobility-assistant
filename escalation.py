"""Automatic escalation after a confirmed fall.

A confirmed fall opens a short cancel window. If the user does not say they are
okay (and does not get back up) before it closes, an SOS goes out automatically.
Disabled by default (``FALL_AUTO_SOS=false``) until the false-alarm rate has been
measured; see docs/EVALUATION.md.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from config import Config
from core import logger

IDLE, PENDING, SENT, CANCELLED = "IDLE", "PENDING", "SENT", "CANCELLED"
_RECOVERED_STATES = ("RECOVERING", "UPRIGHT")


class FallEscalator:
    def __init__(self, state=None, sos_fn: Optional[Callable[[str], bool]] = None):
        self._state_obj = state
        self._sos_fn = sos_fn
        self._lock = threading.Lock()
        self.status = IDLE
        self.deadline: Optional[float] = None

    # Late-bound so tests (and main) can swap the shared state / SOS sender.
    @property
    def _gs(self):
        if self._state_obj is not None:
            return self._state_obj
        from core import global_state
        return global_state

    def _sos(self, source: str) -> bool:
        if self._sos_fn is not None:
            return self._sos_fn(source)
        from sos import trigger_sos
        return trigger_sos(source)

    @property
    def pending(self) -> bool:
        return self.status == PENDING

    def on_fall_confirmed(self, now: float) -> None:
        if not Config.FALL_AUTO_SOS:
            return
        with self._lock:
            if self.status == PENDING:
                return
            self.status = PENDING
            self.deadline = now + Config.FALL_ESCALATION_SEC
        secs = int(Config.FALL_ESCALATION_SEC)
        self._gs.queue_alert(
            f"Fall detected. Say I'm okay within {secs} seconds, or I will send an S O S.",
            force=True,
        )
        logger.warning(f"Fall escalation armed: SOS in {secs}s unless cancelled.")

    def cancel(self, reason: str = "user") -> bool:
        with self._lock:
            if self.status != PENDING:
                return False
            self.status = CANCELLED
            self.deadline = None
        logger.info(f"Fall escalation cancelled ({reason}).")
        if reason == "recovered":
            self._gs.queue_alert("Glad you are back up. I will not send an S O S.", force=True)
        else:
            self._gs.queue_alert("Okay. I will not send an S O S.", force=True)
        return True

    def tick(self, now: float, fall_state: str) -> Optional[str]:
        """Call every loop. Returns 'cancelled', 'sent' or None when something happened."""
        fire = False
        with self._lock:
            if self.status == PENDING:
                if fall_state in _RECOVERED_STATES:
                    pass  # handled below, outside the lock
                elif self.deadline is not None and now >= self.deadline:
                    self.status = SENT
                    self.deadline = None
                    fire = True
            elif self.status in (SENT, CANCELLED) and fall_state not in ("FALLEN", "FALLING"):
                self.status = IDLE  # re-arm for the next, separate fall
        if self.status == PENDING and fall_state in _RECOVERED_STATES:
            return "cancelled" if self.cancel("recovered") else None
        if fire:
            self._sos("fall")
            return "sent"
        return None

    def try_cancel_from_transcript(self, text_l: str) -> bool:
        if not self.pending or not text_l:
            return False
        if any(p in text_l for p in Config.ESCALATION_CANCEL_PHRASES):
            return self.cancel("voice")
        return False


# Shared instance used by the orchestrator and the voice listener.
fall_escalator = FallEscalator()
