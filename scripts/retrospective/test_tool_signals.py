# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the deterministic tool/plugin/automation/issue extractors and the
two coverage sections they feed (§10 plugin_tooling_observations, §11
automation_candidates). These signals run headlessly (no LLM) so the
retrospective can fire for FREE at SessionEnd on non-run sessions.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from retrospective.sections import (  # noqa: E402
    SECTION_KEYS,
    build,
    extract_issue_signals,
    extract_tool_sequences,
    extract_tool_usage,
)


def _rec(rtype: str, content: list) -> str:
    return json.dumps({"type": rtype, "message": {"role": rtype, "content": content}})


def _tool_use(uid: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": uid, "name": name, "input": inp}


def _tool_result(uid: str, text: str, is_error: bool = False) -> dict:
    return {"type": "tool_result", "tool_use_id": uid, "is_error": is_error,
            "content": [{"type": "text", "text": text}]}


def _write(tmpdir: Path, lines: list[str]) -> Path:
    p = tmpdir / "tx.jsonl"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


class ExtractToolUsageTests(unittest.TestCase):
    def _tmp(self) -> Path:
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        return Path(t.name)

    def test_counts_tools_skills_plugins_subagents(self) -> None:
        d = self._tmp()
        p = _write(d, [
            _rec("assistant", [_tool_use("a1", "Bash", {"command": "ls"})]),
            _rec("assistant", [_tool_use("a2", "Skill", {"skill": "build-loop:run"})]),
            _rec("assistant", [_tool_use("a3", "Task", {"subagent_type": "build-loop:implementer"})]),
            _rec("assistant", [_tool_use("a4", "mcp__plugin_ibr_ibr__scan", {})]),
        ])
        u = extract_tool_usage(p)
        self.assertEqual(u["total_uses"], 4)
        self.assertIn("Skill(build-loop:run)", u["tools"])
        self.assertIn("ibr:scan", u["tools"])
        # plugins attributes both the skill owner and the mcp plugin + subagent owner
        self.assertEqual(u["plugins"].get("build-loop"), 2)  # skill + subagent owner
        self.assertEqual(u["plugins"].get("ibr"), 1)
        self.assertEqual(u["subagents"].get("build-loop:implementer"), 1)  # full type

    def test_attributes_errors_back_to_tool(self) -> None:
        d = self._tmp()
        p = _write(d, [
            _rec("assistant", [_tool_use("a1", "Bash", {"command": "boom"})]),
            _rec("user", [_tool_result("a1", "Error: boom", is_error=True)]),
        ])
        u = extract_tool_usage(p)
        self.assertEqual(u["errored"].get("Bash"), 1)

    def test_empty_and_missing_transcript_safe(self) -> None:
        self.assertEqual(extract_tool_usage(None)["total_uses"], 0)
        self.assertEqual(extract_tool_usage(Path("/nope/x.jsonl"))["total_uses"], 0)


class ExtractToolSequencesTests(unittest.TestCase):
    def _tmp(self) -> Path:
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        return Path(t.name)

    def test_pure_generic_sequence_is_dropped(self) -> None:
        d = self._tmp()
        lines = []
        for _ in range(5):
            lines.append(_rec("assistant", [_tool_use("x", "Edit", {})]))
            lines.append(_rec("assistant", [_tool_use("y", "Bash", {})]))
            lines.append(_rec("assistant", [_tool_use("z", "Read", {})]))
        p = _write(d, lines)
        # Edit→Bash→Read is the universal edit-test loop → never a candidate.
        self.assertEqual(extract_tool_sequences(p), [])

    def test_content_bearing_sequence_surfaces(self) -> None:
        d = self._tmp()
        lines = []
        for _ in range(4):
            lines.append(_rec("assistant", [_tool_use("x", "Bash", {"command": "rally room"})]))
            lines.append(_rec("assistant", [_tool_use("y", "Skill", {"skill": "build-loop:run"})]))
            lines.append(_rec("assistant", [_tool_use("z", "Bash", {"command": "git push"})]))
        p = _write(d, lines)
        out = extract_tool_sequences(p)
        self.assertTrue(out, "content-bearing ritual should surface")
        self.assertIn("Skill(build-loop:run)", out[0]["sequence"])
        self.assertGreaterEqual(out[0]["count"], 3)


class ExtractIssueSignalsTests(unittest.TestCase):
    def _tmp(self) -> Path:
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        return Path(t.name)

    def test_captures_errored_tool_results(self) -> None:
        d = self._tmp()
        p = _write(d, [
            _rec("user", [_tool_result("a1", "Traceback (most recent call last): boom", is_error=True)]),
            _rec("user", [_tool_result("a2", "all good", is_error=False)]),
        ])
        sigs = extract_issue_signals(p)
        self.assertEqual(len(sigs), 1)
        self.assertIn("Traceback", sigs[0])

    def test_none_safe(self) -> None:
        self.assertEqual(extract_issue_signals(None), [])


class NewSectionsInBuildTests(unittest.TestCase):
    def _tmp(self) -> Path:
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        return Path(t.name)

    def test_build_emits_new_sections_and_meta(self) -> None:
        d = self._tmp()
        p = _write(d, [
            _rec("user", [{"type": "text", "text": "do the thing"}]),
            _rec("assistant", [_tool_use("a1", "Skill", {"skill": "build-loop:run"})]),
            _rec("user", [_tool_result("a1", "Error: failed", is_error=True)]),
        ])
        s = build(p, {}, "", "", "session-test")
        # both new sections present in the canonical key list and populated
        self.assertIn("plugin_tooling_observations", SECTION_KEYS)
        self.assertIn("automation_candidates", SECTION_KEYS)
        for k in SECTION_KEYS:
            self.assertIn(k, s)
        self.assertIn("build-loop", s["plugin_tooling_observations"])
        # error signal enriches §4 and §9
        self.assertIn("error", s["what_could_be_better"].lower())
        self.assertIn("tool_use_count", s["meta"])
        self.assertEqual(s["meta"]["tool_use_count"], 1)

    def test_automation_candidate_becomes_enforce_candidate(self) -> None:
        d = self._tmp()
        lines = []
        for _ in range(4):
            lines.append(_rec("assistant", [_tool_use("x", "Bash", {"command": "rally room"})]))
            lines.append(_rec("assistant", [_tool_use("y", "Skill", {"skill": "build-loop:run"})]))
            lines.append(_rec("assistant", [_tool_use("z", "Bash", {"command": "git push"})]))
        p = _write(d, lines)
        s = build(p, {}, "", "", "session-test")
        joined = " ".join(s["enforce_candidates"])
        self.assertIn("Automate recurring ritual", joined)


if __name__ == "__main__":
    unittest.main()
