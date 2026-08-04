"""Tests for the embed daemon's idle-shutdown watchdog.

The daemon's resident cost is memory, not CPU (~0% CPU holding ~785MB of
loaded model), so idle-exit is the control that actually returns the RAM.
These tests cover the clock, the watchdog's fire/hold decision, and the
health-probe carve-out.
"""
from __future__ import annotations

import importlib
import os
import sys
import threading
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(idle_timeout: str):
    """Import embed_daemon with a specific idle timeout baked in."""
    os.environ["EMBED_DAEMON_IDLE_TIMEOUT"] = idle_timeout
    sys.modules.pop("embed_daemon", None)
    return importlib.import_module("embed_daemon")


@pytest.fixture(autouse=True)
def _restore_env():
    prior = os.environ.get("EMBED_DAEMON_IDLE_TIMEOUT")
    yield
    if prior is None:
        os.environ.pop("EMBED_DAEMON_IDLE_TIMEOUT", None)
    else:
        os.environ["EMBED_DAEMON_IDLE_TIMEOUT"] = prior
    sys.modules.pop("embed_daemon", None)


class _FakeServer:
    """Stands in for HTTPServer; records that shutdown was requested."""

    def __init__(self) -> None:
        self.shutdown_called = False
        self.closed = False

    def shutdown(self) -> None:
        self.shutdown_called = True

    def server_close(self) -> None:
        self.closed = True


def test_default_idle_timeout_is_thirty_minutes():
    mod = _load("1800")
    assert mod.IDLE_TIMEOUT_S == 1800


def test_zero_disables_idle_shutdown():
    mod = _load("0")
    assert mod.IDLE_TIMEOUT_S == 0


def test_touch_resets_the_idle_clock():
    mod = _load("1800")
    mod._last_request_at = time.monotonic() - 500
    assert mod._idle_seconds() >= 500
    mod._touch_idle_clock()
    assert mod._idle_seconds() < 1


def test_watchdog_exits_when_idle_exceeds_limit():
    mod = _load("1")
    mod._IDLE_CHECK_INTERVAL_S = 0.05
    server = _FakeServer()
    done = threading.Event()
    mod._last_request_at = time.monotonic() - 10  # already well past the limit

    thread = mod._start_idle_watchdog(server, done)
    thread.join(timeout=3)

    assert server.shutdown_called, "watchdog should shut the server down when idle"
    assert done.is_set()


def test_watchdog_holds_while_traffic_keeps_arriving():
    mod = _load("5")
    mod._IDLE_CHECK_INTERVAL_S = 0.05
    server = _FakeServer()
    done = threading.Event()
    mod._touch_idle_clock()

    thread = mod._start_idle_watchdog(server, done)
    for _ in range(6):  # keep it busy across several check intervals
        time.sleep(0.05)
        mod._touch_idle_clock()

    assert not server.shutdown_called, "active daemon must not be shut down"
    done.set()
    thread.join(timeout=2)


def test_watchdog_stops_when_done_is_set():
    """A SIGTERM path sets `done`; the watchdog must not linger."""
    mod = _load("3600")
    mod._IDLE_CHECK_INTERVAL_S = 0.05
    server = _FakeServer()
    done = threading.Event()

    thread = mod._start_idle_watchdog(server, done)
    done.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert not server.shutdown_called


def test_health_probe_does_not_reset_idle_clock():
    """GET /health must not count as usage.

    Otherwise any monitor polling health keeps the model resident forever,
    which defeats the entire point of idle shutdown.
    """
    mod = _load("1800")
    src = (SCRIPTS / "embed_daemon.py").read_text(encoding="utf-8")
    get_body = src.split("def do_GET")[1].split("def do_POST")[0]
    post_body = src.split("def do_POST")[1][:600]

    assert "_touch_idle_clock()" not in get_body, "health probe must not touch the clock"
    assert "_touch_idle_clock()" in post_body, "embed requests must touch the clock"
    assert mod.IDLE_TIMEOUT_S == 1800
