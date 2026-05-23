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
from datetime import timedelta
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import leadership as ld  # noqa: E402

_APP = "example-app"


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
