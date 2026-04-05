"""Emergency SMS + spoken confirmation; shared by hardware button and voice."""

import time

from twilio.rest import Client

from config import Config
from core import global_state, logger


def _send_twilio_sms(sensors: dict, source: str) -> None:
    lat = sensors.get("gps_lat", 0.0)
    lng = sensors.get("gps_lng", 0.0)
    map_link = f"https://www.google.com/maps?q={lat},{lng}"
    via = "voice command" if source == "voice" else "SOS button"
    msg_body = (
        f"🚨 EMERGENCY: AI Mobility Assistant SOS via {via}.\n"
        f"Location: {map_link}\n"
        f"Time: {time.strftime('%H:%M:%S')}"
    )
    client = Client(Config.TWILIO_SID, Config.TWILIO_TOKEN)
    client.messages.create(
        body=msg_body,
        from_=Config.TWILIO_PHONE,
        to=Config.EMERGENCY_PHONE,
    )


def trigger_sos(source: str = "hardware") -> bool:
    """
    Send emergency SMS (if Twilio works) and queue spoken confirmation.
    Returns False if rate-limited by cooldown.
    """
    if not global_state.try_acquire_sos_cooldown(Config.SOS_COOLDOWN_SEC):
        logger.info("SOS ignored: still within cooldown window.")
        global_state.queue_alert("S O S was sent recently. Wait before sending again.", force=True)
        return False
    sensors = global_state.get_sensor_data()
    try:
        _send_twilio_sms(sensors, source)
        logger.info(f"SOS SMS dispatched ({source}).")
    except Exception as e:
        logger.error(f"Failed to send SOS SMS: {e}")
    if source == "voice":
        global_state.queue_alert(
            "Voice S O S activated. Notifying your emergency contact.",
            force=True,
        )
    else:
        global_state.queue_alert(
            "S O S activated. Notifying emergency contact.",
            force=True,
        )
    return True


def try_voice_sos_from_transcript(text_l: str) -> bool:
    """If transcript matches voice SOS rules, trigger SOS and return True."""
    if not text_l:
        return False
    if Config.SOS_VOICE_REQUIRES_WAKE:
        if Config.WAKE_WORD.lower() not in text_l:
            return False
        phrases = Config.SOS_VOICE_PHRASES
    else:
        phrases = Config.SOS_VOICE_PHRASES_NO_WAKE
    if not any(p in text_l for p in phrases):
        return False
    trigger_sos("voice")
    return True
