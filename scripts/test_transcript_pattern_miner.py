#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Black-box characterization test for transcript-pattern-miner.

Builds a synthetic fixture covering all 5 mining categories, runs the current
miner end-to-end, and asserts on stable structural properties of the output.
Uses fixed timestamps so --all prevents any day-window filtering.

Mining categories exercised:
  (a) user corrections  — "no, actually you should use uv not pip"
                          repeated with 3-gram overlap across 3+ sessions,
                          always preceded by an assistant turn so _prev_was_assistant=True.
  (b) repeated tool seq — Read→Edit→Bash chain appears in 3 sessions.
  (c) cross-project file— shared-config/settings.json touched in 3 projects.
  (d) manual ritual     — git status --short repeated 10+ times total (≥5 threshold).
  (e) secret            — sk-ant- fake key in a user message body.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixed timestamps (ISO8601, not wall-clock)
# ---------------------------------------------------------------------------

_T0 = "2026-01-15T10:00:00.000Z"
_T1 = "2026-01-15T10:05:00.000Z"
_T2 = "2026-01-15T10:10:00.000Z"
_T3 = "2026-01-15T10:15:00.000Z"
_T4 = "2026-01-15T10:20:00.000Z"

# Fake-but-shaped Anthropic secret (40+ chars after sk-ant-)
_FAKE_SECRET = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE1234"

_CWD_A = "/Users/tyroneross/dev/git-folder/my-project-alpha/src"
_CWD_B = "/Users/tyroneross/dev/git-folder/my-project-beta/src"
_CWD_C = "/Users/tyroneross/dev/git-folder/my-project-gamma/src"

# Correction messages — same 3-gram spine "no actually you should use uv not pip"
# Each has ≥2 shared 3-grams with the others so they cluster.
_CORRECTIONS = [
    "no, actually you should use uv not pip for this project",
    "no, actually you should use uv not pip here again please",
    "no, actually you should use uv not pip commands generally",
    "no, actually you should use uv not pip always",
    "no, actually you should use uv not pip in this context",
]


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------

def _user_text(ts: str, text: str, cwd: str, sid: str, uid: str) -> dict:
    return {"type": "user", "uuid": uid, "parentUuid": None,
            "timestamp": ts, "sessionId": sid, "cwd": cwd,
            "message": {"role": "user", "content": text}}


def _asst_text(ts: str, text: str, cwd: str, sid: str, uid: str) -> dict:
    return {"type": "assistant", "uuid": uid, "parentUuid": None,
            "timestamp": ts, "sessionId": sid, "cwd": cwd,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _asst_tool(ts: str, name: str, inp: dict, tuid: str, cwd: str, sid: str) -> dict:
    return {"type": "assistant", "uuid": f"asst-{tuid}", "parentUuid": None,
            "timestamp": ts, "sessionId": sid, "cwd": cwd,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tuid, "name": name, "input": inp}
            ]}}


def _tool_result(ts: str, tuid: str, out: str, is_err: bool, cwd: str, sid: str) -> dict:
    return {"type": "user", "uuid": f"res-{tuid}", "parentUuid": f"asst-{tuid}",
            "timestamp": ts, "sessionId": sid, "cwd": cwd,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tuid, "content": out, "is_error": is_err}
            ]}}


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# Session builders
# ---------------------------------------------------------------------------

def _session_1(sessions_dir: Path) -> None:
    """Session 1 in project-alpha.
    - 2 corrections (require assistant turn before each)
    - Read→Edit→Bash sequence (category b)
    - cross-project file touch (category c)
    - 3 git-status rituals (category d)
    - secret in user message (category e)
    """
    sid = "session-aaa111"
    cwd = _CWD_A
    r: list[dict] = []

    # Assistant speaks first → sets _prev_was_assistant=True
    r.append(_asst_text(_T0, "I'll use pip to install dependencies.", cwd, sid, "a-init"))

    # Correction 1 (now _prev_was_assistant=True)
    r.append(_user_text(_T0, _CORRECTIONS[0], cwd, sid, "u-corr-1"))

    # Read→Edit→Bash sequence (1st pass)
    r.append(_asst_tool(_T1, "Read", {"file_path": "/proj/pyproject.toml"}, "r1-read", cwd, sid))
    r.append(_tool_result(_T1, "r1-read", "content", False, cwd, sid))
    r.append(_asst_tool(_T1, "Edit", {"file_path": "/proj/pyproject.toml", "old_string": "pip", "new_string": "uv"}, "r1-edit", cwd, sid))
    r.append(_tool_result(_T1, "r1-edit", "ok", False, cwd, sid))
    r.append(_asst_tool(_T1, "Bash", {"command": "uv run pytest scripts/ -q"}, "r1-bash", cwd, sid))
    r.append(_tool_result(_T1, "r1-bash", "3 passed", False, cwd, sid))

    # Assistant speaks again → sets _prev_was_assistant=True for next correction
    r.append(_asst_text(_T2, "Done. Updated to uv.", cwd, sid, "a-resp"))

    # Correction 2
    r.append(_user_text(_T2, _CORRECTIONS[1], cwd, sid, "u-corr-2"))

    # Cross-project shared file
    r.append(_asst_tool(_T3, "Read", {"file_path": "/Users/tyroneross/dev/git-folder/shared-config/settings.json"}, "r1-xread", cwd, sid))
    r.append(_tool_result(_T3, "r1-xread", "config", False, cwd, sid))

    # Rituals (3 git status invocations)
    for i, ts in enumerate([_T0, _T1, _T2]):
        r.append(_asst_tool(ts, "Bash", {"command": "git status --short"}, f"r1-ritual-{i}", cwd, sid))
        r.append(_tool_result(ts, f"r1-ritual-{i}", "M file.py", False, cwd, sid))

    # Secret in user message body
    r.append(_asst_text(_T4, "What is your API key?", cwd, sid, "a-ask-key"))
    r.append(_user_text(_T4, f"my key is {_FAKE_SECRET}", cwd, sid, "u-secret"))

    _write_jsonl(sessions_dir / f"{sid}.jsonl", r)


def _session_2(sessions_dir: Path) -> None:
    """Session 2 in project-beta.
    - 2 corrections
    - Read→Edit→Bash
    - cross-project shared file
    - 2 git-status rituals
    """
    sid = "session-bbb222"
    cwd = _CWD_B
    r: list[dict] = []

    r.append(_asst_text(_T0, "I'll use pip here.", cwd, sid, "b-init"))
    r.append(_user_text(_T0, _CORRECTIONS[2], cwd, sid, "u-corr-3"))

    r.append(_asst_tool(_T1, "Read", {"file_path": "/proj2/requirements.txt"}, "r2-read", cwd, sid))
    r.append(_tool_result(_T1, "r2-read", "content", False, cwd, sid))
    r.append(_asst_tool(_T1, "Edit", {"file_path": "/proj2/requirements.txt", "old_string": "pip", "new_string": "uv"}, "r2-edit", cwd, sid))
    r.append(_tool_result(_T1, "r2-edit", "ok", False, cwd, sid))
    r.append(_asst_tool(_T1, "Bash", {"command": "uv run pytest scripts/ -q"}, "r2-bash", cwd, sid))
    r.append(_tool_result(_T1, "r2-bash", "5 passed", False, cwd, sid))

    r.append(_asst_text(_T2, "Updated.", cwd, sid, "b-resp"))
    r.append(_user_text(_T2, _CORRECTIONS[3], cwd, sid, "u-corr-4"))

    r.append(_asst_tool(_T3, "Read", {"file_path": "/Users/tyroneross/dev/git-folder/shared-config/settings.json"}, "r2-xread", cwd, sid))
    r.append(_tool_result(_T3, "r2-xread", "config", False, cwd, sid))

    for i, ts in enumerate([_T0, _T1]):
        r.append(_asst_tool(ts, "Bash", {"command": "git status --short"}, f"r2-ritual-{i}", cwd, sid))
        r.append(_tool_result(ts, f"r2-ritual-{i}", "", False, cwd, sid))

    _write_jsonl(sessions_dir / f"{sid}.jsonl", r)


def _session_3(sessions_dir: Path) -> None:
    """Session 3 in project-gamma.
    - 1 correction (cluster now has 5 occurrences total across sessions)
    - Read→Edit→Bash (3rd session — triggers repeated sequence detection)
    - cross-project shared file edit (3rd project → triggers cross-project detection)
    - 5 git-status rituals (total now 3+2+5=10 ≥ 5 threshold)
    """
    sid = "session-ccc333"
    cwd = _CWD_C
    r: list[dict] = []

    r.append(_asst_text(_T0, "Running pip install.", cwd, sid, "c-init"))
    r.append(_user_text(_T0, _CORRECTIONS[4], cwd, sid, "u-corr-5"))

    r.append(_asst_tool(_T1, "Read", {"file_path": "/proj3/setup.py"}, "r3-read", cwd, sid))
    r.append(_tool_result(_T1, "r3-read", "content", False, cwd, sid))
    r.append(_asst_tool(_T1, "Edit", {"file_path": "/proj3/setup.py", "old_string": "pip", "new_string": "uv"}, "r3-edit", cwd, sid))
    r.append(_tool_result(_T1, "r3-edit", "ok", False, cwd, sid))
    r.append(_asst_tool(_T1, "Bash", {"command": "uv run pytest scripts/ -q"}, "r3-bash", cwd, sid))
    r.append(_tool_result(_T1, "r3-bash", "7 passed", False, cwd, sid))

    # Edit the shared-config file (3rd project touch)
    r.append(_asst_tool(_T2, "Edit", {"file_path": "/Users/tyroneross/dev/git-folder/shared-config/settings.json"}, "r3-xedit", cwd, sid))
    r.append(_tool_result(_T2, "r3-xedit", "edited", False, cwd, sid))

    # 5 git status rituals — pushes total to 10
    for i, ts in enumerate([_T0, _T1, _T2, _T3, _T4]):
        r.append(_asst_tool(ts, "Bash", {"command": "git status --short"}, f"r3-ritual-{i}", cwd, sid))
        r.append(_tool_result(ts, f"r3-ritual-{i}", "", False, cwd, sid))

    # Failing bash for test-outcome coverage
    r.append(_asst_tool(_T3, "Bash", {"command": "pytest scripts/ -q"}, "r3-fail", cwd, sid))
    r.append(_tool_result(_T3, "r3-fail", "error", True, cwd, sid))

    _write_jsonl(sessions_dir / f"{sid}.jsonl", r)


def build_fixture(tmp_path: Path) -> Path:
    """Create sessions_dir with 3 synthetic session JSONL files. Returns sessions_dir."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    _session_1(sessions_dir)
    _session_2(sessions_dir)
    _session_3(sessions_dir)
    return sessions_dir


# ---------------------------------------------------------------------------
# Miner loader — importlib handles the hyphenated filename
# ---------------------------------------------------------------------------

def _load_miner():
    """Load transcript-pattern-miner.py (or its shim after the split)."""
    script = Path(__file__).parent / "transcript-pattern-miner.py"
    spec = importlib.util.spec_from_file_location("transcript_pattern_miner_shim", script)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Shared fixture: run miner once per test session
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fixture_run(tmp_path_factory):
    """Run the miner against the synthetic fixture; return out_dir for all tests."""
    tmp = tmp_path_factory.mktemp("miner_run")
    sessions_dir = build_fixture(tmp)
    out_dir = tmp / "out"
    out_dir.mkdir()

    miner = _load_miner()
    rc = miner.main([
        "--all",           # ignore day-window; process all files
        "--force",         # ignore .processed.json cache
        "--sessions-dir", str(sessions_dir),
        "--out-dir", str(out_dir),
    ])
    assert rc == 0, f"miner.main() returned {rc}"
    return out_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _report_text(fixture_run) -> str:
    reports = list(fixture_run.glob("*.md"))
    assert reports, "no .md report file generated"
    return reports[0].read_text()


def _candidates(fixture_run) -> dict:
    cand_path = fixture_run / ".candidates.json"
    assert cand_path.exists(), ".candidates.json not written"
    return json.loads(cand_path.read_text())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReportStructure:
    def test_all_6_section_headers_present(self, fixture_run):
        text = _report_text(fixture_run)
        for header in [
            "## 1. Recurring user corrections",
            "## 2. Repeated tool sequences",
            "## 3. Cross-project file patterns",
            "## 4. Manual command rituals",
            "## 5. Secrets observed",
            "## 6. Test patterns + outcomes",
        ]:
            assert header in text, f"missing section: {header!r}"

    def test_window_label_all_history(self, fixture_run):
        text = _report_text(fixture_run)
        assert "all history" in text


class TestCorrectionCluster:
    """Category (a): 5 correction messages with ≥2 shared 3-grams cluster into 1 group."""

    def test_cluster_detected(self, fixture_run):
        text = _report_text(fixture_run)
        assert "_No clusters with 3+ occurrences found._" not in text, \
            "correction cluster not detected — expected 5 occurrences with 3-gram overlap"

    def test_feedback_candidate_in_candidates_json(self, fixture_run):
        data = _candidates(fixture_run)
        kinds = [c["kind"] for c in data["candidates"]]
        assert "feedback_candidate" in kinds, f"kinds found: {kinds}"


class TestRepeatedToolSequence:
    """Category (b): Read→Edit→Bash in 3 sessions triggers sequence detection."""

    def test_sequence_detected(self, fixture_run):
        text = _report_text(fixture_run)
        assert "_No sequences" not in text, "no repeated tool sequences detected"

    def test_read_edit_bash_in_report(self, fixture_run):
        text = _report_text(fixture_run)
        # All three tool names must appear in the sequence section
        assert "Read" in text and "Edit" in text and "Bash" in text

    def test_skill_candidate_present(self, fixture_run):
        data = _candidates(fixture_run)
        kinds = [c["kind"] for c in data["candidates"]]
        assert "skill_or_workflow_candidate" in kinds, f"kinds: {kinds}"


class TestCrossProjectFile:
    """Category (c): settings.json touched in 3 projects."""

    def test_cross_project_detected(self, fixture_run):
        text = _report_text(fixture_run)
        assert "_No cross-project file patterns found._" not in text, \
            "cross-project file not detected"

    def test_settings_json_in_report(self, fixture_run):
        text = _report_text(fixture_run)
        assert "settings.json" in text


class TestManualCommandRitual:
    """Category (d): git status --short repeated 10 times total (≥5 threshold)."""

    def test_ritual_detected(self, fixture_run):
        text = _report_text(fixture_run)
        assert "_No bash command shapes repeating 5+ times._" not in text, \
            "ritual not detected — expected git status --short ≥5 times"

    def test_git_in_ritual_section(self, fixture_run):
        text = _report_text(fixture_run)
        # The ritual section shows the command shape; git should appear
        ritual_start = text.find("## 4. Manual command rituals")
        ritual_end = text.find("## 5.", ritual_start)
        ritual_section = text[ritual_start:ritual_end]
        assert "git" in ritual_section.lower()


class TestSecretDetection:
    """Category (e): planted sk-ant- token appears in the secrets section."""

    def test_secret_section_has_entries(self, fixture_run):
        text = _report_text(fixture_run)
        assert "_No secrets matched" not in text, \
            "no secrets detected — planted sk-ant- token not found"

    def test_sk_ant_prefix_in_report(self, fixture_run):
        text = _report_text(fixture_run)
        assert "sk-ant-" in text

    def test_anthropic_kind_in_report(self, fixture_run):
        text = _report_text(fixture_run)
        assert "anthropic" in text


class TestCandidatesJson:
    def test_schema(self, fixture_run):
        data = _candidates(fixture_run)
        assert "generated_at" in data
        assert "window_label" in data
        assert isinstance(data["candidates"], list)

    def test_nonzero_candidates(self, fixture_run):
        data = _candidates(fixture_run)
        assert len(data["candidates"]) > 0


class TestProcessedCache:
    def test_processed_json_written_with_3_entries(self, fixture_run):
        proc_path = fixture_run / ".processed.json"
        assert proc_path.exists()
        data = json.loads(proc_path.read_text())
        assert len(data) >= 3, f"expected ≥3 entries, got {len(data)}"


class TestOutcomesJsonl:
    def test_outcomes_jsonl_rows_have_required_fields(self, fixture_run):
        path = fixture_run / ".outcomes.jsonl"
        if not (path.exists() and path.stat().st_size > 0):
            pytest.skip("no outcomes rows written (no test invocations in fixture)")
        rows = [json.loads(line) for line in path.read_text().strip().splitlines()]
        for row in rows:
            assert "test_category" in row
            assert "outcome_class" in row
            assert "session_id" in row
