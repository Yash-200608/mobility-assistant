"""Speech-to-text: local faster-whisper and/or Groq Whisper API."""

import numpy as np

from audio_util import pcm_to_wav
from config import Config
from core import logger


def transcribe_microphone_chunk(
    pcm_float32: np.ndarray,
    sample_rate: int,
    groq_client,
) -> str:
    """
    Return lowercased transcript. Respects STT_BACKEND: auto | local | groq.
    """
    backend = (Config.STT_BACKEND or "auto").lower().strip()
    pcm_float32 = np.asarray(pcm_float32, dtype=np.float32)

    use_local = backend == "local" or (backend == "auto" and Config.LOCAL_STT_ENABLED)
    if use_local:
        from local_stt import transcribe_local, local_stt_available

        if local_stt_available():
            text = transcribe_local(pcm_float32, sample_rate)
            if text:
                return text
            if backend == "local":
                return ""
        elif backend == "local":
            logger.warning("STT_BACKEND=local but faster-whisper is not available.")
            return ""

    use_groq = groq_client is not None and (backend == "groq" or backend == "auto")
    if use_groq:
        try:
            completion = groq_client.audio.transcriptions.create(
                file=("audio.wav", pcm_to_wav(pcm_float32, sample_rate)),
                model="whisper-large-v3-turbo",
                language="en",
                temperature=0.0,
            )
            return (completion.text or "").lower().strip()
        except Exception as e:
            logger.error(f"Groq STT failed: {e}")

    return ""


def stt_configured(groq_client) -> bool:
    backend = (Config.STT_BACKEND or "auto").lower().strip()
    if backend == "groq":
        return groq_client is not None
    if groq_client is not None:
        return True
    if backend == "local" or (backend == "auto" and Config.LOCAL_STT_ENABLED):
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            logger.error(
                "Local STT selected but faster-whisper is missing. Run: pip install faster-whisper"
            )
            return False
        return True
    return False
