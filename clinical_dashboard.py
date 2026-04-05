"""Session summaries for rehab progress (JSON sidecar to research CSV)."""

import json
import os
import time
from typing import Any, Dict

from config import Config


def write_session_summary(summary: Dict[str, Any], label: str = "progress") -> str:
    os.makedirs(Config.RESEARCH_EXPORT_DIR, exist_ok=True)
    path = os.path.join(
        Config.RESEARCH_EXPORT_DIR,
        f"summary_{label}_{time.strftime('%Y%m%d_%H%M%S')}.json",
    )
    payload = {"written_at": time.time(), **summary}
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)
    return path
