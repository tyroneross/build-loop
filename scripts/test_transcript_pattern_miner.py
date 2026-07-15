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

_CWD_A = "/Users/devuser/dev/git-folder/my-project-alpha/src"
_CWD_B = "/Users/devuser/dev/git-folder/my-project-beta/src"
_CWD_C = "/Users/devuser/dev/git-folder/my-project-gamma/src"

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
    r.append(_asst_tool(_T3, "Read", {"file_path": "/Users/devuser/dev/git-folder/shared-config/settings.json"}, "r1-xread", cwd, sid))
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

    r.append(_asst_tool(_T3, "Read", {"file_path": "/Users/devuser/dev/git-folder/shared-config/settings.json"}, "r2-xread", cwd, sid))
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
    r.append(_asst_tool(_T2, "Edit", {"file_path": "/Users/devuser/dev/git-folder/shared-config/settings.json"}, "r3-xedit", cwd, sid))
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

    def test_directional_only_field_present(self, fixture_run):
        """Trap 3: every outcomes row must carry directional_only (bool)."""
        path = fixture_run / ".outcomes.jsonl"
        if not (path.exists() and path.stat().st_size > 0):
            pytest.skip("no outcomes rows written")
        rows = [json.loads(line) for line in path.read_text().strip().splitlines()]
        for row in rows:
            assert "directional_only" in row, f"missing directional_only in row: {row}"
            assert isinstance(row["directional_only"], bool), (
                f"directional_only must be bool, got {type(row['directional_only'])!r}"
            )


# ---------------------------------------------------------------------------
# Trap-guard unit tests (do NOT depend on fixture_run)
# ---------------------------------------------------------------------------

class TestTrap1StrictFailureClassification:
    """Trap 1: tool_result containing error-like text in valid output must NOT be a failure."""

    def test_tool_result_with_error_text_in_output_not_counted_as_failure(self, tmp_path):
        """A tool_result whose content contains 'error' / 'FAIL' / 'warning' but has
        is_error=False (i.e. the tool succeeded) must classify as MIXED or POSITIVE,
        never REWORK."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from transcript_pattern_miner.session import (
            SessionAggregate, classify_outcome, _record_tool_results,
            _process_assistant_tool_use_item,
        )
        from transcript_pattern_miner.io_cache import parse_ts

        # Build a minimal aggregate with one Bash tool call whose result
        # contains "error" and "FAIL" text but is_error=False.
        agg = SessionAggregate("trap1-test")
        ts = parse_ts("2026-01-15T10:00:00Z")

        # Assistant issues a Bash tool call
        tool_item = {
            "type": "tool_use",
            "id": "tool-abc-1",
            "name": "Bash",
            "input": {"command": "pytest scripts/ -q"},
        }
        _process_assistant_tool_use_item(agg, tool_item, ts, "project-x")

        # User returns tool result: content has "error" text but is_error=False
        result_content = [
            {
                "type": "tool_result",
                "tool_use_id": "tool-abc-1",
                "content": "IBR scan complete. No error found. FAIL rate: 0%. 37 warnings suppressed.",
                "is_error": False,
            }
        ]
        _record_tool_results(agg, result_content, ts, "project-x")

        # The pytest invocation is in test_invocations (B_runner category)
        assert agg.test_invocations, "expected a B_runner test invocation to be recorded"
        inv = agg.test_invocations[0]

        outcome, evidence, directional = classify_outcome(agg, inv)

        assert outcome != "REWORK", (
            f"Trap 1 violation: tool_result containing 'error'/'FAIL' text but "
            f"is_error=False was classified as REWORK (got: {outcome!r}, evidence: {evidence!r}). "
            "Failure classification must be based on is_error flag, not text content."
        )
        # MIXED is the correct outcome: tool ok, no follow-up user signal
        assert outcome in ("MIXED", "POSITIVE", "NO_SIGNAL"), (
            f"Expected MIXED/POSITIVE/NO_SIGNAL, got {outcome!r}"
        )


class TestTrap2DeduplicationPreventsInflation:
    """Trap 2: duplicated transcript records (resumed sessions) must not inflate counts."""

    def test_duplicated_uuid_records_not_double_counted(self, tmp_path):
        """Writing the same record twice (same uuid) in one JSONL file must produce
        the same aggregate counts as writing it once."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from transcript_pattern_miner.session import process_session_file
        import json as _json

        cwd = "/Users/devuser/dev/git-folder/some-project/src"
        sid = "dedup-test-session"

        # A tool_use + correction record with a uuid
        asst_record = {
            "type": "assistant", "uuid": "uuid-asst-001", "parentUuid": None,
            "timestamp": "2026-01-15T10:00:00.000Z", "sessionId": sid, "cwd": cwd,
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": "I will use pip."}
            ]},
        }
        user_correction = {
            "type": "user", "uuid": "uuid-user-001", "parentUuid": None,
            "timestamp": "2026-01-15T10:01:00.000Z", "sessionId": sid, "cwd": cwd,
            "message": {"role": "user", "content": "no, actually you should use uv not pip"},
        }

        def _write(path, records):
            with path.open("w") as f:
                for r in records:
                    f.write(_json.dumps(r) + "\n")

        # File with records once
        once_path = tmp_path / "once.jsonl"
        _write(once_path, [asst_record, user_correction])

        # File with records duplicated (simulates resume replay)
        dup_path = tmp_path / "dup.jsonl"
        _write(dup_path, [asst_record, user_correction, asst_record, user_correction])

        agg_once = process_session_file(once_path, None)
        agg_dup = process_session_file(dup_path, None)

        assert agg_once is not None
        assert agg_dup is not None

        # User messages should be the same count regardless of duplication
        assert len(agg_once.user_messages) == len(agg_dup.user_messages), (
            f"Trap 2 violation: duplicated records inflated user_messages count. "
            f"once={len(agg_once.user_messages)}, dup={len(agg_dup.user_messages)}"
        )


class TestIsMetaRecordsExcluded:
    """isMeta records (Stop-hook injections, slash-command templates,
    skill-load bodies) carry type='user'/'assistant' but must NOT
    contribute to user_messages, secret_hits, tool_sequence, or
    test_invocations.

    Claude Code marks these with top-level isMeta=true. The existing
    META_PREFIXES text-pattern allowlist in textproc.py is brittle
    (misses isMeta records whose text doesn't start with a known
    prefix — SPDX skill bodies, skill base-dir scaffolding, future
    hook shapes). Mirrors the canonical v0.29.1
    `scripts/retrospective/sections.py` guard at record level.
    """

    def test_isMeta_user_records_do_not_pollute_aggregate(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from transcript_pattern_miner.session import process_session_file
        import json as _json

        cwd = "/Users/devuser/dev/git-folder/some-project/src"
        sid = "ismeta-test-session"

        # 1. Genuine assistant turn that uses a tool.
        asst_with_tool = {
            "type": "assistant", "uuid": "u-a1", "timestamp": "2026-01-15T10:00:00.000Z",
            "sessionId": sid, "cwd": cwd,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t-1", "name": "Edit",
                 "input": {"file_path": "/x/y.py"}},
                {"type": "text", "text": "Done."},
            ]},
        }
        # 2. Stop-hook injection — isMeta=true. Contains a "fake" API key
        #    string so we can prove secret-scan does NOT pick it up.
        hook_inject = {
            "type": "user", "isMeta": True, "uuid": "u-h1",
            "timestamp": "2026-01-15T10:01:00.000Z", "sessionId": sid, "cwd": cwd,
            "message": {"role": "user", "content":
                "Stop hook feedback: cd /tmp && git diff staged "
                "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
        }
        # 3. Skill-load body — isMeta=true, SPDX-style content with a
        #    secret-shaped string.
        skill_body = {
            "type": "user", "isMeta": True, "uuid": "u-h2",
            "timestamp": "2026-01-15T10:02:00.000Z", "sessionId": sid, "cwd": cwd,
            "message": {"role": "user", "content":
                "# SPDX-FileCopyrightText: example header content "
                "ANTHROPIC_API_KEY=sk-ant-api03-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"},
        }
        # 4. Skill base-dir scaffolding — isMeta=true.
        skill_scaffold = {
            "type": "user", "isMeta": True, "uuid": "u-h3",
            "timestamp": "2026-01-15T10:03:00.000Z", "sessionId": sid, "cwd": cwd,
            "message": {"role": "user", "content":
                "Base directory for this skill: /Users/x/.claude/plugins/foo"},
        }
        # 5. Assistant turn so the next user prompt qualifies as "real"
        #    (the miner only counts user prose that follows an assistant).
        asst_response = {
            "type": "assistant", "uuid": "u-a2", "timestamp": "2026-01-15T10:04:00.000Z",
            "sessionId": sid, "cwd": cwd,
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Acknowledged."},
            ]},
        }
        # 6. Genuine human prompt — should be the ONLY user_messages entry.
        real_user = {
            "type": "user", "uuid": "u-u1",
            "timestamp": "2026-01-15T10:05:00.000Z", "sessionId": sid, "cwd": cwd,
            "message": {"role": "user",
                "content": "Please add a regression test for this case."},
        }

        path = tmp_path / f"{sid}.jsonl"
        with path.open("w") as f:
            for r in [asst_with_tool, hook_inject, skill_body, skill_scaffold,
                      asst_response, real_user]:
                f.write(_json.dumps(r) + "\n")

        agg = process_session_file(path, None)
        assert agg is not None, "expected aggregate, got None"

        # (a) Only the real user message survives.
        assert len(agg.user_messages) == 1, (
            f"isMeta records leaked into user_messages: {agg.user_messages}")
        assert "regression test" in agg.user_messages[0][1], (
            f"wrong user message captured: {agg.user_messages[0]}")

        # (b) No isMeta-injected secrets surfaced. The two fake keys
        #     embedded in isMeta records must not appear in secret_hits.
        leaked = [h for h in agg.secret_hits if "AAAA" in h[1] or "BBBB" in h[1]]
        assert leaked == [], (
            f"secret_hits leaked from isMeta records: {leaked}")

        # (c) The real assistant tool_use (Edit) is still captured. This
        #     proves the guard doesn't over-filter genuine assistant turns.
        assert any("Edit" in s for s in agg.tool_sequence), (
            f"real assistant tool_use missing from tool_sequence: {agg.tool_sequence}")

        # (d) No isMeta record's text appears as an event.
        for ev in agg.events:
            txt = ev.get("text") or ""
            assert "Stop hook feedback" not in txt, (
                f"Stop-hook text leaked into events: {txt!r}")
            assert "SPDX-FileCopyrightText" not in txt, (
                f"skill-load SPDX text leaked into events: {txt!r}")
            assert "Base directory for this skill" not in txt, (
                f"skill base-dir text leaked into events: {txt!r}")
