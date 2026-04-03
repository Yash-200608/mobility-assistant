import os
import logging
from typing import List, Tuple
from dotenv import load_dotenv

load_dotenv()

class Config:
    # API Keys
    GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
    ELEVENLABS_API_KEY: str | None = os.getenv("ELEVENLABS_API_KEY")

    # Twilio SOS Settings
    TWILIO_SID = "AC01a1d0308732c7680d646d6934f7195f" 
    TWILIO_TOKEN = "430c9720bb0c635d5409c656b10ced0b"
    TWILIO_PHONE = "+16623988325"
    EMERGENCY_PHONE = "+919211059110"
    TWILIO_MESSAGING_SERVICE_SID = "MG0a7549211d71d0f09d2fd3e5d9fdcf4c"

    # System
    CAMERA_URL = 0
    ARDUINO_BAUD: int = 115200
    LOG_LEVEL: int = logging.INFO

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