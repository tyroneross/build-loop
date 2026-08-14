# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Build Loop watcher metadata routing.

Watcher metadata belongs to Build Loop, even when standalone Rally owns the
coordination ledger.  These tests verify that ``session_probe._launch_watcher``
uses the backend context's identity-keyed private channel and never creates a
``watchers`` sidecar inside a healthy ``.rally`` room:

  AC-C1: Native Rally selected -> watcher metadata stays private.
  AC-C2: Build Loop fallback selected -> watcher metadata uses that fallback.
  AC-C3: Resolver failure -> direct fallback remains repo-identity keyed.

The post-commit hook assertion below remains as a source-wiring guard.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from rally_point import discovery_bridge as _bridge  # noqa: E402
from rally_point import session_probe  # noqa: E402
from rally_point.backend_adapter import BackendContext  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _canonical_envelope(channel_dir: Path) -> _bridge.DiscoveryEnvelope:
    return _bridge.DiscoveryEnvelope(
        channel_dir=str(channel_dir),
        app_slug="test-app",
        repo_id="repo-id-x",
        channel_layout="canonical",
        policy="canonical",
        protocol_version="1.0",
        last_resolved_at="2026-05-25T00:00:00Z",
        resolved_via="path-binary",
        legacy_channel_dir=None,
        merged_view=False,
        coordination_unavailable=None,
        raw={},
    )


def _migration_envelope(
    canonical: Path, legacy: Path
) -> _bridge.DiscoveryEnvelope:
    return _bridge.DiscoveryEnvelope(
        channel_dir=str(canonical),
        app_slug="test-app",
        repo_id="repo-id-x",
        channel_layout="canonical",
        policy="migration",
        protocol_version="1.0",
        last_resolved_at="2026-05-25T00:00:00Z",
        resolved_via="path-binary",
        legacy_channel_dir=str(legacy),
        merged_view=True,
        coordination_unavailable=None,
        raw={},
    )


def _internal_envelope(legacy: Path) -> _bridge.DiscoveryEnvelope:
    return _bridge.DiscoveryEnvelope(
        channel_dir=str(legacy),
        app_slug="test-app",
        repo_id=None,
        channel_layout="legacy",
        policy="legacy-only",
        protocol_version="1.0",
        last_resolved_at="2026-05-25T00:00:00Z",
        resolved_via="build-loop-internal",
        legacy_channel_dir=str(legacy),
        merged_view=False,
        coordination_unavailable=None,
        raw={},
    )


# ---------------------------------------------------------------------------
# session_probe._launch_watcher keeps Build Loop metadata private
# ---------------------------------------------------------------------------

class _CapturedLauncher:
    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, *, workdir, session_id, tool, watch_script):
        self.calls.append(
            {
                "workdir": workdir,
                "session_id": session_id,
                "tool": tool,
                "watch_script": watch_script,
            }
        )
        return 12345


def test_launch_watcher_keeps_metadata_private_when_native_rally_is_canonical(
    tmp_path: Path, monkeypatch
):
    canonical = tmp_path / "canonical"
    private = tmp_path / "private-fallback"
    workdir = tmp_path / "repo"
    monkeypatch.setattr(
        session_probe,
        "resolve_context",
        lambda wd: BackendContext(
            workdir=Path(wd).resolve(),
            envelope=_canonical_envelope(canonical),
            local_channel_dir=private,
        ),
    )
    launcher = _CapturedLauncher()
    pid_file = session_probe._launch_watcher(
        workdir=str(workdir),
        session_id="s1",
        tool="claude_code",
        slug="test-app",
        watcher_launcher=launcher,
        errors=[],
    )
    assert pid_file is not None
    # PID files are Build Loop process metadata, never Rally ledger state.
    assert str(private) in pid_file
    assert str(canonical) not in pid_file


def test_launch_watcher_uses_private_channel_when_backend_is_internal_fallback(
    tmp_path: Path, monkeypatch
):
    private = tmp_path / "private-fallback"
    workdir = tmp_path / "repo"
    monkeypatch.setattr(
        session_probe,
        "resolve_context",
        lambda wd: BackendContext(
            workdir=Path(wd).resolve(),
            envelope=_internal_envelope(private),
            local_channel_dir=private,
        ),
    )
    launcher = _CapturedLauncher()
    pid_file = session_probe._launch_watcher(
        workdir=str(workdir),
        session_id="s2",
        tool="claude_code",
        slug="test-app",
        watcher_launcher=launcher,
        errors=[],
    )
    assert pid_file is not None
    assert str(private) in pid_file


def test_launch_watcher_falls_back_when_bridge_raises(
    tmp_path: Path, monkeypatch
):
    def _boom(wd):
        raise RuntimeError("bridge unavailable")

    monkeypatch.setattr(session_probe, "resolve_context", _boom)
    launcher = _CapturedLauncher()
    apps_root = tmp_path / "fallback-apps"
    monkeypatch.setenv("BUILD_LOOP_APPS_ROOT", str(apps_root))
    workdir = tmp_path / "repo"
    pid_file = session_probe._launch_watcher(
        workdir=str(workdir),
        session_id="s3",
        tool="claude_code",
        slug="test-app",
        watcher_launcher=launcher,
        errors=[],
    )
    assert pid_file is not None
    expected = (
        session_probe.channel_paths.fallback_channel_dir(workdir, "test-app")
        / "watchers"
        / "s3.json"
    )
    assert Path(pid_file) == expected


# ---------------------------------------------------------------------------
# post-commit hook source uses the bridge
# ---------------------------------------------------------------------------

def test_capture_src_imports_discovery_bridge():
    """The generated post-commit hook must import and use the bridge.

    The rendered ``_CAPTURE_SRC`` is the code that runs in every
    consumer repo's ``.git/hooks/.../capture.py``. Pre-cutover this
    called ``channel_paths.ensure_channel_dir(slug)`` directly. After
    the fix it MUST consult the bridge so policy=canonical is honored.
    """
    from rally_point import install_git_hook

    src = install_git_hook._CAPTURE_SRC
    assert "from discovery_bridge import resolve" in src
    assert "_bridge_resolve(repo)" in src
    # And the legacy direct path is no longer used.
    assert "ap.ensure_channel_dir" not in src
    assert "ap.app_slug" not in src
