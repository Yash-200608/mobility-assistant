# AI Mobility Assistant: Full Project Context & System Architecture

**Status:** Research prototype in development. Not a medical device; not clinically validated.  
**Last Updated:** April 2026  
**Scope:** Real-time vision, sensor fusion, fall detection, gait analysis, voice interaction  

---

## 1. PROJECT OVERVIEW

### Mission

The AI Mobility Assistant is a hybrid edge-cloud research prototype exploring rehabilitation support for people recovering from surgery, stroke, or neurological conditions. It has not been validated with patients. It combines:

- **Live computer vision** (YOLOv8, MediaPipe pose) for obstacle detection and gait analysis
- **Hardware sensor fusion** (IMU, ultrasonic, pressure, GPS) for physical safety
- **Voice interaction** (STT + LLM) for coaching and emergency response
- **Adaptive safety modes** (software-only, fused, hardware-strict) to match deployment environments

The system runs on a Raspberry Pi or edge GPU device attached to a walker or held by the patient, with an Arduino microcontroller managing sensors and physical safety triggers.

---

## 2. HARDWARE ARCHITECTURE

### 2.1 Compute Stack

| Component | Model | Role |
|-----------|-------|------|
| **Main CPU** | Raspberry Pi 4B / Jetson Nano | Python app, vision pipeline, voice I/O |
| **Microcontroller** | Arduino Uno/Mega (ATmega328P/2560) | Sensor acquisition, IMU fusion, serial JSON output |
| **Baud Rate** | 57600 | Hand-tuned for reliability over SoftwareSerial on AVR (`config.py` `ARDUINO_BAUD`) |

### 2.2 Sensor Suite

#### Inertial Measurement Unit (IMU)

- **Device:** MPU-9250 (9-axis: accel, gyro, magnetometer)
- **Connection:** I²C (Wire library)
- **Sampling:** ~20 Hz (50 ms loop tick on Arduino)
- **Purpose:** Orientation and motion signals for **Python** fall/tilt logic
- **On Arduino:** Complementary filter → `pitch`, `roll`; raw `ax, ay, az`, `gx, gy, gz`, `accel_mag` in JSON
- **On Python:** `analytics.FallDetector` consumes serialized IMU fields (not a duplicate FSM on the MCU in this repo)

#### Ultrasonic Rangefinders (2×)

- **Device:** HC-SR04 (Maxbotix alternative compatible)
- **Sensors:**
  - **US_FRONT** (Pins 2→TRIG, 3→ECHO): Forward obstacle at chest level
  - **US_FLOOR** (Pins 4→TRIG, 5→ECHO): Downward drop-off detection
- **Range:** 2–400 cm (effective indoors: ~15–100 cm given `pulseIn` timeout in firmware)
- **Fault tolerance:**
  - Transient miss (no echo): returns **999**
  - 10+ consecutive misses: returns **-1** (sensor fault)

#### Foot Pressure Sensors

- **Planned:** FSR402 or similar
- **Status:** Prepared in `SystemState.latest_sensor` (`fsr_left`, `fsr_right`); not yet emitted by `mobility_assistant.ino`

#### GNSS Receiver

- **Device:** NEO-6M GPS (SoftwareSerial on Pins 10→RX, 11→TX)
- **Baudrate:** 9600 (standard u-blox)
- **Outputs:** `gps_lat`, `gps_lng`, `gps_spd` (km/h), `gps_fix` (0/1)
- **Buffer management:** `drainGPS()` around blocking ultrasonic reads

#### Emergency SOS Button

- **Pin:** 6 (`INPUT_PULLUP`, active LOW)
- **Output:** `sos` (0 or 1)
- **Python:** Handled in `main.py` only when `hardware_alerts` is true (`SAFETY_RUN_MODE=fused` + fresh telemetry)

### 2.3 Complementary Filter (Arduino)

```
pitch = 0.98 * (pitch + gx * dt) + 0.02 * accPitch
roll  = 0.98 * (roll  + gy * dt) + 0.02 * accRoll
```

- **dt:** Clamped when `dt <= 0` or `dt > 0.5` s → use **0.05** s (not a 0.01 s floor)

### 2.4 Serial Output Format

Single-line JSON ~every 50 ms at **57600** baud (field names match `hardware.py` / `Config.SENSOR_LIMITS`).

---

## 3. PYTHON SOFTWARE ARCHITECTURE

### 3.1 Core Modules

#### `core.py`

- **`SystemState`:** Sensors, frame, detections, mode, alert queue, LLM/SOS cooldowns, gait snapshot, `sensors_fresh()` / `use_hardware_for_safety()`
- **`CircuitBreaker`:** Default 3 failures → OPEN 30 s
- **`vision_circuit`:** Instantiated with defaults; `local_vision_thread` calls `record_success()` after non-empty YOLO predictions (extensible for failure tripping)
- **`llm_circuit`:** 4 failures → OPEN 45 s (`llm_online.py`)

#### `hardware.py`

Auto-detect COM port, JSON lines, clamp to `SENSOR_LIMITS`, update `global_state` and `arduino_connected`.

#### `analytics.py`

- **FallDetector:** 5-state FSM in Python using IMU fields from serial
- **GaitAnalyser:** MediaPipe heels vs virtual floor (`GAIT_VIRTUAL_FLOOR_Y`)

#### `io_services.py`

Threads: camera, YOLO, TTS (ElevenLabs), `audio_listener` (STT → `voice_commands` / `sos`).

#### `stt_service.py` / `local_stt.py`

Multi-backend STT (`STT_BACKEND`: auto | local | groq).

#### `llm_online.py`

Groq chat when online + `llm_circuit` allows.

#### `voice_commands.py`

Wake-word routing; offline gait/mode; LLM for long/detail utterances.

#### `sos.py`

Twilio SMS dispatched on a background thread (never stalls the safety loop), with retries and an honest failure alert; shared cooldown, shortened after a failed send; **`SOS_VOICE_REQUIRES_WAKE`** default **true** (no-wake phrases only if set false in config/env). `trigger_sos(source, block=False)`; sources: `hardware`, `voice`, `fall`.

#### `escalation.py`

`FallEscalator`: after a confirmed fall, opens a cancel window (`FALL_ESCALATION_SEC`); an unanswered window sends an SOS (`source="fall"`). Cancelled by voice ("I'm okay", no wake word needed) or by getting back up. Disabled unless `FALL_AUTO_SOS=true`.

#### `pipeline.py`

`HardwareSafety` (SOS button, fall state machine, pre-fall, escalation, buzzer) and `WalkerAnalytics` (gait, posture, coaching, research log). No OpenCV or serial, so they run on a simulated clock in tests and in `tools/demo_sim.py`.

#### `navigation.py`

`ObjectTracker`, `estimate_dist`, `NavigationAdvisor`: stick-mode tracking and spoken direction advice. Pure logic.

#### `speech.py`, `local_tts.py`

`AlertSpeaker` routes each alert to ElevenLabs when it works and to on-device `pyttsx3` otherwise (circuit-breaker protected), and `LatencyRecorder` logs alert-to-audio latency.

#### `sensors.py`

`validate_payload`: the one place sensor JSON is coerced, clamped and NaN/inf-filtered; shared by the live serial loop and offline replay.

#### `main.py`

Thin loop: captures state, calls `pipeline` / `navigation`, draws the HUD.

---

## 4. CONFIGURATION & SAFETY MODES

### 4.1 Notable `config.py` / env vars

- API keys: `GROQ_API_KEY`, `ELEVENLABS_API_KEY`; Twilio via `TWILIO_SID`, `TWILIO_TOKEN`, `TWILIO_PHONE`, `EMERGENCY_PHONE` (all from `.env`)
- `FALL_AUTO_SOS` (default false), `FALL_ESCALATION_SEC` (20), `SOS_SEND_ATTEMPTS`, `SOS_RETRY_SEC`
- `SENSOR_RECORD_PATH` (record the raw serial stream), `LATENCY_LOG_ENABLED`
- `SAFETY_RUN_MODE`: `software_only` | `fused` | `hardware_strict`
- `STT_BACKEND`, `LOCAL_STT_*`, `ARDUINO_BAUD` (default **57600**)
- `SOS_COOLDOWN_SEC`, `SOS_VOICE_PHRASES`, `SOS_VOICE_REQUIRES_WAKE`

### 4.2 Safety run modes (actual behavior)

| Mode | Hardware US / IMU fall | Hardware SOS button | Vision / gait / voice SOS |
|------|-------------------------|----------------------|----------------------------|
| **software_only** | Off (no trusted serial) | Ignored | On |
| **fused** | On when `sensors_fresh()` | On when same | On |
| **hardware_strict** | N/A if stale | N/A if blanking | Blank screen if not fresh |

Store secrets in **`.env`** (gitignored), not in source.

---

## 5. THREAD TOPOLOGY

Main thread: display + safety HUD + pose/stick logic.  
Daemons: `HardwareManager`, camera, YOLO, speak, `audio_listener`; short-lived `SOSDispatch` threads for SMS delivery.  
Shared state: `global_state` (`RLock`) + `alert_queue`.

---

## 6. DEPLOYMENT

```bash
pip install -r requirements.txt
# Configure .env (see config.py for variables)
python main.py
```

Flash `mobility_assistant.ino`; match **57600** baud with Python. Optional buzzer: active buzzer on pin 7 (driven by the `BUZZ:<ms>` command).

---

## 7. FILE MANIFEST (this repo)

| File | Purpose |
|------|---------|
| `mobility_assistant.ino` | Arduino firmware |
| `main.py` | Main loop, HUD, tracking, modes |
| `core.py` | State, circuit breakers |
| `hardware.py` | Serial + JSON |
| `analytics.py` | Fall FSM, gait |
| `config.py` | Constants and env |
| `io_services.py` | Camera, vision, audio threads |
| `stt_service.py`, `local_stt.py`, `audio_util.py` | STT |
| `llm_online.py`, `voice_commands.py` | LLM + voice routing |
| `sos.py` | Emergency SMS + voice/hardware |
| `requirements.txt` | Python dependencies |
| `escalation.py` | Automatic SOS after a confirmed fall, with a cancel window |
| `pipeline.py` | `HardwareSafety`, `WalkerAnalytics`: orchestration logic extracted from `main.py` |
| `navigation.py` | Object tracking and spoken navigation advice |
| `speech.py`, `local_tts.py` | Cloud/on-device voice routing, alert latency log |
| `sensors.py` | Shared sensor payload validation |
| `tools/` | Simulator, trace replay, dataset evaluation, metrics, model verification |
| `models.lock.json` | SHA-256 and provenance for model weights |
| `docs/` | Evaluation protocol, model provenance, security notes (see `docs/README.md`) |
| `analytics_enhanced.py` | Gait fingerprint, MET proxy, posture/slouch, pre-fall tilt rate, phase asymmetry, stride proxy, fall-direction hint, rehab tracker, adaptive LLM hints |
| `research_log.py` | Append-only CSV in `exports/session_*.csv` |
| `audio_fallcontext.py` | Loud transient detector (mic RMS) |
| `clinical_dashboard.py` | JSON session summary on exit |
| `tests/` | Hardware-free unit tests (state machines, serial, SOS, escalation, voice/LLM, pipeline, tooling, secrets scan) |

---

## 8. QUICK START CHECKLIST

- [ ] Flash `mobility_assistant.ino` (Uno/Mega; **57600** baud matches `config.py`)
- [ ] Wire MPU I²C, ultrasonics, GPS SoftwareSerial, SOS button
- [ ] Create `.env` with keys (never commit); rotate any credentials that were ever committed
- [ ] `pip install -r requirements.txt`
- [ ] Run `python main.py` (ESC to exit)
- [ ] Confirm HUD; **ARD:ON** when serial works
- [ ] Voice: e.g. “assistant, gait status” (TTS needs ElevenLabs key)
- [ ] SOS: hardware in **fused** mode; voice phrases in `config.py` / `sos.py`
- [ ] Start `SAFETY_RUN_MODE=software_only`; move to `fused` after validation

---

## 9. REFERENCE DIAGRAM (DATA FLOW)

```
[ Camera ] ──► OpenCV frame ──► YOLO / MediaPipe ──► global_state
[ Mic ]    ──► STT (local/Groq) ──► voice_commands / sos ──► alert_queue ──► TTS
[ Arduino] ──► Serial JSON ──► hardware.py ──► global_state ──► main (HUD, fall, US)
[ Twilio ] ◄── sos.py (SMS) ◄── trigger_sos (button or voice)
```

---

## 10. REFERENCES

Groq, ElevenLabs, Twilio, Ultralytics YOLOv8, MediaPipe, faster-whisper, Arduino IDE.

---

**Document version:** 1.1 (aligned with repository code, April 2026)
