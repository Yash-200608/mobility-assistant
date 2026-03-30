import time
import json
import serial
import serial.tools.list_ports
import threading
from core import global_state, logger
from config import Config

class HardwareManager:
    def __init__(self):
        self.port = None
        self.thread = threading.Thread(target=self._serial_loop, daemon=True, name="ArduinoIO")

    def start(self):
        self.thread.start()

    def _find_port(self) -> str | None:
        for port in serial.tools.list_ports.comports():
            desc = port.description.lower()
            if any(k in desc for k in ["arduino", "ch340", "cp210", "ftdi", "usb serial"]):
                return port.device
        return None

    def _validate_payload(self, data: dict) -> dict:
        """Defensive clamping against hardware glitches."""
        clean = {}
        for key, val in data.items():
            try:
                f_val = float(val)
                if key in Config.SENSOR_LIMITS:
                    min_v, max_v = Config.SENSOR_LIMITS[key]
                    clean[key] = max(min_v, min(f_val, max_v))
                else:
                    clean[key] = f_val
            except (ValueError, TypeError):
                continue
        return clean

    def _serial_loop(self):
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
                    
                    while not global_state.is_shutting_down:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        if line.startswith('{'):
                            try:
                                payload = json.loads(line)
                                clean_data = self._validate_payload(payload)
                                global_state.update_sensor_data(clean_data)
                            except json.JSONDecodeError:
                                pass 
            except Exception as e:
                global_state.arduino_connected = False
                logger.warning(f"Hardware disconnected: {type(e).__name__}. Attempting recovery...")
                time.sleep(2.0)