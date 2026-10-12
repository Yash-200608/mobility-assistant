"""Replay a recorded sensor trace through the real fall/pre-fall logic offline.

Record a trace on the device by setting ``SENSOR_RECORD_PATH=trace.jsonl`` (one
JSON object per serial line: ``{"t": <unix seconds>, "raw": "<arduino json>"}``),
then replay it with different thresholds without repeating the falls:

    python tools/replay_trace.py trace.jsonl
    python tools/replay_trace.py trace.jsonl --set FALL_ACCEL_G=3.2 --set FALL_STILL_THRESH=1.2
    python tools/replay_trace.py trace.jsonl --timeline

Also accepts a CSV with columns ``t,pitch,roll,accel_mag``.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics import FallDetector  # noqa: E402
from analytics_enhanced import PreFallDetector  # noqa: E402
from config import Config  # noqa: E402
from sensors import validate_payload  # noqa: E402

Sample = Tuple[float, Dict[str, float]]


@dataclass
class ReplayResult:
    duration_s: float = 0.0
    n_samples: int = 0
    transitions: List[Tuple[float, str]] = field(default_factory=list)   # (t, new state)
    alerts: List[Tuple[float, str]] = field(default_factory=list)
    prefall_alerts: List[float] = field(default_factory=list)

    @property
    def fell(self) -> bool:
        return any(s == "FALLEN" for _, s in self.transitions)

    @property
    def first_fallen_t(self) -> Optional[float]:
        return next((t for t, s in self.transitions if s == "FALLEN"), None)


def load_trace(path: str) -> List[Sample]:
    """Load ``(t_seconds_from_start, clean_sensor_dict)`` samples, validated exactly
    as the live serial loop validates them."""
    p = Path(path)
    samples: List[Sample] = []
    if p.suffix.lower() == ".csv":
        with p.open(newline="", encoding="utf-8") as fp:
            for row in csv.DictReader(fp):
                data = validate_payload({k: v for k, v in row.items() if k != "t"})
                if data:
                    samples.append((float(row["t"]), data))
    else:
        with p.open(encoding="utf-8") as fp:
            for ln in fp:
                ln = ln.strip()
                if not ln:
                    continue
                rec = json.loads(ln)
                try:
                    payload = json.loads(rec["raw"])
                except (json.JSONDecodeError, TypeError):
                    continue  # malformed serial line: the live loop drops it too
                data = validate_payload(payload) if isinstance(payload, dict) else {}
                if data:
                    samples.append((float(rec["t"]), data))
    if not samples:
        return []
    t0 = samples[0][0]
    return [(t - t0, d) for t, d in samples]


def replay(samples: Iterable[Sample], overrides: Optional[Dict[str, Any]] = None) -> ReplayResult:
    """Run samples through FallDetector + PreFallDetector with a simulated clock.

    ``overrides`` temporarily replaces ``Config`` attributes, e.g.
    ``{"FALL_ACCEL_G": 3.2}``, so thresholds can be swept offline.
    """
    res = ReplayResult()
    with ExitStack() as stack:
        for key, val in (overrides or {}).items():
            if not hasattr(Config, key):
                raise KeyError(f"unknown Config setting: {key}")
            stack.enter_context(mock.patch.object(Config, key, val))
        fd, pf = FallDetector(), PreFallDetector()
        BASE = 1_000_000.0  # keeps `now - 0.0` style comparisons away from zero
        last_state = fd.state
        for t, sensors in samples:
            now = BASE + t
            alert = fd.process(sensors, now)
            res.n_samples += 1
            res.duration_s = t
            if fd.state != last_state:
                res.transitions.append((t, fd.state))
                last_state = fd.state
            if alert:
                res.alerts.append((t, alert))
            if pf.process(sensors, fd.state, now):
                res.prefall_alerts.append(t)
    return res


def _coerce(value: str) -> Any:
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def format_timeline(res: ReplayResult) -> str:
    rows = [(t, f"state -> {s}") for t, s in res.transitions]
    rows += [(t, f"ALERT: {a}") for t, a in res.alerts]
    rows += [(t, "PRE-FALL warning") for t in res.prefall_alerts]
    return "\n".join(f"{t:8.2f}s  {msg}" for t, msg in sorted(rows)) or "(no events)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trace")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a Config threshold (repeatable)")
    ap.add_argument("--timeline", action="store_true", help="print every transition and alert")
    args = ap.parse_args(argv)

    overrides = {}
    for item in args.set:
        key, _, val = item.partition("=")
        overrides[key.strip()] = _coerce(val.strip())

    samples = load_trace(args.trace)
    if not samples:
        print("No usable samples in trace.", file=sys.stderr)
        return 2
    res = replay(samples, overrides)
    print(f"samples={res.n_samples}  duration={res.duration_s:.1f}s  "
          f"fall_confirmed={res.fell}  first_fallen_t={res.first_fallen_t}  "
          f"alerts={len(res.alerts)}  prefall_alerts={len(res.prefall_alerts)}")
    if overrides:
        print(f"overrides: {overrides}")
    if args.timeline:
        print(format_timeline(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
