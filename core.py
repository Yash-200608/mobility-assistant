import time
import queue
import logging
import threading
from typing import Dict, Tuple
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
    """Prevents system hangs by cutting off failing external APIs."""
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
                return False  # Only one test request allowed per recovery cycle
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
    """Centralized, rigorously locked state container."""
    def __init__(self):
        self._lock = threading.RLock()
        self.mode: str = "stick"
        self.alerts_enabled: bool = True
        self.is_shutting_down: bool = False
        self.arduino_connected: bool = False
        
        self.latest_sensor: Dict[str, float] = {
            "us_front": 999.0, "us_floor": 10.0, "fsr_left": 0.0, "fsr_right": 0.0,
            "ax": 0.0, "ay": 0.0, "az": 1.0, "gx": 0.0, "gy": 0.0, "gz": 0.0,
            "pitch": 0.0, "roll": 0.0, "accel_mag": 1.0,
            "gps_lat": 0.0, "gps_lng": 0.0, "gps_spd": 0.0, "gps_fix": 0.0
        }
        self.latest_frame = None
        self.latest_detections: list = []
        self.alert_queue = queue.Queue(maxsize=15)

    def update_sensor_data(self, valid_data: Dict[str, float]):
        with self._lock: self.latest_sensor.update(valid_data)

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
        """Non-blocking queueing with forced eviction for critical system alerts."""
        with self._lock:
            if not self.alerts_enabled and not force:
                return
        try:
            self.alert_queue.put_nowait((time.time(), msg))
        except queue.Full:
            if force:
                try: self.alert_queue.get_nowait() # Evict oldest
                except queue.Empty: pass
                try: self.alert_queue.put_nowait((time.time(), msg))
                except queue.Full: pass

global_state = SystemState()
vision_circuit = CircuitBreaker()