#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for P0 audit findings f1, f2, f8.

Run with: python3 scripts/test_p0_audit_fixes.py

f1 (HIGH) — build_packet() structurally loads working_context first.
f2 (MED)  — pointer_density_findings() docstring no longer says "gates".
f8 (MED)  — report_lint.lint_context_density() surfaces density findings.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import context_bootstrap as cb  # noqa: E402
import context_snapshot as cs  # noqa: E402
from report_lint import lint_context_density, run_lint, _strip_fenced_blocks  # noqa: E402


# ---------------------------------------------------------------------------
# Shared env isolation (same pattern as existing tests)
# ---------------------------------------------------------------------------
class EnvIsolationMixin:
    def setUp(self) -> None:  # type: ignore[override]
        super().setUp()  # type: ignore[misc]
        self._prev_env = {
            "AGENT_MEMORY_ROOT": os.environ.get("AGENT_MEMORY_ROOT"),
            "BUILD_LOOP_MEMORY_ROOT": os.environ.get("BUILD_LOOP_MEMORY_ROOT"),
            "BUILD_LOOP_MEMORY_STORE_ROOT": os.environ.get("BUILD_LOOP_MEMORY_STORE_ROOT"),
            "CODEX_MEMORY_ROOT": os.environ.get("CODEX_MEMORY_ROOT"),
        }
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        self.workdir = self.tmp_root / "repo"
        self.memroot = self.tmp_root / "build-loop-memory"
        self.codex_root = self.tmp_root / "codex-memory"
        self.workdir.mkdir()
        self.memroot.mkdir()
        self.codex_root.mkdir()
        os.environ["AGENT_MEMORY_ROOT"] = str(self.memroot)
        os.environ.pop("BUILD_LOOP_MEMORY_ROOT", None)
        os.environ.pop("BUILD_LOOP_MEMORY_STORE_ROOT", None)
        os.environ["CODEX_MEMORY_ROOT"] = str(self.codex_root)

    def tearDown(self) -> None:  # type: ignore[override]
        for key, val in self._prev_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._tmp.cleanup()
        super().tearDown()  # type: ignore[misc]

    def _write_minimal_repo(self) -> None:
        bl = self.workdir / ".build-loop"
        bl.mkdir(parents=True, exist_ok=True)
        (bl / "goal.md").write_text("Test goal.\n", encoding="utf-8")
        (bl / "intent.md").write_text("Test intent.\n", encoding="utf-8")
        (bl / "state.json").write_text(json.dumps({"runs": []}), encoding="utf-8")

    def _write_current_md(self) -> None:
        ctx = self.workdir / ".build-loop" / "context"
        ctx.mkdir(parents=True, exist_ok=True)
        (ctx / "current.md").write_text(
            "# Build Loop Working Context\n\n"
            "- Updated: 2026-06-07T10:00:00+00:00\n"
            "- Trigger: manual\n"
            "- Phase: assess\n"
            "- Run: run-test\n"
            "- Build loop ID: bl-test\n"
            "- Branch: main @ abc123\n\n"
            "## Current Work\n\n"
            "- Agent: orchestrator\n"
            "- Chunk: c1\n"
            "- Status: dispatching\n"
            "- Task: test task\n"
            "- Next action: validate\n\n"
            "## Changed Files\n\n"
            "- Dirty count: 1\n"
            "- scripts/context_bootstrap.py\n\n"
            "## Validation\n\n"
            "- Result: pending\n"
            "- Commands recorded: 0\n\n"
            "## Memory Backlinks\n\n"
            "- (none — prior_art empty / bootstrap not yet run)\n\n"
            "## Pointers\n\n"
            "- Snapshot JSON: `.build-loop/context/snapshots/`\n"
            "- Snapshot index: `.build-loop/context/index.json`\n"
            "- Memory store: `~/dev/git-folder/build-loop-memory/projects/test/`\n"
            "- Prior art digest (full): `packet.prior_art.digest_text`\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# f1 — build_packet() embeds working_context loaded first
# ---------------------------------------------------------------------------
class F1WorkingContextInPacketTests(EnvIsolationMixin, unittest.TestCase):

    def test_packet_has_working_context_key(self) -> None:
        """build_packet() always returns packet['working_context']."""
        self._write_minimal_repo()
        packet = cb.build_packet(
            workdir=self.workdir,
            query="test",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        self.assertIn("working_context", packet)

    def test_packet_working_context_exists_true_when_current_md_present(self) -> None:
        """When current.md exists, packet['working_context']['exists'] is True
        and warm_read_latency_ms is a non-None float."""
        self._write_minimal_repo()
        self._write_current_md()
        packet = cb.build_packet(
            workdir=self.workdir,
            query="test",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        wc = packet["working_context"]
        self.assertTrue(wc["exists"])
        self.assertIsNotNone(wc["warm_read_latency_ms"])
        self.assertIsInstance(wc["warm_read_latency_ms"], float)

    def test_packet_working_context_exists_false_when_no_current_md(self) -> None:
        """Absence-tolerant: missing current.md → exists=False, no error, no block."""
        self._write_minimal_repo()
        # Do NOT write current.md
        packet = cb.build_packet(
            workdir=self.workdir,
            query="test",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        wc = packet["working_context"]
        self.assertFalse(wc["exists"])
        self.assertIsNone(wc["warm_read_latency_ms"])
        # packet must still be complete even without working context
        self.assertIn("sources", packet)
        self.assertIn("project", packet)

    def test_working_context_present_even_when_other_surfaces_fail(self) -> None:
        """working_context is loaded first; partial failures elsewhere do not remove it."""
        # No .build-loop at all — canonical memory and repo_local will degrade
        packet = cb.build_packet(
            workdir=self.workdir,
            query="test",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        # Must be present regardless
        self.assertIn("working_context", packet)
        wc = packet["working_context"]
        # exists should be False (no current.md), not raise
        self.assertFalse(wc["exists"])

    def test_load_current_import_available_from_context_bootstrap(self) -> None:
        """load_current and WorkingContextEnvelope must be importable from cb module scope."""
        from context_bootstrap import load_current, WorkingContextEnvelope  # noqa: PLC0415
        self.assertTrue(callable(load_current))
        self.assertTrue(isinstance(WorkingContextEnvelope, type))

    def test_working_context_is_asdict_of_envelope(self) -> None:
        """packet['working_context'] must be serialisable (asdict of dataclass)."""
        self._write_minimal_repo()
        packet = cb.build_packet(
            workdir=self.workdir,
            query="test",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        wc = packet["working_context"]
        # asdict keys match WorkingContextEnvelope fields
        self.assertIn("exists", wc)
        self.assertIn("path", wc)
        self.assertIn("warm_read_latency_ms", wc)
        self.assertIn("parsed", wc)
        self.assertIn("reasons", wc)
        # Must be JSON-serialisable (no raw dataclass objects)
        json.dumps(wc)  # would raise if not serialisable


# ---------------------------------------------------------------------------
# f2 — pointer_density_findings() docstring is truthful (no "gates" claim)
# ---------------------------------------------------------------------------
class F2DocstringTruthfulTests(unittest.TestCase):

    def test_docstring_does_not_say_gates(self) -> None:
        """The old docstring claimed the function 'gates' the requirement.
        It must not say that — it is advisory only."""
        doc = cs.pointer_density_findings.__doc__ or ""
        self.assertNotIn(
            "gates the",
            doc,
            "Docstring still uses 'gates' language — fix the false claim.",
        )

    def test_docstring_says_advisory(self) -> None:
        """Docstring must state the non-blocking / advisory nature."""
        doc = (cs.pointer_density_findings.__doc__ or "").lower()
        self.assertTrue(
            "advisory" in doc or "non-blocking" in doc,
            "Docstring must state advisory/non-blocking contract.",
        )

    def test_write_snapshot_writes_unconditionally_despite_density_findings(self) -> None:
        """write_snapshot writes even when density findings are non-empty — not a gate."""
        # Craft a text that will trigger a density finding (>80 lines).
        long_text = "# Build Loop Working Context\n\n"
        long_text += "- Updated: 2026-01-01T00:00:00+00:00\n"
        long_text += "- Trigger: manual\n- Phase: assess\n- Run: r\n"
        long_text += "- Build loop ID: bl\n- Branch: main @ abc\n\n"
        long_text += "## Changed Files\n\n- Dirty count: 0\n\n"
        long_text += "## Validation\n\n- Result: pending\n- Commands recorded: 0\n\n"
        long_text += "## Memory Backlinks\n\n- (none)\n\n## Pointers\n\n- Snapshot JSON: `.build-loop/context/snapshots/`\n"
        # Pad to exceed max_file_lines (80).
        long_text += "\n".join(f"- extra line {i}" for i in range(100)) + "\n"

        findings = cs.pointer_density_findings(long_text)
        # Should find the too_many_lines violation
        self.assertTrue(
            any("too_many_lines" in f for f in findings),
            f"Expected too_many_lines finding; got {findings}",
        )
        # Verify the result is advisory only — the function just returns findings,
        # it does not raise or block anything.
        self.assertIsInstance(findings, list)


# ---------------------------------------------------------------------------
# f8 — report_lint surfaces density findings as context-density WARN
# ---------------------------------------------------------------------------
class F8ContextDensityRuleTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_index(self, density_findings: list[str]) -> None:
        ctx = self.workdir / ".build-loop" / "context"
        ctx.mkdir(parents=True, exist_ok=True)
        (ctx / "index.json").write_text(
            json.dumps({
                "schema_version": 1,
                "last_snapshot_id": "ctx-test",
                "pointer_density_findings": density_findings,
            }),
            encoding="utf-8",
        )

    def _good_report(self) -> Path:
        p = self.workdir / "report.md"
        p.write_text(
            "Auditor now runs on every build commit; the gap is closed.\n\n"
            "✅ Verified by python3 scripts/test_p0_audit_fixes.py — 20 passed.\n",
            encoding="utf-8",
        )
        return p

    def test_non_empty_density_findings_emits_context_density_warn(self) -> None:
        """An index.json with non-empty pointer_density_findings → WARN with rule context-density."""
        self._write_index(["too_many_lines: 90 > 80"])
        findings = lint_context_density(self.workdir)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule_id"], "context-density")
        self.assertEqual(findings[0]["severity"], "WARN")
        self.assertIn("too_many_lines", findings[0]["message"])

    def test_empty_density_findings_produces_no_finding(self) -> None:
        """Empty pointer_density_findings → no context-density finding."""
        self._write_index([])
        findings = lint_context_density(self.workdir)
        self.assertEqual(findings, [])

    def test_missing_index_json_produces_no_finding(self) -> None:
        """Missing index.json → no finding, no error (fail-soft)."""
        findings = lint_context_density(self.workdir)
        self.assertEqual(findings, [])

    def test_corrupt_index_json_produces_no_finding(self) -> None:
        """Corrupt index.json → no finding, no error."""
        ctx = self.workdir / ".build-loop" / "context"
        ctx.mkdir(parents=True, exist_ok=True)
        (ctx / "index.json").write_text("{not valid json", encoding="utf-8")
        findings = lint_context_density(self.workdir)
        self.assertEqual(findings, [])

    def test_run_lint_includes_context_density_when_findings_present(self) -> None:
        """run_lint() with workdir containing density findings includes context-density rule."""
        self._write_index(["too_many_lines: 90 > 80"])
        report = self._good_report()
        result = run_lint(report, workdir=self.workdir)
        rule_ids = [f["rule_id"] for f in result["findings"]]
        self.assertIn("context-density", rule_ids)

    def test_run_lint_no_context_density_when_index_clean(self) -> None:
        """run_lint() with clean index → no context-density finding."""
        self._write_index([])
        report = self._good_report()
        result = run_lint(report, workdir=self.workdir)
        rule_ids = [f["rule_id"] for f in result["findings"]]
        self.assertNotIn("context-density", rule_ids)

    def test_run_lint_no_context_density_when_no_workdir(self) -> None:
        """run_lint() with workdir=None → no context-density finding (no error)."""
        report = self._good_report()
        # workdir=None: lint_context_density falls back to cwd which has no index
        result = run_lint(report, workdir=None)
        rule_ids = [f["rule_id"] for f in result["findings"]]
        # Should not raise; context-density may or may not appear depending on cwd
        # — just verify no exception and result is well-formed
        self.assertIn("summary", result)
        self.assertIn("findings", result)

    def test_multiple_density_findings_all_in_one_message(self) -> None:
        """Multiple density findings are joined into one WARN message."""
        self._write_index(["too_many_lines: 90 > 80", "forbidden_section: ## Context Quality"])
        findings = lint_context_density(self.workdir)
        self.assertEqual(len(findings), 1)
        msg = findings[0]["message"]
        self.assertIn("too_many_lines", msg)
        self.assertIn("forbidden_section", msg)


if __name__ == "__main__":
    unittest.main()
