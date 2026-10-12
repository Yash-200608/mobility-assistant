
import threading
import time

from twilio.rest import Client

from config import Config
from core import global_state, logger

_SOURCE_LABEL = {
    "voice": "voice command",
    "fall": "automatic fall detection",
}

def _send_twilio_sms(sensors: dict, source: str) -> None:
    if not Config.twilio_configured():
        raise RuntimeError("Twilio is not configured (set TWILIO_* and EMERGENCY_PHONE in .env)")
    lat = sensors.get("gps_lat", 0.0)
    lng = sensors.get("gps_lng", 0.0)
    map_link = f"https://www.google.com/maps?q={lat},{lng}"
    via = _SOURCE_LABEL.get(source, "SOS button")
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

def _run_in_background(fn) -> threading.Thread:
    t = threading.Thread(target=fn, name="SOSDispatch", daemon=True)
    t.start()
    return t

def _deliver(sensors: dict, source: str) -> bool:
    """Send the SMS with retries. Tells the user the truth about the outcome."""
    attempts = max(1, Config.SOS_SEND_ATTEMPTS)
    delay = Config.SOS_ATTEMPT_BACKOFF_SEC
    for i in range(attempts):
        try:
            _send_twilio_sms(sensors, source)
            logger.info(f"SOS SMS dispatched ({source}) on attempt {i + 1}.")
            global_state.queue_alert("S O S sent. Your emergency contact has been notified.", force=True)
            return True
        except Exception as e:
            logger.error(f"Failed to send SOS SMS (attempt {i + 1}/{attempts}): {e}")
            if i < attempts - 1:
                time.sleep(delay)
                delay *= 2
    # Do not burn the full cooldown on a failed send: allow a retry soon.
    global_state.rearm_sos_after_failure(Config.SOS_COOLDOWN_SEC, Config.SOS_RETRY_SEC)
    global_state.queue_alert(
        "S O S message could not be sent. Call your emergency contact directly.",
        force=True,
    )
    return False

def trigger_sos(source: str = "hardware", block: bool = False) -> bool:
    """Request an SOS. Returns False only if throttled by the cooldown.

    By default the network send happens on a background thread so a slow or dead
    connection can never stall the safety loop that called this. ``block=True``
    runs the send inline and returns whether it was delivered.
    """
    if not global_state.try_acquire_sos_cooldown(Config.SOS_COOLDOWN_SEC):
        logger.info("SOS ignored: still within cooldown window.")
        global_state.queue_alert("S O S was sent recently. Wait before sending again.", force=True)
        return False
    sensors = global_state.get_sensor_data()
    global_state.queue_alert("S O S requested. Sending your message now.", force=True)
    if block:
        return _deliver(sensors, source)
    _run_in_background(lambda: _deliver(sensors, source))
    return True

def try_voice_sos_from_transcript(text_l: str) -> bool:
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
