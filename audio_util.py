"""Shared audio helpers for STT uploads."""

import io
import wave

import numpy as np


def pcm_to_wav(pcm: np.ndarray, samplerate: int) -> bytes:
    pcm_int16 = (pcm * 32767).astype(np.int16)
    byte_io = io.BytesIO()
    with wave.open(byte_io, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(pcm_int16.tobytes())
    return byte_io.getvalue()
