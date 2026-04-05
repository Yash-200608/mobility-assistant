
import csv
import os
import threading
import time
from typing import Any, Dict, List, Optional

from analytics_enhanced import RESEARCH_CSV_FIELDS
from config import Config
from core import logger

_lock = threading.Lock()
_csv_path: Optional[str] = None
_fieldnames: List[str] = list(RESEARCH_CSV_FIELDS)

def _ensure_dir() -> str:
    d = Config.RESEARCH_EXPORT_DIR
    os.makedirs(d, exist_ok=True)
    return d

def _path_for_session() -> str:
    global _csv_path
    if _csv_path is None:
        _ensure_dir()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        _csv_path = os.path.join(Config.RESEARCH_EXPORT_DIR, f"session_{stamp}.csv")
        logger.info(f"Research CSV: {_csv_path}")
    return _csv_path

def log_research_row(row: Dict[str, Any]) -> None:
    if not Config.RESEARCH_LOG_ENABLED:
        return
    path = _path_for_session()
    with _lock:
        new_file = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", newline="", encoding="utf-8") as fp:
            w = csv.DictWriter(fp, fieldnames=_fieldnames, extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in _fieldnames})

def reset_session_path() -> None:
    global _csv_path
    with _lock:
        _csv_path = None
