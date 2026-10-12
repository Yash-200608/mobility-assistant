import os
import logging
from typing import List, Tuple
from dotenv import load_dotenv

load_dotenv()

class Config:

    GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
    ELEVENLABS_API_KEY: str | None = os.getenv("ELEVENLABS_API_KEY")

    ENABLE_ONLINE_LLM: bool = os.getenv("ENABLE_ONLINE_LLM", "true").lower() in ("1", "true", "yes", "on")
    GROQ_LLM_MODEL: str = os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile")
    GROQ_LLM_MAX_TOKENS: int = int(os.getenv("GROQ_LLM_MAX_TOKENS", "220"))
    LLM_COOLDOWN_SEC: float = float(os.getenv("LLM_COOLDOWN_SEC", "8"))

    LLM_VOICE_MIN_CHARS: int = int(os.getenv("LLM_VOICE_MIN_CHARS", "36"))

    @staticmethod
    def enable_online_llm() -> bool:
        return bool(Config.GROQ_API_KEY) and Config.ENABLE_ONLINE_LLM

    STT_BACKEND: str = os.getenv("STT_BACKEND", "auto").lower().strip()
    LOCAL_STT_ENABLED: bool = os.getenv("LOCAL_STT_ENABLED", "true").lower() in ("1", "true", "yes", "on")
    LOCAL_STT_MODEL: str = os.getenv("LOCAL_STT_MODEL", "base")
    LOCAL_STT_DEVICE: str = os.getenv("LOCAL_STT_DEVICE", "cpu")
    LOCAL_STT_COMPUTE_TYPE: str = os.getenv("LOCAL_STT_COMPUTE_TYPE", "int8")
    LOCAL_STT_LANGUAGE: str = os.getenv("LOCAL_STT_LANGUAGE", "en")
    AUDIO_SAMPLE_RATE: int = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
    AUDIO_CHUNK_SEC: float = float(os.getenv("AUDIO_CHUNK_SEC", "2.0"))

    SOS_COOLDOWN_SEC: float = float(os.getenv("SOS_COOLDOWN_SEC", "45"))
    SOS_VOICE_REQUIRES_WAKE: bool = os.getenv("SOS_VOICE_REQUIRES_WAKE", "true").lower() in ("1", "true", "yes", "on")
    SOS_VOICE_PHRASES: Tuple[str, ...] = (
        "send sos",
        "voice sos",
        "sos emergency",
        "emergency sos",
        "call for help",
        "call emergency",
        "send help",
        "i need help now",
        "need help now",
        "medical emergency",
        "i've fallen",
        "ive fallen",
        "i fell help",
        "emergency help",
    )
    SOS_VOICE_PHRASES_NO_WAKE: Tuple[str, ...] = (
        "help i've fallen",
        "help ive fallen",
        "medical emergency help",
        "call emergency services",
    )

    # After a failed send, allow another attempt after this many seconds
    # (instead of locking the user out for the full SOS_COOLDOWN_SEC).
    SOS_RETRY_SEC: float = float(os.getenv("SOS_RETRY_SEC", "10"))

    SOS_SEND_ATTEMPTS: int = int(os.getenv("SOS_SEND_ATTEMPTS", "3"))
    SOS_ATTEMPT_BACKOFF_SEC: float = float(os.getenv("SOS_ATTEMPT_BACKOFF_SEC", "2"))

    # Automatic SOS after a confirmed fall. OFF by default: the false-alarm rate is
    # unmeasured, and a false positive would text the emergency contact.
    FALL_AUTO_SOS: bool = os.getenv("FALL_AUTO_SOS", "false").lower() in ("1", "true", "yes", "on")
    FALL_ESCALATION_SEC: float = float(os.getenv("FALL_ESCALATION_SEC", "20"))
    ESCALATION_CANCEL_PHRASES: Tuple[str, ...] = (
        "i'm okay", "im okay", "i am okay", "i'm ok", "im ok", "i am ok",
        "i'm fine", "im fine", "i am fine", "i'm alright", "im alright",
        "false alarm", "cancel",
    )

    # Credentials and contact numbers come from the environment (.env), never source.
    TWILIO_SID: str | None = os.getenv("TWILIO_SID")
    TWILIO_TOKEN: str | None = os.getenv("TWILIO_TOKEN")
    TWILIO_PHONE: str | None = os.getenv("TWILIO_PHONE")
    EMERGENCY_PHONE: str | None = os.getenv("EMERGENCY_PHONE")

    @staticmethod
    def twilio_configured() -> bool:
        return all((Config.TWILIO_SID, Config.TWILIO_TOKEN, Config.TWILIO_PHONE, Config.EMERGENCY_PHONE))

    CAMERA_URL = 1

    ARDUINO_BAUD: int = int(os.getenv("ARDUINO_BAUD", "57600"))
    # Set to a file path to append every raw serial JSON line with a timestamp (JSONL),
    # for offline replay with tools/replay_trace.py.
    SENSOR_RECORD_PATH: str = os.getenv("SENSOR_RECORD_PATH", "")
    LOG_LEVEL: int = logging.INFO

    SAFETY_RUN_MODE: str = os.getenv("SAFETY_RUN_MODE", "software_only")
    SENSOR_STALE_SEC: float = 2.0
    US_FAR_CM: float = 120.0
    VISION_FAST_INTERVAL: float = 0.06
    VISION_SLOW_INTERVAL: float = 0.14

    LOCAL_MODEL_WEIGHTS: str = os.getenv("LOCAL_MODEL_WEIGHTS", "yolov8s.pt")
    VISION_MIN_INTERVAL: float = 0.1
    VISION_CONFIDENCE: float = 0.45
    TRACKED_TTL: int = 10
    IOU_MATCH_THRESH: float = 0.3

    GAIT_VIRTUAL_FLOOR_Y: float = 0.85

    LATENCY_LOG_ENABLED: bool = os.getenv("LATENCY_LOG_ENABLED", "true").lower() in ("1", "true", "yes", "on")
    ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"
    ELEVENLABS_MODEL: str = "eleven_turbo_v2"
    WAKE_WORD: str = "assistant"
    PYGAME_FREQ: int = 44100

    SENSOR_LIMITS = {
        "us_front": (-1.0, 999.0),
        "us_floor": (-1.0, 999.0),
        "pitch": (-180.0, 180.0),
        "roll": (-180.0, 180.0),
        "accel_mag": (0.0, 16.0)
    }

    US_FRONT_WARN_CM: int = 80
    US_FRONT_STOP_CM: int = 30
    US_FLOOR_DROPOFF_CM: int = 40

    FALL_ACCEL_G: float = 2.8
    FALL_PITCH_DEG: int = 50
    FALL_ROLL_DEG: int = 45
    TILT_WARN_PITCH: int = 35
    TILT_WARN_ROLL: int = 25
    FALL_CONFIRM_FRAMES: int = 3
    FALL_SETTLE_SEC: float = 0.3  # ignore impact ringing before judging stillness
    FALL_STILL_THRESH: float = 1.3
    FALL_RECOVERY_ACCEL_LO: float = 0.8
    FALL_RECOVERY_ACCEL_HI: float = 1.2
    FALL_RECOVERY_PITCH: int = 30
    FALL_RECOVERY_ROLL: int = 25
    FALL_RECOVERY_DURATION: float = 2.0
    FALL_ALERT_INTERVAL: float = 8.0
    TILT_ALERT_INTERVAL: float = 4.0

    NAV_ALERT_INTERVAL: float = 3.0
    STAIRS_ALERT_INTERVAL: float = 4.0
    ULTRASONIC_ALERT_INTERVAL: float = 3.0
    GAIT_ALERT_COOLDOWN: float = 6.0
    DIST_THRESHOLDS: List[Tuple[float, str]] = [
        (0.12, "very close"), (0.05, "close"), (0.015, "near"), (0.0, "far"),
    ]

    POSTURE_MIN_NOSE_HIP_GAP: float = float(os.getenv("POSTURE_MIN_NOSE_HIP_GAP", "0.10"))
    POSTURE_SLOUCH_ALERT_THRESH: int = int(os.getenv("POSTURE_SLOUCH_ALERT_THRESH", "55"))
    PREFALL_TILT_RATE_DEG_S: float = float(os.getenv("PREFALL_TILT_RATE_DEG_S", "55"))
    PREFALL_ALERT_INTERVAL: float = float(os.getenv("PREFALL_ALERT_INTERVAL", "6"))
    RESEARCH_LOG_ENABLED: bool = os.getenv("RESEARCH_LOG_ENABLED", "true").lower() in ("1", "true", "yes", "on")
    RESEARCH_LOG_INTERVAL_SEC: float = float(os.getenv("RESEARCH_LOG_INTERVAL_SEC", "0.5"))
    RESEARCH_EXPORT_DIR: str = os.getenv("RESEARCH_EXPORT_DIR", "exports")
    AUDIO_IMPACT_RMS: float = float(os.getenv("AUDIO_IMPACT_RMS", "0.08"))
    AUDIO_IMPACT_COOLDOWN: float = float(os.getenv("AUDIO_IMPACT_COOLDOWN", "4.0"))

    # ── Bluetooth Audio ──────────────────────────────────────────
    BLUETOOTH_DEVICE_NAME: str = os.getenv("BLUETOOTH_DEVICE_NAME", "")
    BLUETOOTH_INPUT_DEVICE_INDEX: int = int(os.getenv("BLUETOOTH_INPUT_DEVICE_INDEX", "-1"))
    BLUETOOTH_OUTPUT_DEVICE_INDEX: int = int(os.getenv("BLUETOOTH_OUTPUT_DEVICE_INDEX", "-1"))