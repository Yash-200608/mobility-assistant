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
                        self.post_fall_still_count = 0
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
        self.l_loaded = False
        self.r_loaded = False
        self.step_times = deque(maxlen=20)
        self.l_strike_times = deque(maxlen=10)
        self.r_strike_times = deque(maxlen=10)
        self.prev_l_t = None
        self.prev_r_t = None
        self.last_alert_time = 0.0

    def process_vision(self, landmarks) -> Dict:
        t = time.time()
        events = []

        if not landmarks:
            return {"pattern": "Searching for User...", "symmetry": 0, "cadence": 0}

        l_heel_y = landmarks[29].y
        r_heel_y = landmarks[30].y

        if l_heel_y > Config.GAIT_VIRTUAL_FLOOR_Y and not self.l_loaded:
            self.l_loaded = True
            events.append("L_FOOT")
            if self.prev_l_t: self.l_strike_times.append(t - self.prev_l_t)
            self.prev_l_t = t
            self.step_times.append(t)
        elif l_heel_y < (Config.GAIT_VIRTUAL_FLOOR_Y - 0.05):
            self.l_loaded = False

        if r_heel_y > Config.GAIT_VIRTUAL_FLOOR_Y and not self.r_loaded:
            self.r_loaded = True
            events.append("R_FOOT")
            if self.prev_r_t: self.r_strike_times.append(t - self.prev_r_t)
            self.prev_r_t = t
            self.step_times.append(t)
        elif r_heel_y < (Config.GAIT_VIRTUAL_FLOOR_Y - 0.05):
            self.r_loaded = False

        symmetry = 100
        if self.l_strike_times and self.r_strike_times:
            l_avg = sum(self.l_strike_times)/len(self.l_strike_times)
            r_avg = sum(self.r_strike_times)/len(self.r_strike_times)
            if max(l_avg, r_avg) > 0:
                symmetry = int((min(l_avg, r_avg) / max(l_avg, r_avg)) * 100)

        cadence = len([s for s in self.step_times if t - s < 10.0]) * 6

        return {
            "events": events,
            "symmetry": symmetry,
            "cadence": cadence,
            "pattern": "Active Walking" if cadence > 10 else "Standing/Idle"
        }

    def get_alert(self, metrics: Dict) -> Optional[str]:
        t = time.time()
        if t - self.last_alert_time < Config.GAIT_ALERT_COOLDOWN: return None
        alert = None

        sym = metrics.get("symmetry", 100)
        cad = metrics.get("cadence", 0)

        if sym < 70 and cad > 0:
            alert = "Step asymmetry detected. Try to equalize your steps."
        elif cad > 0 and cad < 40:
            alert = "Cadence is low. Keep a steady pace."

        if alert: self.last_alert_time = t
        return alert
