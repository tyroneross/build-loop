#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rally Point presence — live session liveness, reaper, and read cursor.

One ``sessions/<session-id>.json`` per live session, overwrite-in-place
via tmp+rename (atomic, no partial reads). Each carries the per-session
read cursor (``revision`` + ``changes.jsonl`` byte offset) so checkpoint
reads are delta-only.

Reaper: a presence file whose ``heartbeat_ts`` is older than
``heartbeat_minutes`` (default 15, overridable via the channel's
``config.json`` — OQ2) is stale and removed. No daemon: ``reap_stale``
runs opportunistically at each checkpoint read.

All reads no-op gracefully when the channel/sessions dir is absent
(returns empty / zero-cursor; lazy-create on write only).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path

try:  # package import
    from .build_loop_id import rally_fields_for
    from . import liveness as _liveness
except ImportError:  # script import
    from build_loop_id import rally_fields_for  # type: ignore
    import liveness as _liveness  # type: ignore

_SESSIONS_DIR = "sessions"
_CONFIG_NAME = "config.json"
_DEFAULT_HEARTBEAT_MIN = 15
# Per-session sha cache for the code-progress liveness signal, keyed by
# session_id -> {"sha": <branch_head_sha>, "observed_ts": <epoch>}. Lives in the
# channel dir; first observation of a moved sha is treated as fresh progress.
_SHA_CACHE_NAME = "liveness-sha-cache.json"
_ZERO_CURSOR = {"revision": 0, "changes_offset": 0}
_GIT_TIMEOUT_S = 0.5  # cap any single git call; fail-open on timeout
_UNKNOWN_BRANCH = {
    "branch_name": "unknown",
    "branch_head_sha": "unknown",
    "branch_merge_status": "unknown",
}


def _compute_branch_status(cwd: Path) -> dict:
    """Return branch_name, branch_head_sha, branch_merge_status for cwd.

    Fail-open: any git error, timeout, detached HEAD, or non-git dir
    returns the all-``unknown`` record. Never raises. ~5 ms per call on
    a healthy repo.

    Merge-status check: ``git merge-base --is-ancestor HEAD <upstream>``
    where upstream is ``origin/main`` with fallback to ``main``. Exit 0
    means HEAD is an ancestor of (i.e. merged into) the upstream tip.
    Squash-merged branches return ``unmerged`` here — file-level fallback
    lives in checkpoint._peer_files_already_landed.
    """
    rec = dict(_UNKNOWN_BRANCH)
    try:
        cwd_str = str(cwd)
        # Branch name (detached HEAD -> "HEAD"; we still return that as-is).
        r = subprocess.run(
            ["git", "-C", cwd_str, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
        if r.returncode == 0 and r.stdout.strip():
            rec["branch_name"] = r.stdout.strip()
        else:
            return rec  # not a git repo (or worse) — bail
        # HEAD SHA.
        r = subprocess.run(
            ["git", "-C", cwd_str, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
        if r.returncode == 0 and r.stdout.strip():
            rec["branch_head_sha"] = r.stdout.strip()
        else:
            return rec
        # Merge-status: try origin/main first, fall back to main.
        for upstream in ("origin/main", "main"):
            # Verify upstream exists before --is-ancestor (cheaper failure).
            v = subprocess.run(
                ["git", "-C", cwd_str, "rev-parse", "--verify", "--quiet",
                 upstream],
                capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
            )
            if v.returncode != 0:
                continue
            a = subprocess.run(
                ["git", "-C", cwd_str, "merge-base", "--is-ancestor",
                 rec["branch_head_sha"], upstream],
                capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
            )
            if a.returncode == 0:
                rec["branch_merge_status"] = "merged"
            elif a.returncode == 1:
                rec["branch_merge_status"] = "unmerged"
            # other exit codes (128 etc.) fall through to "unknown"
            return rec
        return rec  # neither upstream resolved
    except (subprocess.SubprocessError, OSError, ValueError):
        return dict(_UNKNOWN_BRANCH)


def _sessions_dir(channel_dir: Path) -> Path:
    return Path(channel_dir) / _SESSIONS_DIR


def _presence_path(channel_dir: Path, session_id: str) -> Path:
    return _sessions_dir(channel_dir) / f"{session_id}.json"


def heartbeat_minutes(channel_dir: Path) -> int:
    """Stale window in minutes (config.json override, default 15)."""
    try:
        cfg = json.loads((Path(channel_dir) / _CONFIG_NAME).read_text())
        v = int(cfg.get("heartbeat_minutes", _DEFAULT_HEARTBEAT_MIN))
        return v if v > 0 else _DEFAULT_HEARTBEAT_MIN
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return _DEFAULT_HEARTBEAT_MIN


def _atomic_write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}"
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    os.replace(str(tmp), str(path))


def parse_spawned(value: str | dict | None) -> dict:
    """Parse a ``--spawned`` spec (``type:count,type:count``) to a dict.

    Accepts the raw CSV string an agent self-reports for its fan-out
    (e.g. ``coder:2,workflow:21,independent-auditor:1``) and returns
    ``{"coder": 2, "workflow": 21, "independent-auditor": 1}``. A bare
    type with no count defaults to 1 (``coder`` -> ``{"coder": 1}``).
    Already-parsed dicts pass through (coerced to int counts). Malformed
    fragments are skipped — fire-and-forget, never raises.
    """
    if isinstance(value, dict):
        out: dict[str, int] = {}
        for k, v in value.items():
            try:
                out[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
        return out
    if not value:
        return {}
    out = {}
    for frag in str(value).split(","):
        frag = frag.strip()
        if not frag:
            continue
        if ":" in frag:
            name, _, count = frag.partition(":")
            name = name.strip()
            try:
                n = int(count.strip())
            except ValueError:
                n = 1
        else:
            name, n = frag, 1
        if name:
            out[name] = out.get(name, 0) + n
    return out


def write_presence(
    channel_dir: Path,
    *,
    session_id: str,
    tool: str,
    model: str,
    run_id: str,
    app_slug: str,
    phase: str,
    files_in_flight: list | None = None,
    cwd: Path | None = None,
    task: str | None = None,
    parent: str | None = None,
    spawned: str | dict | None = None,
    pid: int | None = None,
    host: str | None = None,
    planned_heartbeat_secs: int | None = None,
) -> None:
    """Write/refresh presence (overwrite-in-place). Preserves the cursor.

    ``cwd`` (optional) — the working directory whose branch state should
    be recorded. When omitted, ``Path.cwd()`` is used. The branch fields
    (``branch_name``, ``branch_head_sha``, ``branch_merge_status``,
    ``branch_merge_status_checked_ts``) are computed via
    ``_compute_branch_status``; any git failure yields ``"unknown"``.

    Roster fields (all optional, additive — existing callers unaffected):
    ``task`` (fuller free-text, falls back to ``phase`` for display),
    ``parent`` (the session_id that spawned this one; ``None`` for
    top-level), ``spawned`` (self-reported fan-out, ``type:count`` CSV
    or dict), ``pid``/``host`` (where it runs; default to this process).
    Every call writes ``last_seen`` (epoch) — presence is the heartbeat.

    Fire-and-forget: never raises, never blocks the host action.
    """
    try:
        p = _presence_path(channel_dir, session_id)
        cursor = dict(_ZERO_CURSOR)
        try:
            cursor = json.loads(p.read_text()).get("cursor", cursor)
        except (FileNotFoundError, OSError, ValueError):
            pass
        branch = _compute_branch_status(cwd if cwd is not None else Path.cwd())
        now = time.time()
        rec = {
            "session_id": session_id,
            "tool": tool or "unknown",
            "model": model or "unknown",
            "run_id": run_id or "unknown",
            "app_slug": app_slug,
            "phase": phase,
            "task": task or phase,
            "parent": parent or None,
            "spawned": parse_spawned(spawned),
            "files_in_flight": list(files_in_flight or []),
            "heartbeat_ts": now,
            "last_seen": now,
            # Adaptive-liveness: the session's declared beat cadence. None ->
            # the reaper assumes the default cadence. Positive ints only.
            "planned_heartbeat_secs": (
                int(planned_heartbeat_secs)
                if planned_heartbeat_secs and int(planned_heartbeat_secs) > 0
                else None
            ),
            "pid": int(pid) if pid is not None else os.getpid(),
            "host": host or socket.gethostname(),
            "cursor": cursor,
            "branch_name": branch["branch_name"],
            "branch_head_sha": branch["branch_head_sha"],
            "branch_merge_status": branch["branch_merge_status"],
            "branch_merge_status_checked_ts": now,
            "cwd": str(cwd) if cwd is not None else str(Path.cwd()),
        }
        # Top-level run-instance identity (orthogonal to runtime identity).
        # ``cwd`` is the run's workdir — read state.execution from there.
        # Absent when no state.execution.build_loop_id — presence proceeds.
        rec.update(rally_fields_for(cwd if cwd is not None else Path.cwd()))
        _atomic_write(p, rec)
    except Exception:  # noqa: BLE001 — fire-and-forget
        return


def _iter_presence(channel_dir: Path):
    sd = _sessions_dir(channel_dir)
    try:
        names = list(sd.glob("*.json"))
    except OSError:
        return
    for f in names:
        try:
            yield f, json.loads(f.read_text())
        except (OSError, ValueError):
            continue


def _sha_cache_path(channel_dir: Path) -> Path:
    return Path(channel_dir) / _SHA_CACHE_NAME


def _read_sha_cache(channel_dir: Path) -> dict:
    try:
        data = json.loads(_sha_cache_path(channel_dir).read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, OSError, ValueError):
        return {}


def _write_sha_cache(channel_dir: Path, cache: dict) -> None:
    try:
        _atomic_write(_sha_cache_path(channel_dir), cache)
    except (OSError, ValueError, TypeError):
        return


def _code_progress_age(
    channel_dir: Path, cache: dict, session_id: str, sha: object, now: float
) -> int | None:
    """Age (seconds) since this session's branch HEAD last MOVED, or ``None``
    when HEAD has never been observed to move.

    Pure over the injected ``cache`` + ``now``; mutates ``cache`` in place. The
    cache entry tracks ``{sha, moved_ts}`` where ``moved_ts`` is the wall-clock of
    the last OBSERVED change (``None`` until a change is seen). Semantics:

    * unknown/blank sha -> ``None`` (signal absent).
    * FIRST observation (nothing cached) -> record ``{sha, moved_ts: None}``,
      return ``None``. A first sighting is not proof of movement.
    * sha CHANGED vs cache -> HEAD moved -> record ``moved_ts = now``, return 0.
    * sha UNCHANGED vs cache -> if a prior move was recorded, return
      ``now - moved_ts`` (the keep-alive decays over the window after the move);
      else ``None`` (never moved -> signal absent).

    A first-sighting is deliberately NOT a free keep-alive, and a sha that never
    moves goes stale once the window since its last move (or, if never moved, the
    heartbeat alone) elapses.
    """
    if not isinstance(sha, str) or not sha or sha == "unknown":
        return None
    prev = cache.get(session_id)
    if not isinstance(prev, dict) or "sha" not in prev:
        cache[session_id] = {"sha": sha, "moved_ts": None}
        return None
    if prev.get("sha") != sha:
        cache[session_id] = {"sha": sha, "moved_ts": now}
        return 0
    # Unchanged sha: age since the last observed move, if any.
    moved_ts = prev.get("moved_ts")
    if moved_ts is None:
        return None
    try:
        return int(max(0.0, now - float(moved_ts)))
    except (TypeError, ValueError):
        return None


def _adaptive_cutoff_secs(channel_dir: Path, rec: dict, pol) -> int:
    """The adaptive staleness window (seconds) for a presence record.

    Cadence source, first present wins: the record's ``planned_heartbeat_secs``;
    else the channel's legacy ``heartbeat_minutes`` config (kept as a cadence
    knob for backward compatibility); else the policy default cadence. The window
    is ``cadence * miss_multiplier + grace`` via the shared liveness math.
    """
    try:
        planned = rec.get("planned_heartbeat_secs")
        planned = int(planned) if planned else 0
    except (TypeError, ValueError):
        planned = 0
    if planned <= 0:
        # Legacy compatibility: an explicit `heartbeat_minutes` config acts as
        # the declared cadence so existing per-channel overrides keep working.
        legacy_min = heartbeat_minutes(channel_dir)
        if legacy_min != _DEFAULT_HEARTBEAT_MIN:
            planned = legacy_min * 60
    return _liveness.adaptive_window_secs(
        planned,
        pol.default_cadence_secs,
        pol.miss_multiplier,
        pol.grace_secs,
    )


def _full_capability_for_channel(channel_dir: Path) -> bool:
    """True only when a full-capability Rust binary owns this channel.

    Reaping presence is a destructive coordination action; it is Rust-only. A
    degraded/unavailable session must NEVER unlink a presence file it cannot
    prove is dead — doing so would hide a still-alive peer and cause the exact
    write-collision this system prevents. Delegates to the single capability
    guard (``capability.full_capability_for_channel``); fail-CLOSED on any error.
    """
    try:
        from . import capability as _cap
    except ImportError:  # script-mode
        try:
            import capability as _cap  # type: ignore
        except ImportError:
            return False
    return _cap.full_capability_for_channel(channel_dir)


def reap_stale(channel_dir: Path, *, apply: bool = True) -> list:
    """Remove ADAPTIVELY-stale live presence, with a code-progress keep-alive.
    Returns reaped session IDs.

    RUST-ONLY when applying: physical unlinking happens only when a
    full-capability Rust binary owns the channel (``_full_capability_for_channel``).
    Below full capability this is a no-op returning ``[]`` — a degraded session
    keeps every presence file rather than risk hiding a live peer. A dry-run
    (``apply=False``) still reports eligibility regardless of capability.

    Staleness is RELATIVE to each session's declared cadence
    (``planned_heartbeat_secs``, else the legacy ``heartbeat_minutes`` config,
    else the policy default) via the shared adaptive-window math: a 5-min cadence
    goes stale at ~31 min; a 5-hour cadence not until ~30 h.

    A presence FILE *is* the heartbeat, so heartbeat is the primary signal. The
    code-progress signal (the session's ``branch_head_sha`` moved since the last
    poll, cached) acts as a KEEP-ALIVE OVERRIDE: a session whose HEAD advanced
    within its window is alive even if its heartbeat lapsed, and is preserved.
    (Inject/plan signals are room-fact projections, not file-derivable here, so
    they live on the Rust squad-visibility path.)

    Pure-reader cursor stubs (``tool == "reader"``, ``heartbeat_ts`` 0) are never
    peers. ``apply`` (default ``True``) — when ``False``, report eligibility
    without unlinking (dry-run).
    """
    # RUST-ONLY destructive guard: never physically reap below full capability.
    if apply and not _full_capability_for_channel(channel_dir):
        return []
    now = time.time()
    pol = _load_policy(channel_dir)
    cache = _read_sha_cache(channel_dir)
    cache_dirty = False
    reaped: list = []
    for f, rec in _iter_presence(channel_dir):
        if rec.get("tool") == "reader":
            continue  # cursor stub — keep, never a peer
        try:
            hb = float(rec.get("heartbeat_ts", 0))
        except (TypeError, ValueError):
            continue
        if hb <= 0:
            continue  # not a live heartbeat
        session_id = rec.get("session_id", f.stem)
        window = _adaptive_cutoff_secs(channel_dir, rec, pol)
        heartbeat_age = now - hb
        # Always observe the branch sha (records first-sightings + movement) so
        # the keep-alive works across polls — even while the heartbeat is fresh.
        before = cache.get(session_id)
        cp_age = _code_progress_age(
            channel_dir, cache, session_id, rec.get("branch_head_sha"), now
        )
        if cache.get(session_id) != before:
            cache_dirty = True
        if heartbeat_age <= window:
            continue  # heartbeat fresh -> alive
        # Heartbeat lapsed. Code-progress keep-alive: did HEAD move within window?
        if cp_age is not None and cp_age <= window:
            continue  # forward code progress -> alive despite stale heartbeat
        # Stale heartbeat AND no fresh code progress -> reap.
        if apply:
            try:
                f.unlink()
            except OSError:
                continue
        reaped.append(session_id)
    if apply and cache_dirty:
        for sid in reaped:
            cache.pop(sid, None)
        _write_sha_cache(channel_dir, cache)
    return reaped


def _load_policy(channel_dir: Path):
    """Resolve the coordination policy (liveness tunables). The policy reads
    ``<workdir>/.build-loop/config.json``; the channel dir is typically inside
    ``.build-loop`` so its parent's parent is the workdir. Fail-open to a default
    ``CoordinationPolicy`` (carrying the pinned liveness defaults).
    """
    try:
        from . import coordination_policy as _cp
    except ImportError:
        try:
            import coordination_policy as _cp  # type: ignore
        except ImportError:
            _cp = None  # type: ignore
    if _cp is None:
        # Minimal stand-in carrying the pinned defaults (no config available).
        class _Default:
            default_cadence_secs = _liveness.DEFAULT_CADENCE_SECS
            miss_multiplier = _liveness.MISS_MULTIPLIER
            grace_secs = _liveness.GRACE_SECS

        return _Default()
    try:
        # channel_dir is .../.build-loop/<channel>; workdir is two levels up.
        workdir = Path(channel_dir).parent.parent
        return _cp.load_policy(workdir)
    except Exception:  # noqa: BLE001 — fail-open to policy defaults
        return _cp.CoordinationPolicy()


def read_active_presence(channel_dir: Path, *, exclude_session: str,
                         reap: bool = True) -> list:
    """Live peers, excluding ``exclude_session`` and reader cursor stubs.
    Never locks.

    ``reap`` (default ``True``, preserving prior behavior) prunes adaptively
    stale presence in place (Rust-only physical unlink under full capability; a
    no-op otherwise). Pass ``reap=False`` for a strictly NON-MUTATING read:
    stale sessions are excluded via a dry-run (``apply=False``) instead of being
    unlinked, and the SHA cache is never written. Advisory read-only callers —
    e.g. the SessionStart peer-collision hook — MUST use ``reap=False`` so a hook
    can never mutate shared room state."""
    stale: set = set()
    if reap:
        reap_stale(channel_dir)
    else:
        stale = set(reap_stale(channel_dir, apply=False))
    out = []
    for _f, rec in _iter_presence(channel_dir):
        if rec.get("tool") == "reader":
            continue  # cursor stub is not a peer
        sid = rec.get("session_id")
        if sid != exclude_session and sid not in stale:
            out.append(rec)
    return out


def get_cursor(channel_dir: Path, session_id: str) -> dict:
    """Return this session's read cursor (zero-cursor if absent)."""
    try:
        rec = json.loads(
            _presence_path(channel_dir, session_id).read_text()
        )
        c = rec.get("cursor", {})
        return {
            "revision": int(c.get("revision", 0)),
            "changes_offset": int(c.get("changes_offset", 0)),
        }
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return dict(_ZERO_CURSOR)


def set_cursor(
    channel_dir: Path, session_id: str, *, revision: int, changes_offset: int
) -> None:
    """Advance this session's own cursor (preserves other fields).

    Pure readers (the SessionStart / pre-edit hooks) have no presence
    file of their own, yet their cursor MUST persist or every poll
    re-surfaces the same delta. So when no presence file exists we write
    a minimal cursor-only stub with ``heartbeat_ts: 0`` — the reaper
    treats it as long-stale (never a "live peer") and eventually cleans
    it, but the cursor survives between polls. Delta-only reads for
    readers are thus first-class, not a special case.
    """
    try:
        p = _presence_path(channel_dir, session_id)
        try:
            rec = json.loads(p.read_text())
        except (FileNotFoundError, OSError, ValueError):
            rec = {
                "session_id": session_id,
                "tool": "reader",
                "model": "n/a",
                "run_id": "n/a",
                "app_slug": "",
                "phase": "reader",
                "files_in_flight": [],
                "heartbeat_ts": 0,  # never counts as a live peer
            }
        rec["cursor"] = {
            "revision": int(revision),
            "changes_offset": int(changes_offset),
        }
        _atomic_write(p, rec)
    except (OSError, ValueError, TypeError):
        return
