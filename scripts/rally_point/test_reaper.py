#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Rally Point Python fallback reaper (reaper.py) and heartbeat parity.

Run via:
    uv run --with pytest python -m pytest scripts/rally_point/test_reaper.py -v
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

# Make the rally_point package importable as scripts/rally_point/ when running
# from the repo root.
_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import decay as _decay
import presence as _presence
import leadership as _leadership
import reaper as _reaper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sessions_dir(channel_dir: Path) -> Path:
    d = channel_dir / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_presence_file(channel_dir: Path, session_id: str, hb_ts: float,
                          tool: str = "claude_code") -> Path:
    sd = _make_sessions_dir(channel_dir)
    p = sd / f"{session_id}.json"
    p.write_text(json.dumps({
        "session_id": session_id,
        "tool": tool,
        "model": "sonnet",
        "run_id": "test-run",
        "app_slug": "test-app",
        "phase": "execute",
        "files_in_flight": [],
        "heartbeat_ts": hb_ts,
        "last_seen": hb_ts,
        "pid": 12345,
        "host": "localhost",
        "cursor": {"revision": 0, "changes_offset": 0},
    }), encoding="utf-8")
    return p


def _write_lead_json(channel_dir: Path, lease_until: str,
                     session_id: str = "test-lead") -> Path:
    rd = channel_dir / "rally"
    rd.mkdir(parents=True, exist_ok=True)
    p = rd / "lead.json"
    p.write_text(json.dumps({
        "schema_version": "1.0",
        "run_id": "test-run",
        "lead": {
            "session_id": session_id,
            "tool": "claude_code",
            "model": "sonnet",
            "lease_until": lease_until,
            "renew_every_minutes": 15,
            "work_size": "large",
            "parent_lead": None,
            "max_direct_reports": 4,
            "current_reports": [],
            "owns": ["plan"],
        },
        "chunk_owners": {},
        "conflict_rule": "owner decides",
    }), encoding="utf-8")
    return p


def _write_claim_index(channel_dir: Path, claims: dict) -> Path:
    p = channel_dir / "claim-index.json"
    p.write_text(json.dumps({"claims": claims}), encoding="utf-8")
    return p


def _utc_iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# (a) Over-TTL presence file is physically unlinked by reaper apply
# ---------------------------------------------------------------------------

def test_presence_stale_file_unlinked(tmp_path):
    """An over-TTL presence file is physically deleted when apply=True."""
    channel_dir = tmp_path / "channel"
    now_ts = time.time()
    stale_ts = now_ts - (40 * 60)  # 40 min: past the default adaptive window (31 min)
    sess_file = _write_presence_file(channel_dir, "sess-stale", stale_ts)

    report = _reaper.reap_channel(
        channel_dir, tmp_path, apply=True, now=now_ts
    )
    assert "sess-stale" in report["presence_reaped"]
    assert not sess_file.exists(), "stale presence file must be physically unlinked"


def test_presence_fresh_file_kept(tmp_path):
    """A fresh presence file is NOT unlinked."""
    channel_dir = tmp_path / "channel"
    now_ts = time.time()
    fresh_ts = now_ts - 60  # 1 minute old — well within 15-min window
    sess_file = _write_presence_file(channel_dir, "sess-fresh", fresh_ts)

    report = _reaper.reap_channel(
        channel_dir, tmp_path, apply=True, now=now_ts
    )
    assert "sess-fresh" not in report["presence_reaped"]
    assert sess_file.exists(), "fresh presence file must survive"


def test_presence_dry_run_no_unlink(tmp_path):
    """Dry-run reports stale sessions but does NOT unlink."""
    channel_dir = tmp_path / "channel"
    now_ts = time.time()
    stale_ts = now_ts - (40 * 60)
    sess_file = _write_presence_file(channel_dir, "sess-dry", stale_ts)

    report = _reaper.reap_channel(
        channel_dir, tmp_path, apply=False, now=now_ts
    )
    # dry-run: file still present
    assert sess_file.exists(), "dry-run must not unlink"
    assert report["applied"] is False


# ---------------------------------------------------------------------------
# (b) Expired claim-index entry is removed (Python store path)
# ---------------------------------------------------------------------------

def test_claims_expired_removed_python_store(tmp_path):
    """An expired claim is removed from claim-index.json when Python owns the store."""
    channel_dir = tmp_path / "channel"
    channel_dir.mkdir(parents=True, exist_ok=True)
    now_dt = _now_utc()
    expired_at = _utc_iso(now_dt - timedelta(minutes=5))
    _write_claim_index(channel_dir, {
        "claim-expired": {
            "claim_id": "claim-expired",
            "owner_tool": "claude_code",
            "raw_scope": "src/foo.py",
            "resource_scopes": ["src/foo.py"],
            "lease_expires_at": expired_at,
        }
    })

    # Force resolved_via to something that is NOT repo-local-rally-cli
    with mock.patch.object(_reaper, "_resolve") as mock_resolve:
        env_mock = mock.MagicMock()
        env_mock.resolved_via = "build-loop-internal"
        mock_resolve.return_value = env_mock
        report = _reaper.reap_channel(
            channel_dir, tmp_path, apply=True, now=now_dt.timestamp()
        )

    assert "claim-expired" in report["claims_reaped"]
    data = json.loads((channel_dir / "claim-index.json").read_text())
    assert "claim-expired" not in data["claims"], "expired claim must be removed from file"


# ---------------------------------------------------------------------------
# (c) Claim with missing/unparseable lease_expires_at is NEVER removed (fail-closed)
# ---------------------------------------------------------------------------

def test_claims_missing_lease_kept(tmp_path):
    """A claim without a lease_expires_at is NEVER removed (fail-closed)."""
    channel_dir = tmp_path / "channel"
    channel_dir.mkdir(parents=True, exist_ok=True)
    _write_claim_index(channel_dir, {
        "claim-no-lease": {
            "claim_id": "claim-no-lease",
            "owner_tool": "codex",
            "raw_scope": "src/bar.py",
            "resource_scopes": ["src/bar.py"],
            # lease_expires_at intentionally absent
        }
    })

    with mock.patch.object(_reaper, "_resolve") as mock_resolve:
        env_mock = mock.MagicMock()
        env_mock.resolved_via = "build-loop-internal"
        mock_resolve.return_value = env_mock
        now_dt = _now_utc()
        report = _reaper.reap_channel(
            channel_dir, tmp_path, apply=True, now=now_dt.timestamp()
        )

    assert "claim-no-lease" not in report["claims_reaped"]
    data = json.loads((channel_dir / "claim-index.json").read_text())
    assert "claim-no-lease" in data["claims"], "claim with missing lease must survive"


def test_claims_unparseable_lease_kept(tmp_path):
    """A claim with an unparseable lease_expires_at is NEVER removed (fail-closed)."""
    channel_dir = tmp_path / "channel"
    channel_dir.mkdir(parents=True, exist_ok=True)
    _write_claim_index(channel_dir, {
        "claim-bad-ts": {
            "claim_id": "claim-bad-ts",
            "owner_tool": "claude_code",
            "raw_scope": "src/baz.py",
            "resource_scopes": ["src/baz.py"],
            "lease_expires_at": "not-a-real-timestamp",
        }
    })

    with mock.patch.object(_reaper, "_resolve") as mock_resolve:
        env_mock = mock.MagicMock()
        env_mock.resolved_via = "build-loop-internal"
        mock_resolve.return_value = env_mock
        now_dt = _now_utc()
        report = _reaper.reap_channel(
            channel_dir, tmp_path, apply=True, now=now_dt.timestamp()
        )

    assert "claim-bad-ts" not in report["claims_reaped"]


# ---------------------------------------------------------------------------
# (d) Future-dated claim is KEPT
# ---------------------------------------------------------------------------

def test_claims_future_lease_kept(tmp_path):
    """A claim with a future lease is always kept."""
    channel_dir = tmp_path / "channel"
    channel_dir.mkdir(parents=True, exist_ok=True)
    future_at = _utc_iso(_now_utc() + timedelta(hours=2))
    _write_claim_index(channel_dir, {
        "claim-future": {
            "claim_id": "claim-future",
            "owner_tool": "claude_code",
            "raw_scope": "src/future.py",
            "resource_scopes": ["src/future.py"],
            "lease_expires_at": future_at,
        }
    })

    with mock.patch.object(_reaper, "_resolve") as mock_resolve:
        env_mock = mock.MagicMock()
        env_mock.resolved_via = "build-loop-internal"
        mock_resolve.return_value = env_mock
        now_dt = _now_utc()
        report = _reaper.reap_channel(
            channel_dir, tmp_path, apply=True, now=now_dt.timestamp()
        )

    assert "claim-future" not in report["claims_reaped"]
    data = json.loads((channel_dir / "claim-index.json").read_text())
    assert "claim-future" in data["claims"]


# ---------------------------------------------------------------------------
# (e) lead.json: expired → deleted; valid → kept
# ---------------------------------------------------------------------------

def test_lead_expired_deleted(tmp_path):
    """An expired lead.json is deleted when apply=True."""
    channel_dir = tmp_path / "channel"
    past_iso = _utc_iso(_now_utc() - timedelta(minutes=10))
    lead_file = _write_lead_json(channel_dir, past_iso)

    now_ts = time.time()
    report = _reaper.reap_channel(
        channel_dir, tmp_path, apply=True, now=now_ts
    )
    assert report["lead_relinquished"] is True
    assert not lead_file.exists(), "expired lead.json must be deleted"


def test_lead_valid_kept(tmp_path):
    """A lead.json with a future lease_until is preserved."""
    channel_dir = tmp_path / "channel"
    future_iso = _utc_iso(_now_utc() + timedelta(hours=1))
    lead_file = _write_lead_json(channel_dir, future_iso)

    now_ts = time.time()
    report = _reaper.reap_channel(
        channel_dir, tmp_path, apply=True, now=now_ts
    )
    assert report["lead_relinquished"] is False
    assert lead_file.exists(), "valid lead.json must survive"


def test_lead_missing_lease_kept(tmp_path):
    """FAIL-CLOSED: a lead.json with no lead section is never removed."""
    channel_dir = tmp_path / "channel"
    rd = channel_dir / "rally"
    rd.mkdir(parents=True, exist_ok=True)
    lead_file = rd / "lead.json"
    lead_file.write_text(json.dumps({"schema_version": "1.0", "run_id": "test"}))

    now_ts = time.time()
    report = _reaper.reap_channel(
        channel_dir, tmp_path, apply=True, now=now_ts
    )
    assert report["lead_relinquished"] is False
    assert lead_file.exists()


# ---------------------------------------------------------------------------
# (f) Idempotent: second reap = no-op
# ---------------------------------------------------------------------------

def test_idempotent_second_reap(tmp_path):
    """Running reap_channel twice yields the same result — second is a no-op."""
    channel_dir = tmp_path / "channel"
    now_ts = time.time()
    stale_ts = now_ts - (40 * 60)
    sess_file = _write_presence_file(channel_dir, "sess-idem", stale_ts)

    report1 = _reaper.reap_channel(
        channel_dir, tmp_path, apply=True, now=now_ts
    )
    report2 = _reaper.reap_channel(
        channel_dir, tmp_path, apply=True, now=now_ts
    )
    assert "sess-idem" in report1["presence_reaped"]
    assert "sess-idem" not in report2["presence_reaped"]
    assert not sess_file.exists()


# ---------------------------------------------------------------------------
# (g) Parity: heartbeat_parity_vectors.json asserts decay.recency_weight
#     is tool-agnostic and matches expected values within 1e-4
# ---------------------------------------------------------------------------

def _vectors_path() -> Path:
    return _HERE / "heartbeat_parity_vectors.json"


def test_heartbeat_parity_vectors_file_exists():
    assert _vectors_path().exists(), "heartbeat_parity_vectors.json must be present"


def test_heartbeat_parity_weight_and_staleness():
    """For every case in heartbeat_parity_vectors.json:
    - decay.recency_weight(age_secs, half_life_secs) is within 1e-4 of expected_weight
    - staleness verdict (age > heartbeat_minutes*60) matches stale_at_15m
    - result is identical for tool_a and tool_b (tool-agnostic)
    """
    vectors = json.loads(_vectors_path().read_text())
    half_life_secs = vectors["half_life_secs"]
    heartbeat_minutes = vectors["heartbeat_minutes"]
    stale_cutoff_secs = heartbeat_minutes * 60

    for case in vectors["cases"]:
        age = case["age_secs"]
        expected_weight = case["expected_weight"]
        stale_at_15m = case["stale_at_15m"]

        # Weight check (tool-agnostic — same computation regardless of tool)
        w = _decay.recency_weight(age, half_life_secs)
        assert abs(w - expected_weight) < 1e-4, (
            f"age={age}: recency_weight={w:.6f} != expected={expected_weight:.6f} "
            f"(delta={abs(w - expected_weight):.2e})"
        )

        # Staleness verdict
        is_stale = age > stale_cutoff_secs
        assert is_stale == stale_at_15m, (
            f"age={age}: stale verdict {is_stale} != expected {stale_at_15m}"
        )


def test_heartbeat_parity_tool_agnostic():
    """Parity proof: tool_a and tool_b get identical weight (tool field is irrelevant
    to the decay computation)."""
    vectors = json.loads(_vectors_path().read_text())
    half_life_secs = vectors["half_life_secs"]
    for case in vectors["cases"]:
        age = case["age_secs"]
        # Both tools → same age → same weight
        w_a = _decay.recency_weight(age, half_life_secs)
        w_b = _decay.recency_weight(age, half_life_secs)
        assert w_a == w_b, f"tool parity broken at age={age}"


# ---------------------------------------------------------------------------
# (h) Rust-owned store: claims deferred, not physically removed
# ---------------------------------------------------------------------------

def test_claims_deferred_when_rust_owns_store(tmp_path):
    """When resolved_via == repo-local-rally-cli, expired claims are deferred to Rust."""
    channel_dir = tmp_path / "channel"
    channel_dir.mkdir(parents=True, exist_ok=True)
    now_dt = _now_utc()
    expired_at = _utc_iso(now_dt - timedelta(minutes=5))
    _write_claim_index(channel_dir, {
        "claim-rust": {
            "claim_id": "claim-rust",
            "owner_tool": "claude_code",
            "raw_scope": "src/x.py",
            "resource_scopes": ["src/x.py"],
            "lease_expires_at": expired_at,
        }
    })

    with mock.patch.object(_reaper, "_resolve") as mock_resolve:
        env_mock = mock.MagicMock()
        env_mock.resolved_via = "repo-local-rally-cli"
        mock_resolve.return_value = env_mock
        report = _reaper.reap_channel(
            channel_dir, tmp_path, apply=True, now=now_dt.timestamp()
        )

    assert report["claims_deferred_to_rust"] == 1
    assert "claim-rust" not in report["claims_reaped"]
    # claim-index.json must NOT have been rewritten
    data = json.loads((channel_dir / "claim-index.json").read_text())
    assert "claim-rust" in data["claims"], "Rust-owned claims must not be physically removed"


# ---------------------------------------------------------------------------
# (i) reap_stale apply=False dry-run does not unlink (presence.py change)
# ---------------------------------------------------------------------------

def test_presence_reap_stale_dry_run(tmp_path):
    """presence.reap_stale(channel_dir, apply=False) returns IDs without unlinking."""
    channel_dir = tmp_path / "channel"
    now_ts = time.time()
    stale_ts = now_ts - (40 * 60)
    sess_file = _write_presence_file(channel_dir, "sess-p-dry", stale_ts)

    with mock.patch("time.time", return_value=now_ts):
        reaped = _presence.reap_stale(channel_dir, apply=False)

    assert "sess-p-dry" in reaped
    assert sess_file.exists(), "apply=False must not unlink"


def test_presence_reap_stale_apply_true_unlinks(tmp_path):
    """presence.reap_stale(channel_dir, apply=True) [default] physically unlinks."""
    channel_dir = tmp_path / "channel"
    now_ts = time.time()
    stale_ts = now_ts - (40 * 60)
    sess_file = _write_presence_file(channel_dir, "sess-p-apply", stale_ts)

    with mock.patch("time.time", return_value=now_ts):
        reaped = _presence.reap_stale(channel_dir, apply=True)

    assert "sess-p-apply" in reaped
    assert not sess_file.exists()
