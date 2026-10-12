"""Sensor payload validation shared by the live serial loop and offline replay."""
from __future__ import annotations

import math
from typing import Dict

from config import Config


def validate_payload(data: dict) -> Dict[str, float]:
    """Coerce a decoded Arduino JSON object to clean floats.

    * non-numeric values are dropped
    * NaN/inf are dropped, not clamped (clamping turns a NaN pitch into -180 deg
      and a NaN accel into 0 g, which reads as a fall)
    * keys with configured limits are clamped to them
    """
    clean: Dict[str, float] = {}
    for key, val in data.items():
        try:
            f_val = float(val)
        except (ValueError, TypeError):
            continue
        if not math.isfinite(f_val):
            continue
        if key in Config.SENSOR_LIMITS:
            lo, hi = Config.SENSOR_LIMITS[key]
            f_val = max(lo, min(f_val, hi))
        clean[key] = f_val
    return clean
