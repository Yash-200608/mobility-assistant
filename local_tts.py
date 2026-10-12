"""On-device text-to-speech fallback (pyttsx3). No network, no API key.

pyttsx3 is optional: if it is not installed, ``speak`` raises and the caller
logs it, so the rest of the system is unaffected.
"""
from __future__ import annotations

import threading
from typing import Callable

_lock = threading.Lock()


def speak(text: str, on_start: Callable[[], None]) -> None:
    try:
        import pyttsx3
    except ImportError as e:
        raise RuntimeError("pyttsx3 is not installed (pip install pyttsx3)") from e
    with _lock:
        # A fresh engine per utterance: pyttsx3 engines are not safe to reuse
        # across threads and can hang if the audio device changed.
        engine = pyttsx3.init()
        try:
            engine.setProperty("rate", 165)
            on_start()
            engine.say(text)
            engine.runAndWait()
        finally:
            try:
                engine.stop()
            except Exception:
                pass
