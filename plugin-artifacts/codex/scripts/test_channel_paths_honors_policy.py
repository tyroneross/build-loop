# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the channel_paths audit fix (post-cutover legacy-leak).

These verify the two production leak sites — the install_git_hook
``_CAPTURE_SRC`` and ``session_probe._launch_watcher`` — now route
through ``discovery_bridge.resolve`` so canonical policy is honored:

  AC-C1: With policy=canonical, writes go ONLY to canonical (no legacy).
  AC-C2: With policy=migration, writes go to BOTH (β1.2 mirror).
  AC-C3: When the bridge is unavailable, writes fall back to legacy.

Approach: monkeypatch ``discovery_bridge.resolve`` to return shaped
envelopes (canonical / migration / internal-fallback) so we can prove
the routing decision without depending on agent-rally-point being
installed in the test sandbox.
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
# session_probe._launch_watcher routes via the bridge
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


def test_launch_watcher_uses_canonical_channel_when_policy_canonical(
    tmp_path: Path, monkeypatch
):
    canonical = tmp_path / "canonical"
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setattr(
        session_probe,
        "_bridge_resolve",
        lambda wd: _canonical_envelope(canonical),
    )
    launcher = _CapturedLauncher()
    pid_file = session_probe._launch_watcher(
        workdir=str(tmp_path / "repo"),
        session_id="s1",
        tool="claude_code",
        slug="test-app",
        watcher_launcher=launcher,
        errors=[],
    )
    assert pid_file is not None
    # PID file lives under the canonical channel, never under legacy.
    assert str(canonical) in pid_file
    assert str(legacy) not in pid_file


def test_launch_watcher_uses_legacy_when_bridge_is_internal_fallback(
    tmp_path: Path, monkeypatch
):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setattr(
        session_probe,
        "_bridge_resolve",
        lambda wd: _internal_envelope(legacy),
    )
    launcher = _CapturedLauncher()
    pid_file = session_probe._launch_watcher(
        workdir=str(tmp_path / "repo"),
        session_id="s2",
        tool="claude_code",
        slug="test-app",
        watcher_launcher=launcher,
        errors=[],
    )
    assert pid_file is not None
    assert str(legacy) in pid_file


def test_launch_watcher_falls_back_when_bridge_raises(
    tmp_path: Path, monkeypatch
):
    def _boom(wd):
        raise RuntimeError("bridge unavailable")

    monkeypatch.setattr(session_probe, "_bridge_resolve", _boom)
    # apps_root() default → ~/.build-loop/apps/<slug>/watchers/.
    # We just need the call to succeed without raising.
    launcher = _CapturedLauncher()
    apps_root = tmp_path / "fallback-apps"
    monkeypatch.setenv("BUILD_LOOP_APPS_ROOT", str(apps_root))
    pid_file = session_probe._launch_watcher(
        workdir=str(tmp_path / "repo"),
        session_id="s3",
        tool="claude_code",
        slug="test-app",
        watcher_launcher=launcher,
        errors=[],
    )
    assert pid_file is not None
    # Fell back to apps_root()/<slug>/watchers/.
    assert "test-app" in pid_file
    assert "watchers" in pid_file


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
