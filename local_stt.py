
import threading
from typing import Any

import numpy as np

from config import Config
from core import logger

_lock = threading.Lock()
_model: Any = None

def _get_model():
    global _model
    with _lock:
        if _model is False:
            return None
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel

            _model = WhisperModel(
                Config.LOCAL_STT_MODEL,
                device=Config.LOCAL_STT_DEVICE,
                compute_type=Config.LOCAL_STT_COMPUTE_TYPE,
            )
            logger.info(
                f"Local STT loaded: model={Config.LOCAL_STT_MODEL} "
                f"device={Config.LOCAL_STT_DEVICE} compute={Config.LOCAL_STT_COMPUTE_TYPE}"
            )
        except Exception as e:
            logger.error(f"Local STT failed to load (install faster-whisper): {e}")
            _model = False
            return None
        return _model

def local_stt_available() -> bool:
    return _get_model() is not None

def transcribe_local(pcm_float32: np.ndarray, sample_rate: int) -> str:
    model = _get_model()
    if model is None:
        return ""
    audio = np.asarray(pcm_float32, dtype=np.float32).flatten()
    segments, _ = model.transcribe(
        audio,
        language=Config.LOCAL_STT_LANGUAGE,
        without_timestamps=True,
        vad_filter=True,
    )
    parts = [s.text.strip() for s in segments]
    return " ".join(parts).strip().lower()
