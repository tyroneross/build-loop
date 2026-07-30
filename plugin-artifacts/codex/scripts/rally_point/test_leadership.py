# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/rally_point/leadership.py — the lead lease (G1).

Covers:
  - claim on an empty channel succeeds
  - a second claim while the lease is valid returns the incumbent
  - takeover succeeds once the lease has expired
  - renew extends the lease; renew from a non-lead is rejected
  - transfer hands the lead over; transfer from a non-lead is rejected
  - relinquish frees the lead so the next claim succeeds immediately
  - two concurrent claim_lead() processes -> exactly one wins
  - rebuild_lead_from_changes reconstructs the lead from the changes tail
"""
from __future__ import annotations

import multiprocessing as mp
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import capability as cap  # noqa: E402
import changes  # noqa: E402
import leadership as ld  # noqa: E402

_APP = "example-app"


@pytest.fixture(autouse=True)
def _force_full_capability(monkeypatch):
    """Reclaim (taking a peer's lease) is Rust-only: claim_lead reclaims only at
    full capability. These tests exercise the reclaim ACTION, so force full
    capability rather than depend on a live Rust binary on the runner. The
    refuse-below-full contract has its own dedicated test below."""
    monkeypatch.setattr(cap, "full_capability_for_channel", lambda *_a, **_k: True)


@pytest.fixture()
def channel(tmp_path: Path) -> Path:
    d = tmp_path / "channel"
    d.mkdir()
    return d


def _claim(channel: Path, session: str, **kw):
    return ld.claim_lead(
        channel,
        run_id=kw.get("run_id", "run-test"),
        session_id=session,
        tool=kw.get("tool", "claude_code"),
        model=kw.get("model", "claude-opus-4-7"),
        app_slug=_APP,
        renew_every_minutes=kw.get("renew_every_minutes", 15),
        now=kw.get("now"),
    )


def test_claim_on_empty_channel_succeeds(channel: Path):
    result = _claim(channel, "claude-r1")
    assert result["claimed"] is True
    assert result["lead"]["lead"]["session_id"] == "claude-r1"
    assert ld.is_lease_valid(channel) is True


def test_second_claim_while_valid_returns_incumbent(channel: Path):
    _claim(channel, "claude-r1")
    result = _claim(channel, "codex-r1")
    assert result["claimed"] is False
    assert result["lead"]["lead"]["session_id"] == "claude-r1"


def test_takeover_after_lease_expires(channel: Path):
    first = _claim(channel, "claude-r1", renew_every_minutes=15)
    expiry = ld._parse_iso(first["lead"]["lead"]["lease_until"])
    after = expiry + timedelta(minutes=1)
    result = _claim(channel, "codex-r1", now=after)
    assert result["claimed"] is True
    assert result["lead"]["lead"]["session_id"] == "codex-r1"


def test_reclaim_refused_below_full_capability(channel: Path, monkeypatch):
    """RUST-ONLY guard: taking a peer's EXPIRED lease (reclaim) is refused below
    full capability — a degraded session must never reclaim a peer's lease. The
    empty-seat claim and the incumbent stay intact."""
    monkeypatch.setattr(cap, "full_capability_for_channel", lambda *_a, **_k: False)
    first = _claim(channel, "claude-r1", renew_every_minutes=15)
    assert first["claimed"] is True  # empty-seat claim still works degraded
    expiry = ld._parse_iso(first["lead"]["lead"]["lease_until"])
    after = expiry + timedelta(minutes=1)
    result = _claim(channel, "codex-r1", now=after)
    assert result["claimed"] is False
    assert result.get("reclaimed") is False
    assert result.get("coordination_unavailable") == "no_binary"
    # The incumbent's lease is untouched — no shadow reclaim happened.
    assert ld.read_lead(channel)["lead"]["session_id"] == "claude-r1"


def test_empty_seat_claim_allowed_below_full_capability(channel: Path, monkeypatch):
    """Seeding an EMPTY lead seat is breadcrumb-class (not a peer-destructive
    reclaim), so it is allowed even below full capability."""
    monkeypatch.setattr(cap, "full_capability_for_channel", lambda *_a, **_k: False)
    result = _claim(channel, "claude-r1")
    assert result["claimed"] is True
    assert result["lead"]["lead"]["session_id"] == "claude-r1"


def test_renew_extends_lease(channel: Path):
    first = _claim(channel, "claude-r1", renew_every_minutes=15)
    old_expiry = ld._parse_iso(first["lead"]["lead"]["lease_until"])
    later = old_expiry - timedelta(minutes=1)
    result = ld.renew_lease(
        channel, session_id="claude-r1", app_slug=_APP, now=later
    )
    assert result["renewed"] is True
    new_expiry = ld._parse_iso(result["lead"]["lead"]["lease_until"])
    assert new_expiry > old_expiry


def test_renew_from_non_lead_rejected(channel: Path):
    _claim(channel, "claude-r1")
    result = ld.renew_lease(channel, session_id="codex-r1", app_slug=_APP)
    assert result["renewed"] is False
    assert result["reason"] == "not_lead"


def test_transfer_hands_lead_over(channel: Path):
    _claim(channel, "claude-r1")
    result = ld.transfer_lead(
        channel,
        from_session_id="claude-r1",
        to_session_id="codex-r1",
        to_tool="codex",
        to_model="gpt-5",
        app_slug=_APP,
    )
    assert result["transferred"] is True
    assert ld.read_lead(channel)["lead"]["session_id"] == "codex-r1"
    assert ld.read_lead(channel)["lead"]["tool"] == "codex"


def test_transfer_from_non_lead_rejected(channel: Path):
    _claim(channel, "claude-r1")
    result = ld.transfer_lead(
        channel,
        from_session_id="codex-r1",
        to_session_id="someone",
        to_tool="codex",
        to_model="gpt-5",
        app_slug=_APP,
    )
    assert result["transferred"] is False
    assert result["reason"] == "not_lead"


def test_relinquish_frees_lead(channel: Path):
    _claim(channel, "claude-r1")
    rel = ld.relinquish_lead(channel, session_id="claude-r1", app_slug=_APP)
    assert rel["relinquished"] is True
    assert ld.read_lead(channel) is None
    # next claim succeeds immediately, no expiry wait
    result = _claim(channel, "codex-r1")
    assert result["claimed"] is True


def test_relinquish_from_non_lead_rejected(channel: Path):
    _claim(channel, "claude-r1")
    rel = ld.relinquish_lead(channel, session_id="codex-r1", app_slug=_APP)
    assert rel["relinquished"] is False
    assert ld.read_lead(channel) is not None


def _claim_worker(channel_str: str, session: str, queue):
    import leadership as _ld  # re-import in child

    res = _ld.claim_lead(
        Path(channel_str),
        run_id="run-race",
        session_id=session,
        tool="claude_code",
        model="m",
        app_slug=_APP,
    )
    queue.put((session, res["claimed"]))


def test_concurrent_claim_exactly_one_wins(channel: Path):
    queue: mp.Queue = mp.Queue()
    procs = [
        mp.Process(target=_claim_worker, args=(str(channel), f"s{i}", queue))
        for i in range(6)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=10)
    results = [queue.get() for _ in procs]
    winners = [s for s, claimed in results if claimed]
    assert len(winners) == 1, f"expected exactly one winner, got {winners}"
    # the lead.json must name the single winner
    assert ld.read_lead(channel)["lead"]["session_id"] == winners[0]


def test_rebuild_lead_from_changes(channel: Path):
    _claim(channel, "claude-r1")
    # delete the index; the durable changes.jsonl trail survives
    ld.lead_path(channel).unlink()
    assert ld.read_lead(channel) is None
    rebuilt = ld.rebuild_lead_from_changes(channel)
    assert rebuilt is not None
    assert rebuilt["lead"]["session_id"] == "claude-r1"


def test_rebuild_returns_none_after_relinquish(channel: Path):
    _claim(channel, "claude-r1")
    ld.relinquish_lead(channel, session_id="claude-r1", app_slug=_APP)
    assert ld.rebuild_lead_from_changes(channel) is None


# --------------------------------------------------------------------------
# Size-scaled auto-reclaim + fail-closed (Feature B)
# --------------------------------------------------------------------------

def _claim_sized(channel: Path, session: str, *, work_size=None, effort=None,
                 owns=None, now=None, workdir=None):
    return ld.claim_lead(
        channel,
        run_id="run-test",
        session_id=session,
        tool="claude_code",
        model="claude-opus",
        app_slug=_APP,
        work_size=work_size,
        effort=effort,
        owns=owns,
        now=now,
        workdir=workdir,
    )


def test_small_claim_lease_window_is_30m(channel: Path):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    r = _claim_sized(channel, "s1", work_size="small", now=base)
    expiry = ld._parse_iso(r["lead"]["lead"]["lease_until"])
    assert (expiry - base) == timedelta(minutes=30)
    assert r["lead"]["lead"]["work_size"] == "small"


def test_large_claim_lease_window_is_2h(channel: Path):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    r = _claim_sized(channel, "l1", work_size="large", now=base)
    expiry = ld._parse_iso(r["lead"]["lead"]["lease_until"])
    assert (expiry - base) == timedelta(hours=2)


def test_small_claim_reclaimable_just_over_not_just_under(channel: Path):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _claim_sized(channel, "owner", work_size="small", now=base)
    # 29m later: NOT reclaimable
    under = base + timedelta(minutes=29)
    r_under = _claim_sized(channel, "peer", work_size="small", now=under)
    assert r_under["claimed"] is False, "single-file lease not expired at 29m"
    # 31m later: reclaimable
    over = base + timedelta(minutes=31)
    r_over = _claim_sized(channel, "peer", work_size="small", now=over)
    assert r_over["claimed"] is True, "single-file lease reclaimable at 31m"
    assert r_over.get("reclaimed") is True


def test_large_claim_not_reclaimable_at_31m(channel: Path):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _claim_sized(channel, "owner", work_size="large", now=base)
    over_small = base + timedelta(minutes=31)
    r = _claim_sized(channel, "peer", work_size="large", now=over_small)
    assert r["claimed"] is False, "multi-file/coarse lease still valid at 31m"
    # but reclaimable past 2h
    over_large = base + timedelta(minutes=121)
    r2 = _claim_sized(channel, "peer", work_size="large", now=over_large)
    assert r2["claimed"] is True


def test_reclaim_emits_reclaim_record_with_provenance(channel: Path):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _claim_sized(channel, "owner-sess", work_size="small", now=base)
    over = base + timedelta(minutes=31)
    _claim_sized(channel, "peer-sess", work_size="small", now=over)
    # Read the durable changes trail: a lead-reclaim record must exist with
    # the reason + prior owner provenance.
    recs, _ = changes.read_changes_since(channel, 0)
    reclaims = [r for r in recs if r.get("kind") == "lead-reclaim"]
    assert reclaims, "a lead-reclaim record must be posted on auto-reclaim"
    payload = reclaims[-1].get("payload", {})
    assert payload.get("reclaim_reason") == "stale-by-timeout"
    assert payload.get("work_size") == "small"
    assert payload.get("reclaimed_from_session") == "owner-sess"


def test_malformed_lease_is_fail_closed_not_reclaimable(channel: Path):
    # An incumbent with a garbage lease_until must NEVER be auto-reclaimed.
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _claim_sized(channel, "owner", work_size="small", now=base)
    # Corrupt the on-disk lease_until.
    doc = ld.read_lead(channel)
    doc["lead"]["lease_until"] = "not-a-timestamp"
    ld._atomic_write(ld.lead_path(channel), doc)
    # Far in the future — would be reclaimable if parse succeeded.
    way_later = base + timedelta(days=30)
    r = _claim_sized(channel, "peer", work_size="small", now=way_later)
    assert r["claimed"] is False, "fail-closed: malformed lease never reclaimed"
    # incumbent unchanged
    assert ld.read_lead(channel)["lead"]["session_id"] == "owner"


def test_effort_grade_sizes_lease(channel: Path):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    r = _claim_sized(channel, "e1", effort="XS", now=base)
    expiry = ld._parse_iso(r["lead"]["lead"]["lease_until"])
    assert (expiry - base) == timedelta(minutes=30), "XS -> small (30m)"


def test_no_size_signal_preserves_renew_window(channel: Path):
    # Backward compat: no work_size/effort -> lease window stays renew cadence.
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    r = ld.claim_lead(
        channel, run_id="r", session_id="x", tool="t", model="m",
        app_slug=_APP, renew_every_minutes=15, now=base,
    )
    expiry = ld._parse_iso(r["lead"]["lead"]["lease_until"])
    assert (expiry - base) == timedelta(minutes=15), "no size signal keeps 15m window"
