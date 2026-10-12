import time
import json
import serial
import serial.tools.list_ports
import threading
from core import global_state, logger
from config import Config
from sensors import validate_payload

# Host -> Arduino commands. The firmware parses "BUZZ:<milliseconds>\n".
BUZZ_PATTERNS_MS = {"fall": 3000, "sos": 1500, "off": 0}


class HardwareManager:
    def __init__(self):
        self.port = None
        self.thread = threading.Thread(target=self._serial_loop, daemon=True, name="ArduinoIO")
        self._ser = None
        self._ser_lock = threading.Lock()
        self._record_fp = None
        self._record_failed = False

    def start(self):
        self.thread.start()

    def _find_port(self) -> str | None:
        for port in serial.tools.list_ports.comports():
            desc = port.description.lower()
            if any(k in desc for k in ["arduino", "ch340", "cp210", "ftdi", "usb serial"]):
                return port.device
        return None

    def _validate_payload(self, data: dict) -> dict:
        return validate_payload(data)

    # ── Host -> Arduino ──────────────────────────────────────────────────────
    def send_command(self, text: str) -> bool:
        """Write one command line to the Arduino. False if there is no live link."""
        with self._ser_lock:
            ser = self._ser
            if ser is None:
                return False
            try:
                ser.write((text.strip() + "\n").encode("ascii", errors="ignore"))
                return True
            except Exception as e:
                logger.warning(f"Arduino command failed: {type(e).__name__}")
                return False

    def buzz(self, pattern: str = "fall") -> bool:
        ms = BUZZ_PATTERNS_MS.get(pattern)
        if ms is None:
            return False
        return self.send_command(f"BUZZ:{ms}")

    # ── Raw-stream recording (for offline replay / threshold tuning) ─────────
    def _record(self, line: str) -> None:
        path = Config.SENSOR_RECORD_PATH
        if not path or self._record_failed:
            return
        try:
            if self._record_fp is None:
                self._record_fp = open(path, "a", encoding="utf-8")
                logger.info(f"Recording raw sensor stream to {path}")
            self._record_fp.write(json.dumps({"t": time.time(), "raw": line}) + "\n")
            self._record_fp.flush()
        except OSError as e:
            # Recording is a convenience: never let it break the safety loop.
            self._record_failed = True
            logger.warning(f"Sensor recording disabled: {e}")

    def close_recording(self) -> None:
        fp, self._record_fp = self._record_fp, None
        if fp is not None:
            try:
                fp.close()
            except OSError:
                pass

    def _serial_loop(self):
        try:
            self._serial_loop_inner()
        finally:
            self.close_recording()

    def _serial_loop_inner(self):
        backoff = 1.0
        while not global_state.is_shutting_down:
            try:
                self.port = self._find_port()
                if not self.port:
                    global_state.arduino_connected = False
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 10.0)
                    continue

                with serial.Serial(self.port, Config.ARDUINO_BAUD, timeout=1) as ser:
                    global_state.arduino_connected = True
                    logger.info(f"Arduino hardware linked on {self.port}")
                    backoff = 1.0
                    with self._ser_lock:
                        self._ser = ser
                    try:
                        while not global_state.is_shutting_down:
                            line = ser.readline().decode('utf-8', errors='ignore').strip()
                            if line.startswith('{'):
                                self._record(line)
                                try:
                                    payload = json.loads(line)
                                    clean_data = self._validate_payload(payload)
                                    # A payload with nothing usable must not refresh the
                                    # freshness timestamp, or stale values look live.
                                    if clean_data:
                                        global_state.update_sensor_data(clean_data)
                                except json.JSONDecodeError:
                                    pass
                    finally:
                        with self._ser_lock:
                            self._ser = None
            except Exception as e:
                global_state.arduino_connected = False
                logger.warning(f"Hardware disconnected: {type(e).__name__}. Attempting recovery...")
                time.sleep(2.0)
