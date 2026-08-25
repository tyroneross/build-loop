#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/consent_conformance.py — the runner, not the implementation
it grades.

Scope: these tests verify the RUNNER's own mechanics (case loading, store
materialization, env isolation, grading logic) plus one end-to-end run against
the real Python implementation as a regression check. They do NOT re-derive
whether the contract itself is satisfied — that's what
`consent_conformance.py --impl python` running clean is for; see the
acceptance run recorded in the task report.

The one thing every test here shares: the real ~/.agent-consent store must
never be touched. `test_real_store_never_touched_by_full_suite_run` is the
load-bearing test for that guarantee.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import consent_conformance as cc  # noqa: E402

CASES_PATH = HERE.parent / "references" / "consent-conformance-cases.json"


# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------

def test_real_cases_file_loads_and_has_required_schema() -> None:
    cases = cc.load_cases(CASES_PATH)
    assert len(cases) >= 20, "expected the full conformance case set"
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    for c in cases:
        assert set(c["expect"].keys()) == {"allowed", "exit"}
        assert isinstance(c["expect"]["allowed"], bool)
        assert isinstance(c["expect"]["exit"], int)
        assert ":" in c["key"]
        assert c["description"], f"{c['id']}: empty description"
        assert c["contract_ref"], f"{c['id']}: empty contract_ref"


def test_load_cases_rejects_missing_required_field(tmp_path: Path) -> None:
    bad = tmp_path / "cases.json"
    bad.write_text(json.dumps({"cases": [{"id": "x"}]}))
    with pytest.raises(ValueError, match="missing fields"):
        cc.load_cases(bad)


def test_load_cases_rejects_duplicate_ids(tmp_path: Path) -> None:
    one = {
        "id": "dup", "description": "d", "contract_ref": "x", "store": None,
        "env": {}, "key": "build-loop:codex", "expect": {"allowed": False, "exit": 1},
    }
    bad = tmp_path / "cases.json"
    bad.write_text(json.dumps({"cases": [one, dict(one)]}))
    with pytest.raises(ValueError, match="duplicate case id"):
        cc.load_cases(bad)


def test_load_cases_rejects_key_without_colon(tmp_path: Path) -> None:
    bad_case = {
        "id": "x", "description": "d", "contract_ref": "x", "store": None,
        "env": {}, "key": "build-loop-codex", "expect": {"allowed": False, "exit": 1},
    }
    bad = tmp_path / "cases.json"
    bad.write_text(json.dumps({"cases": [bad_case]}))
    with pytest.raises(ValueError, match="product:vendor"):
        cc.load_cases(bad)


# ---------------------------------------------------------------------------
# Store materialization
# ---------------------------------------------------------------------------

def test_materialize_store_null_means_no_file(tmp_path: Path) -> None:
    p = tmp_path / "store.json"
    p.write_text("pre-existing content")  # should be removed
    cc.materialize_store(None, p)
    assert not p.exists()


def test_materialize_store_dict_writes_json(tmp_path: Path) -> None:
    p = tmp_path / "store.json"
    cc.materialize_store({"version": 2, "log": []}, p)
    data = json.loads(p.read_text())
    assert data == {"version": 2, "log": []}


def test_materialize_store_str_writes_raw_bytes(tmp_path: Path) -> None:
    p = tmp_path / "store.json"
    cc.materialize_store("{not valid json", p)
    assert p.read_text() == "{not valid json"


def test_materialize_store_empty_str_writes_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "store.json"
    cc.materialize_store("", p)
    assert p.exists()
    assert p.read_text() == ""


def test_materialize_store_rejects_unexpected_type(tmp_path: Path) -> None:
    p = tmp_path / "store.json"
    with pytest.raises(TypeError):
        cc.materialize_store(12345, p)


# ---------------------------------------------------------------------------
# Env isolation — the safety-critical piece
# ---------------------------------------------------------------------------

def test_build_env_forces_selftest_and_store_path(tmp_path: Path) -> None:
    store_path = tmp_path / "store.json"
    env = cc.build_env({}, store_path)
    assert env["AGENT_CONSENT_SELFTEST"] == "1"
    assert env["AGENT_CONSENT_STORE_PATH"] == str(store_path)


def test_build_env_never_points_at_real_home_store(tmp_path: Path) -> None:
    store_path = tmp_path / "store.json"
    env = cc.build_env({}, store_path)
    assert env["AGENT_CONSENT_STORE_PATH"] != str(cc.REAL_STORE)
    assert str(Path.home()) not in env["AGENT_CONSENT_STORE_PATH"] or str(tmp_path).startswith(str(Path.home()))
    # The store path must be exactly the tmp path handed in, never derived
    # from Path.home() by the runner itself.
    assert env["AGENT_CONSENT_STORE_PATH"] == str(store_path)


def test_build_env_strips_pytest_current_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "some::test (call)")
    env = cc.build_env({}, tmp_path / "s.json")
    assert "PYTEST_CURRENT_TEST" not in env


def test_build_env_clears_ambient_depth_when_case_does_not_specify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_DEPTH", "99")
    env = cc.build_env({}, tmp_path / "s.json")
    assert "AGENT_DISPATCH_DEPTH" not in env, (
        "an ambient depth value must not leak into a case that doesn't specify one"
    )


def test_build_env_applies_case_specified_depth(tmp_path: Path) -> None:
    env = cc.build_env({"AGENT_DISPATCH_DEPTH": "3"}, tmp_path / "s.json")
    assert env["AGENT_DISPATCH_DEPTH"] == "3"


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def _case(expect_allowed: bool, expect_exit: int) -> dict:
    return {"id": "x", "expect": {"allowed": expect_allowed, "exit": expect_exit}}


def test_grade_pass_when_exact_match() -> None:
    result = cc.AdapterResult(exit_code=0, allowed=True, raw_stdout="{}")
    graded = cc.grade(_case(True, 0), result)
    assert graded.passed


def test_grade_fail_on_exit_mismatch() -> None:
    result = cc.AdapterResult(exit_code=1, allowed=True, raw_stdout="{}")
    graded = cc.grade(_case(True, 0), result)
    assert not graded.passed
    assert "exit=1" in graded.detail


def test_grade_fail_on_allowed_mismatch() -> None:
    result = cc.AdapterResult(exit_code=0, allowed=False, raw_stdout="{}")
    graded = cc.grade(_case(True, 0), result)
    assert not graded.passed
    assert "allowed=False" in graded.detail


def test_grade_fail_on_adapter_error() -> None:
    result = cc.AdapterResult(exit_code=0, allowed=None, raw_stdout="not json", error="bad json")
    graded = cc.grade(_case(True, 0), result)
    assert not graded.passed
    assert "adapter error" in graded.detail


# ---------------------------------------------------------------------------
# End-to-end regression: the real Python implementation, full suite
# ---------------------------------------------------------------------------

def test_full_suite_passes_against_real_python_implementation() -> None:
    cases = cc.load_cases(CASES_PATH)
    adapter = cc.PythonCLIAdapter()
    results = cc.run_suite(adapter, cases)
    failed = [r for r in results if not r.passed]
    assert not failed, f"unexpected conformance failures: {[(r.case_id, r.detail) for r in failed]}"


def test_main_exits_zero_for_passing_suite(capsys: pytest.CaptureFixture) -> None:
    rc = cc.main(["--impl", "python", "--cases", str(CASES_PATH)])
    assert rc == 0


def test_main_json_output_is_well_formed(capsys: pytest.CaptureFixture) -> None:
    rc = cc.main(["--impl", "python", "--cases", str(CASES_PATH), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["passed"] == out["total"]
    assert out["failed"] == 0
    assert len(out["results"]) == out["total"]


def test_rust_adapter_is_an_unimplemented_seam() -> None:
    adapter = cc.RustAdapter()
    with pytest.raises(NotImplementedError):
        adapter.check("build-loop", "codex", {}, Path("/dev/null"))


# ---------------------------------------------------------------------------
# The load-bearing safety guarantee: real store never touched
# ---------------------------------------------------------------------------

def test_real_store_never_touched_by_full_suite_run() -> None:
    existed_before = cc.REAL_STORE.exists()
    stat_before = None
    if existed_before:
        st = cc.REAL_STORE.stat()
        stat_before = (st.st_mtime_ns, st.st_size)

    cases = cc.load_cases(CASES_PATH)
    adapter = cc.PythonCLIAdapter()
    cc.run_suite(adapter, cases)  # raises RuntimeError itself if the store moved

    existed_after = cc.REAL_STORE.exists()
    assert existed_after == existed_before, "real store existence changed during the suite run"
    if existed_after:
        st = cc.REAL_STORE.stat()
        stat_after = (st.st_mtime_ns, st.st_size)
        assert stat_after == stat_before, "real store content changed during the suite run"


def test_assert_real_store_untouched_raises_on_existence_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_store = tmp_path / "fake-real-store.json"
    monkeypatch.setattr(cc, "REAL_STORE", fake_store)
    # Simulate: store did not exist "before", but exists "after" (a leak).
    fake_store.write_text("{}")
    with pytest.raises(RuntimeError, match="REFUSING TO REPORT RESULTS"):
        cc._assert_real_store_untouched((False, None))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
