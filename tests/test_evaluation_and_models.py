"""The evaluation maths and the model lock, so reported numbers can be trusted."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import _support as S

sys.path.insert(0, str(S.ROOT / "tools"))
import evaluate_fall_detection as ev  # noqa: E402
import verify_models  # noqa: E402


def trial(i, gt, alert, latency=None, scenario="s", duration=60.0, subject="p1"):
    return {"trial_id": str(i), "subject_id": subject, "scenario": scenario,
            "ground_truth": str(int(gt)), "alert": str(int(alert)),
            "latency_s": "" if latency is None else str(latency), "duration_s": str(duration)}


class MetricsTests(unittest.TestCase):
    def metrics(self, rows):
        return ev.compute_metrics(ev.load_trials(rows))

    def test_confusion_matrix_and_rates(self):
        rows = (
            [trial(i, 1, 1, latency=1.0 + i * 0.1) for i in range(8)]       # 8 TP
            + [trial(10 + i, 1, 0) for i in range(2)]                        # 2 FN
            + [trial(20 + i, 0, 1, duration=1800) for i in range(1)]         # 1 FP, 0.5 h
            + [trial(30 + i, 0, 0, duration=1800) for i in range(3)]         # 3 TN, 1.5 h
        )
        m = self.metrics(rows)
        self.assertEqual((m["tp"], m["fn"], m["fp"], m["tn"]), (8, 2, 1, 3))
        self.assertAlmostEqual(m["recall"], 0.8)
        self.assertAlmostEqual(m["precision"], 8 / 9)
        self.assertAlmostEqual(m["specificity"], 0.75)
        self.assertAlmostEqual(m["f1"], 16 / 19)
        self.assertAlmostEqual(m["negative_hours"], 2.0)
        self.assertAlmostEqual(m["false_alarms_per_hour"], 0.5)

    def test_latency_summary_uses_true_positives_only(self):
        rows = [trial(1, 1, 1, latency=1.0), trial(2, 1, 1, latency=2.0), trial(3, 1, 1, latency=3.0),
                trial(4, 1, 0), trial(5, 0, 1, latency=99.0)]
        m = self.metrics(rows)
        self.assertEqual(m["latency_n"], 3)
        self.assertEqual(m["latency_median_s"], 2.0)
        self.assertEqual(m["latency_max_s"], 3.0)
        self.assertAlmostEqual(m["latency_p95_s"], 2.9)

    def test_wilson_interval_matches_reference_values(self):
        lo, hi = ev.wilson(8, 10)
        self.assertAlmostEqual(lo, 0.4902, places=3)
        self.assertAlmostEqual(hi, 0.9433, places=3)
        self.assertEqual(ev.wilson(0, 0), None)
        self.assertEqual(ev.wilson(0, 10)[0], 0.0)
        self.assertAlmostEqual(ev.wilson(10, 10)[1], 1.0)

    def test_empty_classes_yield_none_not_a_crash(self):
        m = self.metrics([trial(1, 0, 0)])  # no positives at all
        self.assertIsNone(m["recall"])
        self.assertIsNone(m["precision"])
        self.assertEqual(m["specificity"], 1.0)
        self.assertIn("n/a", ev.render_report(m))

    def test_per_scenario_breakdown(self):
        rows = [trial(1, 1, 1, latency=1.0, scenario="fall_forward"),
                trial(2, 1, 0, scenario="fall_forward"),
                trial(3, 0, 1, scenario="sit_down")]
        per = self.metrics(rows)["per_scenario"]
        self.assertEqual(per["fall_forward"], {"n": 2, "tp": 1, "fn": 1, "fp": 0, "tn": 0})
        self.assertEqual(per["sit_down"], {"n": 1, "tp": 0, "fn": 0, "fp": 1, "tn": 0})

    def test_report_warns_on_small_samples(self):
        report = ev.render_report(self.metrics([trial(1, 1, 1, latency=1.0), trial(2, 0, 0)]))
        self.assertIn("Small sample", report)


class InputValidationTests(unittest.TestCase):
    def test_rejects_bad_rows_instead_of_guessing(self):
        bad = [
            dict(trial(1, 1, 1, latency=1.0), alert="maybe"),
            dict(trial(1, 1, 1, latency=1.0), duration_s="0"),
            dict(trial(1, 1, 1, latency=-1.0)),
            trial(1, 1, 1, latency=None),           # TP with no latency
            {k: v for k, v in trial(1, 1, 1, latency=1.0).items() if k != "scenario"},
        ]
        for row in bad:
            with self.subTest(row=row), self.assertRaises(ValueError):
                ev.load_trials([row])


class ModelLockTests(unittest.TestCase):
    def test_lock_file_is_well_formed_and_matches_repo_files(self):
        lock = json.loads((S.ROOT / "models.lock.json").read_text(encoding="utf-8"))
        for art in lock["artifacts"]:
            for key in ("path", "sha256", "size_bytes", "role", "source", "license", "acquisition"):
                self.assertIn(key, art)
            self.assertEqual(len(art["sha256"]), 64)
        # Every committed weight must be listed (no unexplained binaries in the root).
        listed = {a["path"] for a in lock["artifacts"]}
        self.assertEqual({p.name for p in S.ROOT.glob("*.pt")} - listed, set())
        # The default weight named in Config must be a listed artifact.
        from config import Config
        self.assertIn(Config.LOCAL_MODEL_WEIGHTS, listed)

    def test_verify_detects_ok_missing_and_tampered_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "good.pt").write_bytes(b"weights")
            (root / "bad.pt").write_bytes(b"tampered")
            lock = {"artifacts": [
                {"path": "good.pt", "sha256": hashlib.sha256(b"weights").hexdigest(), "size_bytes": 7},
                {"path": "bad.pt", "sha256": hashlib.sha256(b"original").hexdigest(), "size_bytes": 8},
                {"path": "gone.pt", "sha256": "0" * 64, "size_bytes": 1},
            ]}
            lock_path = root / "lock.json"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            got = dict(verify_models.verify(lock_path, root))
        self.assertEqual(got, {"good.pt": "ok", "bad.pt": "mismatch", "gone.pt": "missing"})


if __name__ == "__main__":
    unittest.main()
