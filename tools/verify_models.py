"""Check model weights against models.lock.json (SHA-256 + size).

    python tools/verify_models.py            # exit 0 if every present file matches
    python tools/verify_models.py --require  # also fail if a listed file is missing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(lock_path: Path = ROOT / "models.lock.json", root: Path = ROOT, require: bool = False):
    """Return a list of (path, status) where status is ok | missing | mismatch."""
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    results = []
    for art in lock["artifacts"]:
        p = root / art["path"]
        if not p.exists():
            results.append((art["path"], "missing"))
        elif p.stat().st_size != art["size_bytes"] or sha256_of(p) != art["sha256"]:
            results.append((art["path"], "mismatch"))
        else:
            results.append((art["path"], "ok"))
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--require", action="store_true", help="treat missing files as failure")
    args = ap.parse_args(argv)
    bad = False
    for path, status in verify(require=args.require):
        print(f"{status:9s} {path}")
        bad |= status == "mismatch" or (status == "missing" and args.require)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
