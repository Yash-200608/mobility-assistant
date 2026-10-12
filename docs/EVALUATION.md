# Evaluation protocol and status

**Status: no formal evaluation has been run yet.** This document defines how it
will be done, so that any number later quoted in the README comes from a
reproducible procedure rather than an impression. Until the results table below
is filled from a real run, treat every detection claim in this project as
unmeasured.

> This is a research prototype. It is **not** a medical device, has not been
> clinically validated, and must not be relied on for safety.

## What the existing evidence does and does not show

| Evidence | What it establishes | What it does not |
| :--- | :--- | :--- |
| Unit tests (`tests/`, run in CI) | The state machines behave as coded: transitions, rate limits, dropout handling, offline behaviour. These are regression guards. | Detection accuracy. A test that feeds a hand-written "fall" trace proves the code reacts to that trace, not that real falls look like it. |
| `exports/session_*.csv` logs (3,436 rows, 27 files) | Gait metrics were logged during development. | Fall detection: every logged row has `fall_state = CAMERA/MIC`, meaning the run used `software_only` mode, so the IMU fall state machine never ran in any recorded session. The logs are also unlabelled (no ground truth). |
| README "Performance Characteristics" | Informal single-machine observations. | A benchmark. Method, sample size and variance were not recorded. |

## Scope

In scope: the IMU-based fall state machine (`analytics.FallDetector`) in
`SAFETY_RUN_MODE=fused`, plus the pre-fall warning (`PreFallDetector`) as a
secondary signal. Out of scope for the first report: camera-only fall inference,
gait-metric accuracy against a reference system, obstacle-detection mAP.

## Offline evaluation before any volunteers

Two tools let you measure the algorithm without staging falls:

* **Public datasets.** `tools/dataset_eval.py` replays labelled accelerometer
  traces (SisFall layout, or any `t,pitch,roll,accel_mag` CSV plus a manifest)
  through the real `FallDetector` and writes the same trials CSV and report as
  below. This is *algorithm on dataset X*: sensor position, range, sampling rate
  and axis orientation differ from the walker-mounted IMU, so the vertical-axis
  assumption must be verified against the dataset's documentation and the
  result must not be quoted as device performance.
* **Recorded sessions.** Set `SENSOR_RECORD_PATH` to log the raw serial stream,
  then `tools/replay_trace.py` re-runs it with different thresholds
  (`--set FALL_ACCEL_G=3.2`). Use this to sweep thresholds on data you already
  have, then confirm the chosen setting on *new* trials, not the ones you tuned on.

Changes made so far were verified on synthetic traces only:

| Change | Evidence (synthetic) |
| :--- | :--- |
| Impact spike no longer inflates the stillness average | Confirmation latency 0.45 s at 20 Hz for any impact (was 0.45 s rising to 0.9 s; at 10 Hz 0.5 s rising to 1.5 s) |

Known behaviours the protocol should quantify rather than assume:

* An upright knock followed by stillness is treated as a fall (`device_bump`).
* The pre-fall warning differentiates raw samples at about 20 Hz with no
  smoothing, so a few degrees of single-sample jitter can trip it. Tune it from
  replayed `walk_normal` trials.

## Ethics and safety (do these before any data is collected)

1. Healthy adult volunteers only, each giving informed, written, revocable
   consent. No participants with balance, bone or cardiovascular conditions.
2. All falls are scripted onto a crash mat, supervised by a second person who
   can stop any trial. Participants may stop at any time.
3. If results will be published or shown outside this project, obtain review
   from your institution's ethics process first.
4. Store only what the metrics need: trial CSV and sensor logs keyed by an
   anonymous subject ID. Do not commit video or any identifying data.

## Trials

Each trial is one continuous recording with one expected outcome.

| Scenario (`scenario` column) | `ground_truth` | Notes |
| :--- | :---: | :--- |
| `fall_forward`, `fall_backward`, `fall_left`, `fall_right` | 1 | Scripted fall onto mat from standing |
| `fall_then_recover` | 1 | Fall, lie 5 s, stand up unaided |
| `stumble_recover` | 0 | Loses balance, catches self; no fall |
| `sit_down_fast`, `sit_down_slow` | 0 | Chair at normal and abrupt speed |
| `lie_down_deliberate` | 0 | Controlled lie-down onto mat |
| `pick_up_object` | 0 | Bend to the floor and rise |
| `walk_normal`, `walk_with_walker` | 0 | 5 minutes each; counts toward false alarms per hour |
| `device_bump` | 0 | Walker/cane knocked or dropped, wearer upright |

Minimum for a first report: 3 subjects, at least 10 fall trials and 30 non-fall
trials in total. At that size the intervals are wide; the report says so
automatically. Aim for 30 or more per class before quoting a headline figure.

## What to record per trial

`evaluation/trials_template.csv` has the header. One row per trial:

| Column | Meaning |
| :--- | :--- |
| `trial_id`, `subject_id` | Unique id; anonymous subject id |
| `scenario` | From the table above |
| `ground_truth` | `1` if a real fall happened, else `0` (set by the supervisor, not by the system) |
| `alert` | `1` if the system raised the "Fall detected" alert during the trial |
| `latency_s` | Seconds from the impact marker to the alert (true positives only) |
| `duration_s` | Trial length in seconds (drives false alarms per hour) |
| `notes` | Anything unusual (dropped serial link, tripped sensor, etc.) |

`alert` latency from the alert queue to audible output is logged separately in
`exports/alert_latency.csv` (route `cloud` or `local`); report it alongside the
impact-to-alert figure, because cloud text-to-speech dominates it.

For `latency_s`, mark the impact by a consistent method (a clap on a second
microphone, a button press logged with a timestamp, or frame-stepping a side
video) and take the alert time from the application log, then state which one
you used in the report.

## Metrics

Computed by `tools/evaluate_fall_detection.py`, which prints a Markdown report:

```bash
python tools/evaluate_fall_detection.py evaluation/trials.csv --out evaluation/report.md
```

* **Confusion matrix**, **recall**, **precision**, **specificity**, **F1**, each
  rate with a 95% Wilson interval.
* **False alarms per hour** = false positives / hours of non-fall activity.
* **Latency**: median, p95 and max over true positives.

## Reporting checklist

Commit the report with these, or it is not a result:

- [ ] Date, git commit of the code under test, firmware version (`mobility_assistant.ino`)
- [ ] Hardware: compute board, IMU model, mounting position, sample rate
- [ ] `config.py` thresholds in force (`FALL_*`, `TILT_*`, `PREFALL_*`) and `SAFETY_RUN_MODE`
- [ ] Number of subjects and trials per scenario; how scenarios were ordered
- [ ] Ground-truth method and latency-marking method
- [ ] The raw `trials.csv`
- [ ] Limitations: sample size, who the subjects were, what was not tested

## Results

| Date | Commit | Subjects | Fall trials | Non-fall trials | Recall (95% CI) | Precision (95% CI) | False alarms / h | Median latency | p95 latency |
| :--- | :--- | ---: | ---: | ---: | :--- | :--- | ---: | ---: | ---: |
| *Not yet measured* | | | | | | | | | |

Do not add a row without the raw CSV and the completed checklist above.
