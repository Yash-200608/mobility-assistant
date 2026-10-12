"""Fall / pre-fall state machines driven by a fake clock: no IMU, camera or sleep."""
import unittest

import _support as S
import analytics
import analytics_enhanced
from analytics import FallDetector
from analytics_enhanced import PreFallDetector
from config import Config

HZ = 20  # Arduino telemetry rate documented in ARCHITECTURE.md


def run(detector, clock, samples, hz=HZ):
    """Feed samples at a fixed rate; return [(t_since_start, state, alert), ...]."""
    t0 = clock.now
    out = []
    for s in samples:
        alert = detector.process(s)
        out.append((round(clock.now - t0, 3), detector.state, alert))
        clock.advance(1.0 / hz)
    return out


def alerts_of(trace):
    return [(t, a) for t, _, a in trace if a]


def lying_still(seconds, hz=HZ):
    return [S.sensor(pitch=80, roll=5, accel_mag=1.0)] * int(seconds * hz)


def upright(seconds, hz=HZ):
    return [S.sensor(pitch=2, roll=1, accel_mag=1.0)] * int(seconds * hz)


IMPACT = S.sensor(pitch=60, roll=5, accel_mag=3.5)


class FallDetectorTests(unittest.TestCase):
    def setUp(self):
        ctx = S.fake_time(analytics)
        self.clock = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.fd = FallDetector()

    def test_quiet_standing_never_alerts(self):
        trace = run(self.fd, self.clock, upright(10))
        self.assertEqual({s for _, s, _ in trace}, {"UPRIGHT"})
        self.assertEqual(alerts_of(trace), [])

    def test_ordinary_walking_motion_is_not_a_fall(self):
        walk = [
            S.sensor(pitch=15 * ((i % 8) - 4) / 4, roll=10 * ((i % 6) - 3) / 3,
                     accel_mag=1.0 + 0.6 * ((i % 5) / 4))
            for i in range(10 * HZ)
        ]
        trace = run(self.fd, self.clock, walk)
        self.assertEqual({s for _, s, _ in trace}, {"UPRIGHT"})
        self.assertEqual(alerts_of(trace), [])

    def test_empty_samples_are_treated_as_rest_not_as_a_fall(self):
        trace = run(self.fd, self.clock, [{}] * (5 * HZ))
        self.assertEqual({s for _, s, _ in trace}, {"UPRIGHT"})
        self.assertEqual(alerts_of(trace), [])

    def test_impact_then_lying_still_reaches_fallen_and_alerts_once(self):
        trace = run(self.fd, self.clock, upright(1) + [IMPACT] + lying_still(5))
        self.assertEqual(self.fd.state, "FALLEN")
        impact_t = 1.0
        fallen_t = next(t for t, s, _ in trace if s == "FALLEN")
        # Regression bound, not a clinical claim: confirmation within 3 s at 20 Hz.
        self.assertLess(fallen_t - impact_t, 3.0)
        fall_alerts = alerts_of(trace)
        self.assertEqual(len(fall_alerts), 1)
        self.assertIn("Fall detected", fall_alerts[0][1])

    def test_single_spike_followed_by_movement_returns_to_upright_silently(self):
        moving = [S.sensor(pitch=5, roll=3, accel_mag=1.6)] * (3 * HZ)
        trace = run(self.fd, self.clock, upright(1) + [IMPACT] + moving)
        self.assertEqual(self.fd.state, "UPRIGHT")
        self.assertEqual(alerts_of(trace), [])
        self.assertNotIn("FALLEN", {s for _, s, _ in trace})

    def test_spike_with_continued_jitter_is_not_confirmed_as_a_fall(self):
        # Still, then a bump every other sample: never FALL_CONFIRM_FRAMES in a row.
        seq = [IMPACT] + [S.sensor(pitch=5, accel_mag=1.0), S.sensor(pitch=5, accel_mag=2.0)] * 40
        trace = run(self.fd, self.clock, seq)
        self.assertNotEqual(self.fd.state, "FALLEN")

    def test_tilt_warning_alerts_are_rate_limited_and_clear_on_recovery(self):
        tilt = [S.sensor(pitch=Config.TILT_WARN_PITCH + 5)] * (8 * HZ)
        trace = run(self.fd, self.clock, tilt)
        self.assertEqual(self.fd.state, "TILT_WARNING")
        tilt_alerts = alerts_of(trace)
        self.assertGreaterEqual(len(tilt_alerts), 1)
        self.assertLessEqual(len(tilt_alerts), 8 // int(Config.TILT_ALERT_INTERVAL) + 1)
        for (t1, _), (t2, _) in zip(tilt_alerts, tilt_alerts[1:]):
            self.assertGreater(t2 - t1, Config.TILT_ALERT_INTERVAL)

        run(self.fd, self.clock, upright(1))
        self.assertEqual(self.fd.state, "UPRIGHT")

    def test_tilt_past_fall_angle_escalates_to_falling(self):
        run(self.fd, self.clock, [S.sensor(pitch=Config.TILT_WARN_PITCH + 5)] * 3)
        run(self.fd, self.clock, [S.sensor(pitch=Config.FALL_PITCH_DEG + 5)] * 2)
        self.assertEqual(self.fd.state, "FALLING")

    def test_repeated_fall_alerts_respect_the_interval(self):
        trace = run(self.fd, self.clock, [IMPACT] + lying_still(40))
        fall_alerts = alerts_of(trace)
        self.assertGreaterEqual(len(fall_alerts), 4)  # not suppressed forever
        for (t1, _), (t2, _) in zip(fall_alerts, fall_alerts[1:]):
            self.assertGreaterEqual(t2 - t1, Config.FALL_ALERT_INTERVAL)

    def test_full_recovery_sequence_reports_once(self):
        trace = run(self.fd, self.clock, [IMPACT] + lying_still(3) + upright(6))
        states = [s for _, s, _ in trace]
        self.assertIn("RECOVERING", states)
        self.assertEqual(self.fd.state, "UPRIGHT")
        recovery = [a for _, a in alerts_of(trace) if "Recovery" in a]
        self.assertEqual(len(recovery), 1)

    def test_relapse_while_recovering_goes_back_to_fallen(self):
        run(self.fd, self.clock, [IMPACT] + lying_still(3) + upright(2.5))
        self.assertEqual(self.fd.state, "RECOVERING")
        run(self.fd, self.clock, lying_still(1))
        self.assertEqual(self.fd.state, "FALLEN")

    def test_flapping_posture_does_not_clear_a_fall(self):
        flapping = (upright(1) + lying_still(1)) * 6
        run(self.fd, self.clock, [IMPACT] + lying_still(3) + flapping)
        self.assertEqual(self.fd.state, "FALLEN")

    def test_gap_in_samples_does_not_invent_a_fall(self):
        run(self.fd, self.clock, upright(1))
        self.clock.advance(30.0)  # sensor went quiet for 30 s
        trace = run(self.fd, self.clock, upright(1))
        self.assertEqual({s for _, s, _ in trace}, {"UPRIGHT"})
        self.assertEqual(alerts_of(trace), [])


class FallLatencyTests(unittest.TestCase):
    """Confirmation time must not depend on how hard the impact was."""

    def confirm_latency(self, spike, hz):
        fd = FallDetector()
        t = 0.0
        for _ in range(10):
            fd.process(S.sensor(), now=t)
            t += 1.0 / hz
        t_impact = t
        fd.process(S.sensor(pitch=60, accel_mag=spike), now=t)
        for _ in range(int(hz * 6)):
            t += 1.0 / hz
            fd.process(S.sensor(pitch=85, roll=5, accel_mag=1.0), now=t)
            if fd.state == "FALLEN":
                return t - t_impact
        self.fail(f"never confirmed for spike={spike} at {hz} Hz")

    def test_harder_impacts_are_not_confirmed_more_slowly(self):
        for hz in (20, 10):
            lat = {sp: self.confirm_latency(sp, hz) for sp in (2.9, 3.5, 4.5, 6.0, 10.0, 16.0)}
            self.assertLess(max(lat.values()) - min(lat.values()), 1.0 / hz + 1e-9, lat)
            self.assertLess(max(lat.values()), 0.6)

    def test_injected_clock_is_used_instead_of_wall_time(self):
        fd = FallDetector()
        fd.process(S.sensor(accel_mag=3.5), now=5_000.0)
        self.assertEqual(fd.fall_time, 5_000.0)

    def test_clock_starting_at_zero_still_works(self):
        fd = FallDetector()
        t = 0.0
        fd.process(S.sensor(accel_mag=3.5), now=t)
        for _ in range(40):
            t += 0.05
            fd.process(S.sensor(pitch=80, accel_mag=1.0), now=t)
        self.assertEqual(fd.state, "FALLEN")


class PreFallDetectorTests(unittest.TestCase):
    def setUp(self):
        ctx = S.fake_time(analytics_enhanced)
        self.clock = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.pf = PreFallDetector()

    def _step(self, pitch, dt=0.1, state="UPRIGHT"):
        self.clock.advance(dt)
        return self.pf.process(S.sensor(pitch=pitch), state)

    def test_steady_posture_is_silent(self):
        self.assertIsNone(self.pf.process(S.sensor(pitch=0)))
        for _ in range(30):
            self.assertIsNone(self._step(1.0))

    def test_fast_tilt_rate_alerts(self):
        self.pf.process(S.sensor(pitch=0))
        msg = self._step(20.0, dt=0.1)  # 200 deg/s
        self.assertIsNotNone(msg)
        self.assertIn("Unsteady", msg)

    def test_slow_drift_is_ignored(self):
        self.pf.process(S.sensor(pitch=0))
        pitch = 0.0
        for _ in range(50):
            pitch += 0.5  # 5 deg/s
            self.assertIsNone(self._step(pitch, dt=0.1))

    def test_dropped_samples_reset_the_baseline_instead_of_faking_a_rate(self):
        self.pf.process(S.sensor(pitch=0))
        self.assertIsNone(self._step(40.0, dt=1.5))  # 1.5 s gap > 0.4 s limit

    def test_alerts_are_rate_limited(self):
        self.pf.process(S.sensor(pitch=0))
        self.assertIsNotNone(self._step(20.0))
        self.assertIsNone(self._step(0.0))
        self.assertIsNone(self._step(20.0))  # still inside PREFALL_ALERT_INTERVAL
        self.clock.advance(Config.PREFALL_ALERT_INTERVAL)
        self._step(0.0)
        self.assertIsNotNone(self._step(20.0))

    def test_silent_once_a_fall_is_already_in_progress(self):
        self.pf.process(S.sensor(pitch=0))
        for state in ("FALLING", "FALLEN", "RECOVERING"):
            self.assertIsNone(self._step(20.0, state=state))


if __name__ == "__main__":
    unittest.main()
