import time
from collections import deque
from typing import Dict, Optional
from config import Config
from core import logger

class FallDetector:
    def __init__(self):
        self.state = "UPRIGHT"
        self.fall_time: Optional[float] = None
        self.accel_history = deque(maxlen=10)
        self.last_alert_time: float = 0
        self.last_tilt_alert: float = 0
        self.recovery_start: Optional[float] = None
        self.post_fall_still_count: int = 0

    def process(self, sensor: Dict[str, float]) -> Optional[str]:
        pitch = sensor.get("pitch", 0.0)
        roll = sensor.get("roll", 0.0)
        accel_mag = sensor.get("accel_mag", 1.0)
        self.accel_history.append(accel_mag)
        avg_accel = sum(self.accel_history) / len(self.accel_history) if self.accel_history else 1.0
        current_time = time.time()
        alert = None

        if self.state == "UPRIGHT":
            if accel_mag > Config.FALL_ACCEL_G:
                self.state = "FALLING"
                self.fall_time = current_time
                logger.warning("Fall detected! Accel spike.")
            elif abs(pitch) > Config.TILT_WARN_PITCH or abs(roll) > Config.TILT_WARN_ROLL:
                self.state = "TILT_WARNING"

        elif self.state == "TILT_WARNING":
            if abs(pitch) > Config.FALL_PITCH_DEG or abs(roll) > Config.FALL_ROLL_DEG:
                self.state = "FALLING"
                self.fall_time = current_time
            elif abs(pitch) < (Config.TILT_WARN_PITCH * 0.7) and abs(roll) < (Config.TILT_WARN_ROLL * 0.7):
                self.state = "UPRIGHT"
            else:
                if current_time - self.last_tilt_alert > Config.TILT_ALERT_INTERVAL:
                    alert = "Warning, tilt detected. Please stabilise."
                    self.last_tilt_alert = current_time

        elif self.state == "FALLING":
            if self.fall_time and current_time - self.fall_time > 0.3:
                if avg_accel < Config.FALL_STILL_THRESH:
                    self.post_fall_still_count += 1
                    if self.post_fall_still_count >= Config.FALL_CONFIRM_FRAMES:
                        self.state = "FALLEN"
                        self.post_fall_still_count = 0  # Reset for next fall cycle
                else:
                    self.state = "UPRIGHT"
                    self.post_fall_still_count = 0

        elif self.state == "FALLEN":
            if current_time - self.last_alert_time > Config.FALL_ALERT_INTERVAL:
                alert = "Fall detected. Do you need assistance?"
                self.last_alert_time = current_time
            
            if (Config.FALL_RECOVERY_ACCEL_LO <= accel_mag <= Config.FALL_RECOVERY_ACCEL_HI) and \
               abs(pitch) < Config.FALL_RECOVERY_PITCH and abs(roll) < Config.FALL_RECOVERY_ROLL:
                if self.recovery_start is None:
                    self.recovery_start = current_time
                elif current_time - self.recovery_start > Config.FALL_RECOVERY_DURATION:
                    self.state = "RECOVERING"
                    self.recovery_start = None
            else:
                self.recovery_start = None

        elif self.state == "RECOVERING":
            if (Config.FALL_RECOVERY_ACCEL_LO <= accel_mag <= Config.FALL_RECOVERY_ACCEL_HI) and \
               abs(pitch) < Config.FALL_RECOVERY_PITCH and abs(roll) < Config.FALL_RECOVERY_ROLL:
                if self.recovery_start is None:
                    self.recovery_start = current_time
                elif current_time - self.recovery_start > Config.FALL_RECOVERY_DURATION:
                    self.state = "UPRIGHT"
                    alert = "Recovery detected. Welcome back."
                    self.recovery_start = None
            else:
                self.state = "FALLEN"
                self.recovery_start = None

        return alert

class GaitAnalyser:
    def __init__(self):
        self.l_loaded: bool = False
        self.r_loaded: bool = False
        self.walker_moving: bool = False
        self.l_strike_times: deque = deque(maxlen=10)
        self.r_strike_times: deque = deque(maxlen=10)
        self.prev_l_strike: Optional[float] = None
        self.prev_r_strike: Optional[float] = None
        self.step_times: deque = deque(maxlen=20)
        self.phase_buffer: deque = deque(maxlen=Config.GAIT_BUFFER_SIZE)
        self.last_event_t: Dict[str, float] = {"WALKER": 0, "L_FOOT": 0, "R_FOOT": 0}
        self.last_any_event: float = time.time()
        self.current_pattern: str = "Analysing..."
        self.pattern_confidence: float = 0.0
        self.affected_side: Optional[str] = None
        self.trunk_sway_history: deque = deque(maxlen=30)
        self.last_alert_time: float = 0.0
        self.last_alert_text: str = ""

    def process(self, sensor: Dict[str, float]) -> Dict:
        t = time.time()
        fsr_l = sensor.get("fsr_left", 0)
        fsr_r = sensor.get("fsr_right", 0)
        ax = sensor.get("ax", 0)
        ay = sensor.get("ay", 0)
        roll = sensor.get("roll", 0)
        
        self.trunk_sway_history.append(abs(roll))
        horiz_accel = abs(ax) + abs(ay)
        events_this_frame = []

        if horiz_accel > Config.GAIT_WALKER_HORIZ_THRESH and not self.walker_moving:
            if t - self.last_event_t["WALKER"] > Config.GAIT_EVENT_COOLDOWN:
                self.walker_moving = True
                events_this_frame.append("WALKER")
                self.last_event_t["WALKER"] = t
        elif horiz_accel < Config.GAIT_WALKER_IDLE_THRESH:
            self.walker_moving = False

        if fsr_l > Config.FSR_THRESHOLD and not self.l_loaded:
            if t - self.last_event_t["L_FOOT"] > Config.GAIT_EVENT_COOLDOWN:
                self.l_loaded = True
                events_this_frame.append("L_FOOT")
                self.last_event_t["L_FOOT"] = t
                if self.prev_l_strike: self.l_strike_times.append(t - self.prev_l_strike)
                self.prev_l_strike = t
                self.step_times.append(t)
        elif fsr_l <= Config.FSR_THRESHOLD:
            self.l_loaded = False

        if fsr_r > Config.FSR_THRESHOLD and not self.r_loaded:
            if t - self.last_event_t["R_FOOT"] > Config.GAIT_EVENT_COOLDOWN:
                self.r_loaded = True
                events_this_frame.append("R_FOOT")
                self.last_event_t["R_FOOT"] = t
                if self.prev_r_strike: self.r_strike_times.append(t - self.prev_r_strike)
                self.prev_r_strike = t
                self.step_times.append(t)
        elif fsr_r <= Config.FSR_THRESHOLD:
            self.r_loaded = False

        for ev in events_this_frame:
            self.phase_buffer.append(ev)
            self.last_any_event = t

        if t - self.last_any_event > Config.GAIT_PAUSE_THRESH and (not self.phase_buffer or self.phase_buffer[-1] != "PAUSE"):
            self.phase_buffer.append("PAUSE")
            self.last_any_event = t
            events_this_frame.append("PAUSE")

        self._analyse_affected_side()
        self._analyse_pattern()
        
        recent_steps = [s for s in self.step_times if t - s < 10.0]
        cadence = len(recent_steps) * 6 if len(recent_steps) >= 2 else None
        trunk_sway = sum(self.trunk_sway_history)/len(self.trunk_sway_history) if self.trunk_sway_history else 0
        
        symmetry = None
        if self.l_strike_times and self.r_strike_times:
            l_avg = sum(self.l_strike_times) / len(self.l_strike_times)
            r_avg = sum(self.r_strike_times) / len(self.r_strike_times)
            if max(l_avg, r_avg) > 0:
                symmetry = int((min(l_avg, r_avg) / max(l_avg, r_avg)) * 100)

        return {
            "pattern": self.current_pattern, "confidence": self.pattern_confidence,
            "events": events_this_frame, "trunk_sway": trunk_sway, "symmetry": symmetry,
            "affected": self.affected_side, "cadence": cadence, 
            "phase_buffer": list(self.phase_buffer)
        }

    def _analyse_affected_side(self):
        if len(self.l_strike_times) >= Config.GAIT_AFFECTED_MIN_STEPS and len(self.r_strike_times) >= Config.GAIT_AFFECTED_MIN_STEPS:
            l_avg = sum(self.l_strike_times) / len(self.l_strike_times)
            r_avg = sum(self.r_strike_times) / len(self.r_strike_times)
            if abs(l_avg - r_avg) >= Config.GAIT_AFFECTED_MIN_DIFF:
                self.affected_side = "LEFT" if l_avg > r_avg else "RIGHT"

    def _analyse_pattern(self):
        if len(self.phase_buffer) < 2: return
        pb = list(self.phase_buffer)
        
        norm_pb = []
        for p in pb:
            if p in ("L_FOOT", "R_FOOT"):
                if self.affected_side:
                    if (p == "L_FOOT" and self.affected_side == "LEFT") or (p == "R_FOOT" and self.affected_side == "RIGHT"):
                        norm_pb.append("AFFECTED")
                    else: norm_pb.append("UNAFFECTED")
                else: norm_pb.append("FOOT")
            else: norm_pb.append(p)

        pb_str = ",".join(norm_pb[-5:])
        
        if "WALKER,FOOT,WALKER,FOOT,PAUSE" in pb_str or "WALKER,AFFECTED,WALKER,UNAFFECTED,PAUSE" in pb_str:
            self.current_pattern, self.pattern_confidence = "5-Point Gait", 0.95
        elif "WALKER,FOOT,WALKER,FOOT" in pb_str or "WALKER,AFFECTED,WALKER,UNAFFECTED" in pb_str:
            if len(pb) >= 4 and pb[-1] in ("L_FOOT", "R_FOOT") and pb[-3] in ("L_FOOT", "R_FOOT") and pb[-1] != pb[-3]:
                self.current_pattern, self.pattern_confidence = "4-Point Gait", 0.95
        elif "WALKER,AFFECTED,UNAFFECTED" in pb_str:
            self.current_pattern, self.pattern_confidence = "3-Point Gait", 0.95
        elif "WALKER,FOOT,FOOT" in pb_str and pb[-1] != pb[-2] and not self.affected_side:
            self.current_pattern, self.pattern_confidence = "3-Point Gait", 0.80
        elif "WALKER,AFFECTED,UNAFFECTED" in pb_str and "WALKER,AFFECTED" in pb_str:
            self.current_pattern, self.pattern_confidence = "2-Point Gait", 0.90
        elif "WALKER,UNAFFECTED" in pb_str:
             self.current_pattern, self.pattern_confidence = "2-Point Gait", 0.75

    def get_alert(self, metrics: Dict) -> Optional[str]:
        t = time.time()
        if t - self.last_alert_time < Config.GAIT_ALERT_COOLDOWN: return None
        alert = None
        
        if metrics["trunk_sway"] >= Config.GAIT_SWAY_WARN:
            alert = "Excessive trunk sway. Please stand taller."
        elif metrics["symmetry"] and metrics["symmetry"] < Config.GAIT_SYMMETRY_WARN:
            alert = "Step asymmetry detected. Try to equalize your steps."
        elif metrics["cadence"] and metrics["cadence"] < Config.GAIT_CADENCE_WARN:
            alert = "Cadence is low. Keep a steady pace."
        elif self.current_pattern != self.last_alert_text and self.pattern_confidence > 0.8:
            if "2-Point" in self.current_pattern: alert = "Good 2-point pattern."
            elif "3-Point" in self.current_pattern: alert = "3-point gait detected."
            elif "4-Point" in self.current_pattern: alert = "4-point gait detected. Good stability."
            elif "5-Point" in self.current_pattern: alert = "5-point gait detected. Good control."
            self.last_alert_text = self.current_pattern

        if alert: self.last_alert_time = t
        return alert