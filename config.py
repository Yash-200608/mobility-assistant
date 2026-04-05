import os
import logging
from typing import List, Tuple
from dotenv import load_dotenv

load_dotenv()

class Config:
    # API Keys
    GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
    ELEVENLABS_API_KEY: str | None = os.getenv("ELEVENLABS_API_KEY")

    # Online LLM (Groq Chat — same key as Whisper; only used when network check passes)
    ENABLE_ONLINE_LLM: bool = os.getenv("ENABLE_ONLINE_LLM", "true").lower() in ("1", "true", "yes", "on")
    GROQ_LLM_MODEL: str = os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile")
    GROQ_LLM_MAX_TOKENS: int = int(os.getenv("GROQ_LLM_MAX_TOKENS", "220"))
    LLM_COOLDOWN_SEC: float = float(os.getenv("LLM_COOLDOWN_SEC", "8"))
    # Utterances shorter than this (and without detail cues) use offline gait / help, not the LLM.
    LLM_VOICE_MIN_CHARS: int = int(os.getenv("LLM_VOICE_MIN_CHARS", "36"))

    @staticmethod
    def enable_online_llm() -> bool:
        return bool(Config.GROQ_API_KEY) and Config.ENABLE_ONLINE_LLM

    # Speech-to-text: auto tries local faster-whisper first when enabled, then Groq.
    STT_BACKEND: str = os.getenv("STT_BACKEND", "auto").lower().strip()
    LOCAL_STT_ENABLED: bool = os.getenv("LOCAL_STT_ENABLED", "true").lower() in ("1", "true", "yes", "on")
    LOCAL_STT_MODEL: str = os.getenv("LOCAL_STT_MODEL", "base")
    LOCAL_STT_DEVICE: str = os.getenv("LOCAL_STT_DEVICE", "cpu")
    LOCAL_STT_COMPUTE_TYPE: str = os.getenv("LOCAL_STT_COMPUTE_TYPE", "int8")
    LOCAL_STT_LANGUAGE: str = os.getenv("LOCAL_STT_LANGUAGE", "en")
    AUDIO_SAMPLE_RATE: int = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
    AUDIO_CHUNK_SEC: float = float(os.getenv("AUDIO_CHUNK_SEC", "2.0"))

    # Voice SOS (substring match on transcript; wake word required by default)
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

    # Twilio SOS Settings
    TWILIO_SID = "AC01a1d0308732c7680d646d6934f7195f" 
    TWILIO_TOKEN = "430c9720bb0c635d5409c656b10ced0b"
    TWILIO_PHONE = "+16623988325"
    EMERGENCY_PHONE = "+919211059110"
    TWILIO_MESSAGING_SERVICE_SID = "MG0a7549211d71d0f09d2fd3e5d9fdcf4c"

    # System
    CAMERA_URL = 0
    # Must match mobility_assistant.ino Serial.begin(...) (57600 for reliable AVR + SoftwareSerial)
    ARDUINO_BAUD: int = int(os.getenv("ARDUINO_BAUD", "57600"))
    LOG_LEVEL: int = logging.INFO

    # Patient safety: how sensor hardware is used vs camera/mic-only operation.
    # - "software_only": camera, mic, and local vision only; IMU/US/GPS not used for safety alerts
    #   (avoids acting on default/stale serial values when no Arduino is attached).
    # - "fused": use hardware when connected and telemetry is fresh; fuse with vision; adaptive vision rate.
    # - "hardware_strict": if hardware is missing or telemetry is stale, show blank output (fail-closed).
    SAFETY_RUN_MODE: str = os.getenv("SAFETY_RUN_MODE", "software_only")
    SENSOR_STALE_SEC: float = 2.0
    US_FAR_CM: float = 120.0  # beyond this, throttle vision slightly when fused
    VISION_FAST_INTERVAL: float = 0.06  # when obstacle likely (fused + close US)
    VISION_SLOW_INTERVAL: float = 0.14  # when fused + clear path ahead

    # Vision & ML (Local Edge AI)
    LOCAL_MODEL_WEIGHTS: str = "yolov8n.pt" 
    VISION_MIN_INTERVAL: float = 0.1 
    VISION_CONFIDENCE: float = 0.45
    TRACKED_TTL: int = 10
    IOU_MATCH_THRESH: float = 0.3

    # Vision Gait Thresholds
    GAIT_VIRTUAL_FLOOR_Y: float = 0.85 

    # Audio
    ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"
    ELEVENLABS_MODEL: str = "eleven_turbo_v2"
    WAKE_WORD: str = "assistant"
    PYGAME_FREQ: int = 44100

    # Sensor Thresholds
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

    # Fall Detection
    FALL_ACCEL_G: float = 2.8
    FALL_PITCH_DEG: int = 50
    FALL_ROLL_DEG: int = 45
    TILT_WARN_PITCH: int = 35
    TILT_WARN_ROLL: int = 25
    FALL_CONFIRM_FRAMES: int = 3
    FALL_STILL_THRESH: float = 1.3
    FALL_RECOVERY_ACCEL_LO: float = 0.8
    FALL_RECOVERY_ACCEL_HI: float = 1.2
    FALL_RECOVERY_PITCH: int = 30
    FALL_RECOVERY_ROLL: int = 25
    FALL_RECOVERY_DURATION: float = 2.0
    FALL_ALERT_INTERVAL: float = 8.0
    TILT_ALERT_INTERVAL: float = 4.0

    # Navigation & Alerts
    NAV_ALERT_INTERVAL: float = 3.0
    STAIRS_ALERT_INTERVAL: float = 4.0
    ULTRASONIC_ALERT_INTERVAL: float = 3.0
    GAIT_ALERT_COOLDOWN: float = 6.0
    DIST_THRESHOLDS: List[Tuple[float, str]] = [
        (0.12, "very close"), (0.05, "close"), (0.015, "near"), (0.0, "far"),
    ]

    # Path A+B — posture, pre-fall, research logging (not clinically validated defaults)
    POSTURE_MIN_NOSE_HIP_GAP: float = float(os.getenv("POSTURE_MIN_NOSE_HIP_GAP", "0.10"))
    POSTURE_SLOUCH_ALERT_THRESH: int = int(os.getenv("POSTURE_SLOUCH_ALERT_THRESH", "55"))
    PREFALL_TILT_RATE_DEG_S: float = float(os.getenv("PREFALL_TILT_RATE_DEG_S", "55"))
    PREFALL_ALERT_INTERVAL: float = float(os.getenv("PREFALL_ALERT_INTERVAL", "6"))
    RESEARCH_LOG_ENABLED: bool = os.getenv("RESEARCH_LOG_ENABLED", "true").lower() in ("1", "true", "yes", "on")
    RESEARCH_LOG_INTERVAL_SEC: float = float(os.getenv("RESEARCH_LOG_INTERVAL_SEC", "0.5"))
    RESEARCH_EXPORT_DIR: str = os.getenv("RESEARCH_EXPORT_DIR", "exports")
    AUDIO_IMPACT_RMS: float = float(os.getenv("AUDIO_IMPACT_RMS", "0.08"))
    AUDIO_IMPACT_COOLDOWN: float = float(os.getenv("AUDIO_IMPACT_COOLDOWN", "4.0"))