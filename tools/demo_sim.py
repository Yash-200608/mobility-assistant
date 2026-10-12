"""Run the real safety pipeline against synthetic IMU scenarios: no hardware needed.

    python tools/demo_sim.py --list
    python tools/demo_sim.py fall_unanswered
    python tools/demo_sim.py fall_cancelled
    python tools/demo_sim.py walk --record walk.jsonl     # then: tools/replay_trace.py walk.jsonl

It drives pipeline.HardwareSafety (fall state machine, pre-fall, escalation,
buzzer) with a simulated clock and prints what the device would say and do.
SMS and buzzer calls are printed, never sent. The traces are hand-built, so this
demonstrates behaviour; it is not evidence of detection accuracy.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from config import Config  # noqa: E402
from escalation import FallEscalator  # noqa: E402
from pipeline import HardwareSafety  # noqa: E402

HZ = 20
Sample = Dict[str, float]


def _s(pitch=0.0, roll=0.0, accel=1.0, **extra) -> Sample:
    d = {"pitch": pitch, "roll": roll, "accel_mag": accel, "us_front": 200.0, "us_floor": 10.0}
    d.update(extra)
    return d


def _n(seconds: float) -> int:
    return int(round(seconds * HZ))


def _walking(seconds: float) -> List[Sample]:
    # ~1.8 steps/s: smooth sway, as a real torso/walker produces (not a sawtooth).
    out = []
    for i in range(_n(seconds)):
        ph = 2 * math.pi * 1.8 * (i / HZ)
        out.append(_s(pitch=3.0 * math.sin(ph), roll=2.0 * math.sin(ph / 2), accel=1.0 + 0.3 * abs(math.sin(ph))))
    return out


def _upright(seconds: float) -> List[Sample]:
    return [_s(pitch=2, roll=1) for _ in range(_n(seconds))]


def _lying(seconds: float) -> List[Sample]:
    return [_s(pitch=80, roll=5) for _ in range(_n(seconds))]


SCENARIOS: Dict[str, Tuple[str, Callable[[], List[Sample]]]] = {
    "walk": ("Ordinary walking for 15 s. Expect: silence.",
             lambda: _walking(15)),
    "stumble": ("Loses balance (tilt to 40 deg) then recovers. Expect: tilt warning, no fall.",
                lambda: _upright(2) + [_s(pitch=min(40, 8 * i)) for i in range(6)]
                + [_s(pitch=40)] * _n(2) + [_s(pitch=max(0, 40 - 8 * i)) for i in range(6)] + _upright(3)),
    "fall_unanswered": ("Falls and does not respond. Expect: fall alert, buzzer, then automatic SOS.",
                        lambda: _walking(3) + [_s(pitch=60, accel=3.8)] + _lying(30)),
    "fall_cancelled": ("Falls, then says 'I'm okay' 6 s later. Expect: SOS cancelled.",
                       lambda: _walking(3) + [_s(pitch=60, accel=3.8)] + _lying(30)),
    "fall_recover": ("Falls, then gets back up on their own. Expect: SOS cancelled, buzzer off.",
                     lambda: _walking(3) + [_s(pitch=60, accel=3.8)] + _lying(8) + _upright(8)),
    "prefall": ("A fast lurch with no fall. Expect: an 'unsteady motion' warning.",
                lambda: _upright(2) + [_s(pitch=p) for p in (0, 12, 26, 18, 6)] + _upright(3)),
    "bump": ("KNOWN LIMITATION: walker knocked while upright, then still. Acceleration alone "
             "cannot tell this from a fall, so it is treated as one.",
             lambda: _upright(2) + [_s(accel=3.2)] + _upright(15)),
}
# `fall_cancelled` is driven by --say-at; give it a default.
DEFAULT_SAY_AT = {"fall_cancelled": 6.0}


def run_scenario(name: str, auto_sos: bool = True, say_at: Optional[float] = None,
                 say_text: str = "i'm okay", record: Optional[str] = None,
                 emit: Callable[[str], None] = print) -> List[Tuple[float, str, str]]:
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}; choose from {', '.join(SCENARIOS)}")
    samples = SCENARIOS[name][1]()
    if say_at is None:
        say_at = DEFAULT_SAY_AT.get(name)

    events: List[Tuple[float, str, str]] = []
    t = 0.0
    BASE = 1_000_000.0

    def log(kind: str, msg: str) -> None:
        events.append((t, kind, msg))
        emit(f"{t:7.2f}s  {kind:<8} {msg}")

    state = core.SystemState()
    escalator = FallEscalator(state=state, sos_fn=lambda src: log("SMS", f"SOS text sent ({src}) [simulated]") or True)
    safety = HardwareSafety(
        state=state, escalator=escalator,
        sos_fn=lambda src: log("SMS", f"SOS text sent ({src}) [simulated]") or True,
        buzz_fn=lambda p: log("BUZZER", f"pattern={p}") or True,
    )

    rec = open(record, "w", encoding="utf-8") if record else None
    last_state = safety.fall.state
    said = False
    try:
        with mock.patch.object(Config, "FALL_AUTO_SOS", auto_sos):
            for s in samples:
                now = BASE + t
                if rec:
                    rec.write(json.dumps({"t": now, "raw": json.dumps(s)}) + "\n")
                res = safety.step(s, now)
                if res.fall_state != last_state:
                    log("STATE", f"{last_state} -> {res.fall_state}")
                    last_state = res.fall_state
                while not state.alert_queue.empty():
                    log("SPOKEN", state.alert_queue.get_nowait()[1])
                if say_at is not None and not said and t >= say_at:
                    said = True
                    log("VOICE", f"user says: {say_text!r}")
                    escalator.try_cancel_from_transcript(say_text)
                    while not state.alert_queue.empty():
                        log("SPOKEN", state.alert_queue.get_nowait()[1])
                t += 1.0 / HZ
    finally:
        if rec:
            rec.close()
    if not events:
        emit("(no events: the system stayed silent)")
    return events


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", nargs="?", default="fall_unanswered")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-auto-sos", action="store_true", help="simulate FALL_AUTO_SOS=false (the default setting)")
    ap.add_argument("--say-at", type=float, help="seconds into the scenario when the user speaks")
    ap.add_argument("--say", default="i'm okay", help="what the user says")
    ap.add_argument("--record", help="write the synthetic stream as replayable JSONL")
    args = ap.parse_args(argv)

    if args.list:
        for k, (desc, _) in SCENARIOS.items():
            print(f"{k:<16} {desc}")
        return 0
    if args.scenario not in SCENARIOS:
        print(f"unknown scenario {args.scenario!r}. Use --list.", file=sys.stderr)
        return 2
    print(f"# {args.scenario}: {SCENARIOS[args.scenario][0]}")
    print(f"# FALL_AUTO_SOS={'false' if args.no_auto_sos else 'true'}  window={Config.FALL_ESCALATION_SEC:g}s")
    run_scenario(args.scenario, auto_sos=not args.no_auto_sos, say_at=args.say_at,
                 say_text=args.say, record=args.record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
