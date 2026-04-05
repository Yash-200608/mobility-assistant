
import time
from typing import Optional

from config import Config

class AudioFallDetector:
    def __init__(self):
        self._last_trigger = 0.0

    def check_loud_transient(self, rms: float, now: Optional[float] = None) -> bool:
        t = now if now is not None else time.time()
        if rms < Config.AUDIO_IMPACT_RMS:
            return False
        if t - self._last_trigger < Config.AUDIO_IMPACT_COOLDOWN:
            return False
        self._last_trigger = t
        return True
