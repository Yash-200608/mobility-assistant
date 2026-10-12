# Documentation index

| Document | What it covers |
| :--- | :--- |
| [../README.md](../README.md) | Overview, quick start, testing, configuration |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | Hardware, threads, safety modes, file manifest |
| [EVALUATION.md](EVALUATION.md) | How detection accuracy is (and will be) measured, current status |
| [MODELS.md](MODELS.md) | Model weights: which feature uses which, source, licence, hashes |
| [SECURITY.md](SECURITY.md) | Secret handling, rotating the leaked Twilio credential |

## Tools (`tools/`)

| Tool | Purpose |
| :--- | :--- |
| `demo_sim.py` | Run the real safety pipeline on synthetic scenarios, no hardware |
| `replay_trace.py` | Replay a recorded sensor stream through the fall logic, with threshold overrides |
| `dataset_eval.py` | Score the fall detector on a public accelerometer dataset |
| `evaluate_fall_detection.py` | Confusion matrix, precision/recall (95% CI), false alarms per hour, latency |
| `verify_models.py` | Check model weights against `models.lock.json` |
