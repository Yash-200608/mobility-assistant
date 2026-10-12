"""HardwareManager against a scripted fake serial port: no Arduino, no sleeping."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import _support as S
import analytics
import hardware
from config import Config
from hardware import HardwareManager


class FakeSerialSession:
    """One open() of the port. Items are bytes lines or Exception instances to raise."""

    def __init__(self, script, state):
        self._script = list(script)
        self._state = state
        self.exhausted_shuts_down = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def readline(self):
        if self._script:
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self.exhausted_shuts_down:
            self._state.is_shutting_down = True
        return b""


class FakePort:
    """Stands in for serial.Serial: each open() consumes the next scripted session."""

    def __init__(self, sessions, state):
        self.sessions = list(sessions)
        self.state = state
        self.opens = 0
        self.connected_at_open = []

    def __call__(self, port, baud, timeout=1):
        self.opens += 1
        self.connected_at_open.append(self.state.arduino_connected)
        script = self.sessions.pop(0) if self.sessions else []
        return FakeSerialSession(script, self.state)


def line(**payload):
    return (json.dumps(payload) + "\n").encode()


ARDUINO = SimpleNamespace(description="Arduino Uno", device="COM3")


class HardwareHarness(unittest.TestCase):
    def run_loop(self, sessions, ports=None, clock_cls=S.FakeClock):
        ports = [ARDUINO] if ports is None else ports
        with S.fresh_state(hardware) as state, S.fake_time(hardware) as clock:
            fake_port = FakePort(sessions, state)
            with mock.patch.object(hardware.serial, "Serial", fake_port), \
                    mock.patch("hardware.serial.tools.list_ports.comports", return_value=ports):
                HardwareManager()._serial_loop()
            return state, fake_port, clock


class PayloadValidationTests(unittest.TestCase):
    def setUp(self):
        self.hm = HardwareManager()

    def test_out_of_range_values_are_clamped_to_configured_limits(self):
        clean = self.hm._validate_payload({"pitch": 400, "roll": -400, "accel_mag": 99, "us_front": 5000})
        self.assertEqual(clean["pitch"], 180.0)
        self.assertEqual(clean["roll"], -180.0)
        self.assertEqual(clean["accel_mag"], 16.0)
        self.assertEqual(clean["us_front"], 999.0)

    def test_non_numeric_values_are_dropped(self):
        clean = self.hm._validate_payload({"pitch": "abc", "roll": None, "accel_mag": "1.1", "x": []})
        self.assertEqual(clean, {"accel_mag": 1.1})

    def test_nan_and_inf_are_dropped_not_clamped(self):
        nan, inf = float("nan"), float("inf")
        clean = self.hm._validate_payload(
            {"pitch": nan, "roll": inf, "accel_mag": nan, "ax": nan, "us_front": -inf, "gps_lat": 12.5}
        )
        self.assertEqual(clean, {"gps_lat": 12.5})


class SerialLoopTests(HardwareHarness):
    def test_valid_line_updates_shared_state_and_marks_fresh(self):
        state, port, _ = self.run_loop([[line(pitch=12.5, accel_mag=1.02, us_front=88)]])
        data = state.get_sensor_data()
        self.assertEqual(data["pitch"], 12.5)
        self.assertEqual(data["us_front"], 88.0)
        self.assertGreater(state.last_sensor_update_mono, 0.0)
        self.assertTrue(state.arduino_connected)

    def test_garbage_and_truncated_lines_are_ignored(self):
        script = [
            b"\xff\xfe\x00 not utf8 \n",
            b"\n",
            b"{not json\n",
            b'["a", "list"]\n',
            b'{"pitch": "abc"}\n',  # parses, but nothing usable inside
            line(pitch=7.0),
        ]
        state, _, _ = self.run_loop([script])
        self.assertEqual(state.get_sensor_data()["pitch"], 7.0)

    def test_payload_with_nothing_usable_does_not_refresh_freshness(self):
        state, _, _ = self.run_loop([[b'{"pitch": "abc"}\n', b'{"pitch": NaN}\n']])
        self.assertEqual(state.last_sensor_update_mono, 0.0)
        self.assertFalse(state.sensors_fresh())

    def test_nan_imu_stream_does_not_fabricate_a_fall(self):
        script = [b'{"pitch": NaN, "roll": NaN, "accel_mag": NaN}\n'] * 5
        state, _, _ = self.run_loop([script])
        with S.fake_time(analytics) as clock:
            fd = analytics.FallDetector()
            for _ in range(60):
                fd.process(state.get_sensor_data())
                clock.advance(0.05)
        self.assertEqual(fd.state, "UPRIGHT")

    def test_disconnect_marks_offline_then_recovers_on_reconnect(self):
        err = hardware.serial.SerialException("device reports readiness to read but returned no data")
        sessions = [[line(pitch=1.0), err], [line(pitch=2.0)]]
        state, port, clock = self.run_loop(sessions)
        self.assertEqual(port.opens, 2)
        # Between the two opens the manager must have reported the link as down.
        self.assertEqual(port.connected_at_open, [False, False])
        self.assertEqual(state.get_sensor_data()["pitch"], 2.0)
        self.assertTrue(state.arduino_connected)

    def test_no_port_backs_off_exponentially_and_reports_disconnected(self):
        with S.fresh_state(hardware) as state:
            sleeps = []

            class Clock(S.FakeClock):
                def sleep(self, seconds):
                    sleeps.append(seconds)
                    if len(sleeps) >= 6:
                        state.is_shutting_down = True

            state.arduino_connected = True  # stale "connected" from before
            with mock.patch.object(hardware, "time", Clock()), \
                    mock.patch("hardware.serial.tools.list_ports.comports", return_value=[]):
                HardwareManager()._serial_loop()
        self.assertEqual(sleeps, [1.0, 2.0, 4.0, 8.0, 10.0, 10.0])
        self.assertFalse(state.arduino_connected)

    def test_find_port_matches_known_adapters_only(self):
        hm = HardwareManager()
        ports = [SimpleNamespace(description="Standard Serial over Bluetooth link", device="COM4"),
                 SimpleNamespace(description="USB-SERIAL CH340", device="COM7")]
        with mock.patch("hardware.serial.tools.list_ports.comports", return_value=ports):
            self.assertEqual(hm._find_port(), "COM7")
        with mock.patch("hardware.serial.tools.list_ports.comports", return_value=ports[:1]):
            self.assertIsNone(hm._find_port())


class RecordingAndCommandTests(HardwareHarness):
    def test_raw_stream_is_recorded_as_replayable_jsonl(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trace.jsonl")
            with mock.patch.object(Config, "SENSOR_RECORD_PATH", path):
                self.run_loop([[line(pitch=1.5, accel_mag=1.0), b"{not json\n", line(pitch=2.5)]])
            with open(path, encoding="utf-8") as fp:
                rows = [json.loads(x) for x in fp]
        self.assertEqual(len(rows), 3)  # raw lines kept, including the malformed one
        self.assertTrue(all({"t", "raw"} <= set(r) for r in rows))
        self.assertEqual(json.loads(rows[0]["raw"])["pitch"], 1.5)

    def test_unwritable_record_path_does_not_stop_the_safety_loop(self):
        with mock.patch.object(Config, "SENSOR_RECORD_PATH", "/nonexistent-dir/x/trace.jsonl"):
            state, _, _ = self.run_loop([[line(pitch=9.0)]])
        self.assertEqual(state.get_sensor_data()["pitch"], 9.0)

    def test_no_recording_when_path_unset(self):
        with mock.patch.object(Config, "SENSOR_RECORD_PATH", ""):
            hm = HardwareManager()
            hm._record("{}")
            self.assertIsNone(hm._record_fp)


class CommandChannelTests(unittest.TestCase):
    def test_commands_fail_cleanly_without_a_live_link(self):
        hm = HardwareManager()
        self.assertFalse(hm.send_command("BUZZ:100"))
        self.assertFalse(hm.buzz("fall"))

    def test_buzz_patterns_map_to_firmware_commands(self):
        hm = HardwareManager()
        written = []
        hm._ser = SimpleNamespace(write=lambda b: written.append(b))
        self.assertTrue(hm.buzz("fall"))
        self.assertTrue(hm.buzz("off"))
        self.assertFalse(hm.buzz("nonsense"))
        self.assertEqual(written, [b"BUZZ:3000\n", b"BUZZ:0\n"])

    def test_write_errors_are_swallowed(self):
        hm = HardwareManager()

        def boom(_):
            raise OSError("unplugged")

        hm._ser = SimpleNamespace(write=boom)
        self.assertFalse(hm.send_command("BUZZ:1"))


class FreshnessGatingTests(unittest.TestCase):
    def test_stale_feed_turns_hardware_safety_off_after_timeout(self):
        import core
        with S.fake_time(core) as clock, mock.patch.object(Config, "SAFETY_RUN_MODE", "fused"):
            st = core.SystemState()
            self.assertFalse(st.use_hardware_for_safety())  # never connected
            st.arduino_connected = True
            st.update_sensor_data({"pitch": 0.0})
            self.assertTrue(st.use_hardware_for_safety())
            clock.advance(Config.SENSOR_STALE_SEC - 0.1)
            self.assertTrue(st.use_hardware_for_safety())
            clock.advance(0.2)
            self.assertFalse(st.use_hardware_for_safety())
            st.update_sensor_data({"pitch": 0.0})  # data resumes
            self.assertTrue(st.use_hardware_for_safety())

    def test_software_only_never_trusts_hardware(self):
        import core
        with S.fake_time(core), mock.patch.object(Config, "SAFETY_RUN_MODE", "software_only"):
            st = core.SystemState()
            st.arduino_connected = True
            st.update_sensor_data({"pitch": 0.0})
            self.assertTrue(st.sensors_fresh())
            self.assertFalse(st.use_hardware_for_safety())

    @unittest.expectedFailure
    def test_hardware_strict_uses_hardware_when_feed_is_fresh(self):
        """KNOWN ISSUE: use_hardware_for_safety() only accepts 'fused', so in
        hardware_strict mode main.py never runs the IMU fall detector even with a
        healthy feed. ARCHITECTURE.md's mode table implies it should. Flip this to
        a normal test once the intended semantics are confirmed."""
        import core
        with S.fake_time(core), mock.patch.object(Config, "SAFETY_RUN_MODE", "hardware_strict"):
            st = core.SystemState()
            st.arduino_connected = True
            st.update_sensor_data({"pitch": 0.0})
            self.assertTrue(st.use_hardware_for_safety())


if __name__ == "__main__":
    unittest.main()
