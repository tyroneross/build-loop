"""Dry-run tests for the per-commit dispatch loop.

Pure stdlib. The real dispatcher (skill body) fires `Agent(...)` once per
commit; here we replace that with a `DispatchRecorder` to lock in topological
order, deterministic tie-break, packet self-containment, partial-failure
behavior, and `prior_hashes` accumulation. No Agent calls fire here. The
contract matches `skills/build-loop/SKILL.md` §"Per-Commit Mode" step 2.
"""
from __future__ import annotations

import pytest

PACKET_KEYS = {"commit_id", "subject", "spec", "files_planned", "prior_hashes"}


# ---------- recorder + dry-run dispatcher under test -----------------------

class DispatchRecorder:
    """Stand-in for `Agent(subagent_type='build-loop:build-orchestrator', ...)`.

    Records each (commit_id, packet, prior_hashes) call and returns a deterministic
    fake commit hash unless the call matches `fail_on`.
    """

    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.calls = []  # ordered list of (commit_id, packet, prior_hashes)

    def dispatch(self, commit_id, packet, prior_hashes):
        self.calls.append((commit_id, dict(packet), list(prior_hashes)))
        if commit_id == self.fail_on:
            return {"hash": None, "status": "failed"}
        # Deterministic fake hash derived from commit_id
        return {"hash": f"hash_{commit_id}", "status": "ok"}


def _topological_order(commits):
    """Return commits ordered by depends_on; ties broken alphabetically by id."""
    by_id = {c["id"]: c for c in commits}
    pending = {c["id"] for c in commits}
    ordered = []
    while pending:
        ready = sorted(cid for cid in pending if all(d not in pending for d in by_id[cid]["depends_on"]))
        if not ready:
            raise ValueError("cycle or unresolved dependency in plan")
        for cid in ready:
            ordered.append(by_id[cid])
            pending.remove(cid)
    return ordered


def _build_packet(commit, prior_hashes):
    """Self-contained packet — only `prior_hashes` carries cross-commit info."""
    return {
        "commit_id": commit["id"],
        "subject": commit["subject"],
        "spec": commit["spec"],
        "files_planned": list(commit["files_planned"]),
        "prior_hashes": list(prior_hashes),
    }


def dry_run_dispatch(plan, recorder, fail_on=None):
    """Walk the plan in topological order; stop on failure; return summary."""
    if fail_on is not None:
        recorder.fail_on = fail_on
    ordered = _topological_order(plan["commits"])
    completed = []
    prior_hashes = []
    failed = None
    queued_at_failure = []
    for idx, commit in enumerate(ordered):
        packet = _build_packet(commit, prior_hashes)
        result = recorder.dispatch(commit["id"], packet, prior_hashes)
        if result["status"] == "failed":
            failed = commit["id"]
            queued_at_failure = [c["id"] for c in ordered[idx + 1 :]]
            break
        completed.append(commit["id"])
        prior_hashes = prior_hashes + [result["hash"]]
    return {
        "completed": completed,
        "failed": failed,
        "queued_at_failure": queued_at_failure,
        "plan_path_retained": failed is not None,
    }


# ---------- fixtures --------------------------------------------------------

def _commit(cid, deps):
    return {"id": cid, "subject": f"s{cid[1:]}", "scope": "x",
            "files_planned": [f"{cid}.py"], "spec": f"do {cid}", "depends_on": list(deps)}


def _linear_plan():
    return {"run_id": "run_test_linear", "branch": "feat/x", "from_branch": "main",
            "commits": [_commit("c1", []), _commit("c2", ["c1"]), _commit("c3", ["c2"])]}


def _diamond_plan():
    return {"run_id": "run_test_diamond", "branch": "feat/y", "from_branch": "main",
            "commits": [_commit("c1", []), _commit("c2", []), _commit("c3", ["c1", "c2"])]}


# ---------- tests -----------------------------------------------------------

def test_linear_plan_full_success():
    plan = _linear_plan()
    rec = DispatchRecorder()
    result = dry_run_dispatch(plan, rec)
    assert [cid for cid, _pkt, _ph in rec.calls] == ["c1", "c2", "c3"]
    assert result["completed"] == ["c1", "c2", "c3"]
    assert result["failed"] is None
    assert result["queued_at_failure"] == []
    assert result["plan_path_retained"] is False


def test_linear_plan_fails_at_c2_stops_c3():
    plan = _linear_plan()
    rec = DispatchRecorder()
    result = dry_run_dispatch(plan, rec, fail_on="c2")
    assert [cid for cid, _pkt, _ph in rec.calls] == ["c1", "c2"]
    assert result["completed"] == ["c1"]
    assert result["failed"] == "c2"
    assert result["queued_at_failure"] == ["c3"]
    assert result["plan_path_retained"] is True


def test_diamond_plan_topological_order_alphabetical_tiebreak():
    plan = _diamond_plan()
    rec = DispatchRecorder()
    result = dry_run_dispatch(plan, rec)
    order = [cid for cid, _pkt, _ph in rec.calls]
    # c1 and c2 are both ready first; alphabetical tiebreak → c1 then c2; then c3
    assert order == ["c1", "c2", "c3"]
    assert result["completed"] == ["c1", "c2", "c3"]


def test_packet_is_self_contained():
    plan = _linear_plan()
    rec = DispatchRecorder()
    dry_run_dispatch(plan, rec)
    for commit_id, packet, _prior in rec.calls:
        # Exact key set — no leak of foreign-commit refs
        assert set(packet.keys()) == PACKET_KEYS
        # spec / subject / files_planned do not name OTHER commit ids
        other_ids = {c["id"] for c in plan["commits"]} - {commit_id}
        for field in ("subject", "spec"):
            for other in other_ids:
                assert other not in packet[field], (
                    f"packet for {commit_id} leaks foreign id {other!r} via {field}"
                )
        # prior_hashes carries only hashes (strings), not commit ids
        for h in packet["prior_hashes"]:
            assert isinstance(h, str)
            assert h.startswith("hash_")


def test_fail_on_first_commit_stops_everything():
    plan = _linear_plan()
    rec = DispatchRecorder()
    result = dry_run_dispatch(plan, rec, fail_on="c1")
    assert [cid for cid, _pkt, _ph in rec.calls] == ["c1"]
    assert result["completed"] == []
    assert result["failed"] == "c1"
    assert result["queued_at_failure"] == ["c2", "c3"]
    assert result["plan_path_retained"] is True


def test_empty_plan_no_dispatch():
    plan = {"run_id": "run_empty", "branch": "feat/z", "from_branch": "main", "commits": []}
    rec = DispatchRecorder()
    result = dry_run_dispatch(plan, rec)
    assert rec.calls == []
    assert result["completed"] == []
    assert result["failed"] is None
    assert result["plan_path_retained"] is False


def test_single_commit_plan_success():
    plan = {"run_id": "run_single", "branch": "feat/single", "from_branch": "main",
            "commits": [_commit("c1", [])]}
    rec = DispatchRecorder()
    result = dry_run_dispatch(plan, rec)
    assert result["completed"] == ["c1"]
    assert result["failed"] is None
    assert rec.calls[0][2] == []  # no prior hashes for the very first commit


def test_prior_hashes_accumulate_through_chain():
    plan = _linear_plan()
    rec = DispatchRecorder()
    dry_run_dispatch(plan, rec)
    # c1 sees no prior hashes; c2 sees c1's; c3 sees both
    _, _pkt1, prior1 = rec.calls[0]
    _, _pkt2, prior2 = rec.calls[1]
    _, _pkt3, prior3 = rec.calls[2]
    assert prior1 == []
    assert prior2 == ["hash_c1"]
    assert prior3 == ["hash_c1", "hash_c2"]
