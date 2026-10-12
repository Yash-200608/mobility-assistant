"""Spoken-alert routing: cloud voice when it works, on-device voice when it does not.

Pure routing logic (no audio libraries) so the offline guarantee is testable.
``cloud_speak`` / ``local_speak`` are callables ``(text, on_start) -> None`` that
block until playback finishes and call ``on_start()`` just before audio begins.
They raise on failure.
"""
from __future__ import annotations

import csv
import os
import statistics
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

from core import CircuitBreaker, logger

Speak = Callable[[str, Callable[[], None]], None]


class LatencyRecorder:
    """Alert creation -> audio start, per route. Optionally appends to a CSV."""

    def __init__(self, csv_path: Optional[str] = None, maxlen: int = 500):
        self._samples: Deque[Tuple[str, float]] = deque(maxlen=maxlen)
        self._csv_path = csv_path
        self._lock = threading.Lock()

    def record(self, route: str, created_ts: float, started_ts: float, text: str = "") -> float:
        ms = max(0.0, (started_ts - created_ts) * 1000.0)
        with self._lock:
            self._samples.append((route, ms))
            if self._csv_path:
                try:
                    new = not os.path.exists(self._csv_path)
                    os.makedirs(os.path.dirname(self._csv_path) or ".", exist_ok=True)
                    with open(self._csv_path, "a", newline="", encoding="utf-8") as fp:
                        w = csv.writer(fp)
                        if new:
                            w.writerow(["created_ts", "started_ts", "latency_ms", "route", "chars"])
                        w.writerow([f"{created_ts:.3f}", f"{started_ts:.3f}", f"{ms:.0f}", route, len(text)])
                except OSError:
                    self._csv_path = None  # logging is optional; never break speech
        return ms

    def summary(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            by_route: Dict[str, List[float]] = {}
            for route, ms in self._samples:
                by_route.setdefault(route, []).append(ms)
        out = {}
        for route, vals in by_route.items():
            s = sorted(vals)
            p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]
            out[route] = {"n": len(s), "median_ms": statistics.median(s), "p95_ms": p95, "max_ms": s[-1]}
        return out


class AlertSpeaker:
    def __init__(
        self,
        cloud_speak: Optional[Speak] = None,
        local_speak: Optional[Speak] = None,
        breaker: Optional[CircuitBreaker] = None,
        recorder: Optional[LatencyRecorder] = None,
        max_age_sec: float = 8.0,
        clock=time,
    ):
        self.cloud_speak = cloud_speak
        self.local_speak = local_speak
        self.breaker = breaker or CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
        self.recorder = recorder or LatencyRecorder()
        self.max_age_sec = max_age_sec
        self._clock = clock

    def handle(self, created_ts: float, text: str) -> str:
        """Speak one alert. Returns 'stale' | 'cloud' | 'local' | 'silent'."""
        if self._clock.time() - created_ts > self.max_age_sec:
            return "stale"

        def started_marker(route):
            def _on_start():
                self.recorder.record(route, created_ts, self._clock.time(), text)
            return _on_start

        if self.cloud_speak is not None and self.breaker.can_execute():
            try:
                self.cloud_speak(text, started_marker("cloud"))
                self.breaker.record_success()
                return "cloud"
            except Exception as e:
                self.breaker.record_failure()
                logger.warning(f"Cloud TTS failed ({type(e).__name__}); using on-device voice.")

        if self.local_speak is not None:
            try:
                self.local_speak(text, started_marker("local"))
                return "local"
            except Exception as e:
                logger.error(f"On-device TTS failed: {type(e).__name__}: {e}")
        return "silent"
