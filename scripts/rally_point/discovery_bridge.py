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
2. Native ``rally enter/say/whoami`` CLI (rally's real surface) backed by
   ``<repo>/.rally`` — resolved from ``$AGENT_RALLY_BINARY``, the fetched
   pinned binary cache, ``rally`` on ``$PATH``, or (lowest priority) a
   repo-associated sibling ``agent-rally-point/target/*/rally`` checkout. The
   pinned cache is the source of truth for version currency, so a sibling dev
   build is only used when nothing higher-priority resolves — it must never
   shadow the pin with a stale version.
3. ``agent-rally-discover`` console script on ``$PATH`` (pipx /
   system install of agent-rally-point >= 0.3.0).
4. ``agent_rally_point.discover`` Python import (sibling-repo install
   or local ``.venv``).
5. Fetch-on-install: provision the pinned ``rally`` release, then resolve.
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

try:  # binary-probe timeouts must fit under the rally hook wall-clock budget
    from rally_point import hook_budget
except ImportError:
    try:  # package-relative import
        from . import hook_budget
    except ImportError:  # script import (post-commit capture: dir on sys.path)
        import hook_budget  # type: ignore

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
    "rally enter --tool",
    "rally say <kind>",
    "rally whoami",
)
"""Rally's REAL CLI surface — the commands Build Loop actually shells out to.

These three fragments appear verbatim in ``rally`` top-level usage (verified
against the local source build and the fetched pinned release). The surface-
pinning regression test asserts a real ``rally`` binary's ``--help`` contains
every fragment, so this tuple can never silently drift back to a phantom
surface rally does not expose (the v0.12.x ``setup``/``post``/``start``/
``replay`` defect class). ``REQUIRED_REPO_LOCAL_RALLY_HELP_FRAGMENTS`` is an
alias kept for callers that imported the older name."""

REQUIRED_REPO_LOCAL_RALLY_HELP_FRAGMENTS = REQUIRED_RALLY_HELP_FRAGMENTS
"""Back-compat alias. Rally standardized on a single native surface; there is
no separate "newer" cross-host surface to gate on."""


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
    """One of ``env-override``, ``repo-local-rally-cli``, ``path-binary``,
    ``python-import``, ``fetched-binary``, ``build-loop-internal``.

    (``rust-cli`` was removed: it gated on a ``setup``/``post``/``start``
    surface rally never shipped, so it could never resolve a real binary.)"""
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

    @property
    def capability_level(self) -> str:
        """Coordination capability this resolution affords (see ``capability.py``).

        ``full`` for a healthy native binary, ``degraded-breadcrumb`` for the
        embedded fallback, ``unavailable`` for an incompatible protocol. The
        single mapping lives in ``capability.level_for_resolved_via``.
        """
        try:
            from . import capability as _cap
        except ImportError:  # script-mode
            import capability as _cap  # type: ignore
        return _cap.level_for_resolved_via(
            self.resolved_via, self.coordination_unavailable
        )

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
            "capability_level": self.capability_level,
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
    """Return a native ``rally`` binary path when one exposes rally's real surface.

    Production installs should put ``rally`` on ``PATH`` or set
    ``AGENT_RALLY_BINARY``. The workdir sibling-checkout probe keeps
    Build Loop aligned with the Rally binary that belongs to the repo
    being coordinated, even when Build Loop is running from an installed
    plugin cache.

    A candidate is accepted iff its top-level usage exposes every fragment in
    ``REQUIRED_RALLY_HELP_FRAGMENTS`` (``enter``/``say``/``whoami``) — rally's
    actual surface. Build Loop shells out to ``rally sessions --reap`` (reaper),
    ``rally migrate-legacy`` (zero-seam migration), ``rally stop <session|name|
    tool>``, ``rally enter``, and ``rally say`` against this binary; all of those
    live on the same real surface, so the single help-fragment gate is sufficient.

    This used to be a separate "newer cross-host" tier gated on a ``rally setup``
    identity probe and ``stop <tool>``/``post --kind`` help fragments — a surface
    rally never shipped, so the tier could never resolve a real binary and every
    downstream caller silently fell through. It is now collapsed onto rally's real
    surface; ``repo_local_rally_binary`` is a back-compat alias of this function.
    """
    workdir_path = Path(workdir).expanduser().resolve() if workdir else None
    for candidate in _rally_binary_candidates(workdir_path):
        if _rally_binary_supports_required_surface(candidate):
            return candidate
    return None


# Back-compat alias. Both names resolve rally's single real surface; the historic
# split (repo-local enter/say vs a phantom setup/start/post tier) is gone.
repo_local_rally_binary = rust_rally_binary


def _rally_binary_candidates(workdir: Path | None) -> list[str]:
    """Return candidate ``rally`` paths in priority order.

    Priority (highest → lowest): env override → fetch-on-install pinned cache
    → ``rally`` on ``$PATH`` → repo-associated sibling ``target/{release,debug}``
    dev builds (checked last, across all resolved roots).

    The pinned cache is the source of truth for version currency (see
    ``binary_fetch.PINNED_VERSION``). A sibling ``target/release/rally`` /
    ``target/debug/rally`` checkout is the LEAST trustworthy candidate for
    version currency — it is whatever a prior local ``cargo build`` happened to
    produce, with no guarantee it matches the pin — so it must never shadow a
    live system binary or the pin. It is still probed (never removed): a
    contributor actively developing ``agent-rally-point`` locally needs their
    freshly-built binary reachable when nothing higher-priority resolves.
    """
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

    # Fetch-on-install: a previously-fetched pinned binary in the build-loop
    # runtime cache. Checked before PATH/sibling so the pin — the source of
    # truth for version currency — is preferred over a live-but-possibly-stale
    # system or dev-checkout binary. We do NOT trigger a fetch here (that is
    # the discovery-tier's job); we only ADD an already-cached path.
    if not os.environ.get("BUILD_LOOP_DISABLE_BINARY_FETCH"):
        try:
            from . import binary_fetch as _fetch
        except ImportError:
            try:
                import binary_fetch as _fetch  # type: ignore
            except ImportError:
                _fetch = None  # type: ignore
        if _fetch is not None:
            cached = _fetch.cached_binary_path()
            if cached.is_file():
                add(cached)

    add(shutil.which("rally"))

    # Sibling dev-checkout builds — LAST priority (see docstring above).
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
    """Return True when ``binary`` exposes rally's real CLI surface.

    Checks for every fragment in ``REQUIRED_RALLY_HELP_FRAGMENTS`` in the
    binary's top-level usage — rally's actual ``enter``/``say``/``whoami``
    commands. The surface-pinning regression test asserts a real rally binary
    passes this check, so it can never silently drift back to a phantom surface.
    """
    path = Path(binary).expanduser()
    try:
        if not path.is_file() or not os.access(path, os.X_OK):
            return False
        proc = subprocess.run(
            [str(path)],
            capture_output=True,
            text=True,
            timeout=hook_budget.inner_timeout_seconds(hook_budget.MARGIN_CHILD),
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False
    help_text = f"{proc.stdout}\n{proc.stderr}"
    return all(fragment in help_text for fragment in REQUIRED_RALLY_HELP_FRAGMENTS)


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
            timeout=hook_budget.inner_timeout_seconds(hook_budget.MARGIN_CHILD),
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
            timeout=hook_budget.inner_timeout_seconds(hook_budget.MARGIN_CHILD),
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


def _try_fetched_binary(workdir: Path) -> DiscoveryEnvelope | None:
    """Fetch-on-install tier: provision the PINNED rally binary, then resolve.

    Fires only after the live-binary probes (env / repo-local / path-binary /
    python-import) miss — i.e. no rally is installed. Fetches the host-platform
    asset from the pinned release (sha256-verified, version-pinned, quarantine-
    stripped, cached), then runs the same ``rally whoami --json`` resolution the
    repo-local tier uses. An unsupported host (no matching asset) returns None so
    the chain falls through to the loud internal fallback.

    The fetched binary is treated as a first-class native source: the envelope
    carries ``resolved_via: "fetched-binary"`` (a full-capability source) and the
    same repo-local ``.rally`` channel layout as ``repo-local-rally-cli``.
    """
    if os.environ.get("BUILD_LOOP_DISABLE_BINARY_FETCH"):
        return None
    try:  # package import with script-mode fallback
        from . import binary_fetch as _fetch
    except ImportError:
        try:
            import binary_fetch as _fetch  # type: ignore
        except ImportError:
            return None
    try:
        binary = _fetch.ensure_binary()
    except Exception:  # noqa: BLE001 — fetch must never crash discovery
        return None
    if binary is None:
        return None
    return _resolve_fetched_binary_channel(str(binary), workdir)


def _resolve_fetched_binary_channel(
    binary: str, workdir: Path
) -> DiscoveryEnvelope | None:
    """Resolve a channel for an already-provisioned fetched binary.

    The pinned binary exposes rally's real ``whoami`` surface (protocol 1.0,
    ``.rally`` ledger), so resolution mirrors ``_try_repo_local_rally_cli`` but
    stamps ``resolved_via: "fetched-binary"`` so the source is attributable to
    the fetch tier. Still a FULL-capability source.
    """
    try:
        proc = subprocess.run(
            [binary, "whoami", "--json"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=hook_budget.inner_timeout_seconds(hook_budget.MARGIN_CHILD),
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
        "policy": "fetched-binary",
        "protocol_version": "1.0",
        "last_resolved_at": _utc_iso(),
        "rally_binary": binary,
        "whoami": raw,
    }
    return _shape_envelope_from_discover(shaped, resolved_via="fetched-binary")


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


def _host_can_fetch_binary() -> bool:
    """True when this host has a fetchable pinned-binary asset.

    An UNSUPPORTED host (no matching release asset — Intel mac / musl / exotic
    arch) cannot ever reach full capability, so its fallback must surface a LOUD
    ``unavailable``, not a degraded breadcrumb. Best-effort: a missing fetch
    module is treated as "could fetch" (don't escalate to loud on an import
    quirk).
    """
    if os.environ.get("BUILD_LOOP_DISABLE_BINARY_FETCH"):
        return True  # fetch deliberately off → not an unsupported-host signal
    try:
        from . import binary_fetch as _fetch
    except ImportError:
        try:
            import binary_fetch as _fetch  # type: ignore
        except ImportError:
            return True
    try:
        return _fetch.host_triple() is not None
    except Exception:  # noqa: BLE001
        return True


def _internal_fallback(workdir: Path) -> DiscoveryEnvelope:
    """Last-resort resolver using the embedded ``channel_paths`` API.

    Returns ``resolved_via: "build-loop-internal"`` so callers can refuse to
    write when their contract requires native package discovery. The embedded
    fallback is NEVER silently treated as native agent-rally-point discovery.

    Capability split (loud-vs-degraded):
      * UNSUPPORTED host (no fetchable asset) → ``coordination_unavailable:
        "unsupported_host"`` → capability ``unavailable`` (LOUD no-coordination;
        never a policy mirror, per the migration contract).
      * Supported host that simply has no binary yet → ``coordination_
        unavailable: None`` → capability ``degraded-breadcrumb`` (may write
        capability-marked breadcrumb facts only).
    """
    slug = channel_paths.app_slug(workdir)
    channel_dir = channel_paths.app_channel_dir(slug)
    unsupported = not _host_can_fetch_binary()
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
        coordination_unavailable="unsupported_host" if unsupported else None,
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

    The order is: env override → native rally CLI (real enter/say/whoami
    surface) → ``agent-rally-discover`` binary → Python import → fetched
    binary → internal fallback. The first non-``None`` source wins.
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
        ("repo-local-rally-cli", _try_repo_local_rally_cli),
        ("path-binary", _try_path_binary),
        ("python-import", _try_python_import),
        ("fetched-binary", _try_fetched_binary),
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


# Once-per-process guard so the seam does not re-shell on every coordination
# write. migrate-legacy is itself idempotent (event_id dedup), so this is an
# efficiency layer, not a correctness one. Keyed by resolved channel dir.
_MIGRATED_THIS_PROCESS: set[str] = set()


def maybe_auto_migrate(
    workdir: Path | str, envelope: "DiscoveryEnvelope | None" = None
) -> dict | None:
    """Auto-run ``rally migrate-legacy`` on the fallback→ARP transition seam.

    Fires when (a) the resolved envelope is FULL capability (any real binary owns
    the active channel — ``repo-local-rally-cli``, ``fetched-binary``,
    ``env-override``, ``path-binary``, ``python-import``) AND (b) a stranded global
    fallback store exists at the embedded apps path
    (``channel_paths.app_channel_dir(slug)/changes.jsonl``) holding ≥1
    ``agent-rally.fact.v1`` line. Shells out to ``rally migrate-legacy --json``
    (binary from ``rust_rally_binary``), which losslessly + idempotently replays the
    stranded store into the ARP repo ledger — a ONE-WAY migration of the retired
    ``build-loop-internal`` fallback logs into ``.rally``.

    Returns the parsed migrate result dict (``slugs_found``, ``facts_read``,
    ``facts_migrated``, ``facts_skipped_existing``, ``warnings``) on success, or
    ``None`` when not applicable / on any error. Fire-and-forget — never raises into
    the caller, never imports agent-rally-point.

    A per-process marker (``<fallback_channel>/.migrated``) + an in-memory set skip
    re-invocation; migrate-legacy's own event_id dedup is the correctness backstop.
    """
    try:
        env = envelope if envelope is not None else resolve(workdir)
        if env.capability_level != "full":
            return None

        # Locate the stranded global fallback store for this repo's slug.
        slug = channel_paths.app_slug(workdir)
        fallback_dir = channel_paths.app_channel_dir(slug)
        store = fallback_dir / "changes.jsonl"
        if not store.exists():
            return None

        marker = fallback_dir / ".migrated"
        store_key = str(store.resolve())
        if store_key in _MIGRATED_THIS_PROCESS or marker.exists():
            return None

        # Require ≥1 fact.v1 line — otherwise migrate-legacy would migrate zero
        # (it silently skips non-fact.v1 lines).
        if not _has_fact_v1_line(store):
            return None

        binary = rust_rally_binary(workdir)
        if not binary:
            return None

        try:
            proc = subprocess.run(
                [binary, "migrate-legacy", "--json"],
                cwd=str(Path(workdir)),
                capture_output=True,
                text=True,
                timeout=hook_budget.inner_timeout_seconds(hook_budget.MARGIN_CHILD),
            )
        except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            return None

        # Process guard suppresses per-session retries (efficiency, not correctness)
        # and is set even on a non-zero/timeout result so a transient failure does
        # not retry-loop within this session. The .migrated file is the CROSS-session
        # durability marker; it is written below only after the subprocess returns,
        # so a failed run leaves it absent and a future process re-attempts.
        _MIGRATED_THIS_PROCESS.add(store_key)
        try:
            marker.write_text(_utc_iso(), encoding="utf-8")
        except OSError:
            pass

        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        try:
            out = json.loads(proc.stdout)
        except (ValueError, TypeError):
            return None
        if isinstance(out, dict) and isinstance(out.get("data"), dict):
            data = out["data"]
            # Live rally emits the result under the hyphenated command name.
            return (
                data.get("migrate-legacy")
                or data.get("migrate_legacy")
                or data
            )
        return out if isinstance(out, dict) else None
    except Exception:  # noqa: BLE001 — fire-and-forget seam, never block a host action
        return None


def _has_fact_v1_line(store: Path) -> bool:
    """Return True if ``store`` holds ≥1 ``agent-rally.fact.v1`` line."""
    try:
        with open(store, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(obj, dict) and obj.get("schema") == "agent-rally.fact.v1":
                    return True
    except OSError:
        return False
    return False


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
