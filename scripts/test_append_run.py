# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for append_run.py — canonical, atomic, Learn-visible run records."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent / "append_run.py"


def _run(workdir, *args, expect_ok=True):
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir), "--json", *args],
        capture_output=True, text=True,
    )
    if expect_ok:
        assert res.returncode == 0, res.stderr
        return json.loads(res.stdout)
    return res


def _state(workdir):
    return json.loads((workdir / ".build-loop" / "state.json").read_text())


def test_canonical_shape(tmp_path):
    out = _run(tmp_path, "--run-id", "r1", "--goal", "ship X", "--outcome", "done")
    assert out["action"] == "appended" and out["runs_count"] == 1
    rec = _state(tmp_path)["runs"][0]
    assert rec["outcome"] == "pass"           # done -> canonical pass
    assert isinstance(rec["phases"], dict)     # phases is a DICT, not a list (f3)
    for k in ("run_id", "date", "goal", "outcome", "filesTouched", "manualInterventions"):
        assert k in rec
    # validators.validate_entry must accept it (canonical contract)
    sys.path.insert(0, str(SCRIPT.parent))
    from write_run_entry.validators import validate_entry
    validate_entry(rec)


def test_outcome_mapping(tmp_path):
    _run(tmp_path, "--run-id", "r1", "--outcome", "blocked")
    assert _state(tmp_path)["runs"][0]["outcome"] == "fail"  # blocked -> fail


def test_phases_dict_and_manual_intervention(tmp_path):
    _run(tmp_path, "--run-id", "r3", "--outcome", "done",
         "--manual-intervention", "6:prompted for fable", "--phase", "4:fail", "--phase", "2:pass")
    rec = _state(tmp_path)["runs"][0]
    assert rec["phases"] == {"4": {"status": "fail"}, "2": {"status": "pass"}}
    assert rec["manualInterventions"] == [{"phase": "6", "note": "prompted for fable"}]


def test_idempotent_on_run_id(tmp_path):
    _run(tmp_path, "--run-id", "r1", "--goal", "v1", "--outcome", "done")
    out = _run(tmp_path, "--run-id", "r1", "--goal", "v2", "--outcome", "partial")
    assert out["action"] == "replaced" and out["runs_count"] == 1
    runs = _state(tmp_path)["runs"]
    assert len(runs) == 1 and runs[0]["goal"] == "v2" and runs[0]["outcome"] == "partial"


def test_preserves_other_state_keys(tmp_path):
    sp = tmp_path / ".build-loop" / "state.json"
    sp.parent.mkdir(parents=True)
    sp.write_text(json.dumps({"phase": "report", "execution": {"x": 1}, "runs": [{"run_id": "old", "source": "append_run"}]}))
    _run(tmp_path, "--run-id", "r2", "--outcome", "done")
    st = _state(tmp_path)
    assert st["phase"] == "report" and st["execution"] == {"x": 1}
    assert [r["run_id"] for r in st["runs"]] == ["old", "r2"]


def test_refuses_to_clobber_unparseable_state(tmp_path):  # f5
    sp = tmp_path / ".build-loop" / "state.json"
    sp.parent.mkdir(parents=True)
    sp.write_text("{ this is : not json,,, ")
    before = sp.read_bytes()
    res = _run(tmp_path, "--run-id", "r1", "--outcome", "done", expect_ok=False)
    assert res.returncode != 0 and "refusing to overwrite" in res.stderr
    assert sp.read_bytes() == before  # untouched


def test_refuses_to_replace_richer_orchestrator_record(tmp_path):  # f9
    sp = tmp_path / ".build-loop" / "state.json"
    sp.parent.mkdir(parents=True)
    sp.write_text(json.dumps({"runs": [{"run_id": "r1", "judge_decisions": [{"x": 1}]}]}))  # no source=append_run
    res = _run(tmp_path, "--run-id", "r1", "--outcome", "done", expect_ok=False)
    assert res.returncode != 0 and "refusing to overwrite a richer record" in res.stderr


def test_extra_json_cannot_override_identity(tmp_path):  # f9
    _run(tmp_path, "--run-id", "r1", "--outcome", "done",
         "--extra-json", json.dumps({"run_id": "HACK", "source": "spoof", "security_findings": [{"mapped_risk": "LLM01"}]}))
    rec = _state(tmp_path)["runs"][0]
    assert rec["run_id"] == "r1" and rec["source"] == "append_run"   # identity preserved
    assert rec["security_findings"][0]["mapped_risk"] == "LLM01"     # non-identity extra applied


# ---------------------------------------------------------------------------
# GAP-1 closure: a run cannot close with neither an auditor verdict nor a
# manifest saying one is owed. Each case below is a real run observed in
# RossLabs-AI-Assistant/.build-loop/state.json between 2026-07-30 and
# 2026-08-04, all of which closed with neither.
# ---------------------------------------------------------------------------

def _manifest(workdir):
    path = workdir / ".build-loop" / "owed-verification.json"
    return json.loads(path.read_text()) if path.exists() else None


def test_not_run_auditor_status_writes_owed_manifest(tmp_path):
    """The exact shape of bl-20260730T060344Z and bl-20260804T072147Z.

    Both honestly recorded `auditor_status: not-run:parent-must-dispatch` --
    the branch the orchestrator contract calls a MANDATORY manifest write --
    and neither wrote one, because "MANDATORY" was a sentence in a markdown
    file rather than a call.
    """
    _run(tmp_path, "--run-id", "r-notrun", "--outcome", "partial",
         "--extra-json", json.dumps({"auditor_status": "not-run:parent-must-dispatch"}))
    man = _manifest(tmp_path)
    assert man is not None, "a not-run auditor_status must leave a manifest on disk"
    assert "independent-auditor" in man["owed"]
    assert _state(tmp_path).get("review_incomplete") is True


def test_pending_hook_packet_is_not_a_verdict(tmp_path):
    """The shape of hook_20260721T071240Z and hook_20260803T180457Z.

    Six `independent-auditor-hook` rows, every one `verdict: pending`,
    `status: packet_emitted`. A packet is a request for a verdict, not a
    verdict, and `auditor_present` matched it on the judge_id substring alone.
    """
    _run(tmp_path, "--run-id", "r-pending", "--outcome", "partial",
         "--extra-json", json.dumps({"judge_decisions": [
             {"judge_id": "independent-auditor-hook", "verdict": "pending",
              "status": "packet_emitted"}]}))
    assert _manifest(tmp_path) is not None, (
        "a packet with no verdict must not satisfy the auditor requirement"
    )


def test_code_touching_run_without_a_verdict_owes_one(tmp_path):
    _run(tmp_path, "--run-id", "r-code", "--outcome", "done",
         "--files-touched", "core/thing.py,scripts/other.py")
    man = _manifest(tmp_path)
    assert man is not None and "independent-auditor" in man["owed"]


def test_a_real_verdict_owes_nothing(tmp_path):
    """The acquittal half. Without this the enforcement could owe on every run
    and still look identically green above."""
    _run(tmp_path, "--run-id", "r-ok", "--outcome", "done",
         "--files-touched", "core/thing.py",
         "--extra-json", json.dumps({"judge_decisions": [
             {"judge_id": "independent-auditor", "verdict": "approve"}]}))
    assert _manifest(tmp_path) is None, "a real verdict must not owe a manifest"
    assert _state(tmp_path).get("review_incomplete") is not True


def test_a_quiet_run_that_touched_nothing_owes_nothing(tmp_path):
    """Precision guard. A no-files run with no auditor engagement is not an
    escaped review, and flagging it would make review_incomplete meaningless --
    a noisy gate gets disabled, which is worse than no gate."""
    _run(tmp_path, "--run-id", "r-quiet", "--outcome", "partial")
    assert _manifest(tmp_path) is None


# ---------------------------------------------------------------------------
# E3 closure: append_run corroborates the caller's commit + goal instead of
# trusting them. The anchor is the real record written 2026-07-09, whose commit
# `6616b71` was reachable from neither the run's push range nor its branch.
# ---------------------------------------------------------------------------

def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _repo(tmp_path):
    """A two-commit repo. Returns [sha1, sha2] (full SHAs)."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    shas = []
    for i in range(2):
        (tmp_path / f"f{i}.txt").write_text(str(i))
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-q", "-m", f"c{i}")
        shas.append(_git(tmp_path, "rev-parse", "HEAD"))
    return shas


def test_unreachable_commit_is_recorded_as_pending(tmp_path):
    """The defect. A SHA that corroborates against nothing must not be written as
    if it did; `pending` is the honest value, and the refused SHA stays on the
    record so the wrong claim is auditable rather than erased."""
    _repo(tmp_path)
    out = _run(tmp_path, "--run-id", "r-bad", "--outcome", "done", "--commit", "6616b71")
    rec = _state(tmp_path)["runs"][0]
    assert rec["commit"] == "pending"
    assert rec["provenance"]["ok"] is False
    assert rec["provenance"]["supplied_commit"] == "6616b71"
    assert out["provenance"]["findings"][0]["code"] == "commit_unreachable"


def test_reachable_commit_is_written_through(tmp_path):
    """Acquittal. Without this the gate could downgrade every commit to pending
    and every assertion above would still pass."""
    shas = _repo(tmp_path)
    _run(tmp_path, "--run-id", "r-good", "--outcome", "done", "--commit", shas[-1])
    rec = _state(tmp_path)["runs"][0]
    assert rec["commit"] == shas[-1] and rec["provenance"]["ok"] is True


def test_derived_head_commit_is_written_through(tmp_path):
    """The default path: no --commit, so append_run derives HEAD itself. Its own
    derivation must not trip the gate it now runs."""
    shas = _repo(tmp_path)
    _run(tmp_path, "--run-id", "r-head", "--outcome", "done")
    rec = _state(tmp_path)["runs"][0]
    assert shas[-1].startswith(rec["commit"]) and rec["provenance"]["ok"] is True


def test_commit_outside_push_range_is_pending(tmp_path):
    shas = _repo(tmp_path)
    _run(tmp_path, "--run-id", "r-range", "--outcome", "done",
         "--commit", shas[0], "--push-range", f"{shas[0]}..{shas[1]}")
    assert _state(tmp_path)["runs"][0]["commit"] == "pending"


def test_strict_provenance_raises_instead_of_recording(tmp_path):
    """The review arm. A caller that would rather stop than record `pending`."""
    _repo(tmp_path)
    res = _run(tmp_path, "--run-id", "r-strict", "--outcome", "done",
               "--commit", "6616b71", "--strict-provenance", expect_ok=False)
    assert res.returncode != 0 and "run provenance rejected" in res.stderr
    assert not (tmp_path / ".build-loop" / "state.json").exists()


def test_extra_json_commit_is_validated_too(tmp_path):
    """--extra-json can set `commit`. Validating before the merge would leave the
    same defect reachable through the other door."""
    _repo(tmp_path)
    _run(tmp_path, "--run-id", "r-extra", "--outcome", "done",
         "--extra-json", json.dumps({"commit": "6616b71"}))
    assert _state(tmp_path)["runs"][0]["commit"] == "pending"


def test_extra_json_cannot_forge_provenance(tmp_path):
    """`provenance` is the gate's own verdict; a caller that could set it could
    certify its own SHA."""
    _repo(tmp_path)
    _run(tmp_path, "--run-id", "r-forge", "--outcome", "done", "--commit", "6616b71",
         "--extra-json", json.dumps({"provenance": {"ok": True, "findings": []}}))
    rec = _state(tmp_path)["runs"][0]
    assert rec["provenance"]["ok"] is False and rec["commit"] == "pending"


def test_goal_mismatch_warns_without_changing_the_record(tmp_path):
    shas = _repo(tmp_path)
    bl = tmp_path / ".build-loop"
    bl.mkdir(exist_ok=True)
    (bl / "intent.md").write_text("# Intent — Fix the retrospective pipeline\n")
    out = _run(tmp_path, "--run-id", "r-goal", "--outcome", "done",
               "--commit", shas[-1], "--goal", "Bump the dependency allowlist")
    rec = _state(tmp_path)["runs"][0]
    assert rec["commit"] == shas[-1], "a goal warning must not touch the commit"
    assert rec["goal"] == "Bump the dependency allowlist"
    assert out["provenance"]["ok"] is True
    assert out["provenance"]["findings"][0]["code"] == "goal_mismatch"


def test_non_git_workdir_still_records_the_run(tmp_path):
    """Every test above this block runs in a non-git tmp_path. The gate must not
    have made a run record conditional on a git checkout."""
    out = _run(tmp_path, "--run-id", "r-nogit", "--goal", "ship X", "--outcome", "done")
    assert out["action"] == "appended"
    assert _state(tmp_path)["runs"][0]["commit"] == ""
