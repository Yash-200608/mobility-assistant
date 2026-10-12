"""replay_trace, dataset_eval and demo_sim: the offline evaluation tooling."""
import contextlib
import csv
import io
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import _support as S

sys.path.insert(0, str(S.ROOT / "tools"))
import dataset_eval  # noqa: E402
import demo_sim  # noqa: E402
import evaluate_fall_detection as ev  # noqa: E402
import replay_trace  # noqa: E402

HZ = 20


def quiet(fn, *args):
    """Run a CLI entry point without cluttering test output."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args)


def jsonl(path, samples, t0=1_700_000_000.0, hz=HZ, extra_lines=()):
    with open(path, "w", encoding="utf-8") as fp:
        for i, s in enumerate(samples):
            fp.write(json.dumps({"t": t0 + i / hz, "raw": json.dumps(s)}) + "\n")
        for raw in extra_lines:
            fp.write(json.dumps({"t": t0 + 99, "raw": raw}) + "\n")


def fall_samples():
    return ([{"pitch": 1.0, "roll": 0.0, "accel_mag": 1.0}] * 40
            + [{"pitch": 60.0, "roll": 5.0, "accel_mag": 3.6}]
            + [{"pitch": 80.0, "roll": 5.0, "accel_mag": 1.0}] * 80)


class ReplayTests(unittest.TestCase):
    def test_recorded_fall_is_confirmed_with_a_known_latency(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            jsonl(p, fall_samples())
            res = replay_trace.replay(replay_trace.load_trace(str(p)))
        self.assertTrue(res.fell)
        self.assertAlmostEqual(res.first_fallen_t - 40 / HZ, 0.45, delta=0.06)

    def test_malformed_and_nonfinite_lines_are_dropped_like_the_live_loop(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            jsonl(p, [{"pitch": 1.0, "accel_mag": 1.0}] * 3,
                  extra_lines=["{not json", '{"pitch": NaN, "accel_mag": NaN}', "[1,2]"])
            samples = replay_trace.load_trace(str(p))
        self.assertEqual(len(samples), 3)
        self.assertEqual(samples[0][0], 0.0)  # times are relative to the first sample

    def test_thresholds_can_be_swept_without_touching_the_trace(self):
        samples = [(i / HZ, s) for i, s in enumerate(
            [{"pitch": 1.0, "roll": 0.0, "accel_mag": 1.0}] * 20
            + [{"pitch": 5.0, "roll": 0.0, "accel_mag": 2.0}]      # modest bump, upright
            + [{"pitch": 1.0, "roll": 0.0, "accel_mag": 1.0}] * 60)]
        self.assertFalse(replay_trace.replay(samples).fell)                          # 2 g < 2.8 g
        self.assertTrue(replay_trace.replay(samples, {"FALL_ACCEL_G": 1.5}).fell)    # now it trips

    def test_overrides_are_scoped_and_unknown_keys_rejected(self):
        from config import Config
        before = Config.FALL_ACCEL_G
        replay_trace.replay([(0.0, {"pitch": 0.0, "accel_mag": 1.0})], {"FALL_ACCEL_G": 9.0})
        self.assertEqual(Config.FALL_ACCEL_G, before)
        with self.assertRaises(KeyError):
            replay_trace.replay([(0.0, {})], {"NOT_A_SETTING": 1})

    def test_csv_traces_are_supported(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.csv"
            with open(p, "w", newline="", encoding="utf-8") as fp:
                w = csv.writer(fp)
                w.writerow(["t", "pitch", "roll", "accel_mag"])
                for i, s in enumerate(fall_samples()):
                    w.writerow([i / HZ, s["pitch"], s["roll"], s["accel_mag"]])
            self.assertTrue(replay_trace.replay(replay_trace.load_trace(str(p))).fell)

    def test_cli_prints_a_summary(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            jsonl(p, fall_samples())
            self.assertEqual(quiet(replay_trace.main, [str(p), "--set", "FALL_STILL_THRESH=1.25"]), 0)
            empty = Path(d) / "empty.jsonl"
            empty.write_text("", encoding="utf-8")
            self.assertEqual(quiet(replay_trace.main, [str(empty)]), 2)


LSB = 256  # ADXL345 +-16 g, 13 bit: 1 g = 256 LSB


def sisfall_file(path, rows):
    with open(path, "w", encoding="utf-8") as fp:
        for ax, ay, az in rows:
            fp.write(f"{ax},{ay},{az},0,0,0,0,0,0;\n")


def sisfall_fall():
    up = [(0, 0, LSB)] * 400            # 2 s standing at 200 Hz
    impact = [(0, 0, 4 * LSB)] * 10
    lying = [(LSB, 0, 0)] * 800         # 4 s on its side
    return up + impact + lying


def sisfall_adl():
    return [(8 * math.sin(i / 5), 8 * math.cos(i / 7), LSB + 10 * math.sin(i / 3)) for i in range(1200)]


class DatasetEvalTests(unittest.TestCase):
    def test_unit_conversion_and_tilt(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "D01_SA01_R01.txt"
            sisfall_file(p, [(0, 0, LSB)] * 20)
            t, s = dataset_eval.parse_sisfall(p, target_hz=20)[0]
        self.assertAlmostEqual(s["accel_mag"], 1.0, places=3)
        self.assertAlmostEqual(s["pitch"], 0.0, places=3)
        pitch, roll = dataset_eval.tilt_from_accel(1.0, 0.0, 0.0, vertical="z")
        self.assertAlmostEqual(pitch, 90.0, places=3)

    def test_downsampling_matches_target_rate(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "D01_SA01_R01.txt"
            sisfall_file(p, [(0, 0, LSB)] * 400)  # 2 s at 200 Hz
            out = dataset_eval.parse_sisfall(p, target_hz=20)
        self.assertEqual(len(out), 40)
        self.assertAlmostEqual(out[1][0] - out[0][0], 0.05, places=6)

    def test_trials_are_labelled_from_filenames_and_scored(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "SA01").mkdir()
            sisfall_file(root / "SA01" / "F01_SA01_R01.txt", sisfall_fall())
            sisfall_file(root / "SA01" / "D01_SA01_R01.txt", sisfall_adl())
            (root / "SA01" / "README.txt").write_text("ignore me", encoding="utf-8")
            rows = list(dataset_eval.sisfall_trials(root, 20.0, "z"))
        by_id = {r["trial_id"]: r for r in rows}
        self.assertEqual(set(by_id), {"F01_SA01_R01", "D01_SA01_R01"})
        fall, adl = by_id["F01_SA01_R01"], by_id["D01_SA01_R01"]
        self.assertEqual((fall["ground_truth"], fall["alert"]), ("1", "1"))
        self.assertEqual((adl["ground_truth"], adl["alert"]), ("0", "0"))
        self.assertEqual(fall["subject_id"], "SA01")
        self.assertLess(float(fall["latency_s"]), 1.5)
        m = ev.compute_metrics(ev.load_trials(rows))
        self.assertEqual((m["tp"], m["fn"], m["fp"], m["tn"]), (1, 0, 0, 1))

    def test_cli_writes_trials_and_report_and_flags_the_caveat(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ds"
            (root / "SA01").mkdir(parents=True)
            sisfall_file(root / "SA01" / "F01_SA01_R01.txt", sisfall_fall())
            sisfall_file(root / "SA01" / "D01_SA01_R01.txt", sisfall_adl())
            trials, report = Path(d) / "trials.csv", Path(d) / "report.md"
            rc = quiet(dataset_eval.main, [str(root), "--trials-out", str(trials), "--out", str(report)])
            text = report.read_text(encoding="utf-8")
            with open(trials, newline="", encoding="utf-8") as fp:
                n_rows = len(list(csv.DictReader(fp)))
        self.assertEqual(rc, 0)
        self.assertEqual(n_rows, 2)
        self.assertIn("Not device performance", text)
        self.assertIn("Small sample", text)

    def test_empty_directory_is_an_error_not_a_zero_report(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(quiet(dataset_eval.main, [d]), 2)


class DemoSimTests(unittest.TestCase):
    def run_demo(self, name, **kw):
        lines = []
        events = demo_sim.run_scenario(name, emit=lines.append, **kw)
        return events, "\n".join(lines)

    def kinds(self, events):
        return [k for _, k, _ in events]

    def test_walking_is_silent(self):
        events, out = self.run_demo("walk")
        self.assertEqual(events, [])
        self.assertIn("silent", out)

    def test_unanswered_fall_buzzes_then_sends_one_simulated_sos(self):
        events, _ = self.run_demo("fall_unanswered")
        kinds = self.kinds(events)
        self.assertEqual(kinds.count("SMS"), 1)
        self.assertIn(("BUZZER", "pattern=fall"), [(k, m) for _, k, m in events])
        t_fallen = next(t for t, k, m in events if k == "STATE" and m.endswith("FALLEN"))
        t_sms = next(t for t, k, _ in events if k == "SMS")
        self.assertAlmostEqual(t_sms - t_fallen, 20.0, delta=0.2)

    def test_voice_cancel_suppresses_the_sos_and_the_repeat_prompts(self):
        events, _ = self.run_demo("fall_cancelled")
        self.assertNotIn("SMS", self.kinds(events))
        spoken = [m for _, k, m in events if k == "SPOKEN"]
        self.assertEqual(sum("Do you need assistance" in m for m in spoken), 0)

    def test_standing_up_cancels(self):
        events, _ = self.run_demo("fall_recover")
        self.assertNotIn("SMS", self.kinds(events))
        self.assertIn(("BUZZER", "pattern=off"), [(k, m) for _, k, m in events])

    def test_auto_sos_off_never_sends(self):
        events, _ = self.run_demo("fall_unanswered", auto_sos=False)
        self.assertNotIn("SMS", self.kinds(events))

    def test_one_prompt_when_a_fall_is_first_confirmed(self):
        events, _ = self.run_demo("fall_unanswered")
        first_fall_prompts = [m for t, k, m in events if k == "SPOKEN" and t < 5.0]
        self.assertEqual(len(first_fall_prompts), 1)
        self.assertIn("Say I'm okay", first_fall_prompts[0])

    def test_known_limitation_upright_bump_is_treated_as_a_fall(self):
        """Documents current behaviour (also listed in EVALUATION.md as device_bump)."""
        events, _ = self.run_demo("bump", auto_sos=False)
        self.assertTrue(any(k == "STATE" and m.endswith("FALLEN") for _, k, m in events))

    def test_recorded_demo_stream_round_trips_through_replay(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "demo.jsonl"
            self.run_demo("fall_unanswered", record=str(p))
            res = replay_trace.replay(replay_trace.load_trace(str(p)))
        self.assertTrue(res.fell)

    def test_unknown_scenario_is_rejected(self):
        with self.assertRaises(KeyError):
            demo_sim.run_scenario("nope", emit=lambda s: None)
        self.assertEqual(quiet(demo_sim.main, ["nope"]), 2)


if __name__ == "__main__":
    unittest.main()
