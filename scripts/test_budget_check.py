from __future__ import annotations

from datetime import datetime, timezone

import budget_check


NOW = datetime(2026, 8, 8, 5, 0, tzinfo=timezone.utc)


def state(*, start: str, deadline: str, last: str | None = None, commits: int = 0, mode: str = "default") -> dict:
    return {"execution": {"budget": {
        "mode": mode, "started_at": start, "deadline_at": deadline,
        "last_checkin_at": last, "commits_since_push": commits,
        "checkin_interval_pct": 50,
    }}}


def test_absent_state_degrades_to_continue() -> None:
    result = budget_check.compute_envelope(None, {}, now=NOW)
    assert result["action"] == "continue"
    assert result["budget_seconds"] == 0


def test_budget_continues_before_checkpoint() -> None:
    result = budget_check.compute_envelope(
        state(start="2026-08-08T04:30:00Z", deadline="2026-08-08T06:30:00Z"), {}, now=NOW
    )
    assert result["action"] == "continue"
    assert result["remaining_seconds"] == 5400


def test_budget_requests_checkin_at_configured_interval() -> None:
    result = budget_check.compute_envelope(
        state(start="2026-08-08T03:00:00Z", deadline="2026-08-08T07:00:00Z"), {}, now=NOW
    )
    assert result["action"] == "checkin"
    assert result["within_budget"] is True


def test_elapsed_budget_finalizes() -> None:
    result = budget_check.compute_envelope(
        state(start="2026-08-08T01:00:00Z", deadline="2026-08-08T04:59:59Z"), {}, now=NOW
    )
    assert result["action"] == "finalize_and_stop"
    assert result["remaining_seconds"] == -1


def test_batch_signal_obeys_config() -> None:
    result = budget_check.compute_envelope(
        state(start="2026-08-08T04:30:00Z", deadline="2026-08-08T06:30:00Z", commits=2),
        {"batchSize": 2}, now=NOW,
    )
    assert result["should_push_now"] is True


def test_malformed_timestamps_fail_open_with_reason() -> None:
    result = budget_check.compute_envelope(
        state(start="not-a-date", deadline="also-not-a-date"), {}, now=NOW
    )
    assert result["action"] == "continue"
    assert "malformed" in result["reason"]
