
from __future__ import annotations

import math
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from config import Config
from core import logger

class GaitFingerprint:

    def __init__(self, window: int = 45):
        self._sym = deque(maxlen=window)
        self._cad = deque(maxlen=window)

    def update(self, symmetry: int, cadence: int) -> None:
        if cadence > 3:
            self._sym.append(float(symmetry))
            self._cad.append(float(cadence))

    def deviation_score(self) -> float:
        if len(self._sym) < 8:
            return 0.0
        s = list(self._sym)
        c = list(self._cad)
        ms, mc = sum(s) / len(s), sum(c) / len(c)
        vs = math.sqrt(sum((x - ms) ** 2 for x in s) / len(s)) or 1.0
        vc = math.sqrt(sum((x - mc) ** 2 for x in c) / len(c)) or 1.0
        ds = abs(s[-1] - ms) / vs
        dc = abs(c[-1] - mc) / vc
        return float(min(10.0, (ds + dc) / 2.0))

    def baseline_summary(self) -> Tuple[float, float]:
        if not self._sym:
            return 0.0, 0.0
        return sum(self._sym) / len(self._sym), sum(self._cad) / len(self._cad)

class EnergyExpenditure:

    @staticmethod
    def estimate_met(cadence_spm: int, active_walk: bool) -> float:
        if not active_walk or cadence_spm < 5:
            return 1.0
        return float(min(5.5, 2.0 + cadence_spm / 75.0))

class PostureMonitor:

    @staticmethod
    def slouch_score(landmarks) -> int:
        try:
            nose_y = landmarks[0].y
            hip_y = (landmarks[23].y + landmarks[24].y) / 2.0
            gap = hip_y - nose_y
            if gap >= Config.POSTURE_MIN_NOSE_HIP_GAP:
                return 0
            deficit = Config.POSTURE_MIN_NOSE_HIP_GAP - gap
            return int(min(100, 100.0 * deficit / max(0.02, Config.POSTURE_MIN_NOSE_HIP_GAP)))
        except (IndexError, AttributeError, TypeError):
            return 0

    @staticmethod
    def alert_if_bad(slouch: int) -> Optional[str]:
        if slouch >= Config.POSTURE_SLOUCH_ALERT_THRESH:
            return "Try gently lifting your chest and looking a little forward."
        return None

class PreFallDetector:

    def __init__(self):
        self._pp = self._pr = 0.0
        self._pt = 0.0
        self._last_alert = 0.0

    def process(self, sensors: Dict[str, float], fall_state: str = "UPRIGHT") -> Optional[str]:
        if fall_state not in ("UPRIGHT", "TILT_WARNING"):
            return None
        now = time.time()
        if now - self._last_alert < Config.PREFALL_ALERT_INTERVAL:
            return None
        pitch = float(sensors.get("pitch", 0.0))
        roll = float(sensors.get("roll", 0.0))
        if self._pt <= 0.0:
            self._pp, self._pr, self._pt = pitch, roll, now
            return None
        dt = now - self._pt
        if dt <= 1e-3 or dt > 0.4:
            self._pp, self._pr, self._pt = pitch, roll, now
            return None
        dp = (pitch - self._pp) / dt
        dr = (roll - self._pr) / dt
        self._pp, self._pr, self._pt = pitch, roll, now
        if max(abs(dp), abs(dr)) >= Config.PREFALL_TILT_RATE_DEG_S:
            self._last_alert = now
            return "Unsteady motion sensed. Widen your stance and hold something stable if needed."
        return None

class PhaseDetector:

    def __init__(self):
        self.l_strikes = 0
        self.r_strikes = 0

    def ingest_events(self, events: List[str]) -> None:
        for e in events or []:
            if e == "L_FOOT":
                self.l_strikes += 1
            elif e == "R_FOOT":
                self.r_strikes += 1

    def asymmetry_ratio(self) -> float:
        t = self.l_strikes + self.r_strikes
        if t < 4:
            return 0.0
        return abs(self.l_strikes - self.r_strikes) / float(t)

    def reset(self) -> None:
        self.l_strikes = self.r_strikes = 0

class StrideLengthProxy:

    @staticmethod
    def estimate(landmarks) -> float:
        try:
            ankle_w = abs(landmarks[27].x - landmarks[28].x)
            shoulder_w = abs(landmarks[11].x - landmarks[12].x)
            if shoulder_w < 1e-4:
                shoulder_w = 0.1
            return float(ankle_w / shoulder_w)
        except (IndexError, AttributeError, TypeError):
            return 0.0

class FallDirectionPredictor:

    @staticmethod
    def predict(pitch_deg: float, roll_deg: float) -> str:
        if abs(pitch_deg) >= abs(roll_deg):
            return "forward" if pitch_deg > 0 else "backward"
        return "to the right" if roll_deg > 0 else "to the left"

class RehabProgressTracker:

    def __init__(self):
        self.session_start = time.time()
        self.frames_walker = 0
        self.sum_sym = 0
        self.sum_cad = 0
        self.n_metrics = 0
        self.max_deviation = 0.0
        self.prefall_alerts = 0
        self.slouch_alerts = 0

    def tick_walker(self, sym: int, cad: int, dev: float) -> None:
        self.frames_walker += 1
        self.sum_sym += sym
        self.sum_cad += cad
        self.n_metrics += 1
        self.max_deviation = max(self.max_deviation, dev)

    def summary(self) -> Dict[str, Any]:
        elapsed = max(1e-3, time.time() - self.session_start)
        n = max(1, self.n_metrics)
        return {
            "session_seconds": round(elapsed, 1),
            "walker_frames": self.frames_walker,
            "avg_symmetry": round(self.sum_sym / n, 1),
            "avg_cadence": round(self.sum_cad / n, 1),
            "max_gait_deviation": round(self.max_deviation, 2),
            "prefall_alerts": self.prefall_alerts,
            "slouch_alerts": self.slouch_alerts,
        }

class AdaptiveCoachingEngine:

    @staticmethod
    def context_line(
        gait_deviation: float,
        met: float,
        slouch: int,
        phase_asym: float,
    ) -> str:
        parts = []
        if gait_deviation > 2.5:
            parts.append("gait_recently_unusual_vs_baseline")
        if met > 3.8:
            parts.append("estimated_effort_elevated")
        if slouch >= 40:
            parts.append("posture_may_need_cueing")
        if phase_asym > 0.35:
            parts.append("left_right_step_imbalance")
        if not parts:
            parts.append("steady_state")
        return "coaching_hints=" + ",".join(parts)

def build_rehab_frame(
    mode: str,
    fall_state: str,
    metrics: Optional[Dict[str, Any]],
    sensors: Dict[str, float],
    fingerprint: GaitFingerprint,
    phase: PhaseDetector,
    stride_val: float,
    slouch: int,
    met: float,
    prefall_fired: bool,
    audio_impact: bool,
    direction_hint: str,
) -> Dict[str, Any]:
    sym = int(metrics.get("symmetry", 0)) if metrics else 0
    cad = int(metrics.get("cadence", 0)) if metrics else 0
    dev = fingerprint.deviation_score() if metrics and cad > 3 else 0.0
    return {
        "t": time.time(),
        "mode": mode,
        "fall_state": fall_state,
        "symmetry": sym,
        "cadence": cad,
        "gait_deviation_z": round(dev, 3),
        "met_proxy": round(met, 2),
        "slouch_score": slouch,
        "phase_asym": round(phase.asymmetry_ratio(), 3),
        "stride_proxy": round(stride_val, 3),
        "pitch": round(sensors.get("pitch", 0.0), 2),
        "roll": round(sensors.get("roll", 0.0), 2),
        "accel_mag": round(sensors.get("accel_mag", 0.0), 2),
        "prefall_alert": int(prefall_fired),
        "audio_impact_hint": int(audio_impact),
        "fall_direction_hint": direction_hint,
    }

RESEARCH_CSV_FIELDS = (
    "t",
    "mode",
    "fall_state",
    "symmetry",
    "cadence",
    "gait_deviation_z",
    "met_proxy",
    "slouch_score",
    "phase_asym",
    "stride_proxy",
    "pitch",
    "roll",
    "accel_mag",
    "prefall_alert",
    "audio_impact_hint",
    "fall_direction_hint",
)
