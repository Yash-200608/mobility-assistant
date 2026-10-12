"""Orchestration logic that used to live inline in main.py's loop.

Both classes are free of OpenCV/MediaPipe/serial so they can be driven with a
fake clock and synthetic data (see tests/test_pipeline.py and demo_sim.py).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from analytics import FallDetector, GaitAnalyser
from analytics_enhanced import (
    AdaptiveCoachingEngine,
    EnergyExpenditure,
    FallDirectionPredictor,
    GaitFingerprint,
    PhaseDetector,
    PostureMonitor,
    PreFallDetector,
    RehabProgressTracker,
    StrideLengthProxy,
    build_rehab_frame,
)
from audio_fallcontext import AudioFallDetector
from config import Config
from core import global_state, logger
from escalation import FallEscalator, fall_escalator


@dataclass
class SafetyResult:
    fall_state: str
    fall_alert: Optional[str] = None
    prefall_alert: Optional[str] = None
    sos_requested: bool = False
    escalation: Optional[str] = None      # "sent" | "cancelled" | None
    newly_fallen: bool = False


class HardwareSafety:
    """IMU-driven safety: SOS button, fall state machine, pre-fall, escalation, buzzer."""

    def __init__(
        self,
        state=None,
        fall_detector: Optional[FallDetector] = None,
        prefall: Optional[PreFallDetector] = None,
        escalator: Optional[FallEscalator] = None,
        sos_fn: Optional[Callable[[str], bool]] = None,
        buzz_fn: Optional[Callable[[str], bool]] = None,
    ):
        self._state = state
        self.fall = fall_detector or FallDetector()
        self.prefall = prefall or PreFallDetector()
        self.escalator = escalator or fall_escalator
        self._sos_fn = sos_fn
        self._buzz_fn = buzz_fn
        self._prev_state = "UPRIGHT"
        self._armed_at = float("-inf")

    ARM_QUIET_SEC = 1.0

    @property
    def _gs(self):
        return self._state if self._state is not None else global_state

    def _sos(self, source: str) -> bool:
        if self._sos_fn is not None:
            return self._sos_fn(source)
        from sos import trigger_sos
        return trigger_sos(source)

    def _buzz(self, pattern: str) -> None:
        if self._buzz_fn is None:
            return
        try:
            self._buzz_fn(pattern)
        except Exception as e:  # a dead buzzer must never break the safety loop
            logger.warning(f"Buzzer command failed: {type(e).__name__}")

    def step(self, sensors: Dict[str, float], now: Optional[float] = None) -> SafetyResult:
        now = now if now is not None else time.time()
        res = SafetyResult(fall_state=self.fall.state)

        if sensors.get("sos", 0) == 1:
            res.sos_requested = True
            self._sos("hardware")
            self._buzz("sos")

        res.fall_alert = self.fall.process(sensors, now)
        res.fall_state = self.fall.state
        res.newly_fallen = res.fall_state == "FALLEN" and self._prev_state != "FALLEN"

        if res.newly_fallen:
            # Arm first: the escalation prompt already asks "are you okay?", so the
            # generic prompt that follows immediately would just talk over it.
            self.escalator.on_fall_confirmed(now)
            if self.escalator.pending:
                self._armed_at = now
        if res.fall_alert:
            just_armed = self.escalator.pending and now - self._armed_at < self.ARM_QUIET_SEC
            acknowledged = self.escalator.status == "CANCELLED"
            if not (just_armed or acknowledged):
                self._gs.queue_alert(res.fall_alert, force=True)

        res.prefall_alert = self.prefall.process(sensors, res.fall_state, now)
        if res.prefall_alert:
            self._gs.queue_alert(res.prefall_alert, force=True)

        if res.newly_fallen:
            self._buzz("fall")
        elif self._prev_state == "FALLEN" and res.fall_state != "FALLEN":
            self._buzz("off")
        self._prev_state = res.fall_state

        res.escalation = self.escalator.tick(now, res.fall_state)
        return res

    def idle_tick(self, now: Optional[float] = None) -> Optional[str]:
        """Keep a pending escalation running when the sensor feed has gone stale:
        the user has not cancelled, so the deadline must still be honoured."""
        now = now if now is not None else time.time()
        return self.escalator.tick(now, self.fall.state)


@dataclass
class WalkerFrame:
    metrics: Dict[str, Any]
    symmetry: int
    cadence: int
    deviation: float
    met: float
    slouch: int
    stride: float
    phase_asym: float
    direction_hint: str
    audio_hit: bool
    gait_alert: Optional[str] = None
    posture_alert: Optional[str] = None
    logged_row: Optional[Dict[str, Any]] = field(default=None, repr=False)


class WalkerAnalytics:
    """Camera-landmark analytics for walker mode (gait, posture, coaching, logging)."""

    SLOUCH_ALERT_MIN_GAP_SEC = 15.0

    def __init__(self, state=None, rehab: Optional[RehabProgressTracker] = None,
                 log_fn: Optional[Callable[[Dict[str, Any]], None]] = None):
        self._state = state
        self.gait = GaitAnalyser()
        self.fingerprint = GaitFingerprint()
        self.phase = PhaseDetector()
        self.rehab = rehab or RehabProgressTracker()
        self.audio = AudioFallDetector()
        self._log_fn = log_fn
        self._last_slouch_alert = 0.0
        self._last_log = 0.0

    @property
    def _gs(self):
        return self._state if self._state is not None else global_state

    def step(self, landmarks, mode: str, fall_state: str, sensors: Dict[str, float],
             hardware_alerts: bool, prefall_fired: bool = False,
             now: Optional[float] = None) -> WalkerFrame:
        now = now if now is not None else time.time()
        gs = self._gs

        metrics = self.gait.process_vision(landmarks)
        gs.set_gait_metrics(metrics)
        gait_alert = self.gait.get_alert(metrics)
        if gait_alert:
            gs.queue_alert(gait_alert)

        sym, cad = metrics["symmetry"], metrics["cadence"]
        self.phase.ingest_events(metrics.get("events") or [])
        self.fingerprint.update(sym, cad)
        dev = self.fingerprint.deviation_score()
        met = EnergyExpenditure.estimate_met(cad, metrics.get("pattern") == "Active Walking")
        slouch = PostureMonitor.slouch_score(landmarks)
        stride = StrideLengthProxy.estimate(landmarks)
        phase_asym = self.phase.asymmetry_ratio()
        gs.set_rehab_llm_context(AdaptiveCoachingEngine.context_line(dev, met, slouch, phase_asym))

        posture_alert = PostureMonitor.alert_if_bad(slouch)
        if posture_alert and now - self._last_slouch_alert > self.SLOUCH_ALERT_MIN_GAP_SEC:
            gs.queue_alert(posture_alert, force=True)
            self._last_slouch_alert = now
            self.rehab.slouch_alerts += 1
        else:
            posture_alert = None

        if cad > 3:
            self.rehab.tick_walker(sym, cad, dev)

        direction_hint = ""
        if hardware_alerts and fall_state in ("FALLING", "FALLEN"):
            direction_hint = FallDirectionPredictor.predict(
                float(sensors.get("pitch", 0.0)), float(sensors.get("roll", 0.0)))
        audio_hit = self.audio.check_loud_transient(gs.get_last_mic_rms(), now)

        row = None
        if now - self._last_log >= Config.RESEARCH_LOG_INTERVAL_SEC:
            row = build_rehab_frame(
                mode, fall_state, metrics, sensors, self.fingerprint, self.phase,
                stride, slouch, met, bool(prefall_fired) and hardware_alerts, audio_hit, direction_hint,
            )
            if self._log_fn is not None:
                self._log_fn(row)
            self._last_log = now

        return WalkerFrame(metrics, sym, cad, dev, met, slouch, stride, phase_asym,
                           direction_hint, audio_hit, gait_alert, posture_alert, row)
