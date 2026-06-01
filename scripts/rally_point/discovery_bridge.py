# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Shared discovery resolver for Rally Point channel selection.

β1 protocol-of-record: every build-loop caller that needs to write or
read a Rally Point channel goes through ``resolve(workdir)``. The bridge
returns a full discovery envelope so callers can branch on policy,
channel layout, and protocol version without re-implementing the
native discovery → embedded fallback chain themselves.

Resolution order (highest → lowest priority):

1. ``$AGENT_RALLY_DISCOVER`` env override (operator-controlled).
2. ``$AGENT_RALLY_BINARY`` / repo-associated sibling
   ``agent-rally-point/target/*/rally`` checkout / ``rally`` Rust CLI
   (agent-rally-point >= 0.4).
3. Repo-local native ``rally enter/say/whoami`` CLI backed by
   ``<repo>/.rally``.
4. ``agent-rally-discover`` console script on ``$PATH`` (pipx /
   system install of agent-rally-point >= 0.3.0).
5. ``agent_rally_point.discover`` Python import (sibling-repo install
   or local ``.venv``).
6. Embedded fallback to ``channel_paths.app_slug`` /
   ``channel_paths.app_channel_dir`` (canonical
   ``~/.agent-rally-point/apps/`` root, compatibility env overrides honored).

The internal fallback is a degraded-coordination path: it surfaces
``resolved_via: "build-loop-internal"`` and ``policy: "legacy-only"``
so callers can distinguish embedded fallback from native package discovery.
The fallback root is canonical, but the protocol source is still not silently
treated as native agent-rally-point (the v0.12.16 defect class — see
``protocol-of-record-audit`` memory note).

Protocol-version compatibility: the bridge pins
``protocol_version >= 1.0, < 3.0``. When the discover envelope reports
a version outside that range, ``resolve()`` returns
``coordination_unavailable: "incompatible_protocol"`` and does NOT
fall back to internal. Loud failure beats silent skew.

Caching: results are cached per (workdir, source) tuple for
``CACHE_TTL_SECONDS`` (60s default). The cache key includes the source
so an env override never serves a stale binary-derived value.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # package import
    from . import channel_paths
except ImportError:  # script import
    import channel_paths  # type: ignore


CACHE_TTL_SECONDS = 60
"""Per-workdir cache lifetime. β-design value; not yet operator-tunable."""

MIN_PROTOCOL_VERSION = (1, 0)
MAX_PROTOCOL_VERSION_EXCLUSIVE = (3, 0)
"""Pinned protocol-version range. Bridge refuses to operate outside this band."""

REQUIRED_RALLY_HELP_FRAGMENTS = (
    "rally stop <tool>",
    "rally post --kind",
)
"""Rust CLI surface Build Loop relies on for current cross-host coordination."""

REQUIRED_REPO_LOCAL_RALLY_HELP_FRAGMENTS = (
    "rally enter --tool",
    "rally say <kind>",
    "rally whoami",
)
"""Older native Rally surface backed by a repo-local ``.rally`` ledger."""


@dataclass
class DiscoveryEnvelope:
    """Canonical envelope returned by ``resolve()``.

    Fields preserved verbatim from agent-rally-point's ``discover()``
    where present; bridge-added fields documented inline.
    """
    channel_dir: str
    app_slug: str
    repo_id: str | None
    channel_layout: str
    policy: str
    protocol_version: str
    last_resolved_at: str
    resolved_via: str
    """One of ``env-override``, ``rust-cli``, ``repo-local-rally-cli``,
    ``path-binary``, ``python-import``, ``build-loop-internal``."""
    legacy_channel_dir: str | None = None
    """Populated during ``policy: "migration"`` so callers can mirror
    or compare reads against the legacy root."""
    merged_view: bool = False
    """True during migration when discover() returns both canonical
    and legacy paths plus a merged read view."""
    coordination_unavailable: str | None = None
    """When set, callers MUST NOT write. Values:
    ``incompatible_protocol`` (loud) — protocol version outside pinned
    range; ``degraded`` (informational) — internal-fallback selected
    when canonical is the policy."""
    raw: dict[str, Any] = field(default_factory=dict)
    """Verbatim discover() output for callers that need fields the
    bridge does not normalize. Empty when ``resolved_via ==
    "build-loop-internal"``."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_dir": self.channel_dir,
            "app_slug": self.app_slug,
            "repo_id": self.repo_id,
            "channel_layout": self.channel_layout,
            "policy": self.policy,
            "protocol_version": self.protocol_version,
            "last_resolved_at": self.last_resolved_at,
            "resolved_via": self.resolved_via,
            "legacy_channel_dir": self.legacy_channel_dir,
            "merged_view": self.merged_view,
            "coordination_unavailable": self.coordination_unavailable,
            "raw": self.raw,
        }


# Process-local cache. Keyed by ``(resolved-workdir, source-priority-tag)``.
_CACHE: dict[tuple[str, str], tuple[float, DiscoveryEnvelope]] = {}


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_semver_pair(value: str) -> tuple[int, int] | None:
    """Return ``(major, minor)`` from ``"X.Y[.Z][...]"`` or None on failure."""
    if not value:
        return None
    parts = value.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    return major, minor


def _protocol_in_range(protocol_version: str) -> bool:
    pair = _parse_semver_pair(protocol_version)
    if pair is None:
        # Unparseable version → treat as out-of-range (loud).
        return False
    return MIN_PROTOCOL_VERSION <= pair < MAX_PROTOCOL_VERSION_EXCLUSIVE


def _shape_envelope_from_discover(
    raw: dict[str, Any], resolved_via: str
) -> DiscoveryEnvelope | None:
    """Normalize a discover() raw envelope into ``DiscoveryEnvelope``.

    Returns ``None`` when the envelope is structurally invalid
    (missing channel_dir or app_slug — both are required).
    """
    channel_dir = raw.get("channel_dir")
    app_slug = raw.get("app_slug")
    if not channel_dir or not app_slug:
        return None
    protocol_version = str(raw.get("protocol_version") or "1.0")
    coordination_unavailable: str | None = None
    if not _protocol_in_range(protocol_version):
        # Loud refusal — do NOT fall back to internal.
        coordination_unavailable = "incompatible_protocol"
    elif raw.get("coordination_unavailable"):
        # discover() can set this itself when canonical is unreachable.
        coordination_unavailable = "degraded"
    return DiscoveryEnvelope(
        channel_dir=str(channel_dir),
        app_slug=str(app_slug),
        repo_id=raw.get("repo_id"),
        channel_layout=str(raw.get("channel_layout") or "unknown"),
        policy=str(raw.get("policy") or "unknown"),
        protocol_version=protocol_version,
        last_resolved_at=str(raw.get("last_resolved_at") or _utc_iso()),
        resolved_via=resolved_via,
        legacy_channel_dir=(
            str(raw["legacy_channel_dir"])
            if raw.get("legacy_channel_dir") else None
        ),
        merged_view=bool(raw.get("merged_view", False)),
        coordination_unavailable=coordination_unavailable,
        raw=dict(raw),
    )


def _try_env_override(workdir: Path) -> DiscoveryEnvelope | None:
    override = os.environ.get("AGENT_RALLY_DISCOVER")
    if not override:
        return None
    # Operator points env var at a script or binary. Must be invokable
    # and emit the same JSON envelope discover() does.
    return _invoke_discover_binary(override, workdir, resolved_via="env-override")


def _try_path_binary(workdir: Path) -> DiscoveryEnvelope | None:
    binary = shutil.which("agent-rally-discover")
    if not binary:
        return None
    return _invoke_discover_binary(binary, workdir, resolved_via="path-binary")


def rust_rally_binary(workdir: Path | str | None = None) -> str | None:
    """Return the Rust ``rally`` binary path when available.

    Production installs should put ``rally`` on ``PATH`` or set
    ``AGENT_RALLY_BINARY``. The workdir sibling-checkout probe keeps
    Build Loop aligned with the Rally binary that belongs to the repo
    being coordinated, even when Build Loop is running from an installed
    plugin cache.

    Stale ``rally`` binaries are skipped. During the Rust cutover it is
    common for a shell PATH to point at an older installed binary while a
    sibling checkout has the current CLI. Build Loop depends on the current
    start/stop/post surface for Claude Code, Codex, tmux/Ghostty panes,
    Herdr, cmux, and other host adapters, so discovery must prefer a binary
    that actually exposes those commands.
    """
    workdir_path = Path(workdir).expanduser().resolve() if workdir else None
    for candidate in _rally_binary_candidates(workdir_path):
        if not _rally_binary_supports_required_surface(candidate):
            continue
        if workdir_path is not None and _rally_setup_payload(candidate, workdir_path) is None:
            continue
        return candidate

    return None


def repo_local_rally_binary(workdir: Path | str | None = None) -> str | None:
    """Return a native repo-local ``rally`` binary when available.

    This is the older but still live ``enter/say/whoami`` surface used by
    repos with a source-of-truth ``.rally`` directory. It is intentionally
    separate from ``rust_rally_binary()``, whose callers require the newer
    ``setup/start/post`` surface.
    """
    workdir_path = Path(workdir).expanduser().resolve() if workdir else None
    for candidate in _rally_binary_candidates(workdir_path):
        if _rally_binary_supports_repo_local_surface(candidate):
            return candidate
    return None


def _rally_binary_candidates(workdir: Path | None) -> list[str]:
    """Return candidate ``rally`` paths in repo-associated priority order."""
    candidates: list[str] = []
    seen: set[str] = set()

    def add(path: Path | str | None) -> None:
        if not path:
            return
        expanded = str(Path(path).expanduser())
        if expanded not in seen:
            seen.add(expanded)
            candidates.append(expanded)

    add(os.environ.get("AGENT_RALLY_BINARY"))

    if (
        not os.environ.get("BUILD_LOOP_DISABLE_SIBLING_RALLY")
        and not os.environ.get("BUILD_LOOP_APPS_ROOT")
    ):
        for root in _repo_associated_roots(workdir):
            for base in (root, root.parent / "agent-rally-point"):
                add(base / "target" / "release" / "rally")
                add(base / "target" / "debug" / "rally")

        repo_root = Path(__file__).resolve().parents[2]
        sibling = repo_root.parent / "agent-rally-point"
        add(sibling / "target" / "release" / "rally")
        add(sibling / "target" / "debug" / "rally")

    add(shutil.which("rally"))
    return candidates


def _repo_associated_roots(workdir: Path | None) -> list[Path]:
    if workdir is None:
        return []
    roots: list[Path] = []
    for candidate in (workdir, *workdir.parents):
        if (candidate / ".git").exists() or (candidate / "target" / "release").exists():
            roots.append(candidate)
            break
    if not roots:
        roots.append(workdir)
    return roots


def _rally_binary_supports_required_surface(binary: str) -> bool:
    """Return True when ``binary`` exposes the current host-adapter commands."""
    path = Path(binary).expanduser()
    try:
        if not path.is_file() or not os.access(path, os.X_OK):
            return False
        proc = subprocess.run(
            [str(path)],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False
    help_text = f"{proc.stdout}\n{proc.stderr}"
    return all(fragment in help_text for fragment in REQUIRED_RALLY_HELP_FRAGMENTS)


def _rally_binary_supports_repo_local_surface(binary: str) -> bool:
    """Return True when ``binary`` exposes the repo-local enter/say API."""
    path = Path(binary).expanduser()
    try:
        if not path.is_file() or not os.access(path, os.X_OK):
            return False
        proc = subprocess.run(
            [str(path)],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False
    help_text = f"{proc.stdout}\n{proc.stderr}"
    return all(
        fragment in help_text
        for fragment in REQUIRED_REPO_LOCAL_RALLY_HELP_FRAGMENTS
    )


def _rally_setup_payload(binary: str, workdir: Path) -> dict[str, Any] | None:
    try:
        proc = subprocess.run(
            [binary, "setup", "--json"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        raw = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict) or raw.get("ok") is not True or not raw.get("channel"):
        return None
    return raw


def _try_rust_cli(workdir: Path) -> DiscoveryEnvelope | None:
    binary = rust_rally_binary(workdir)
    if not binary:
        return None
    raw = _rally_setup_payload(binary, workdir)
    if raw is None:
        return None
    channel_dir = raw.get("channel")
    if not channel_dir:
        return None
    channel_name = Path(str(channel_dir)).name
    shaped = {
        "installed": True,
        "channel_dir": str(channel_dir),
        "app_slug": channel_name,
        "repo_id": channel_name if channel_name.startswith("repo_") else None,
        "channel_layout": "hash-chain",
        "policy": "rust-cli",
        "protocol_version": "2.0",
        "last_resolved_at": _utc_iso(),
        "rally_binary": binary,
        "setup": raw,
    }
    return _shape_envelope_from_discover(shaped, resolved_via="rust-cli")


def _try_repo_local_rally_cli(workdir: Path) -> DiscoveryEnvelope | None:
    binary = repo_local_rally_binary(workdir)
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "whoami", "--json"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        raw = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict) or raw.get("ok") is not True:
        return None
    whoami = ((raw.get("data") or {}).get("whoami") or {})
    repo_root = whoami.get("repo_root") or str(workdir)
    channel_dir = Path(str(repo_root)).expanduser().resolve() / ".rally"
    repo_id = whoami.get("repo_id") or channel_dir.parent.name
    shaped = {
        "installed": True,
        "channel_dir": str(channel_dir),
        "app_slug": str(repo_id),
        "repo_id": str(repo_id),
        "channel_layout": "repo-local-rally",
        "policy": "repo-local",
        "protocol_version": "1.0",
        "last_resolved_at": _utc_iso(),
        "rally_binary": binary,
        "whoami": raw,
    }
    return _shape_envelope_from_discover(
        shaped,
        resolved_via="repo-local-rally-cli",
    )


def _invoke_discover_binary(
    binary: str, workdir: Path, *, resolved_via: str
) -> DiscoveryEnvelope | None:
    try:
        proc = subprocess.run(
            [binary, "--json"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        raw = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict) or not raw.get("installed", True):
        # The binary signals not-installed via installed=False or by
        # omitting the key. Treat absent installed as still-valid
        # because canonical discover() always sets it true when present.
        return None
    return _shape_envelope_from_discover(raw, resolved_via=resolved_via)


def _try_python_import(workdir: Path) -> DiscoveryEnvelope | None:
    try:
        from agent_rally_point.discover import discover  # noqa: PLC0415
    except ImportError:
        return None
    try:
        raw = discover(workdir)
    except Exception:  # noqa: BLE001 — discovery must never crash callers
        return None
    if not isinstance(raw, dict) or not raw.get("installed", True):
        return None
    return _shape_envelope_from_discover(raw, resolved_via="python-import")


def _internal_fallback(workdir: Path) -> DiscoveryEnvelope:
    """Last-resort resolver using the embedded ``channel_paths`` API.

    Returns ``policy: "legacy-only"`` and ``resolved_via:
    "build-loop-internal"`` so callers can refuse to write when their
    contract requires native package discovery. The embedded fallback is
    NEVER silently treated as native agent-rally-point discovery.
    """
    slug = channel_paths.app_slug(workdir)
    channel_dir = channel_paths.app_channel_dir(slug)
    return DiscoveryEnvelope(
        channel_dir=str(channel_dir),
        app_slug=slug,
        repo_id=None,
        channel_layout="legacy",
        policy="legacy-only",
        protocol_version="1.0",
        last_resolved_at=_utc_iso(),
        resolved_via="build-loop-internal",
        legacy_channel_dir=str(channel_dir),
        merged_view=False,
        coordination_unavailable=None,
        raw={},
    )


def _cache_get(workdir_key: str, source_tag: str) -> DiscoveryEnvelope | None:
    entry = _CACHE.get((workdir_key, source_tag))
    if entry is None:
        return None
    cached_at, envelope = entry
    if (time.time() - cached_at) > CACHE_TTL_SECONDS:
        _CACHE.pop((workdir_key, source_tag), None)
        return None
    return envelope


def _cache_put(
    workdir_key: str, source_tag: str, envelope: DiscoveryEnvelope
) -> None:
    _CACHE[(workdir_key, source_tag)] = (time.time(), envelope)


def resolve(workdir: Path | str) -> DiscoveryEnvelope:
    """Resolve the active Rally Point channel for ``workdir``.

    Always returns a ``DiscoveryEnvelope``. Callers inspect
    ``coordination_unavailable`` and ``resolved_via`` to decide whether
    to write, mirror, or surface a degraded-mode warning.

    The order is: env override → Rust CLI → Python discover binary →
    Python import → internal fallback. The first non-``None`` source wins.
    Each successful resolution is cached for ``CACHE_TTL_SECONDS``.

    Channel-split fix (worktree canonicalization): ``workdir`` is collapsed
    to the canonical repo root via ``channel_paths.canonical_workdir``
    BEFORE any resolver runs. Two ``git worktree`` checkouts of one repo
    pass different paths here; the native ``rally`` / ``agent-rally-discover``
    binaries key the channel on that path verbatim, so without
    canonicalization the worktrees split into separate (empty) channels.
    Canonicalizing once at the entry makes every resolver — native binary
    or embedded fallback — receive the identical main-checkout root. A
    non-git ``workdir`` is returned unchanged, preserving the ``_unscoped``
    behavior downstream.
    """
    workdir_path = channel_paths.canonical_workdir(workdir)
    workdir_key = str(workdir_path)

    # Test-isolation hook: ``BUILD_LOOP_BRIDGE_INTERNAL_ONLY=1`` short-
    # circuits all canonical sources and uses the internal fallback only.
    # Test fixtures set this alongside ``BUILD_LOOP_APPS_ROOT`` so the
    # legacy channel under their tmp dir is the single source of truth.
    # Production never sets this; it is a smoke-test-rigging-aware
    # alternative to leaving canonical sources reachable mid-test.
    if os.environ.get("BUILD_LOOP_BRIDGE_INTERNAL_ONLY"):
        cached = _cache_get(workdir_key, "build-loop-internal")
        if cached is not None:
            return cached
        envelope = _internal_fallback(workdir_path)
        _cache_put(workdir_key, "build-loop-internal", envelope)
        return envelope

    # Probe each source in priority order; cache hits short-circuit.
    for source_tag, probe in (
        ("env-override", _try_env_override),
        ("rust-cli", _try_rust_cli),
        ("repo-local-rally-cli", _try_repo_local_rally_cli),
        ("path-binary", _try_path_binary),
        ("python-import", _try_python_import),
    ):
        cached = _cache_get(workdir_key, source_tag)
        if cached is not None:
            return cached
        envelope = probe(workdir_path)
        if envelope is not None:
            _cache_put(workdir_key, source_tag, envelope)
            return envelope

    # Internal fallback — always succeeds.
    cached = _cache_get(workdir_key, "build-loop-internal")
    if cached is not None:
        return cached
    envelope = _internal_fallback(workdir_path)
    _cache_put(workdir_key, "build-loop-internal", envelope)
    return envelope


def clear_cache() -> None:
    """Drop all cached envelopes. Primarily for tests."""
    _CACHE.clear()


# --------------------------------------------------------------------------
# CLI for ad-hoc debugging (not part of the supported entry surface).
# --------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    import argparse  # local import — CLI is a debug surface

    p = argparse.ArgumentParser(description="Resolve Rally Point channel for cwd.")
    p.add_argument("--workdir", default=".")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON envelope (default).")
    args = p.parse_args(argv)
    envelope = resolve(Path(args.workdir))
    json.dump(envelope.to_dict(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
