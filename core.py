import time
import queue
import logging
import threading
from typing import Any, Dict, Optional, Tuple
from config import Config

def setup_logger():
    logger = logging.getLogger("AI_Mobility")
    logger.setLevel(Config.LOG_LEVEL)
    formatter = logging.Formatter('%(asctime)s | %(levelname)-8s | %(threadName)-12s | %(message)s')
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    if not logger.handlers:
        logger.addHandler(ch)
    return logger

logger = setup_logger()

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"
        self.lock = threading.Lock()

    def can_execute(self) -> bool:
        with self.lock:
            if self.state == "CLOSED": return True
            if self.state == "OPEN":
                if time.time() - self.last_failure_time > self.recovery_timeout:
                    self.state = "HALF_OPEN"
                    return True
                return False
            if self.state == "HALF_OPEN":
                return False
            return False

    def record_success(self):
        with self.lock:
            self.failures = 0
            self.state = "CLOSED"

    def record_failure(self):
        with self.lock:
            self.failures += 1
            self.last_failure_time = time.time()
            if self.failures >= self.failure_threshold:
                self.state = "OPEN"
                logger.warning("Circuit Breaker OPEN. API calls halted to prevent system drag.")

class SystemState:
    def __init__(self):
        self._lock = threading.RLock()
        self.mode: str = "stick"
        self.alerts_enabled: bool = True
        self.is_shutting_down: bool = False
        self.arduino_connected: bool = False
        self.last_sensor_update_mono: float = 0.0

        self.latest_sensor: Dict[str, float] = {
            "us_front": 999.0, "us_floor": 10.0, "fsr_left": 0.0, "fsr_right": 0.0,
            "ax": 0.0, "ay": 0.0, "az": 1.0, "gx": 0.0, "gy": 0.0, "gz": 0.0,
            "pitch": 0.0, "roll": 0.0, "accel_mag": 1.0,
            "gps_lat": 0.0, "gps_lng": 0.0, "gps_spd": 0.0, "gps_fix": 0.0
        }
        self.latest_frame = None
        self.latest_detections: list = []
        self.alert_queue = queue.Queue(maxsize=15)
        self._llm_last_call_mono: float = 0.0
        self._last_sos_mono: float = 0.0
        self._last_gait_metrics: Optional[Dict[str, Any]] = None
        self._rehab_llm_context: str = ""
        self._last_mic_rms: float = 0.0

    def set_last_mic_rms(self, rms: float):
        with self._lock:
            self._last_mic_rms = float(rms)

    def get_last_mic_rms(self) -> float:
        with self._lock:
            return self._last_mic_rms

    def set_rehab_llm_context(self, text: str):
        with self._lock:
            self._rehab_llm_context = (text or "")[:500]

    def get_rehab_llm_context(self) -> str:
        with self._lock:
            return self._rehab_llm_context

    def llm_cooldown_elapsed(self, cooldown_sec: float) -> bool:
        with self._lock:
            return (time.monotonic() - self._llm_last_call_mono) >= cooldown_sec

    def mark_llm_called(self):
        with self._lock:
            self._llm_last_call_mono = time.monotonic()

    def try_acquire_sos_cooldown(self, cooldown_sec: float) -> bool:
        with self._lock:
            now = time.monotonic()
            if now - self._last_sos_mono < cooldown_sec:
                return False
            self._last_sos_mono = now
            return True

    def set_gait_metrics(self, metrics: Optional[Dict[str, Any]]):
        with self._lock:
            if metrics is None:
                self._last_gait_metrics = None
            else:
                self._last_gait_metrics = {
                    "pattern": metrics.get("pattern", "—"),
                    "symmetry": int(metrics.get("symmetry", 0)),
                    "cadence": int(metrics.get("cadence", 0)),
                    "events": list(metrics.get("events") or []),
                }

    def get_gait_metrics(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._last_gait_metrics) if self._last_gait_metrics else None

    def update_sensor_data(self, valid_data: Dict[str, float]):
        with self._lock:
            self.latest_sensor.update(valid_data)
            self.last_sensor_update_mono = time.monotonic()

    def sensors_fresh(self) -> bool:
        with self._lock:
            if not self.arduino_connected:
                return False
            age = time.monotonic() - self.last_sensor_update_mono
            return age <= Config.SENSOR_STALE_SEC and self.last_sensor_update_mono > 0.0

    def use_hardware_for_safety(self) -> bool:
        mode = (Config.SAFETY_RUN_MODE or "software_only").lower().strip()
        if mode != "fused":
            return False
        return self.sensors_fresh()

    def get_sensor_data(self) -> Dict[str, float]:
        with self._lock: return self.latest_sensor.copy()

    def get_frame(self):
        with self._lock: return self.latest_frame.copy() if self.latest_frame is not None else None

    def set_frame(self, frame):
        with self._lock: self.latest_frame = frame

    def get_detections(self):
        with self._lock: return list(self.latest_detections)

    def set_detections(self, detections: list):
        with self._lock: self.latest_detections = detections

    def set_mode(self, new_mode: str):
        with self._lock:
            self.mode = new_mode
            logger.info(f"System mode transitioned to: {new_mode.upper()}")

    def get_mode(self) -> str:
        with self._lock: return self.mode

    def get_alerts_enabled(self) -> bool:
        with self._lock: return self.alerts_enabled

    def set_alerts_enabled(self, value: bool):
        with self._lock:
            self.alerts_enabled = value
            logger.info(f"Alerts {'enabled' if value else 'disabled'}.")

    def queue_alert(self, msg: str, force: bool = False):
        with self._lock:
            if not self.alerts_enabled and not force:
                return
        try:
            self.alert_queue.put_nowait((time.time(), msg))
        except queue.Full:
            if force:
                try: self.alert_queue.get_nowait()
                except queue.Empty: pass
                try: self.alert_queue.put_nowait((time.time(), msg))
                except queue.Full: pass

global_state = SystemState()
vision_circuit = CircuitBreaker()
llm_circuit = CircuitBreaker(failure_threshold=4, recovery_timeout=45.0)
