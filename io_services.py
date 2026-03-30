import os
import time
import queue
import base64
import tempfile
import threading
import numpy as np
import cv2
import requests
import sounddevice as sd
import pygame
import groq
from elevenlabs.client import ElevenLabs
from config import Config
from core import global_state, vision_circuit, logger

# --- AUDIO SERVICES ---
groq_client = groq.Groq(api_key=Config.GROQ_API_KEY) if Config.GROQ_API_KEY else None
el_client = ElevenLabs(api_key=Config.ELEVENLABS_API_KEY) if Config.ELEVENLABS_API_KEY else None

try:
    pygame.mixer.init(frequency=Config.PYGAME_FREQ, size=-16, channels=2, buffer=512)
except Exception as e:
    logger.error(f"Pygame init failed: {e}")

def pcm_to_wav(pcm: np.ndarray, samplerate: int) -> bytes:
    import wave, io
    pcm_int16 = (pcm * 32767).astype(np.int16)
    byte_io = io.BytesIO()
    with wave.open(byte_io, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(pcm_int16.tobytes())
    return byte_io.getvalue()

def speak_thread():
    if not el_client:
        logger.warning("TTS thread disabled — missing API key.")
        return
    while not global_state.is_shutting_down:
        try:
            timestamp, text = global_state.alert_queue.get(timeout=1.0)
            if time.time() - timestamp > 8.0: continue # Drop stale alerts
            
            logger.info(f"Speaking: {text}")
            audio_gen = el_client.text_to_speech.convert(
                text=text, voice_id=Config.ELEVENLABS_VOICE_ID,
                model_id=Config.ELEVENLABS_MODEL, output_format="mp3_44100_128"
            )
            audio_bytes = b"".join(list(audio_gen))
            
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
                    fp.write(audio_bytes)
                    tmp_path = fp.name
                pygame.mixer.music.load(tmp_path)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy() and not global_state.is_shutting_down: 
                    time.sleep(0.1)
            finally:
                pygame.mixer.music.unload()
                if tmp_path and os.path.exists(tmp_path):
                    try: os.unlink(tmp_path)
                    except PermissionError: pass
        except queue.Empty:
            continue
        except Exception as e:
            logger.error(f"TTS Error: {e}")

def audio_listener():
    if not groq_client:
        logger.warning("Voice listener disabled — missing API key.")
        return
    samplerate, duration = 16000, 2.0
    while not global_state.is_shutting_down:
        try:
            pcm = sd.rec(int(samplerate * duration), samplerate=samplerate, channels=1, dtype='float32')
            sd.wait()
            if np.sqrt(np.mean(pcm**2)) < 0.001: continue
            
            completion = groq_client.audio.transcriptions.create(
                file=("audio.wav", pcm_to_wav(pcm, samplerate)),
                model="whisper-large-v3-turbo", language="en", temperature=0.0
            )
            text = completion.text.lower().strip()
            
            if Config.WAKE_WORD not in text: continue
            
            if "walker mode" in text:
                global_state.set_mode("walker")
                global_state.queue_alert("Switched to walker mode.", force=True)
            elif "stick mode" in text:
                global_state.set_mode("stick")
                global_state.queue_alert("Switched to stick mode.", force=True)
            elif "stop alerts" in text:
                global_state.set_alerts_enabled(False)
                global_state.queue_alert("Alerts paused.", force=True)
            elif "resume alerts" in text:
                global_state.set_alerts_enabled(True)
                global_state.queue_alert("Alerts resumed.", force=True)
        except Exception as e:
            logger.error(f"Voice Listener Error: {e}")
            time.sleep(1)

# --- VISION & CAMERA SERVICES ---
def camera_thread():
    cap = cv2.VideoCapture(Config.CAMERA_URL)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    while not global_state.is_shutting_down:
        try:
            ret, frame = cap.read()
            if ret:
                frame = cv2.resize(frame, (640, 480))
                global_state.set_frame(frame)
            else:
                time.sleep(0.05)
                cap.release()  # Prevent handle leak
                cap = cv2.VideoCapture(Config.CAMERA_URL)
        except Exception as e:
            logger.error(f"Camera error: {e}")
            time.sleep(1.0)
    cap.release()

def roboflow_thread():
    if not Config.ROBOFLOW_API_KEY:
        logger.warning("Roboflow disabled — missing API key.")
        return
    url = f"https://detect.roboflow.com/{Config.ROBOFLOW_MODEL}"
    while not global_state.is_shutting_down:
        try:
            start_t = time.time()
            if not vision_circuit.can_execute():
                time.sleep(2.0)
                continue
                
            frame = global_state.get_frame()
            if frame is not None:
                h, w = frame.shape[:2]
                resized = cv2.resize(frame, Config.ROBOFLOW_RESIZE)
                _, enc = cv2.imencode('.jpg', resized, [int(cv2.IMWRITE_JPEG_QUALITY), Config.ROBOFLOW_JPEG_QUALITY])
                
                resp = requests.post(
                    url, data=base64.b64encode(enc).decode("utf-8"),
                    params={"api_key": Config.ROBOFLOW_API_KEY, "confidence": Config.ROBOFLOW_CONFIDENCE, "overlap": Config.ROBOFLOW_OVERLAP},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=2.0
                )
                if resp.status_code == 200:
                    vision_circuit.record_success()
                    preds = resp.json().get("predictions", [])
                    for p in preds:
                        p["x"] *= (w / Config.ROBOFLOW_RESIZE[0])
                        p["y"] *= (h / Config.ROBOFLOW_RESIZE[1])
                        p["width"] *= (w / Config.ROBOFLOW_RESIZE[0])
                        p["height"] *= (h / Config.ROBOFLOW_RESIZE[1])
                    global_state.set_detections(preds)
                else:
                    vision_circuit.record_failure()
            elapsed = time.time() - start_t
            time.sleep(max(0, Config.ROBOFLOW_MIN_INTERVAL - elapsed))
        except requests.exceptions.RequestException as e:
            vision_circuit.record_failure()
            safe_err = str(e).replace(Config.ROBOFLOW_API_KEY, "REDACTED")
            logger.error(f"Roboflow Network Error: {safe_err}")
            time.sleep(2.0)

def start_io_services():
    for target in [camera_thread, speak_thread, audio_listener, roboflow_thread]:
        threading.Thread(target=target, daemon=True).start()