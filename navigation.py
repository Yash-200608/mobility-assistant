"""Stick-mode obstacle tracking and spoken navigation advice.

Pure logic: no OpenCV, no camera. Boxes come from the vision thread as dicts
``{"class", "x", "y", "width", "height"}`` (centre + size, pixels).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from config import Config
from core import global_state


class ObjectTracker:
    def __init__(self):
        self.tracks: Dict[int, Dict[str, Any]] = {}
        self.next_id = 0

    def update(self, detections, w, h, now: Optional[float] = None):
        now = now if now is not None else time.time()
        for tid in [tid for tid, t in self.tracks.items() if now - t["last_seen"] > Config.TRACKED_TTL]:
            del self.tracks[tid]

        updated = []
        unmatched = list(detections)
        for tid, track in list(self.tracks.items()):
            best_i, best_iou = -1, 0.0
            for i, det in enumerate(unmatched):
                if det["class"] != track["label"]:
                    continue
                box1 = (track["x1"], track["y1"], track["x2"], track["y2"])
                box2 = (det["x"] - det["width"] / 2, det["y"] - det["height"] / 2,
                        det["x"] + det["width"] / 2, det["y"] + det["height"] / 2)
                x1, y1 = max(box1[0], box2[0]), max(box1[1], box2[1])
                x2, y2 = min(box1[2], box2[2]), min(box1[3], box2[3])
                inter = max(0, x2 - x1) * max(0, y2 - y1)
                union = ((box1[2] - box1[0]) * (box1[3] - box1[1])
                         + (box2[2] - box2[0]) * (box2[3] - box2[1]) - inter)
                iou = inter / union if union > 0 else 0
                if iou > best_iou:
                    best_i, best_iou = i, iou

            if best_iou > Config.IOU_MATCH_THRESH:
                m = unmatched.pop(best_i)
                track.update({"x1": m["x"] - m["width"] / 2, "y1": m["y"] - m["height"] / 2,
                              "x2": m["x"] + m["width"] / 2, "y2": m["y"] + m["height"] / 2,
                              "last_seen": now})
                updated.append(track)

        for m in unmatched:
            new_t = {"label": m["class"],
                     "x1": m["x"] - m["width"] / 2, "y1": m["y"] - m["height"] / 2,
                     "x2": m["x"] + m["width"] / 2, "y2": m["y"] + m["height"] / 2,
                     "last_seen": now, "last_alert": 0, "id": self.next_id}
            self.tracks[self.next_id] = new_t
            updated.append(new_t)
            self.next_id += 1
        return updated


def estimate_dist(x1, y1, x2, y2, w, h) -> str:
    if w <= 0 or h <= 0:
        return "far"
    area = max(0, (x2 - x1)) * max(0, (y2 - y1))
    ratio = area / (w * h)
    for t, label in Config.DIST_THRESHOLDS:
        if ratio >= t:
            return label
    return "far"


def direction_advice(centers: List[float], w: int) -> Optional[str]:
    """Spoken advice from the horizontal centres of near obstacles, or None."""
    left = sum(1 for c in centers if c < w * 0.4)
    right = sum(1 for c in centers if c > w * 0.6)
    mid = sum(1 for c in centers if w * 0.4 <= c <= w * 0.6)
    if mid > 0:
        if left > right:
            return "Move slightly right."
        if right > left:
            return "Move slightly left."
        return "Stop. Obstacle ahead."
    if left > 0 and right == 0:
        return "Move right."
    if right > 0 and left == 0:
        return "Move left."
    return None


class NavigationAdvisor:
    """Turns detections into tracked boxes and rate-limited spoken alerts."""

    def __init__(self, tracker: Optional[ObjectTracker] = None, state=None):
        self.tracker = tracker or ObjectTracker()
        self._state = state
        self.last_nav = 0.0

    @property
    def _gs(self):
        return self._state if self._state is not None else global_state

    def step(self, detections, w: int, h: int, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Returns [{label, x1, y1, x2, y2, dist}] for drawing; queues alerts."""
        now = now if now is not None else time.time()
        boxes: List[Dict[str, Any]] = []
        near: List[tuple] = []
        for trk in self.tracker.update(detections, w, h, now):
            x1, y1, x2, y2 = int(trk["x1"]), int(trk["y1"]), int(trk["x2"]), int(trk["y2"])
            dist = estimate_dist(x1, y1, x2, y2, w, h)
            boxes.append({"label": trk["label"], "x1": x1, "y1": y1, "x2": x2, "y2": y2, "dist": dist})
            if dist in ("very close", "close"):
                near.append((x1, x2))
            if dist == "very close" and now - trk["last_alert"] > 3.0:
                trk["last_alert"] = now
                self._gs.queue_alert(f"{trk['label']} very close.")

        if near and now - self.last_nav > Config.NAV_ALERT_INTERVAL:
            advice = direction_advice([(a + b) / 2 for a, b in near], w)
            if advice:
                self._gs.queue_alert(advice)
            self.last_nav = now
        return boxes
