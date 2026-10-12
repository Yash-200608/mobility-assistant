"""Compute fall-detection metrics from a labelled trials CSV.

Input columns (see docs/EVALUATION.md):
    trial_id,subject_id,scenario,ground_truth,alert,latency_s,duration_s,notes

    ground_truth  1 = a real (scripted) fall happened in this trial, 0 = it did not
    alert         1 = the system raised a fall alert during the trial, 0 = it did not
    latency_s     seconds from the impact marker to the alert (blank if no alert)
    duration_s    length of the trial, used for false alarms per hour

Usage:
    python tools/evaluate_fall_detection.py evaluation/trials.csv [--out report.md]
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

REQUIRED = ("trial_id", "scenario", "ground_truth", "alert", "duration_s")
Z95 = 1.959963984540054


@dataclass
class Trial:
    trial_id: str
    subject_id: str
    scenario: str
    ground_truth: bool
    alert: bool
    latency_s: Optional[float]
    duration_s: float


def _flag(value: str, field: str, row_no: int) -> bool:
    v = (value or "").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    raise ValueError(f"row {row_no}: {field!r} must be 0/1, got {value!r}")


def load_trials(rows: Iterable[Dict[str, str]]) -> List[Trial]:
    trials: List[Trial] = []
    for i, r in enumerate(rows, start=2):  # the header is line 1
        missing = [c for c in REQUIRED if c not in r or r[c] is None or str(r[c]).strip() == ""]
        if missing:
            raise ValueError(f"row {i}: missing {missing}")
        gt = _flag(r["ground_truth"], "ground_truth", i)
        alert = _flag(r["alert"], "alert", i)
        duration = float(r["duration_s"])
        if duration <= 0:
            raise ValueError(f"row {i}: duration_s must be > 0")
        lat_raw = (r.get("latency_s") or "").strip()
        latency = float(lat_raw) if lat_raw else None
        if latency is not None and latency < 0:
            raise ValueError(f"row {i}: latency_s must be >= 0")
        if gt and alert and latency is None:
            raise ValueError(f"row {i}: true positive without latency_s")
        trials.append(Trial(r["trial_id"], (r.get("subject_id") or "").strip(),
                            r["scenario"].strip(), gt, alert, latency, duration))
    return trials


def wilson(successes: int, n: int, z: float = Z95) -> Optional[Tuple[float, float]]:
    """Wilson score interval: stays sensible for small n and for p near 0 or 1."""
    if n == 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _ratio(num: int, den: int) -> Optional[float]:
    return num / den if den else None


def percentile(values: List[float], q: float) -> Optional[float]:
    """Linear-interpolated percentile, q in [0, 1]."""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def compute_metrics(trials: List[Trial]) -> Dict:
    tp = sum(t.ground_truth and t.alert for t in trials)
    fn = sum(t.ground_truth and not t.alert for t in trials)
    fp = sum((not t.ground_truth) and t.alert for t in trials)
    tn = sum((not t.ground_truth) and not t.alert for t in trials)
    neg_hours = sum(t.duration_s for t in trials if not t.ground_truth) / 3600.0
    lat = [t.latency_s for t in trials if t.ground_truth and t.alert and t.latency_s is not None]

    per_scenario: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"n": 0, "tp": 0, "fn": 0, "fp": 0, "tn": 0})
    for t in trials:
        s = per_scenario[t.scenario]
        s["n"] += 1
        if t.ground_truth:
            s["tp" if t.alert else "fn"] += 1
        else:
            s["fp" if t.alert else "tn"] += 1

    return {
        "n_trials": len(trials),
        "n_subjects": len({t.subject_id for t in trials if t.subject_id}),
        "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "recall": _ratio(tp, tp + fn), "recall_ci": wilson(tp, tp + fn),
        "precision": _ratio(tp, tp + fp), "precision_ci": wilson(tp, tp + fp),
        "specificity": _ratio(tn, tn + fp), "specificity_ci": wilson(tn, tn + fp),
        "f1": _ratio(2 * tp, 2 * tp + fp + fn),
        "negative_hours": neg_hours,
        "false_alarms_per_hour": (fp / neg_hours) if neg_hours > 0 else None,
        "latency_n": len(lat),
        "latency_median_s": statistics.median(lat) if lat else None,
        "latency_p95_s": percentile(lat, 0.95),
        "latency_max_s": max(lat) if lat else None,
        "per_scenario": dict(per_scenario),
    }


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{100 * v:.1f}%"


def _ci(ci: Optional[Tuple[float, float]]) -> str:
    return "n/a" if ci is None else f"{100 * ci[0]:.0f}-{100 * ci[1]:.0f}%"


def _num(v: Optional[float], unit: str = "", digits: int = 2) -> str:
    return "n/a" if v is None else f"{v:.{digits}f}{unit}"


def render_report(m: Dict) -> str:
    out = ["# Fall-detection evaluation report", ""]
    out.append(f"Trials: **{m['n_trials']}** | subjects: **{m['n_subjects']}** | "
               f"fall trials: **{m['tp'] + m['fn']}** | non-fall trials: **{m['fp'] + m['tn']}**")
    out.append("")
    if m["tp"] + m["fn"] < 30 or m["fp"] + m["tn"] < 30:
        out += ["> **Small sample.** Fewer than 30 trials in at least one class; the 95% intervals "
                "below are wide and these numbers must not be generalised.", ""]
    out += ["## Confusion matrix", "",
            "| | Alert raised | No alert |", "| :--- | ---: | ---: |",
            f"| **Fall happened** | TP {m['tp']} | FN {m['fn']} |",
            f"| **No fall** | FP {m['fp']} | TN {m['tn']} |", ""]
    out += ["## Metrics", "",
            "| Metric | Value | 95% CI (Wilson) |", "| :--- | ---: | :--- |",
            f"| Recall (sensitivity) | {_pct(m['recall'])} | {_ci(m['recall_ci'])} |",
            f"| Precision | {_pct(m['precision'])} | {_ci(m['precision_ci'])} |",
            f"| Specificity | {_pct(m['specificity'])} | {_ci(m['specificity_ci'])} |",
            f"| F1 | {_pct(m['f1'])} | |",
            f"| False alarms per hour ({m['negative_hours']:.2f} h of non-fall activity) | "
            f"{_num(m['false_alarms_per_hour'])} | |", ""]
    out += [f"## Latency (impact to alert, true positives only, n={m['latency_n']})", "",
            "| Median | p95 | Max |", "| ---: | ---: | ---: |",
            f"| {_num(m['latency_median_s'], ' s')} | {_num(m['latency_p95_s'], ' s')} | "
            f"{_num(m['latency_max_s'], ' s')} |", ""]
    out += ["## Per scenario", "",
            "| Scenario | Trials | TP | FN | FP | TN |", "| :--- | ---: | ---: | ---: | ---: | ---: |"]
    for name, s in sorted(m["per_scenario"].items()):
        out.append(f"| {name} | {s['n']} | {s['tp']} | {s['fn']} | {s['fp']} | {s['tn']} |")
    out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trials_csv")
    ap.add_argument("--out", help="write the markdown report here instead of stdout")
    args = ap.parse_args(argv)
    with open(args.trials_csv, newline="", encoding="utf-8") as fp:
        trials = load_trials(csv.DictReader(fp))
    if not trials:
        print("No trials found: nothing to evaluate.", file=sys.stderr)
        return 2
    report = render_report(compute_metrics(trials))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fp:
            fp.write(report)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
