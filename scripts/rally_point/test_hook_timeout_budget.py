#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Hook wall-clock budget: cross-process discovery cache + internal deadline.

Claude Code cancels a hook at its ``timeout`` and discards its output. The
Rally hooks used to re-run ``rally whoami`` + ``rally status read`` in every
fresh hook process and had no deadline of their own, so they routinely
overran the 2s host timeout. These tests pin the two fixes without invoking
a real ``rally`` binary.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import pytest  # noqa: E402

from rally_point import discovery_bridge, hooks  # noqa: E402


def _healthy_envelope(binary: str, repo: Path) -> discovery_bridge.DiscoveryEnvelope:
    shaped = {
        "installed": True,
        "channel_dir": str(repo / ".rally"),
        "app_slug": "demo",
        "repo_id": "demo",
        "channel_layout": "repo-local-rally",
        "policy": "repo-local",
        "protocol_version": "1.0",
        "rally_binary": binary,
    }
    envelope = discovery_bridge._shape_envelope_from_discover(
        shaped, resolved_via="repo-local-rally-cli"
    )
    assert envelope is not None
    return envelope


@pytest.fixture
def native_stub(tmp_path, monkeypatch):
    binary = tmp_path / "rally"
    binary.write_text("#!/bin/sh\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = {"probe": 0}

    def fake_probe(bin_path, workdir, *, resolved_via, policy):
        calls["probe"] += 1
        return _healthy_envelope(bin_path, repo)

    monkeypatch.setenv("BUILD_LOOP_RALLY_DISCOVERY_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", raising=False)
    monkeypatch.delenv("AGENT_RALLY_DISCOVER", raising=False)
    monkeypatch.setattr(discovery_bridge, "repo_local_rally_binary", lambda _wd: str(binary))
    monkeypatch.setattr(discovery_bridge, "_resolve_native_rally_channel", fake_probe)
    discovery_bridge.clear_cache()
    previous = discovery_bridge.enable_disk_cache(False)
    yield binary, repo, calls, tmp_path / "cache"
    discovery_bridge.enable_disk_cache(previous)
    discovery_bridge.clear_cache()


def test_disk_cache_reuses_healthy_envelope_across_processes(native_stub):
    binary, repo, calls, _cache = native_stub
    discovery_bridge.enable_disk_cache(True)
    first = discovery_bridge.resolve(repo)
    discovery_bridge.clear_cache()  # simulate a fresh hook process
    second = discovery_bridge.resolve(repo)
    assert calls["probe"] == 1
    assert second.backend == "rally"
    assert second.app_slug == first.app_slug
    assert second.raw["rally_binary"] == str(binary)


def test_disk_cache_is_off_unless_enabled(native_stub):
    _binary, repo, calls, cache = native_stub
    discovery_bridge.resolve(repo)
    discovery_bridge.clear_cache()
    discovery_bridge.resolve(repo)
    assert calls["probe"] == 2
    assert not cache.exists()


def test_disk_cache_invalidated_by_binary_change(native_stub):
    binary, repo, calls, _cache = native_stub
    discovery_bridge.enable_disk_cache(True)
    discovery_bridge.resolve(repo)
    discovery_bridge.clear_cache()
    later = time.time() + 5
    os.utime(binary, (later, later))
    discovery_bridge.resolve(repo)
    assert calls["probe"] == 2


def test_disk_cache_keyed_by_session_identity(native_stub, monkeypatch):
    _binary, repo, calls, _cache = native_stub
    discovery_bridge.enable_disk_cache(True)
    monkeypatch.setenv("RALLY_SESSION_ID", "session-a")
    discovery_bridge.resolve(repo)
    discovery_bridge.clear_cache()
    monkeypatch.setenv("RALLY_SESSION_ID", "session-b")
    discovery_bridge.resolve(repo)
    assert calls["probe"] == 2


def test_disk_cache_expires_after_ttl(native_stub, monkeypatch):
    _binary, repo, calls, _cache = native_stub
    discovery_bridge.enable_disk_cache(True)
    discovery_bridge.resolve(repo)
    discovery_bridge.clear_cache()
    real_time = time.time
    monkeypatch.setattr(
        discovery_bridge.time,
        "time",
        lambda: real_time() + discovery_bridge.CACHE_TTL_SECONDS + 1,
    )
    discovery_bridge.resolve(repo)
    assert calls["probe"] == 2


def test_refusal_envelope_is_never_persisted(native_stub, monkeypatch):
    binary, repo, _calls, cache = native_stub

    def refusing_probe(bin_path, workdir, *, resolved_via, policy):
        return discovery_bridge._native_refusal_envelope(
            binary=bin_path,
            workdir=workdir,
            resolved_via=resolved_via,
            policy=policy,
            reason="ambiguous_host",
            detail="test",
        )

    monkeypatch.setattr(discovery_bridge, "_resolve_native_rally_channel", refusing_probe)
    discovery_bridge.enable_disk_cache(True)
    envelope = discovery_bridge.resolve(repo)
    assert envelope.coordination_unavailable == "ambiguous_host"
    assert not cache.exists() or not any(cache.iterdir())


def test_hook_main_stops_at_internal_deadline(monkeypatch, tmp_path):
    monkeypatch.setenv("BUILD_LOOP_RALLY_HOOK_DEADLINE_SECONDS", "0.3")

    def slow_dispatch(_args, _workdir):
        time.sleep(5)
        return 0

    monkeypatch.setattr(hooks, "_dispatch", slow_dispatch)
    started = time.monotonic()
    rc = hooks.main(["pre-edit", "--workdir", str(tmp_path)])
    elapsed = time.monotonic() - started
    assert rc == 0
    assert elapsed < 2.0
    # Deadline and disk-cache state never leak out of the hook entrypoint.
    assert hooks._DEADLINE_AT is None
    assert discovery_bridge._DISK_CACHE_ENABLED is False


def test_deadline_is_not_swallowed_by_fail_open_handlers(monkeypatch, tmp_path):
    monkeypatch.setenv("BUILD_LOOP_RALLY_HOOK_DEADLINE_SECONDS", "0.3")
    reached_after = []

    def dispatch_with_broad_handler(_args, _workdir):
        try:
            time.sleep(5)
        except Exception:  # the helpers' fail-open shape
            pass
        reached_after.append(True)
        return 0

    monkeypatch.setattr(hooks, "_dispatch", dispatch_with_broad_handler)
    assert hooks.main(["pre-edit", "--workdir", str(tmp_path)]) == 0
    assert reached_after == []


def test_session_start_advance_has_no_deadline(monkeypatch, tmp_path):
    monkeypatch.setenv("BUILD_LOOP_RALLY_HOOK_DEADLINE_SECONDS", "0.2")
    seen = {}

    def record(_args, _workdir):
        seen["deadline"] = hooks._DEADLINE_AT
        return 0

    monkeypatch.setattr(hooks, "_dispatch", record)
    assert hooks.main(["session-start-advance", "--workdir", str(tmp_path)]) == 0
    assert seen["deadline"] is None


def test_native_join_skipped_when_budget_is_low(monkeypatch, tmp_path):
    class _Envelope:
        app_slug = "demo"
        backend = "rally"

    class _Context:
        native = True
        envelope = _Envelope()

    writes = []
    monkeypatch.setattr(hooks, "resolve_operative_repo", lambda *a, **k: tmp_path)
    monkeypatch.setattr(hooks, "resolve_context", lambda _wd: _Context())
    monkeypatch.setattr(hooks, "write_backend_presence", lambda *a, **k: writes.append(k))
    monkeypatch.setattr(hooks, "_DEADLINE_AT", time.monotonic() + 0.1)
    assert hooks.pre_edit_join(tmp_path, session_id="s-low-budget") == 0
    assert writes == []


def test_pre_edit_runs_join_before_hint_and_never_reads_native_rally(monkeypatch, tmp_path):
    """2026-10-19: the native hint ran `rally next --audit` (1.4-3.0 s) before
    the join, so the 1.5 s host deadline killed every call before either
    finished. The join must run first, and the hint must not call Rally."""
    order = []

    class NativeContext:
        native = True

    monkeypatch.setattr(hooks, "resolve_context", lambda _wd: NativeContext())
    monkeypatch.setattr(hooks, "pre_edit_join", lambda *_a, **_k: order.append("join") or 0)
    real_hint = hooks.pre_edit_hint

    def tracking_hint(*a, **k):
        order.append("hint")
        return real_hint(*a, **k)

    monkeypatch.setattr(hooks, "pre_edit_hint", tracking_hint)

    native_calls = []

    def no_native_calls(*a, **_k):
        native_calls.append(a)  # recorded, not raised: dispatch swallows errors

    import rally_point.backend_adapter as backend_adapter
    monkeypatch.setattr(backend_adapter, "invoke_native", no_native_calls)
    rc = hooks.main(["pre-edit", "--workdir", str(tmp_path), "--command", "ls"])
    assert rc == 0
    assert order == ["join", "hint"]
    assert native_calls == [], "pre-edit must not invoke the rally CLI"


def test_join_still_runs_when_hint_raises(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(hooks, "resolve_context", lambda _wd: None)
    monkeypatch.setattr(hooks, "pre_edit_join", lambda *_a, **_k: ran.append("join") or 0)

    def broken_hint(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(hooks, "pre_edit_hint", broken_hint)
    assert hooks.main(["pre-edit", "--workdir", str(tmp_path), "--command", "ls"]) == 0
    assert ran == ["join"]


@pytest.fixture
def nonblocking(native_stub, monkeypatch):
    spawned = []
    monkeypatch.setattr(discovery_bridge, "_spawn_refresh", lambda *a: spawned.append(a) or True)
    discovery_bridge.enable_disk_cache(True)
    previous = discovery_bridge.enable_nonblocking(True)
    yield native_stub, spawned
    discovery_bridge.enable_nonblocking(previous)


def test_nonblocking_cold_cache_never_probes_and_starts_refresh(nonblocking):
    (_binary, repo, calls, _cache), spawned = nonblocking
    with pytest.raises(discovery_bridge.DiscoveryPending):
        discovery_bridge.resolve(repo)
    assert calls["probe"] == 0
    assert len(spawned) == 1


def test_nonblocking_serves_stale_entry_and_refreshes(nonblocking, monkeypatch):
    (_binary, repo, calls, _cache), spawned = nonblocking
    discovery_bridge.enable_nonblocking(False)
    discovery_bridge.resolve(repo)  # blocking probe warms the disk cache
    discovery_bridge.enable_nonblocking(True)
    discovery_bridge.clear_cache()
    real_time = time.time
    monkeypatch.setattr(discovery_bridge.time, "time", lambda: real_time() + discovery_bridge.CACHE_TTL_SECONDS + 5)
    envelope = discovery_bridge.resolve(repo)
    assert envelope.backend == "rally"
    assert calls["probe"] == 1  # only the warm-up
    assert len(spawned) == 1


def test_nonblocking_fresh_entry_needs_no_refresh(nonblocking):
    (_binary, repo, calls, _cache), spawned = nonblocking
    discovery_bridge.enable_nonblocking(False)
    discovery_bridge.resolve(repo)
    discovery_bridge.enable_nonblocking(True)
    discovery_bridge.clear_cache()
    discovery_bridge.resolve(repo)
    assert calls["probe"] == 1
    assert spawned == []


def test_spawn_refresh_is_single_flight(native_stub, monkeypatch):
    binary, repo, _calls, _cache = native_stub
    started = []
    monkeypatch.setattr(discovery_bridge.subprocess, "Popen", lambda *a, **k: started.append(a))
    discovery_bridge.enable_disk_cache(True)
    assert discovery_bridge._spawn_refresh(repo, str(repo), str(binary)) is True
    assert discovery_bridge._spawn_refresh(repo, str(repo), str(binary)) is False
    assert len(started) == 1
    assert "--refresh" in started[0][0]


class _FakeEnvelope:
    app_slug = "demo"
    backend = "rally"


class _FakeNativeContext:
    native = True
    envelope = _FakeEnvelope()


def _native_join_env(monkeypatch, tmp_path):
    writes, spawned = [], []
    monkeypatch.setattr(hooks, "resolve_operative_repo", lambda wd, **_k: tmp_path)
    monkeypatch.setattr(hooks, "resolve_context", lambda _wd: _FakeNativeContext())
    monkeypatch.setattr(hooks, "write_backend_presence", lambda *a, **k: writes.append(k) or type("R", (), {"ok": True})())
    monkeypatch.setattr(hooks, "_spawn_background_join", lambda op, **k: spawned.append(k))
    return writes, spawned


def test_hook_hot_path_hands_presence_write_to_background(monkeypatch, tmp_path):
    writes, spawned = _native_join_env(monkeypatch, tmp_path)
    previous = discovery_bridge.enable_nonblocking(True)
    try:
        hooks.pre_edit_join(tmp_path, command="ls", tool="claude_code", session_id="s1", context=_FakeNativeContext())
        # Second call inside the throttle window does nothing.
        hooks.pre_edit_join(tmp_path, command="ls", tool="claude_code", session_id="s1", context=_FakeNativeContext())
    finally:
        discovery_bridge.enable_nonblocking(previous)
    assert writes == []
    assert len(spawned) == 1


def test_background_join_writes_even_inside_throttle_window(monkeypatch, tmp_path):
    writes, spawned = _native_join_env(monkeypatch, tmp_path)
    previous = discovery_bridge.enable_nonblocking(False)
    try:
        rc = hooks.main(["pre-edit-join-bg", "--workdir", str(tmp_path), "--tool", "claude_code", "--session-id", "s1"])
        rc2 = hooks.main(["pre-edit-join-bg", "--workdir", str(tmp_path), "--tool", "claude_code", "--session-id", "s1"])
    finally:
        discovery_bridge.enable_nonblocking(previous)
    assert rc == rc2 == 0
    assert len(writes) == 2
    assert spawned == []
