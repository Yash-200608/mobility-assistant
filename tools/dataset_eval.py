"""Evaluate the fall detector on public accelerometer datasets (offline).

Turns a folder of labelled traces into a trials CSV that
``tools/evaluate_fall_detection.py`` can score.

Supported inputs
----------------
``--format sisfall``  The SisFall layout: ``<root>/<Subject>/<Activity>_<Subject>_R<NN>.txt``
    where activity codes starting ``D`` are daily activities (no fall) and ``F`` are
    falls. Each line is ``ax,ay,az,gx,gy,gz,mx,my,mz;`` in raw ADC units at 200 Hz.
    The first three columns (ADXL345, +-16 g, 13 bit) are used.

``--format csv``      ``<root>/manifest.csv`` with columns ``path,label,scenario,subject``
    (``label`` 1 = fall, 0 = no fall); each ``path`` is a ``t,pitch,roll,accel_mag`` CSV.

IMPORTANT: public datasets are not this device. Sensor position, range, sampling
rate and axis orientation differ from the walker-mounted IMU, so the output is
"algorithm on dataset X", not performance of the product. Verify the axis
assumption below against the dataset's own documentation before quoting numbers.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import evaluate_fall_detection as ev  # noqa: E402
import replay_trace  # noqa: E402

Sample = Tuple[float, Dict[str, float]]

SISFALL_HZ = 200.0
ADXL345_G_PER_LSB = (2 * 16.0) / (2 ** 13)   # +-16 g, 13-bit
_SISFALL_NAME = re.compile(r"^(?P<act>[DF]\d{2})_(?P<subj>S[AE]\d{2})_R\d{2}\.txt$", re.IGNORECASE)


def tilt_from_accel(ax: float, ay: float, az: float, vertical: str = "z") -> Tuple[float, float]:
    """Pitch/roll (degrees) of the chosen 'vertical' axis away from gravity."""
    a = {"x": ax, "y": ay, "z": az}
    v = a[vertical]
    h1, h2 = (a[k] for k in "xyz" if k != vertical)
    pitch = math.degrees(math.atan2(h1, math.hypot(h2, v)))
    roll = math.degrees(math.atan2(h2, v))
    return pitch, roll


def parse_sisfall(path: Path, target_hz: float = 20.0, vertical: str = "z") -> List[Sample]:
    """SisFall text file -> downsampled ``(t, {pitch, roll, accel_mag})`` samples."""
    step = max(1, int(round(SISFALL_HZ / target_hz)))
    out: List[Sample] = []
    with path.open(encoding="utf-8", errors="ignore") as fp:
        for i, ln in enumerate(fp):
            if i % step:
                continue
            cols = ln.strip().rstrip(";").split(",")
            if len(cols) < 3:
                continue
            try:
                ax, ay, az = (float(c) * ADXL345_G_PER_LSB for c in cols[:3])
            except ValueError:
                continue
            pitch, roll = tilt_from_accel(ax, ay, az, vertical)
            out.append((i / SISFALL_HZ, {"pitch": pitch, "roll": roll,
                                         "accel_mag": math.sqrt(ax * ax + ay * ay + az * az)}))
    return out


def trial_from_samples(trial_id: str, subject: str, scenario: str, is_fall: bool,
                       samples: List[Sample]) -> Optional[Dict[str, str]]:
    """Replay one trace and express it as a row of the evaluation trials CSV."""
    if len(samples) < 2:
        return None
    res = replay_trace.replay(samples)
    t_impact = max(samples, key=lambda s: s[1].get("accel_mag", 0.0))[0]
    alert = res.fell
    latency = ""
    if is_fall and alert and res.first_fallen_t is not None:
        latency = f"{max(0.0, res.first_fallen_t - t_impact):.3f}"
    return {
        "trial_id": trial_id, "subject_id": subject, "scenario": scenario,
        "ground_truth": str(int(is_fall)), "alert": str(int(alert)),
        "latency_s": latency, "duration_s": f"{samples[-1][0] - samples[0][0]:.3f}",
        "notes": "dataset replay",
    }


def sisfall_trials(root: Path, target_hz: float, vertical: str) -> Iterable[Dict[str, str]]:
    for path in sorted(root.rglob("*.txt")):
        m = _SISFALL_NAME.match(path.name)
        if not m:
            continue
        samples = parse_sisfall(path, target_hz, vertical)
        row = trial_from_samples(path.stem, m["subj"].upper(), m["act"].upper(),
                                 m["act"].upper().startswith("F"), samples)
        if row:
            yield row


def csv_manifest_trials(root: Path) -> Iterable[Dict[str, str]]:
    with (root / "manifest.csv").open(newline="", encoding="utf-8") as fp:
        for r in csv.DictReader(fp):
            samples = replay_trace.load_trace(str(root / r["path"]))
            row = trial_from_samples(Path(r["path"]).stem, r.get("subject", ""),
                                     r.get("scenario", "unknown"), r["label"].strip() == "1", samples)
            if row:
                yield row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="dataset directory")
    ap.add_argument("--format", choices=("sisfall", "csv"), default="sisfall")
    ap.add_argument("--hz", type=float, default=20.0, help="downsample target (device is ~20 Hz)")
    ap.add_argument("--vertical-axis", choices=("x", "y", "z"), default="z",
                    help="which sensor axis is vertical when upright (VERIFY against the dataset docs)")
    ap.add_argument("--trials-out", help="also write the trials CSV here")
    ap.add_argument("--out", help="write the markdown report here")
    args = ap.parse_args(argv)

    root = Path(args.root)
    rows = list(sisfall_trials(root, args.hz, args.vertical_axis) if args.format == "sisfall"
                else csv_manifest_trials(root))
    if not rows:
        print(f"No usable traces found under {root}.", file=sys.stderr)
        return 2
    if args.trials_out:
        with open(args.trials_out, "w", newline="", encoding="utf-8") as fp:
            w = csv.DictWriter(fp, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    report = ev.render_report(ev.compute_metrics(ev.load_trials(rows)))
    report += ("\n> Dataset replay. Not device performance: sensor position, range, rate and axes differ "
               f"from the walker-mounted IMU. Vertical axis assumed: `{args.vertical_axis}`; "
               f"downsampled to {args.hz:g} Hz.\n")
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
