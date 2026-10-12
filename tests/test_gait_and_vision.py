"""Gait, posture and object-tracking logic using synthetic landmarks and detections.

No camera, MediaPipe or YOLO weights are loaded: landmarks are plain objects with a
``.y`` / ``.x`` and detections are dicts shaped like io_services.local_vision_thread's.
"""
import csv
import importlib
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import _support as S
import analytics
import analytics_enhanced as ae
import navigation
import research_log
from analytics import GaitAnalyser
from audio_fallcontext import AudioFallDetector
from config import Config

FLOOR = Config.GAIT_VIRTUAL_FLOOR_Y


def landmarks(l_heel_y=0.5, r_heel_y=0.5, **overrides):
    pts = [SimpleNamespace(x=0.5, y=0.5) for _ in range(33)]
    pts[29].y, pts[30].y = l_heel_y, r_heel_y
    for idx, (x, y) in overrides.items():
        pts[int(idx.lstrip("p"))] = SimpleNamespace(x=x, y=y)
    return pts


class GaitAnalyserTests(unittest.TestCase):
    def setUp(self):
        self.clock = S.enter(self, S.fake_time(analytics))
        self.g = GaitAnalyser()

    def strike(self, foot, hold=0.1):
        lo, hi = FLOOR - 0.2, FLOOR + 0.05
        up = landmarks(*( (hi, lo) if foot == "L" else (lo, hi) ))
        down = landmarks(lo, lo)
        m = self.g.process_vision(up)
        self.clock.advance(hold)
        self.g.process_vision(down)
        return m

    def walk(self, n_steps, l_period, r_period):
        """Alternate feet; L strikes every l_period s, R every r_period s."""
        t_l = t_r = self.clock.now
        end = self.clock.now + n_steps * max(l_period, r_period)
        last = None
        while self.clock.now < end:
            if self.clock.now >= t_l:
                last, t_l = self.strike("L"), t_l + l_period
            if self.clock.now >= t_r:
                last, t_r = self.strike("R"), t_r + r_period
            self.clock.advance(0.05)
        return last

    def test_no_person_in_frame_reports_searching_and_raises_no_alert(self):
        m = self.g.process_vision(None)
        self.assertEqual(m["pattern"], "Searching for User...")
        self.assertEqual((m["symmetry"], m["cadence"]), (0, 0))
        self.assertIsNone(self.g.get_alert(m))

    def test_heel_crossing_floor_counts_exactly_one_step(self):
        m = self.strike("L")
        self.assertEqual(m["events"], ["L_FOOT"])

    def test_jitter_around_the_floor_line_does_not_double_count(self):
        events = []
        for y in (FLOOR + 0.01, FLOOR - 0.01, FLOOR + 0.02, FLOOR - 0.03, FLOOR + 0.01):
            events += self.g.process_vision(landmarks(l_heel_y=y))["events"]
            self.clock.advance(0.05)
        self.assertEqual(events, ["L_FOOT"])  # hysteresis: must rise 0.05 to re-arm

    def test_even_gait_scores_full_symmetry_and_walking_cadence(self):
        m = self.walk(12, l_period=0.5, r_period=0.5)
        self.assertGreaterEqual(m["symmetry"], 90)
        self.assertEqual(m["pattern"], "Active Walking")
        self.assertGreater(m["cadence"], 60)

    def test_uneven_gait_lowers_symmetry_and_alerts_with_cooldown(self):
        m = self.walk(12, l_period=0.4, r_period=0.9)
        self.assertLess(m["symmetry"], 70)
        alert = self.g.get_alert(m)
        self.assertIn("asymmetry", alert)
        self.assertIsNone(self.g.get_alert(m))  # inside GAIT_ALERT_COOLDOWN
        self.clock.advance(Config.GAIT_ALERT_COOLDOWN + 0.1)
        self.assertIsNotNone(self.g.get_alert(m))

    def test_alert_thresholds(self):
        self.assertIn("Cadence", self.g.get_alert({"symmetry": 95, "cadence": 20}))
        self.clock.advance(Config.GAIT_ALERT_COOLDOWN + 1)
        self.assertIsNone(self.g.get_alert({"symmetry": 95, "cadence": 0}))  # idle: no nagging
        self.assertIsNone(self.g.get_alert({"symmetry": 95, "cadence": 70}))


class EnhancedAnalyticsTests(unittest.TestCase):
    def test_slouch_score_scales_and_survives_bad_landmarks(self):
        upright = landmarks(p0=(0.5, 0.2), p23=(0.5, 0.6), p24=(0.5, 0.6))
        self.assertEqual(ae.PostureMonitor.slouch_score(upright), 0)
        slumped = landmarks(p0=(0.5, 0.55), p23=(0.5, 0.6), p24=(0.5, 0.6))
        self.assertGreater(ae.PostureMonitor.slouch_score(slumped), 0)
        for bad in (None, [], [SimpleNamespace()], "garbage"):
            self.assertEqual(ae.PostureMonitor.slouch_score(bad), 0)

    def test_posture_alert_threshold(self):
        self.assertIsNone(ae.PostureMonitor.alert_if_bad(Config.POSTURE_SLOUCH_ALERT_THRESH - 1))
        self.assertIsNotNone(ae.PostureMonitor.alert_if_bad(Config.POSTURE_SLOUCH_ALERT_THRESH))

    def test_stride_proxy_handles_degenerate_shoulders_and_bad_input(self):
        wide = landmarks(p27=(0.2, 0.9), p28=(0.8, 0.9), p11=(0.4, 0.3), p12=(0.6, 0.3))
        self.assertAlmostEqual(ae.StrideLengthProxy.estimate(wide), 3.0, places=2)
        collapsed = landmarks(p27=(0.2, 0.9), p28=(0.8, 0.9), p11=(0.5, 0.3), p12=(0.5, 0.3))
        self.assertTrue(ae.StrideLengthProxy.estimate(collapsed) > 0)  # no ZeroDivisionError
        self.assertEqual(ae.StrideLengthProxy.estimate(None), 0.0)

    def test_fall_direction_picks_dominant_axis(self):
        p = ae.FallDirectionPredictor.predict
        self.assertEqual(p(40, 5), "forward")
        self.assertEqual(p(-40, 5), "backward")
        self.assertEqual(p(5, 40), "to the right")
        self.assertEqual(p(5, -40), "to the left")

    def test_fingerprint_is_quiet_until_it_has_a_baseline(self):
        g = ae.GaitFingerprint()
        for _ in range(5):
            g.update(80, 60)
        self.assertEqual(g.deviation_score(), 0.0)
        g.update(80, 0)  # standing still is excluded from the baseline
        self.assertEqual(g.baseline_summary(), (80.0, 60.0))

    def test_rehab_summary_with_no_data_does_not_divide_by_zero(self):
        s = ae.RehabProgressTracker().summary()
        self.assertEqual((s["avg_symmetry"], s["avg_cadence"], s["walker_frames"]), (0.0, 0.0, 0))

    def test_met_estimate_is_capped(self):
        self.assertEqual(ae.EnergyExpenditure.estimate_met(10_000, True), 5.5)
        self.assertEqual(ae.EnergyExpenditure.estimate_met(90, False), 1.0)

    def test_coaching_context_line(self):
        c = ae.AdaptiveCoachingEngine.context_line
        self.assertEqual(c(0.0, 1.0, 0, 0.0), "coaching_hints=steady_state")
        line = c(3.0, 4.0, 50, 0.5)
        for tag in ("gait_recently_unusual_vs_baseline", "estimated_effort_elevated",
                    "posture_may_need_cueing", "left_right_step_imbalance"):
            self.assertIn(tag, line)


class AudioImpactTests(unittest.TestCase):
    def test_threshold_and_cooldown(self):
        d = AudioFallDetector()
        self.assertFalse(d.check_loud_transient(Config.AUDIO_IMPACT_RMS / 2, now=100.0))
        self.assertTrue(d.check_loud_transient(Config.AUDIO_IMPACT_RMS * 2, now=100.0))
        self.assertFalse(d.check_loud_transient(Config.AUDIO_IMPACT_RMS * 2, now=101.0))
        self.assertTrue(d.check_loud_transient(Config.AUDIO_IMPACT_RMS * 2,
                                               now=100.0 + Config.AUDIO_IMPACT_COOLDOWN + 0.1))


class ResearchLogTests(unittest.TestCase):
    def test_rows_written_with_header_and_defaults_for_missing_fields(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(Config, "RESEARCH_EXPORT_DIR", d), \
                mock.patch.object(Config, "RESEARCH_LOG_ENABLED", True):
            research_log.reset_session_path()
            self.addCleanup(research_log.reset_session_path)
            research_log.log_research_row({"t": 1.0, "mode": "walker", "unknown_field": "dropped"})
            research_log.log_research_row({"t": 2.0, "mode": "stick", "fall_state": "FALLEN"})
            (path,) = [os.path.join(d, f) for f in os.listdir(d)]
            with open(path, newline="", encoding="utf-8") as fp:
                rows = list(csv.DictReader(fp))
        self.assertEqual(tuple(rows[0].keys()), ae.RESEARCH_CSV_FIELDS)
        self.assertEqual([r["mode"] for r in rows], ["walker", "stick"])
        self.assertEqual(rows[0]["fall_state"], "")
        self.assertEqual(rows[1]["fall_state"], "FALLEN")


class NavigationTests(unittest.TestCase):
    """Object tracking and spoken advice: pure logic, no OpenCV or YOLO needed."""

    def det(self, cls_name, cx, cy, w, h):
        return {"class": cls_name, "x": cx, "y": cy, "width": w, "height": h}

    def test_distance_buckets_follow_box_area_ratio(self):
        est = navigation.estimate_dist
        W, H = 640, 480
        self.assertEqual(est(0, 0, 640, 480, W, H), "very close")
        self.assertEqual(est(0, 0, 250, 200, W, H), "very close")   # ratio ~0.163
        self.assertEqual(est(0, 0, 200, 150, W, H), "close")        # ratio ~0.098
        self.assertEqual(est(0, 0, 100, 100, W, H), "near")         # ratio ~0.033
        self.assertEqual(est(0, 0, 10, 10, W, H), "far")
        self.assertEqual(est(0, 0, 10, 10, 0, 0), "far")            # bad frame size

    def test_tracker_keeps_identity_across_small_motion(self):
        t = navigation.ObjectTracker()
        a = t.update([self.det("chair", 100, 100, 50, 50)], 640, 480, now=0.0)
        b = t.update([self.det("chair", 104, 102, 50, 50)], 640, 480, now=0.1)
        self.assertEqual(a[0]["id"], b[0]["id"])
        self.assertEqual(len(t.tracks), 1)

    def test_tracker_does_not_merge_different_classes_or_distant_boxes(self):
        t = navigation.ObjectTracker()
        t.update([self.det("chair", 100, 100, 50, 50)], 640, 480, now=0.0)
        out = t.update([self.det("person", 100, 100, 50, 50),
                        self.det("chair", 500, 400, 50, 50)], 640, 480, now=0.1)
        self.assertEqual(len({o["id"] for o in out}), 2)
        self.assertEqual(len(t.tracks), 3)

    def test_stale_tracks_expire_after_ttl(self):
        t = navigation.ObjectTracker()
        t.update([self.det("chair", 100, 100, 50, 50)], 640, 480, now=0.0)
        t.update([], 640, 480, now=Config.TRACKED_TTL + 1)
        self.assertEqual(t.tracks, {})

    def test_empty_detections_are_handled(self):
        self.assertEqual(navigation.ObjectTracker().update([], 640, 480, now=0.0), [])

    def test_direction_advice(self):
        adv = navigation.direction_advice
        W = 640
        self.assertEqual(adv([W * 0.5], W), "Stop. Obstacle ahead.")
        self.assertEqual(adv([W * 0.5, W * 0.1], W), "Move slightly right.")
        self.assertEqual(adv([W * 0.5, W * 0.9], W), "Move slightly left.")
        self.assertEqual(adv([W * 0.1], W), "Move right.")
        self.assertEqual(adv([W * 0.9], W), "Move left.")
        self.assertIsNone(adv([W * 0.1, W * 0.9], W))  # both sides blocked: no safe hint
        self.assertIsNone(adv([], W))

    def test_advisor_rate_limits_and_dedupes_per_object_alerts(self):
        with S.fresh_state(navigation) as state:
            adv = navigation.NavigationAdvisor(state=state)
            big = [self.det("chair", 320, 240, 400, 300)]  # fills most of the frame
            boxes = adv.step(big, 640, 480, now=1000.0)
            self.assertEqual(boxes[0]["dist"], "very close")
            first = []
            while not state.alert_queue.empty():
                first.append(state.alert_queue.get_nowait()[1])
            self.assertIn("chair very close.", first)
            self.assertIn("Stop. Obstacle ahead.", first)

            adv.step(big, 640, 480, now=1001.0)  # inside both intervals
            self.assertTrue(state.alert_queue.empty())

            adv.step(big, 640, 480, now=1000.0 + Config.NAV_ALERT_INTERVAL + 4.0)
            self.assertFalse(state.alert_queue.empty())


class MainImportSmokeTest(unittest.TestCase):
    """main.py must still import with no camera, MediaPipe, speaker or GPU."""

    STUBS = ("cv2", "numpy", "mediapipe", "sounddevice", "pygame", "groq",
             "elevenlabs", "elevenlabs.client")

    def test_main_module_imports_and_exposes_entry_points(self):
        names = self.STUBS + ("main", "io_services")
        saved = {n: sys.modules.get(n) for n in names}
        try:
            for name in self.STUBS:
                sys.modules[name] = mock.MagicMock(name=name)
            for name in ("main", "io_services"):
                sys.modules.pop(name, None)
            main = importlib.import_module("main")
            self.assertTrue(callable(main.main))
            self.assertIs(main.ObjectTracker, navigation.ObjectTracker)
        finally:
            for name, mod in saved.items():
                if mod is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = mod


if __name__ == "__main__":
    unittest.main()
