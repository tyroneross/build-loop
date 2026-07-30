# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Producer metadata helper attached to every Rally Point write.

Codex variance (rev ~209) identified that a post.py-only patch would
miss most write paths: ``presence.write_presence``,
``inbox.write_message`` / ``send_to_tool``, leadership writes, and any
direct change-record writer all bypass ``post.py``. This module is the
single source of producer-identity for every write surface.

Returned shape (frozen as part of ``producer_protocol_version = "1.0"``):

    {
      "producer_name": "build-loop",
      "producer_version": "<semver from active plugin.json>",
      "producer_commit_sha": "<12+ char git SHA when source-backed, else None>",
      "producer_runtime_path": "<absolute path to manifest dir>",
      "producer_runtime_surface": "<source-repo | claude-cache | installed-package>",
      "producer_protocol_version": "1.0",
    }

Capture policy:
- Resolved ONCE at module import (process-local cache). The build-loop
  cache or source tree does not change mid-process, so re-running git
  rev-parse per write would be pointless overhead.
- ``producer_commit_sha`` may be ``None`` when neither a ``.git`` nor a
  cached snapshot SHA file is reachable. Policy is **warn, not ok**
  (per ``coordination-version-control.md`` Codex variance) — callers
  may surface a warning but writes still proceed (Rally Point is
  awareness, not enforcement).
- Surface inference is best-effort: ``source-repo`` when a ``.git``
  directory sits next to the manifest, ``claude-cache`` when the path
  contains ``.claude/plugins/cache/``, otherwise ``installed-package``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PRODUCER_NAME = "build-loop"
PRODUCER_PROTOCOL_VERSION = "1.0"

_THIS_FILE = Path(__file__).resolve()
# scripts/rally_point/producer_metadata.py → repo root is parents[2].
_REPO_ROOT = _THIS_FILE.parents[2]


def _read_plugin_manifest(manifest_path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _resolve_runtime_path() -> Path:
    """Return the directory holding the plugin.json manifest in use.

    Order:
      1. ``.claude-plugin/plugin.json`` under the repo root (source-repo).
      2. ``plugin.json`` under the repo root (legacy / fallback).
      3. Repo root itself (the manifest is missing — still return a path
         so the field is non-null).
    """
    claude_plugin = _REPO_ROOT / ".claude-plugin" / "plugin.json"
    if claude_plugin.is_file():
        return claude_plugin.parent
    legacy_plugin = _REPO_ROOT / "plugin.json"
    if legacy_plugin.is_file():
        return legacy_plugin.parent
    return _REPO_ROOT


def _resolve_version(runtime_path: Path) -> str:
    candidates = [
        runtime_path / "plugin.json",
        # When runtime_path is the repo root itself, the manifest is
        # under .claude-plugin/.
        runtime_path / ".claude-plugin" / "plugin.json",
    ]
    for candidate in candidates:
        manifest = _read_plugin_manifest(candidate)
        if manifest and isinstance(manifest.get("version"), str):
            return manifest["version"]
    return "0.0.0"


def _resolve_commit_sha() -> str | None:
    """Capture the current source SHA (12+ chars).

    Order:
      1. ``git rev-parse HEAD`` run from the repo root (source-repo case).
      2. ``BUILD_LOOP_COMMIT_SHA`` env var (CI / packaged-snapshot case).
      3. ``COMMIT_SHA`` file at the repo root (claude-cache snapshot).
      4. ``None`` (callers MUST surface this as warn-not-ok).
    """
    # Source-repo: prefer git, but only when ``.git`` exists at repo root.
    git_dir = _REPO_ROOT / ".git"
    if git_dir.exists():
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(_REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=2,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    # Env override.
    env_sha = os.environ.get("BUILD_LOOP_COMMIT_SHA")
    if env_sha and env_sha.strip():
        return env_sha.strip()
    # Snapshot file under repo root.
    snapshot = _REPO_ROOT / "COMMIT_SHA"
    if snapshot.is_file():
        try:
            value = snapshot.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            pass
    return None


def _resolve_runtime_surface(runtime_path: Path, commit_sha: str | None) -> str:
    """Infer how this build-loop code is being executed.

    ``source-repo``: the runtime directory sits under a writable git
    checkout (``.git`` is present at the repo root).
    ``claude-cache``: the path contains the Claude Code plugin cache
    marker. The cache is a frozen release snapshot.
    ``installed-package``: any other location — pipx, site-packages,
    user-vendored.
    """
    path_str = str(runtime_path)
    if (_REPO_ROOT / ".git").exists() and commit_sha is not None:
        return "source-repo"
    if ".claude/plugins/cache/" in path_str:
        return "claude-cache"
    return "installed-package"


def _capture() -> dict[str, Any]:
    """One-shot capture at import time."""
    runtime_path = _resolve_runtime_path()
    version = _resolve_version(runtime_path)
    commit_sha = _resolve_commit_sha()
    surface = _resolve_runtime_surface(runtime_path, commit_sha)
    return {
        "producer_name": PRODUCER_NAME,
        "producer_version": version,
        "producer_commit_sha": commit_sha,
        "producer_runtime_path": str(runtime_path),
        "producer_runtime_surface": surface,
        "producer_protocol_version": PRODUCER_PROTOCOL_VERSION,
    }


# Cached at import time. Re-import in tests after monkeypatching the
# environment if you need a fresh capture (see test_producer_metadata).
_CACHED: dict[str, Any] = _capture()
_SHA_MISSING_WARNED = False


def producer_metadata() -> dict[str, Any]:
    """Return the cached producer-identity dict.

    Caller merges this into the outgoing record before write. The
    returned dict is a shallow copy so caller mutation cannot corrupt
    the cache.

    Warn-not-ok: when ``producer_commit_sha is None`` the first call
    in a process logs one stderr line so the missing-SHA case never
    silently rides into the channel. Subsequent calls stay silent.
    """
    global _SHA_MISSING_WARNED
    if _CACHED.get("producer_commit_sha") is None and not _SHA_MISSING_WARNED:
        _SHA_MISSING_WARNED = True
        print(
            f"rally-point: producer_commit_sha unavailable for "
            f"{PRODUCER_NAME} runtime at "
            f"{_CACHED.get('producer_runtime_path')} — cache-vs-source "
            f"skew will not be detectable for this session.",
            file=sys.stderr,
        )
    return dict(_CACHED)


def reset_cache_for_tests() -> None:
    """Re-run capture. Tests that monkeypatch env or filesystem call this."""
    global _CACHED, _SHA_MISSING_WARNED
    _CACHED = _capture()
    _SHA_MISSING_WARNED = False
