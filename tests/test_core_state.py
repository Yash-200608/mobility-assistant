"""Shared state, alert queue and circuit breaker: the glue every thread depends on."""
import queue
import threading
import unittest

import _support as S
import core
from core import CircuitBreaker, SystemState


class AlertQueueTests(unittest.TestCase):
    def setUp(self):
        ctx = S.fake_time(core)
        self.clock = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.st = SystemState()

    def messages(self):
        out = []
        while True:
            try:
                out.append(self.st.alert_queue.get_nowait()[1])
            except queue.Empty:
                return out

    def test_paused_alerts_are_dropped_unless_forced(self):
        self.st.set_alerts_enabled(False)
        self.st.queue_alert("routine")
        self.st.queue_alert("critical", force=True)
        self.assertEqual(self.messages(), ["critical"])

    def test_full_queue_drops_routine_alerts_but_never_a_forced_one(self):
        cap = self.st.alert_queue.maxsize
        for i in range(cap):
            self.st.queue_alert(f"old{i}")
        self.st.queue_alert("routine-overflow")
        self.st.queue_alert("FORCED", force=True)
        msgs = self.messages()
        self.assertEqual(len(msgs), cap)
        self.assertNotIn("routine-overflow", msgs)
        self.assertIn("FORCED", msgs)
        self.assertNotIn("old0", msgs)  # oldest evicted to make room

    def test_concurrent_producers_and_a_consumer_do_not_raise_or_deadlock(self):
        errors = []
        stop = threading.Event()

        def producer(n):
            try:
                for i in range(200):
                    self.st.queue_alert(f"p{n}-{i}", force=bool(i % 2))
                    self.st.update_sensor_data({"pitch": float(i)})
                    self.st.get_sensor_data()
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        def consumer():
            while not stop.is_set():
                try:
                    self.st.alert_queue.get(timeout=0.01)
                except queue.Empty:
                    pass

        c = threading.Thread(target=consumer)
        c.start()
        ps = [threading.Thread(target=producer, args=(n,)) for n in range(6)]
        for p in ps:
            p.start()
        for p in ps:
            p.join(timeout=10)
        stop.set()
        c.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertFalse(any(p.is_alive() for p in ps))


class SensorSnapshotTests(unittest.TestCase):
    def test_snapshot_is_a_copy_not_a_live_view(self):
        st = SystemState()
        snap = st.get_sensor_data()
        snap["pitch"] = 999.0
        self.assertNotEqual(st.get_sensor_data()["pitch"], 999.0)

    def test_missing_fields_keep_their_last_value(self):
        st = SystemState()
        st.update_sensor_data({"pitch": 10.0, "roll": 5.0})
        st.update_sensor_data({"pitch": 11.0})
        self.assertEqual(st.get_sensor_data()["roll"], 5.0)

    def test_gait_metrics_are_defensively_copied_and_cleared(self):
        st = SystemState()
        src = {"pattern": "Active Walking", "symmetry": "88", "cadence": 60.7, "events": ["L_FOOT"]}
        st.set_gait_metrics(src)
        src["events"].append("R_FOOT")
        got = st.get_gait_metrics()
        self.assertEqual((got["symmetry"], got["cadence"], got["events"]), (88, 60, ["L_FOOT"]))
        st.set_gait_metrics(None)
        self.assertIsNone(st.get_gait_metrics())


class CircuitBreakerTests(unittest.TestCase):
    def setUp(self):
        ctx = S.fake_time(core)
        self.clock = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)

    def trip(self):
        for _ in range(3):
            self.cb.record_failure()

    def test_opens_at_threshold_and_blocks_calls(self):
        self.cb.record_failure()
        self.cb.record_failure()
        self.assertTrue(self.cb.can_execute())
        self.cb.record_failure()
        self.assertEqual(self.cb.state, "OPEN")
        self.assertFalse(self.cb.can_execute())

    def test_single_probe_after_timeout_then_closes_on_success(self):
        self.trip()
        self.clock.advance(30.1)
        self.assertTrue(self.cb.can_execute())   # the probe
        self.assertFalse(self.cb.can_execute())  # nobody else while probing
        self.cb.record_success()
        self.assertEqual(self.cb.state, "CLOSED")
        self.assertTrue(self.cb.can_execute())

    def test_failed_probe_reopens_for_another_full_timeout(self):
        self.trip()
        self.clock.advance(30.1)
        self.cb.can_execute()
        self.cb.record_failure()
        self.assertEqual(self.cb.state, "OPEN")
        self.clock.advance(10)
        self.assertFalse(self.cb.can_execute())

    def test_unused_probe_can_be_released_so_breaker_does_not_stick(self):
        self.trip()
        self.clock.advance(30.1)
        self.assertTrue(self.cb.can_execute())
        self.cb.release_probe()
        self.assertTrue(self.cb.can_execute())  # a later call may probe again


class SosCooldownTests(unittest.TestCase):
    def test_cooldown_boundaries(self):
        with S.fake_time(core, start=500.0) as clock:
            st = SystemState()
            self.assertTrue(st.try_acquire_sos_cooldown(45))
            clock.advance(44.9)
            self.assertFalse(st.try_acquire_sos_cooldown(45))
            clock.advance(0.2)
            self.assertTrue(st.try_acquire_sos_cooldown(45))


if __name__ == "__main__":
    unittest.main()
