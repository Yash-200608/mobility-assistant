# Models: provenance, licences and acquisition

Everything the project loads at runtime, which feature uses it, where it comes
from and how to confirm you have the right bytes. The machine-readable source of
truth is [`models.lock.json`](../models.lock.json); check it with:

```bash
python tools/verify_models.py --require
```

## Artifacts

| Artifact | Feature | Where it lives | Source | Licence |
| :--- | :--- | :--- | :--- | :--- |
| `yolov8s.pt` | Obstacle detection in stick mode (`io_services.local_vision_thread`) | Repo root; path set by `LOCAL_MODEL_WEIGHTS` (default `yolov8s.pt`) | Ultralytics YOLOv8 pretrained COCO weights | AGPL-3.0 |
| `yolov8n.pt` | None by default. Smaller variant you can select with `LOCAL_MODEL_WEIGHTS=yolov8n.pt` for weaker CPUs | Repo root | Ultralytics YOLOv8 pretrained COCO weights | AGPL-3.0 |
| MediaPipe Pose | Gait landmarks and posture (`main.py`, `mp.solutions.pose`) | Bundled inside the `mediapipe` pip package; nothing to download | Google MediaPipe | Apache-2.0 |
| Whisper `base` (Faster-Whisper) | Offline wake-word / command transcription (`local_stt.py`, `LOCAL_STT_MODEL`) | Downloaded on first run into the Hugging Face cache | OpenAI Whisper weights, CTranslate2 conversion via `faster-whisper` | MIT |

Cloud services (not models you host): Groq (LLM answers), ElevenLabs
(text-to-speech), Twilio (SOS SMS). All three need internet and API credentials.

## Hashes

| File | Size (bytes) | SHA-256 |
| :--- | ---: | :--- |
| `yolov8s.pt` | 22,588,772 | `1f47a78bf100391c2a140b7ac73a1caae18c32779be7d310658112f7ac9aa78a` |
| `yolov8n.pt` | 6,549,796 | `f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36` |

The exact upstream release tag the weights were taken from was not recorded when
they were committed, so the hash is their identity. If you replace a weight file,
update `models.lock.json` in the same commit.

## Licence note

Ultralytics distributes YOLOv8 weights under AGPL-3.0, with a separate
commercial licence available. This repository is a research prototype; anyone
redistributing a product built on these weights should read Ultralytics' current
licence terms first. This document records what the project uses; it is not
legal advice.

## Acquisition

* **Weights in the repo root:** already committed. If you remove them to slim a
  clone, re-download from <https://github.com/ultralytics/assets/releases> and
  confirm with `tools/verify_models.py`.
* **Whisper `base`:** fetched automatically by `faster-whisper` the first time
  local STT starts. Without it (or without `faster-whisper` installed) the app
  falls back to Groq STT when `GROQ_API_KEY` is set, otherwise voice input is
  disabled.
* **Tests never need any of the above.** The suite stubs the vision, audio and
  network layers, so it runs without model downloads, a GPU, or API keys.

## Which weight does what

`Config.LOCAL_MODEL_WEIGHTS` is the only place a YOLO file is named. Only
`io_services.local_vision_thread` loads it, and only for detections that feed
stick-mode obstacle alerts. Pose, gait and fall logic do not use YOLO.
