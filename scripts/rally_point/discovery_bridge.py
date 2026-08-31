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
   ``<repo>/.rally`` — resolved from ``$AGENT_RALLY_BINARY``, standalone
   ``rally`` on ``$PATH``, the fetched pinned compatibility cache, or (lowest priority) a
   repo-associated sibling ``agent-rally-point/target/*/rally`` checkout. The
   standalone binary is the default owner. The compatibility cache and sibling
   build are used only when no higher-priority standalone binary resolves.
3. ``agent-rally-discover`` console script on ``$PATH`` (pipx /
   system install of agent-rally-point >= 0.3.0).
4. ``agent_rally_point.discover`` Python import (sibling-repo install
   or local ``.venv``).
5. Fetch-on-install: provision the pinned ``rally`` release, then resolve.
6. Embedded fallback to ``channel_paths.fallback_channel_dir`` under the
   Build Loop-owned, canonical-repository-keyed
   ``~/.build-loop/apps/<repo>-<identity>/`` root (policy overrides honored).

The internal fallback is a degraded-coordination path: it surfaces
``resolved_via: "build-loop-internal"`` and ``policy: "legacy-only"``
so callers can distinguish embedded fallback from native package discovery.
Only Build Loop readers consume this backend. It preserves host identity
(``codex``, ``claude_code``, ``cursor``) as data, but those hosts coordinate
there only while running Build Loop. The fallback is never treated as native
Rally (the v0.12.16 defect class — see ``protocol-of-record-audit`` memory note).

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

import hashlib
import json
import math
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
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

_CACHE_MAX_ENTRIES = 256
"""Hard process-local bound; expired and least-recently-written entries prune."""

_MAX_MIGRATION_MARKER_BYTES = 16 * 1024

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

    @property
    def backend(self) -> str:
        """Name the coordination authority selected for this run."""
        # Discovery can locate a Rally transport and still refuse to authorize
        # it. Refusal wins over source labeling so callers cannot mistake an
        # incompatible protocol or ambiguous host for a usable Rally backend.
        if self.coordination_unavailable:
            return "unavailable"
        if self.resolved_via in {"repo-local-rally-cli", "fetched-binary"}:
            return "rally"
        if self.resolved_via == "build-loop-internal":
            return "build-loop-local"
        if self.capability_level == "unavailable":
            return "unavailable"
        return "rally-legacy-discovery"

    @property
    def transport(self) -> str:
        """Name the only supported write transport for the selected backend."""
        if self.backend == "rally":
            return "rally-cli"
        if self.backend == "build-loop-local":
            return "fact-v1"
        if self.backend == "unavailable":
            return "none"
        return "legacy-discovery"

    @property
    def refusal_reason(self) -> str | None:
        """Human-readable cause for a discovery refusal."""
        if not self.coordination_unavailable:
            return None
        detail = self.raw.get("detail") if isinstance(self.raw, dict) else None
        if detail:
            return str(detail)
        return f"coordination unavailable: {self.coordination_unavailable}"

    @property
    def refusal_remedy(self) -> str | None:
        """Bounded operator action that can restore coordination authority."""
        if not self.coordination_unavailable:
            return None
        configured = self.raw.get("remedy") if isinstance(self.raw, dict) else None
        if configured:
            return str(configured)
        if self.coordination_unavailable == "incompatible_protocol":
            return (
                "select a Rally build compatible with protocol >=1.0,<3.0, "
                "then retry"
            )
        if self.coordination_unavailable == "ambiguous_host":
            return (
                "run `rally whoami --json`, resolve the ambiguous host runtime, "
                "then retry"
            )
        return "restore the selected coordination backend, then retry"

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
            "backend": self.backend,
            "transport": self.transport,
            "capability_level": self.capability_level,
            "legacy_channel_dir": self.legacy_channel_dir,
            "merged_view": self.merged_view,
            "coordination_unavailable": self.coordination_unavailable,
            "reason": self.refusal_reason,
            "remedy": self.refusal_remedy,
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

    Priority (highest → lowest): env override → standalone ``rally`` on
    ``$PATH`` → fetch-on-install pinned compatibility cache → repo-associated
    sibling ``target/{release,debug}``
    dev builds (checked last, across all resolved roots).

    Standalone Rally owns its own version and ledger. The Build Loop pin is a
    compatibility fallback, not an authority that may shadow a newer installed
    Rally. A sibling development build is least trustworthy and remains last.
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

    add(shutil.which("rally"))

    # Fetch-on-install: a previously-fetched pinned binary in the build-loop
    # runtime cache. It follows standalone Rally on PATH and precedes only the
    # unversioned sibling development builds. We do not trigger a fetch here;
    # this tier only adds an already-cached compatibility binary.
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


@dataclass(frozen=True)
class _NativeProbe:
    status: str
    payload: dict[str, Any] | None = None
    reason: str | None = None


def _run_rally_json(
    binary: str, args: list[str], workdir: Path, *, expected_schema: str
) -> _NativeProbe:
    """Run one read-only Rally command and validate its command envelope."""
    try:
        proc = subprocess.run(
            [binary, *args],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=hook_budget.inner_timeout_seconds(hook_budget.MARGIN_CHILD),
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
        return _NativeProbe("unavailable", reason=str(exc))
    if not proc.stdout.strip():
        return _NativeProbe("unavailable", reason=proc.stderr.strip() or "empty reply")
    try:
        raw = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return _NativeProbe(
            "incompatible" if proc.returncode == 0 else "unavailable",
            reason="Rally returned malformed JSON",
        )
    if not isinstance(raw, dict):
        return _NativeProbe("incompatible", reason="Rally reply is not an object")
    product = raw.get("product")
    schema = raw.get("schema")
    if (product is not None and product != "rally") or (
        schema is not None and schema != expected_schema
    ):
        return _NativeProbe(
            "incompatible",
            payload=raw,
            reason=f"expected rally/{expected_schema}",
        )
    if proc.returncode != 0 or raw.get("ok") is not True:
        return _NativeProbe(
            "unavailable",
            payload=raw,
            reason=proc.stderr.strip() or str(raw.get("error") or "Rally read failed"),
        )
    if product != "rally" or schema != expected_schema:
        return _NativeProbe(
            "incompatible",
            payload=raw,
            reason=f"expected rally/{expected_schema}",
        )
    return _NativeProbe("ok", payload=raw)


def _native_refusal_envelope(
    *,
    binary: str,
    workdir: Path,
    resolved_via: str,
    policy: str,
    reason: str,
    detail: str | None,
    whoami: dict[str, Any] | None = None,
) -> DiscoveryEnvelope:
    identity = ((whoami or {}).get("data") or {}).get("whoami") or {}
    repo_root = identity.get("repo_root") or str(channel_paths.canonical_workdir(workdir))
    repo_id = identity.get("repo_id") or channel_paths.app_slug(workdir)
    return DiscoveryEnvelope(
        channel_dir=str(Path(str(repo_root)).expanduser().resolve() / ".rally"),
        app_slug=str(repo_id),
        repo_id=str(repo_id),
        channel_layout="repo-local-rally",
        policy=policy,
        protocol_version="1.0",
        last_resolved_at=_utc_iso(),
        resolved_via=resolved_via,
        coordination_unavailable=reason,
        raw={
            "rally_binary": binary,
            "refusal_reason": reason,
            "detail": detail,
            "whoami": whoami,
        },
    )


def _resolve_native_rally_channel(
    binary: str,
    workdir: Path,
    *,
    resolved_via: str,
    policy: str,
) -> DiscoveryEnvelope | None:
    """Resolve Rally only when identity and the room store are operational.

    ``whoami`` intentionally remains readable when a room ledger is corrupt, so
    binary presence is not availability. ``status read`` is the bounded,
    read-only store probe that prevents Build Loop from routing writes into a
    Rally room that cannot currently accept or project them.
    """
    version_probe = _run_rally_json(
        binary,
        ["version", "--json"],
        workdir,
        expected_schema="agent-rally.command.version.v1",
    )
    whoami_probe = _run_rally_json(
        binary,
        ["whoami", "--json"],
        workdir,
        expected_schema="agent-rally.command.whoami.v1",
    )
    status_probe = _run_rally_json(
        binary,
        ["status", "--json", "read", "--tool", "build_loop:discovery"],
        workdir,
        expected_schema="agent-rally.command.status_read.v1",
    )
    probes = (version_probe, whoami_probe, status_probe)
    incompatible = next((probe for probe in probes if probe.status == "incompatible"), None)
    if incompatible is not None:
        return _native_refusal_envelope(
            binary=binary,
            workdir=workdir,
            resolved_via=resolved_via,
            policy=policy,
            reason="incompatible_protocol",
            detail=incompatible.reason,
            whoami=whoami_probe.payload,
        )
    if any(probe.status != "ok" for probe in probes):
        return None
    version = version_probe.payload or {}
    whoami_raw = whoami_probe.payload or {}
    status_raw = status_probe.payload or {}
    whoami = ((whoami_raw.get("data") or {}).get("whoami") or {})
    required = ("repo_root", "repo_id", "room_id", "worktree", "build_id")
    if not isinstance(whoami, dict) or any(not whoami.get(k) for k in required):
        return None
    host_runtime = whoami.get("host_runtime") or {}
    if not isinstance(host_runtime, dict) or host_runtime.get("ambiguous") is True:
        return _native_refusal_envelope(
            binary=binary,
            workdir=workdir,
            resolved_via=resolved_via,
            policy=policy,
            reason="ambiguous_host",
            detail="Rally could not identify one host runtime; resolve identity before writing",
            whoami=whoami_raw,
        )
    repo_root = whoami.get("repo_root") or str(workdir)
    channel_dir = Path(str(repo_root)).expanduser().resolve() / ".rally"
    repo_id = whoami.get("repo_id") or channel_dir.parent.name
    shaped = {
        "installed": True,
        "channel_dir": str(channel_dir),
        "app_slug": str(repo_id),
        "repo_id": str(repo_id),
        "channel_layout": "repo-local-rally",
        "policy": policy,
        "protocol_version": "1.0",
        "last_resolved_at": _utc_iso(),
        "rally_binary": binary,
        "rally_version": version,
        "whoami": whoami_raw,
        "status_read": status_raw,
    }
    return _shape_envelope_from_discover(
        shaped,
        resolved_via=resolved_via,
    )


def _try_repo_local_rally_cli(workdir: Path) -> DiscoveryEnvelope | None:
    binary = repo_local_rally_binary(workdir)
    if not binary:
        return None
    return _resolve_native_rally_channel(
        binary,
        workdir,
        resolved_via="repo-local-rally-cli",
        policy="repo-local",
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
    return _resolve_native_rally_channel(
        binary,
        workdir,
        resolved_via="fetched-binary",
        policy="fetched-binary",
    )


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

    A missing or unsupported native Rally binary is exactly the condition that
    authorizes Build Loop's private fallback. Protocol incompatibility remains
    a loud refusal and is handled before this function is selected.
    """
    slug = channel_paths.app_slug(workdir)
    channel_dir = channel_paths.fallback_channel_dir(workdir, slug)
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
        coordination_unavailable=None,
        raw={
            "fallback_reason": (
                "unsupported_host" if unsupported else "rally_unavailable"
            )
        },
    )


def _writable_discovery_result(
    envelope: DiscoveryEnvelope | None,
) -> DiscoveryEnvelope | None:
    """Return a resolver result only when it names a supported writer.

    Legacy discovery remains useful to old readers, but it is not a writable
    authority: Build Loop must never append its private files to a path merely
    returned by ``agent-rally-discover``. An incompatible protocol is retained
    so callers fail loudly instead of silently creating a second ledger.
    """
    if envelope is None:
        return None
    if envelope.coordination_unavailable == "incompatible_protocol":
        return envelope
    if envelope.transport == "rally-cli":
        return envelope
    return None


def _prune_cache(now: float | None = None) -> None:
    """Remove expired entries and cap distinct workdir/source combinations."""
    current = time.time() if now is None else now
    for key, (cached_at, _envelope) in list(_CACHE.items()):
        if (current - cached_at) > CACHE_TTL_SECONDS:
            _CACHE.pop(key, None)
    overflow = len(_CACHE) - _CACHE_MAX_ENTRIES
    if overflow > 0:
        # ``dict`` preserves insertion order, and ``sorted`` is stable, so ties
        # evict the earliest inserted entry instead of the just-written one.
        oldest = sorted(_CACHE.items(), key=lambda item: item[1][0])
        for key, _entry in oldest[:overflow]:
            _CACHE.pop(key, None)


def _cache_get(workdir_key: str, source_tag: str) -> DiscoveryEnvelope | None:
    now = time.time()
    _prune_cache(now)
    entry = _CACHE.get((workdir_key, source_tag))
    if entry is None:
        return None
    cached_at, envelope = entry
    if (now - cached_at) > CACHE_TTL_SECONDS:
        _CACHE.pop((workdir_key, source_tag), None)
        return None
    return envelope


def _cache_put(
    workdir_key: str, source_tag: str, envelope: DiscoveryEnvelope
) -> None:
    now = time.time()
    _prune_cache(now)
    _CACHE[(workdir_key, source_tag)] = (now, envelope)
    _prune_cache(now)


def resolve(workdir: Path | str) -> DiscoveryEnvelope:
    """Resolve the active Rally Point channel for ``workdir``.

    Always returns a ``DiscoveryEnvelope``. Callers inspect
    ``coordination_unavailable`` and ``resolved_via`` to decide whether
    to write, mirror, or surface a degraded-mode warning.

    The order is: env override → native rally CLI (real enter/say/whoami
    surface) → ``agent-rally-discover`` binary → Python import → fetched
    binary → internal fallback. The first non-``None`` source wins.
    Each successful resolution is cached for ``CACHE_TTL_SECONDS``.

    Native Rally receives the original active worktree so its identity and
    branch projection remain accurate; Rally itself resolves all linked
    worktrees to one shared repo room. Legacy discovery and the Build Loop
    fallback receive the canonical checkout root so their path-keyed stores do
    not split across worktrees.
    """
    requested_workdir = Path(workdir).expanduser().resolve()
    workdir_path = channel_paths.canonical_workdir(requested_workdir)
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

    # Operator discovery overrides and the legacy discover surfaces are
    # read-only compatibility inputs. They may veto writes for an incompatible
    # protocol, but they cannot become a Build Loop write authority.
    for source_tag, probe in (("env-override", _try_env_override),):
        cached = _cache_get(workdir_key, source_tag)
        envelope = cached if cached is not None else probe(workdir_path)
        if envelope is not None:
            _cache_put(workdir_key, source_tag, envelope)
            writable = _writable_discovery_result(envelope)
            if writable is not None:
                return writable

    # Standalone Rally is the default only when its read path is operational.
    # ``whoami`` can succeed against a corrupt room, so a failed status-read
    # probe is a deliberate backend transition to Build Loop local storage;
    # do not fall through to a second writer for the same broken Rally room.
    native_key = str(requested_workdir)
    cached = _cache_get(native_key, "repo-local-rally-cli")
    if cached is not None:
        return cached
    native_binary = repo_local_rally_binary(requested_workdir)
    if native_binary:
        envelope = _resolve_native_rally_channel(
            native_binary,
            requested_workdir,
            resolved_via="repo-local-rally-cli",
            policy="repo-local",
        )
        if envelope is not None:
            _cache_put(native_key, "repo-local-rally-cli", envelope)
            return envelope
        fallback = _internal_fallback(workdir_path)
        fallback.raw = {
            "fallback_reason": "rally_unhealthy",
            "rally_binary": native_binary,
        }
        _cache_put(native_key, "repo-local-rally-cli", fallback)
        return fallback

    # Legacy discovery compatibility, then fetch-on-install when no standalone
    # Rally binary exists at all. Only the fetched native CLI is writable.
    for source_tag, probe in (
        ("path-binary", _try_path_binary),
        ("python-import", _try_python_import),
        ("fetched-binary", _try_fetched_binary),
    ):
        cached = _cache_get(workdir_key, source_tag)
        envelope = cached if cached is not None else probe(workdir_path)
        if envelope is not None:
            _cache_put(workdir_key, source_tag, envelope)
            writable = _writable_discovery_result(envelope)
            if writable is not None:
                return writable

    # Internal fallback — always succeeds.
    cached = _cache_get(workdir_key, "build-loop-internal")
    if cached is not None:
        return cached
    envelope = _internal_fallback(workdir_path)
    _cache_put(workdir_key, "build-loop-internal", envelope)
    return envelope


_MIGRATED_THIS_PROCESS: set[str] = set()
_MIGRATED_THIS_PROCESS_MAX_ENTRIES = 256


def clear_cache() -> None:
    """Drop every piece of per-process rally state. Primarily for tests.

    Three independent caches survive across tests in one pytest process: this
    module's envelope cache, ``kind_capability._CACHE``, and the
    ``_MIGRATED_THIS_PROCESS`` once-per-process migration guard below. Clearing
    only the first left a test that called clear_cache() still holding the other
    two, so a write whose store digest a previous test had already "migrated"
    was silently skipped and the expected file never appeared.

    One reset call that misses two of three caches is worse than no reset call,
    because it reads at the call site as though isolation were handled.
    """
    _CACHE.clear()
    _MIGRATED_THIS_PROCESS.clear()
    try:
        from . import kind_capability
    except ImportError:  # pragma: no cover — flat-module import fallback
        try:
            import kind_capability  # type: ignore[no-redef]
        except ImportError:
            kind_capability = None  # type: ignore[assignment]
    if kind_capability is not None:
        kind_capability.clear_cache()


# Once-per-process guard so the seam does not re-shell on every coordination
# write for the same exact fallback contents. migrate-legacy is itself
# idempotent (event_id dedup), so this is an efficiency layer, not correctness.
# Keys include the store digest; appending a fact creates a new sync attempt.


def _remember_migrated(store_key: str) -> None:
    """Bound the process optimization; durable markers retain correctness."""
    while len(_MIGRATED_THIS_PROCESS) >= _MIGRATED_THIS_PROCESS_MAX_ENTRIES:
        _MIGRATED_THIS_PROCESS.pop()
    _MIGRATED_THIS_PROCESS.add(store_key)


def maybe_auto_migrate(
    workdir: Path | str, envelope: "DiscoveryEnvelope | None" = None
) -> dict | None:
    """Auto-run ``rally migrate-legacy`` on the fallback→ARP transition seam.

    Fires when (a) the resolved envelope is FULL capability and (b) a stranded
    identity-keyed Build Loop fallback store holds at least one losslessly
    migratable fact. An exact historical directory may also be included through
    ``BUILD_LOOP_LEGACY_MIGRATION_SOURCE``; basename scans are intentionally
    forbidden because they cannot prove repository identity. The Rally binary
    pinned in the discovery envelope replays each staged source through the
    public ``rally migrate-legacy --json`` command.

    Returns the parsed migrate result dict (``slugs_found``, ``facts_read``,
    ``facts_migrated``, ``facts_skipped_existing``, ``warnings``) on success, or
    ``None`` when not applicable / on any error. Fire-and-forget — never raises into
    the caller, never imports agent-rally-point.

    ``<fallback_channel>/.migrated`` stores the successfully synchronized source
    digest. It is never written for a non-zero exit, malformed response, or an
    incomplete read count. Appended facts change the digest and trigger another
    idempotent reconciliation.
    """
    try:
        env = envelope if envelope is not None else resolve(workdir)
        if env.capability_level != "full":
            return None

        # Reconcile the Build Loop-owned fallback plus, only when the operator
        # selected one exact path, a retired historical store. A temporary HOME
        # bridge lets Rally consume the explicit source without writing the live
        # fallback into Rally's old shared namespace.
        slug = channel_paths.app_slug(workdir)
        binary_value = env.raw.get("rally_binary")
        binary = str(binary_value) if binary_value else None
        if not binary:
            return None
        aggregate: dict[str, Any] | None = None
        for fallback_dir in _migration_source_dirs(Path(workdir), slug):
            try:
                if fallback_dir.is_symlink() or not fallback_dir.is_dir():
                    continue
            except OSError:
                continue
            store = fallback_dir / "changes.jsonl"
            marker = fallback_dir / ".migrated"
            if _regular_path_token(store) is None or not _marker_path_is_safe(marker):
                continue
            if _marker_matches_stat(marker, store):
                continue

            # v0.25 and earlier Build Loop stores used a schema-less record
            # shape Rally silently skips. Preserve those lines byte-for-byte
            # and append one deterministic fact.v1 companion per source row.
            if not _backfill_legacy_fact_rows(store):
                continue
            fingerprint = _store_fingerprint(store)
            if fingerprint is None:
                continue
            store_key = f"{store.resolve()}:{fingerprint['sha256']}"
            if store_key in _MIGRATED_THIS_PROCESS or _marker_matches(
                marker, fingerprint
            ):
                continue
            expected_identity = (
                int(fingerprint["device"]), int(fingerprint["inode"])
            )
            if not _has_fact_v1_line(
                store, expected_identity=expected_identity
            ) or not _store_is_losslessly_migratable(
                store, expected_identity=expected_identity
            ):
                continue

            # The source may be appended concurrently. Only migrate and mark a
            # byte-identical regular file; otherwise the next post retries.
            if _store_fingerprint(store) != fingerprint:
                continue

            result = _migrate_explicit_store(
                binary=binary,
                workdir=Path(workdir),
                store=store,
                repo_basename=channel_paths.canonical_workdir(workdir).name,
                expected_fact_count=_fact_v1_count(
                    store, expected_identity=expected_identity
                ),
                expected_fingerprint=fingerprint,
            )
            if result is None:
                continue
            if (
                _store_fingerprint(store) != fingerprint
                or not _marker_path_is_safe(marker)
            ):
                continue
            _remember_migrated(store_key)
            _publish_marker_atomic(
                marker, {**fingerprint, "synced_at": _utc_iso()}
            )
            aggregate = _merge_migration_results(aggregate, result)
        return aggregate
    except Exception:  # noqa: BLE001 — fire-and-forget seam, never block a host action
        return None


def _migration_source_dirs(workdir: Path, slug: str) -> list[Path]:
    """Return the identity-bound fallback and an explicitly approved legacy dir.

    Historical ``~/.agent-rally-point/apps/<basename>`` directories are not
    bound to a canonical repository identity. Automatically scanning them can
    import an unrelated repository with the same basename. An operator may
    approve one exact historical directory with
    ``BUILD_LOOP_LEGACY_MIGRATION_SOURCE``; symlink sources are refused.
    """
    candidates = [channel_paths.fallback_channel_dir(workdir, slug)]
    approved = os.environ.get("BUILD_LOOP_LEGACY_MIGRATION_SOURCE", "").strip()
    if approved:
        legacy = Path(approved).expanduser()
        try:
            if not legacy.is_symlink() and legacy.is_dir():
                candidates.append(legacy.resolve(strict=True))
        except (OSError, RuntimeError):
            pass
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.expanduser().resolve(strict=False))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _migrate_explicit_store(
    *,
    binary: str,
    workdir: Path,
    store: Path,
    repo_basename: str,
    expected_fact_count: int,
    expected_fingerprint: dict[str, Any],
) -> dict[str, Any] | None:
    """Replay one exact fallback store through Rally's public migrator."""
    source_digest = hashlib.sha256(str(store.resolve()).encode("utf-8")).hexdigest()[:12]
    import_slug = f"{repo_basename}-{source_digest}"
    try:
        with tempfile.TemporaryDirectory(prefix="build-loop-rally-import-") as temp_home:
            bridge_dir = (
                Path(temp_home)
                / ".agent-rally-point"
                / "apps"
                / import_slug
            )
            bridge_dir.mkdir(parents=True, exist_ok=False)
            source_fd = os.open(
                str(store), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                source_stat = os.fstat(source_fd)
                expected_stat = {
                    key: expected_fingerprint.get(key)
                    for key in ("device", "inode", "size", "mtime_ns", "ctime_ns")
                }
                actual_stat = {
                    "device": source_stat.st_dev,
                    "inode": source_stat.st_ino,
                    "size": source_stat.st_size,
                    "mtime_ns": source_stat.st_mtime_ns,
                    "ctime_ns": source_stat.st_ctime_ns,
                }
                if (
                    not stat.S_ISREG(source_stat.st_mode)
                    or actual_stat != expected_stat
                ):
                    return None
                with os.fdopen(source_fd, "rb", closefd=False) as source, open(
                    bridge_dir / "changes.jsonl", "xb"
                ) as destination:
                    copied_digest = hashlib.sha256()
                    copied_size = 0
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        destination.write(chunk)
                        copied_digest.update(chunk)
                        copied_size += len(chunk)
                    destination.flush()
                    os.fsync(destination.fileno())
                    final_stat = os.fstat(source.fileno())
                final_token = {
                    "device": final_stat.st_dev,
                    "inode": final_stat.st_ino,
                    "size": final_stat.st_size,
                    "mtime_ns": final_stat.st_mtime_ns,
                    "ctime_ns": final_stat.st_ctime_ns,
                }
                if (
                    final_token != expected_stat
                    or copied_size != expected_fingerprint.get("size")
                    or copied_digest.hexdigest()
                    != expected_fingerprint.get("sha256")
                ):
                    return None
            finally:
                os.close(source_fd)
            child_env = dict(os.environ)
            child_env["HOME"] = temp_home
            proc = subprocess.run(
                [binary, "migrate-legacy", "--json"],
                cwd=str(workdir),
                env=child_env,
                capture_output=True,
                text=True,
                timeout=hook_budget.inner_timeout_seconds(hook_budget.MARGIN_CHILD),
            )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        out = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    return _migration_result(
        out,
        expected_slug=import_slug,
        expected_fact_count=expected_fact_count,
    )


def _merge_migration_results(
    aggregate: dict[str, Any] | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    if aggregate is None:
        return dict(result)
    merged = dict(aggregate)
    for key in ("facts_read", "facts_migrated", "facts_skipped_existing"):
        merged[key] = int(merged.get(key, 0)) + int(result.get(key, 0))
    for key in ("slugs_found", "append_outcomes", "outcome_unknowns", "warnings"):
        left = merged.get(key) if isinstance(merged.get(key), list) else []
        right = result.get(key) if isinstance(result.get(key), list) else []
        merged[key] = [*left, *right]
    return merged


def _has_fact_v1_line(
    store: Path, *, expected_identity: tuple[int, int] | None = None
) -> bool:
    """Return True if ``store`` holds ≥1 ``agent-rally.fact.v1`` line."""
    try:
        with _open_regular_text(store, expected_identity=expected_identity) as fh:
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


def _fact_v1_count(
    store: Path, *, expected_identity: tuple[int, int] | None = None
) -> int:
    """Count fact rows in the exact staged source after strict preflight."""
    count = 0
    try:
        with _open_regular_text(store, expected_identity=expected_identity) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except (ValueError, TypeError):
                    return 0
                if (
                    isinstance(value, dict)
                    and value.get("schema") == "agent-rally.fact.v1"
                ):
                    count += 1
    except OSError:
        return 0
    return count


_LEGACY_REQUIRED_KEYS = frozenset(
    {"ts", "kind", "tool", "model", "run_id", "app_slug", "payload", "revision"}
)
_LEGACY_STRING_KEYS = ("kind", "tool", "model", "run_id", "app_slug")
_LEGACY_COMPANION_FIELDS = (
    "schema",
    "event_id",
    "thread_id",
    "kind",
    "subject",
    "scope",
    "created_at",
    "evidence",
    "tool",
    "summary",
    "target",
    "ref",
    "status",
    "severity",
    "uri",
)


def _is_standard_legacy_record(record: Any) -> bool:
    """Recognize the schema-less Build Loop record contract, not arbitrary JSON."""
    if not isinstance(record, dict) or "schema" in record:
        return False
    if not _LEGACY_REQUIRED_KEYS.issubset(record):
        return False
    if any(not isinstance(record.get(key), str) for key in _LEGACY_STRING_KEYS):
        return False
    ts = record.get("ts")
    if (
        isinstance(ts, bool)
        or not isinstance(ts, (int, float))
        or not math.isfinite(float(ts))
    ):
        return False
    revision = record.get("revision")
    return bool(
        isinstance(record.get("payload"), dict)
        and isinstance(revision, int)
        and not isinstance(revision, bool)
        and revision >= 0
    )


def _legacy_created_at(ts: int | float) -> str | None:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts)))
    except (OverflowError, OSError, ValueError):
        return None


def _legacy_fact(record: dict[str, Any]) -> dict[str, Any] | None:
    """Build the deterministic append-only companion for one historical row."""
    if not _is_standard_legacy_record(record):
        return None
    created_at = _legacy_created_at(record["ts"])
    if created_at is None:
        return None
    try:
        try:
            from .fact_v1 import to_fact_v1
            from .payload_codec import has_oversize_marker
        except ImportError:
            from fact_v1 import to_fact_v1  # type: ignore
            from payload_codec import has_oversize_marker  # type: ignore

        producer = {
            key: value for key, value in record.items() if key.startswith("producer_")
        }
        build_loop_fields = {
            key: record[key]
            for key in (
                "build_loop_id",
                "build_loop_started_at",
                "build_loop_run_label",
            )
            if key in record
        }
        fact = to_fact_v1(
            kind=record["kind"],
            tool=record["tool"],
            model=record["model"],
            run_id=record["run_id"],
            app_slug=record["app_slug"],
            payload=record["payload"],
            revision=record["revision"],
            producer=producer or None,
            build_loop_fields=build_loop_fields or None,
            source_record=record,
            created_at=created_at,
        )
        if has_oversize_marker(fact.get("evidence")):
            return None
        return fact
    except (TypeError, ValueError):
        return None


def _is_exact_legacy_companion(fact: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Compare every Rally-surviving field emitted for a historical row."""
    return all(
        (key in fact) == (key in expected) and fact.get(key) == expected.get(key)
        for key in _LEGACY_COMPANION_FIELDS
    )


def _represented_legacy_event_id(fact: dict[str, Any]) -> str | None:
    try:
        try:
            from .payload_codec import decode_event, has_oversize_marker
        except ImportError:
            from payload_codec import decode_event, has_oversize_marker  # type: ignore
        evidence = fact.get("evidence")
        if (
            not isinstance(evidence, list)
            or has_oversize_marker(evidence)
        ):
            return None
        event = decode_event(evidence)
        source = event.get("source_record") if isinstance(event, dict) else None
        expected = _legacy_fact(source) if isinstance(source, dict) else None
        if expected is None or not _is_exact_legacy_companion(fact, expected):
            return None
        return str(expected["event_id"])
    except (TypeError, ValueError):
        return None


def _backfill_legacy_fact_rows(store: Path) -> bool:
    """Append missing fact.v1 companions without rewriting historical lines."""
    try:
        try:
            from .fact_v1 import FACT_SCHEMA, write_missing_fact_v1_lines
        except ImportError:
            from fact_v1 import FACT_SCHEMA, write_missing_fact_v1_lines  # type: ignore

        with tempfile.TemporaryDirectory(
            prefix="build-loop-legacy-index-"
        ) as temp_dir:
            conn = sqlite3.connect(str(Path(temp_dir) / "legacy.sqlite3"))
            try:
                conn.execute("PRAGMA journal_mode=OFF")
                conn.execute("PRAGMA synchronous=OFF")
                conn.execute("PRAGMA cache_size=-1024")
                conn.execute(
                    "CREATE TABLE candidates ("
                    "event_id TEXT PRIMARY KEY, source_ordinal INTEGER NOT NULL, "
                    "fact_json TEXT NOT NULL"
                    ") WITHOUT ROWID"
                )
                conn.execute(
                    "CREATE TABLE represented (event_id TEXT PRIMARY KEY) WITHOUT ROWID"
                )
                source_ordinal = 0
                with _open_regular_text(store) as fh:
                    source_stat = os.fstat(fh.fileno())
                    source_identity = (source_stat.st_dev, source_stat.st_ino)
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            record = json.loads(line)
                        except (ValueError, TypeError):
                            return False
                        if not isinstance(record, dict):
                            return False
                        if record.get("schema") == FACT_SCHEMA:
                            represented_id = _represented_legacy_event_id(record)
                            if represented_id is not None:
                                conn.execute(
                                    "INSERT OR IGNORE INTO represented VALUES (?)",
                                    (represented_id,),
                                )
                            continue
                        expected = _legacy_fact(record)
                        if expected is None:
                            return False
                        source_ordinal += 1
                        conn.execute(
                            "INSERT OR IGNORE INTO candidates VALUES (?, ?, ?)",
                            (
                                str(expected["event_id"]),
                                source_ordinal,
                                json.dumps(expected, separators=(",", ":")),
                            ),
                        )
                conn.commit()

                def _missing_facts():
                    rows = conn.execute(
                        "SELECT c.fact_json FROM candidates c "
                        "LEFT JOIN represented r USING (event_id) "
                        "WHERE r.event_id IS NULL ORDER BY c.source_ordinal"
                    )
                    for (raw_fact,) in rows:
                        yield json.loads(raw_fact)

                if write_missing_fact_v1_lines(
                    store.parent,
                    _missing_facts(),
                    expected_identity=source_identity,
                ) is None:
                    return False
            finally:
                conn.close()
        return _store_is_losslessly_migratable(
            store, expected_identity=source_identity
        )
    except OSError:
        return False


def _store_is_losslessly_migratable(
    store: Path, *, expected_identity: tuple[int, int] | None = None
) -> bool:
    """Require valid facts and an exact companion for every historical row."""
    try:
        try:
            from .fact_v1 import FACT_SCHEMA
            from .payload_codec import decode_event, has_oversize_marker
        except ImportError:
            from fact_v1 import FACT_SCHEMA  # type: ignore
            from payload_codec import decode_event, has_oversize_marker  # type: ignore
        found = False
        with tempfile.TemporaryDirectory(
            prefix="build-loop-lossless-index-"
        ) as temp_dir:
            conn = sqlite3.connect(str(Path(temp_dir) / "lossless.sqlite3"))
            try:
                conn.execute("PRAGMA journal_mode=OFF")
                conn.execute("PRAGMA synchronous=OFF")
                conn.execute("PRAGMA cache_size=-1024")
                conn.execute(
                    "CREATE TABLE required (event_id TEXT PRIMARY KEY) WITHOUT ROWID"
                )
                conn.execute(
                    "CREATE TABLE represented (event_id TEXT PRIMARY KEY) WITHOUT ROWID"
                )
                with _open_regular_text(
                    store, expected_identity=expected_identity
                ) as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                        except (ValueError, TypeError):
                            return False
                        if not isinstance(obj, dict):
                            return False
                        if obj.get("schema") != FACT_SCHEMA:
                            expected = _legacy_fact(obj)
                            if expected is None:
                                return False
                            conn.execute(
                                "INSERT OR IGNORE INTO required VALUES (?)",
                                (str(expected["event_id"]),),
                            )
                            continue
                        found = True
                        evidence = obj.get("evidence")
                        if not isinstance(evidence, list) or has_oversize_marker(evidence):
                            return False
                        event = decode_event(evidence)
                        if event is None:
                            return False
                        represented_id = _represented_legacy_event_id(obj)
                        if (
                            isinstance(event, dict)
                            and "source_record" in event
                            and represented_id is None
                        ):
                            return False
                        if represented_id is not None:
                            conn.execute(
                                "INSERT OR IGNORE INTO represented VALUES (?)",
                                (represented_id,),
                            )
                missing = conn.execute(
                    "SELECT 1 FROM required q "
                    "LEFT JOIN represented r USING (event_id) "
                    "WHERE r.event_id IS NULL LIMIT 1"
                ).fetchone()
                return found and missing is None
            finally:
                conn.close()
    except OSError:
        return False


def _store_fingerprint(store: Path) -> dict[str, Any] | None:
    """Return stable content identity for one fallback JSONL store."""
    digest = hashlib.sha256()
    size = 0
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(store), flags)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(fd)
            return None
        with os.fdopen(fd, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
            final_metadata = os.fstat(fh.fileno())
    except OSError:
        return None
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(
        getattr(metadata, field) != getattr(final_metadata, field)
        for field in stable_fields
    ) or size != final_metadata.st_size:
        return None
    metadata = final_metadata
    return {
        "sha256": digest.hexdigest(),
        "size": size,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }


def _regular_path_token(path: Path) -> dict[str, int] | None:
    """Return no-follow identity for a regular file, else None."""
    try:
        metadata = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return None
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }


def _open_regular_text(
    path: Path, *, expected_identity: tuple[int, int] | None = None
):
    """Open one no-follow regular UTF-8 file and retain its verified fd."""
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("migration source must be a regular file")
        if expected_identity is not None and (
            metadata.st_dev,
            metadata.st_ino,
        ) != expected_identity:
            raise OSError("migration source identity changed")
        return os.fdopen(fd, "r", encoding="utf-8")
    except Exception:
        os.close(fd)
        raise


def _read_regular_json(path: Path) -> Any:
    with _open_regular_text(path) as stream:
        if os.fstat(stream.fileno()).st_size > _MAX_MIGRATION_MARKER_BYTES:
            raise ValueError("migration marker exceeds byte ceiling")
        raw = stream.read(_MAX_MIGRATION_MARKER_BYTES + 1)
        if len(raw.encode("utf-8")) > _MAX_MIGRATION_MARKER_BYTES:
            raise ValueError("migration marker exceeds byte ceiling")
        return json.loads(raw)


def _marker_path_is_safe(marker: Path) -> bool:
    """Accept an absent marker or an existing no-follow regular file only."""
    try:
        metadata = marker.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return not stat.S_ISLNK(metadata.st_mode) and stat.S_ISREG(metadata.st_mode)


def _marker_matches_stat(marker: Path, store: Path) -> bool:
    """Fast steady-state marker gate without rereading an unchanged ledger."""
    if not _marker_path_is_safe(marker):
        return False
    token = _regular_path_token(store)
    if token is None:
        return False
    try:
        stored = _read_regular_json(marker)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return False
    return bool(
        isinstance(stored, dict)
        and all(stored.get(key) == value for key, value in token.items())
        and isinstance(stored.get("sha256"), str)
    )


def _publish_marker_atomic(marker: Path, payload: dict[str, Any]) -> bool:
    """Publish a migration marker atomically without following marker links."""
    if not _marker_path_is_safe(marker):
        return False
    temp = marker.with_name(
        f".{marker.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    try:
        fd = os.open(
            str(temp),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            offset = 0
            while offset < len(encoded):
                written = os.write(fd, encoded[offset:])
                if written <= 0:
                    raise OSError("zero-byte migration marker write")
                offset += written
            os.fsync(fd)
        finally:
            os.close(fd)
        if not _marker_path_is_safe(marker):
            return False
        os.replace(temp, marker)
        return True
    except OSError:
        return False
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _marker_matches(marker: Path, fingerprint: dict[str, Any]) -> bool:
    """True only for a validated-success marker of these exact contents."""
    if not _marker_path_is_safe(marker):
        return False
    try:
        stored = _read_regular_json(marker)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return False
    return bool(
        isinstance(stored, dict)
        and stored.get("sha256") == fingerprint.get("sha256")
        and stored.get("size") == fingerprint.get("size")
    )


def _migration_result(
    out: Any,
    *,
    expected_slug: str,
    expected_fact_count: int | None = None,
) -> dict[str, Any] | None:
    """Validate Rally's migration receipt before persisting a watermark."""
    if (
        not isinstance(out, dict)
        or out.get("ok") is not True
        or out.get("product") != "rally"
        or out.get("schema") != "agent-rally.command.migrate-legacy.v1"
    ):
        return None
    data = out.get("data")
    if not isinstance(data, dict):
        return None
    result = data.get("migrate-legacy") or data.get("migrate_legacy")
    if not isinstance(result, dict):
        return None
    values = (
        result.get("facts_read"),
        result.get("facts_migrated"),
        result.get("facts_skipped_existing"),
    )
    if any(type(value) is not int or value < 0 for value in values):
        return None
    facts_read, migrated, skipped = values
    if min(facts_read, migrated, skipped) < 0 or facts_read != migrated + skipped:
        return None
    if expected_fact_count is not None and facts_read != expected_fact_count:
        return None
    slugs = result.get("slugs_found")
    warnings = result.get("warnings")
    if not isinstance(slugs, list) or expected_slug not in slugs:
        return None
    if not isinstance(warnings, list) or warnings:
        return None
    unknowns = result.get("outcome_unknowns", [])
    if not isinstance(unknowns, list) or unknowns:
        return None
    return result


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
