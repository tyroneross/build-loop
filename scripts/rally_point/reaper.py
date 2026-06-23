#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rally Point fallback reaper — physical cleanup of over-TTL presence, claims, and lead.

This is the PYTHON FALLBACK path; when the Rust ``rally`` binary is present it
is canonical (``rally doctor --reap-stale [--apply]``). This reaper runs
opportunistically at session-start (wired via ``hooks/session-start-rally-point.sh``)
and is available as a standalone CLI for manual sweeps.

FAIL-CLOSED invariant: we NEVER reap any record whose ownership timestamp we
cannot unambiguously prove is over-TTL. Unparseable, missing, or future
timestamps are ALWAYS preserved.

Resolved-via rule for claims:
  - ``repo-local-rally-cli`` → Rust binary owns the ``claim-index.json`` projection;
    we reap PRESENCE + the Python lead.json only and report claim count as
    "deferred-to-rust". Physical rewrite of claim-index.json would fight the Rust
    projection, so we skip it.
  - Any other resolved_via → Python reaper physically rewrites claim-index.json,
    removing expired claims.

ReapReport dict shape::

    {
        "presence_reaped":        [session_id, ...],   # IDs physically unlinked
        "claims_reaped":          [claim_id, ...],      # IDs removed from claim-index.json
        "claims_deferred_to_rust": int,                 # count when Rust owns the store
        "lead_relinquished":      bool,                 # True when lead.json deleted
        "preserved":              int,                  # records kept (not eligible)
        "applied":                bool,                 # True = apply mode, False = dry-run
        "resolved_via":           str,                  # from discovery_bridge
    }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Package-relative imports with script-mode fallback
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_SCRIPTS = _HERE.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

try:
    from . import presence as _presence
    from . import leadership as _leadership
    from .discovery_bridge import resolve as _resolve
except ImportError:
    import presence as _presence  # type: ignore
    import leadership as _leadership  # type: ignore
    from discovery_bridge import resolve as _resolve  # type: ignore

_CLAIM_INDEX = "claim-index.json"
_RUST_RESOLVED_VIA = "repo-local-rally-cli"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_rfc3339(value: Any) -> datetime | None:
    """Parse an RFC3339/ISO-8601 UTC timestamp ending in 'Z'. FAIL-CLOSED: returns
    None on any parse failure so callers KEEP the record."""
    if not value or not isinstance(value, str):
        return None
    # datetime.fromisoformat handles most ISO-8601; Python <3.11 doesn't accept
    # trailing 'Z', so normalise it.
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _claim_index_path(channel_dir: Path) -> Path:
    return Path(channel_dir) / _CLAIM_INDEX


def _read_claim_index(channel_dir: Path) -> dict[str, Any] | None:
    """Read claim-index.json. Returns None when absent or invalid."""
    p = _claim_index_path(channel_dir)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_claim_index(channel_dir: Path, data: dict[str, Any]) -> None:
    """Atomic overwrite of claim-index.json."""
    p = _claim_index_path(channel_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / f".{p.name}.tmp.{os.getpid()}"
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(str(tmp), str(p))


# ---------------------------------------------------------------------------
# Core sweep functions
# ---------------------------------------------------------------------------

def _sweep_presence(channel_dir: Path, *, apply: bool, now_ts: float) -> tuple[list[str], int]:
    """Return (reaped_ids, preserved_count). Physically unlinks when apply=True."""
    window = _presence.heartbeat_minutes(channel_dir) * 60
    cutoff = now_ts - window
    reaped: list[str] = []
    preserved = 0
    for f, rec in _presence._iter_presence(channel_dir):
        if rec.get("tool") == "reader":
            # cursor stubs are kept (heartbeat_ts intentionally 0)
            preserved += 1
            continue
        hb = rec.get("heartbeat_ts", 0)
        try:
            hb_f = float(hb)
        except (TypeError, ValueError):
            # FAIL-CLOSED: unparseable heartbeat is kept
            preserved += 1
            continue
        if hb_f > 0 and hb_f < cutoff:
            if apply:
                try:
                    f.unlink()
                except OSError:
                    preserved += 1
                    continue
            reaped.append(rec.get("session_id", f.stem))
        else:
            preserved += 1
    return reaped, preserved


def _sweep_claims(
    channel_dir: Path, *, apply: bool, now_dt: datetime
) -> tuple[list[str], int, int]:
    """Return (reaped_ids, preserved_count, deferred_count_if_not_our_store).

    ``deferred_count`` is non-zero only when resolved_via == rust; in that path
    we report how many expired claims we found but did NOT reap. When we OWN
    the store we reap them physically (apply=True) or count them (dry-run).
    """
    data = _read_claim_index(channel_dir)
    if data is None:
        return [], 0, 0
    claims = data.get("claims")
    if not isinstance(claims, dict):
        return [], 0, 0

    reaped: list[str] = []
    kept: list[str] = []
    for claim_id, claim in claims.items():
        if not isinstance(claim, dict):
            kept.append(claim_id)
            continue
        expires = _parse_rfc3339(claim.get("lease_expires_at"))
        if expires is None:
            # FAIL-CLOSED: missing/unparseable → keep
            kept.append(claim_id)
            continue
        if expires <= now_dt:
            reaped.append(claim_id)
        else:
            kept.append(claim_id)

    if apply and reaped:
        new_claims = {k: v for k, v in claims.items() if k not in set(reaped)}
        _write_claim_index(channel_dir, {"claims": new_claims})

    return reaped, len(kept), 0


def _sweep_lead(channel_dir: Path, *, apply: bool, now_dt: datetime) -> bool:
    """Delete lead.json when _reclaimable (expired + parseable). Returns True if deleted."""
    doc = _leadership.read_lead(channel_dir)
    if doc is None:
        return False
    if not _leadership._reclaimable(doc, now_dt):
        return False
    if apply:
        def _do() -> bool:
            # Re-read under lock to avoid TOCTOU
            inner_doc = _leadership.read_lead(channel_dir)
            if inner_doc is None:
                return False
            if not _leadership._reclaimable(inner_doc, now_dt):
                return False
            try:
                _leadership.lead_path(channel_dir).unlink()
                return True
            except OSError:
                return False
        return bool(_leadership._with_lock(channel_dir, _do))
    # dry-run: just report eligibility
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reap_channel(
    channel_dir: Path | str,
    workdir: Path | str,
    *,
    apply: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Sweep a Rally Point channel for over-TTL presence, claims, and lead.

    Parameters
    ----------
    channel_dir:
        The channel directory (resolved via discovery_bridge by the caller).
    workdir:
        Repo root (used for resolved_via lookup and coordination_policy).
    apply:
        ``False`` (default) = dry-run; ``True`` = physically remove.
    now:
        Injectable wall-clock (seconds since epoch). Defaults to
        ``time.time()`` / ``datetime.now(utc)`` for deterministic tests.

    Returns
    -------
    ReapReport dict.
    """
    channel_dir = Path(channel_dir)
    workdir = Path(workdir)
    now_ts: float = now if now is not None else time.time()
    now_dt: datetime = datetime.fromtimestamp(now_ts, tz=timezone.utc)

    # Resolve the channel so we know whether Rust owns the claim store.
    try:
        env = _resolve(workdir)
        resolved_via = env.resolved_via
    except Exception:
        # Fail-open on discovery: proceed as build-loop-internal
        resolved_via = "build-loop-internal"

    # 1. Presence sweep
    try:
        presence_reaped, presence_preserved = _sweep_presence(
            channel_dir, apply=apply, now_ts=now_ts
        )
    except Exception:
        presence_reaped, presence_preserved = [], 0

    # 2. Claims sweep
    claims_reaped: list[str] = []
    claims_deferred = 0
    claims_preserved = 0
    if resolved_via == _RUST_RESOLVED_VIA:
        # Rust owns the claim store; count but do NOT rewrite
        try:
            found_reaped, found_kept, _ = _sweep_claims(
                channel_dir, apply=False, now_dt=now_dt
            )
            claims_deferred = len(found_reaped)
            claims_preserved += found_kept
        except Exception:
            pass
    else:
        try:
            claims_reaped, claims_preserved, _ = _sweep_claims(
                channel_dir, apply=apply, now_dt=now_dt
            )
        except Exception:
            pass

    # 3. Lead sweep (race-safe via leadership._with_lock)
    lead_relinquished = False
    try:
        lead_relinquished = _sweep_lead(channel_dir, apply=apply, now_dt=now_dt)
    except Exception:
        pass

    preserved_total = presence_preserved + claims_preserved

    return {
        "presence_reaped": presence_reaped,
        "claims_reaped": claims_reaped,
        "claims_deferred_to_rust": claims_deferred,
        "lead_relinquished": lead_relinquished,
        "preserved": preserved_total,
        "applied": apply,
        "resolved_via": resolved_via,
    }


# ---------------------------------------------------------------------------
# CLI entrypoint (F1/F5 callable sweep)
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Rally Point stale-state reaper (Python fallback path). "
        "Dry-run by default; pass --apply to physically remove."
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="Repo root (default: cwd or WORKDIR env var)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Physically remove over-TTL records. Without this flag the report "
        "shows what WOULD be removed (dry-run).",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help="Emit the ReapReport as JSON on stdout.",
    )
    args = parser.parse_args(argv)

    workdir = Path(
        args.workdir
        or os.environ.get("WORKDIR", "")
        or os.getcwd()
    ).resolve()

    # Resolve channel_dir
    try:
        env = _resolve(workdir)
        channel_dir = Path(env.channel_dir)
        resolved_via = env.resolved_via
    except Exception as exc:
        if args.json_output:
            print(json.dumps({"error": str(exc), "applied": args.apply}))
        else:
            print(f"[reaper] channel resolution failed: {exc}", file=sys.stderr)
        sys.exit(0)  # fail-open: never crash a session

    report = reap_channel(
        channel_dir, workdir, apply=args.apply
    )

    if args.json_output:
        print(json.dumps(report, indent=2))
    else:
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"[reaper] {mode} via={resolved_via}")
        print(f"  presence_reaped:        {report['presence_reaped']}")
        print(f"  claims_reaped:          {report['claims_reaped']}")
        print(f"  claims_deferred_to_rust:{report['claims_deferred_to_rust']}")
        print(f"  lead_relinquished:      {report['lead_relinquished']}")
        print(f"  preserved:              {report['preserved']}")


if __name__ == "__main__":
    main()
