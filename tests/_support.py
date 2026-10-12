"""Shared helpers that let the safety logic run with no hardware, network or API keys.

Nothing here talks to a camera, serial port, speaker, Twilio or Groq. Third-party
packages that are only needed for real I/O (pyserial, twilio, groq) are replaced
with inert stand-ins when they are not installed, so the suite runs on a bare
CI runner with just the standard library and python-dotenv.
"""
from __future__ import annotations

import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Never let a developer's real .env leak credentials/config into a test run.
for _key in ("GROQ_API_KEY", "ELEVENLABS_API_KEY", "SAFETY_RUN_MODE", "ENABLE_ONLINE_LLM"):
    os.environ.pop(_key, None)


def _stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


def install_fake_io_modules() -> None:
    """Register inert stand-ins for I/O packages that are not installed."""
    try:
        import serial  # noqa: F401
        import serial.tools.list_ports  # noqa: F401
    except ImportError:
        class SerialException(OSError):
            pass

        ser = _stub("serial", Serial=object, SerialException=SerialException)
        tools = _stub("serial.tools")
        ports = _stub("serial.tools.list_ports", comports=lambda: [])
        ser.tools, tools.list_ports = tools, ports

    try:
        import twilio.rest  # noqa: F401
    except ImportError:
        _stub("twilio")
        _stub("twilio.rest", Client=object)

    try:
        import groq  # noqa: F401
    except ImportError:
        _stub("groq", Groq=object)


install_fake_io_modules()

import logging  # noqa: E402

import core  # noqa: E402

core.logger.setLevel(logging.CRITICAL)  # keep test output readable


class FakeClock:
    """Deterministic stand-in for the ``time`` module used by the safety code."""

    def __init__(self, start: float = 1_000.0):
        self.now = float(start)

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)

    def strftime(self, fmt: str) -> str:
        return "00:00:00"

    def advance(self, seconds: float) -> None:
        self.now += seconds


@contextmanager
def fake_time(*modules, start: float = 1_000.0):
    """Replace ``module.time`` with a FakeClock in every given module."""
    clock = FakeClock(start)
    patches = [mock.patch.object(m, "time", clock) for m in modules]
    for p in patches:
        p.start()
    try:
        yield clock
    finally:
        for p in reversed(patches):
            p.stop()


@contextmanager
def fresh_state(*modules):
    """Give each given module its own clean SystemState (they bind it by name)."""
    import core

    state = core.SystemState()
    patches = [mock.patch.object(m, "global_state", state) for m in modules]
    for p in patches:
        p.start()
    try:
        yield state
    finally:
        for p in reversed(patches):
            p.stop()


def enter(testcase, cm):
    """Python 3.10-safe stand-in for TestCase.enterContext (added in 3.11)."""
    value = cm.__enter__()
    testcase.addCleanup(cm.__exit__, None, None, None)
    return value


def sensor(pitch=0.0, roll=0.0, accel_mag=1.0, **extra):
    d = {"pitch": pitch, "roll": roll, "accel_mag": accel_mag}
    d.update(extra)
    return d
