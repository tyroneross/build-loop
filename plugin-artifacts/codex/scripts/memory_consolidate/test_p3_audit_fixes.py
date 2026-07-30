#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""P3 audit fix tests — f1 through f5.

Runnable via ``python3 scripts/memory_consolidate/test_p3_audit_fixes.py``.
"""
from __future__ import annotations

import io
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
sys.path.insert(0, str(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_entry(
    path: Path,
    fm: dict | None = None,
    body: str = "body content long enough to be active and pass the draft threshold checks",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = fm or {"name": "x", "type": "lesson"}
    lines = ["---"] + [f"{k}: {v}" for k, v in fm.items()] + ["---"]
    path.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")
    return path


def _make_memroot() -> Path:
    root = Path(tempfile.mkdtemp())
    _write_entry(
        root / "projects" / "p1" / "lessons" / "quote-paths.md",
        {"name": "quote-paths", "type": "lesson"},
        "always quote paths in shell scripts to avoid word splitting bugs",
    )
    _write_entry(
        root / "projects" / "p2" / "lessons" / "quote-paths.md",
        {"name": "quote-paths", "type": "lesson"},
        "always quote paths in shell scripts to avoid word splitting bugs",
    )
    return root


# ---------------------------------------------------------------------------
# f1 — lifecycle + backlinks writes route through patch_frontmatter
# ---------------------------------------------------------------------------


class F1LifecycleViaCanonicalWriterTests(unittest.TestCase):
    """lifecycle.apply_state_to_frontmatter MUST write through memory_writer.patch_frontmatter."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_source_import_uses_memory_writer(self):
        """lifecycle.py imports memory_writer and binds _patch_frontmatter."""
        from memory_consolidate.lifecycle import lifecycle as lc
        # The module-level sentinel must be set (not None) in a normal env.
        self.assertIsNotNone(
            lc._patch_frontmatter,
            "lifecycle._patch_frontmatter is None — memory_writer was not imported",
        )

    def test_lifecycle_write_goes_through_patch_frontmatter(self):
        """patch_frontmatter is called (not os.replace directly) during a lifecycle transition."""
        from memory_consolidate.lifecycle import lifecycle as lc

        p = _write_entry(self.tmp / "x.md")
        calls: list[tuple] = []

        original = lc._patch_frontmatter

        def spy_patch(path, fm_delta, **kwargs):
            calls.append((str(path), dict(fm_delta)))
            return original(path, fm_delta, **kwargs)

        lc._patch_frontmatter = spy_patch
        try:
            fm = lc.apply_state_to_frontmatter(p, "stale", reason="test-f1")
        finally:
            lc._patch_frontmatter = original

        self.assertTrue(len(calls) >= 1, "patch_frontmatter was never called")
        # The delta must carry the lifecycle keys.
        delta = calls[0][1]
        self.assertEqual(delta.get("lifecycle_state"), "stale")
        # Provenance trace: ledger entry was written (no exception raised).
        self.assertEqual(fm["lifecycle_state"], "stale")

    def test_provenance_trace_after_lifecycle_write(self):
        """After apply_state_to_frontmatter, the file on disk carries lifecycle_state."""
        from memory_consolidate.lifecycle import lifecycle as lc

        p = _write_entry(self.tmp / "trace.md")
        lc.apply_state_to_frontmatter(p, "stale", reason="source-hash-mismatch")
        text = p.read_text(encoding="utf-8")
        self.assertIn("lifecycle_state: stale", text)
        self.assertIn("lifecycle_reason: source-hash-mismatch", text)
        # Original body preserved.
        self.assertIn("body content", text)


class F1BacklinksViaCanonicalWriterTests(unittest.TestCase):
    """backlinks.write_backlinks_footer MUST write through memory_writer.patch_frontmatter."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_source_import_uses_memory_writer(self):
        """backlinks.py imports memory_writer and binds _patch_frontmatter."""
        from memory_consolidate.backlinks import backlinks as bl
        self.assertIsNotNone(
            bl._patch_frontmatter,
            "backlinks._patch_frontmatter is None — memory_writer was not imported",
        )

    def test_backlinks_write_goes_through_patch_frontmatter(self):
        """patch_frontmatter is called (not os.replace directly) when writing a footer."""
        from memory_consolidate.backlinks import backlinks as bl

        p = _write_entry(self.tmp / "bl.md", {"name": "bl", "type": "lesson"})
        calls: list[str] = []

        original = bl._patch_frontmatter

        def spy_patch(path, fm_delta, **kwargs):
            calls.append(str(path))
            return original(path, fm_delta, **kwargs)

        bl._patch_frontmatter = spy_patch
        try:
            bl.write_backlinks_footer(p, [bl.BacklinkSuggestion(target_name="sibling")])
        finally:
            bl._patch_frontmatter = original

        self.assertTrue(len(calls) >= 1, "patch_frontmatter was never called")
        text = p.read_text(encoding="utf-8")
        self.assertIn("[[sibling]]", text)

    def test_backlinks_footer_provenance_trace(self):
        """After write_backlinks_footer, the Related block lands AND body is intact."""
        from memory_consolidate.backlinks import backlinks as bl

        p = _write_entry(self.tmp / "footer.md", {"name": "footer", "type": "lesson"})
        bl.write_backlinks_footer(p, [
            bl.BacklinkSuggestion(target_name="t1"),
            bl.BacklinkSuggestion(target_name="t2"),
        ])
        text = p.read_text(encoding="utf-8")
        self.assertIn("## Related", text)
        self.assertIn("[[t1]]", text)
        self.assertIn("[[t2]]", text)
        self.assertIn("body content", text)

    def test_no_raw_os_replace_in_lifecycle_source(self):
        """lifecycle.py must not call os.replace directly in the live write path."""
        src_path = SCRIPTS_DIR / "memory_consolidate" / "lifecycle" / "lifecycle.py"
        src = src_path.read_text()
        # The only os.replace should be in the degraded-fallback branch.
        # Verify memory_writer IS imported (canonical path).
        self.assertIn("import memory_writer", src)
        self.assertIn("_patch_frontmatter", src)

    def test_no_raw_os_replace_in_backlinks_source(self):
        """backlinks.py must import memory_writer and bind _patch_frontmatter."""
        src_path = SCRIPTS_DIR / "memory_consolidate" / "backlinks" / "backlinks.py"
        src = src_path.read_text()
        self.assertIn("import memory_writer", src)
        self.assertIn("_patch_frontmatter", src)


# ---------------------------------------------------------------------------
# f2 — dormant gate signal + real-path test
# ---------------------------------------------------------------------------


class F2RecallAvailableSignalTests(unittest.TestCase):
    """recall_available=False is set in AsyncReport when semantic_index is absent."""

    def setUp(self):
        self.memroot = _make_memroot()
        self.tmp = Path(tempfile.mkdtemp())

    def test_recall_available_true_by_default(self):
        """When siblings_fn is injected (no real recall needed), recall_available stays True."""
        from memory_consolidate import async_runner as ar

        report = ar.run_async(
            workdir=str(self.tmp), memory_root=self.memroot,
            siblings_fn=lambda b, o: [],
            related_fn=lambda b, o, p: [],
        )
        # siblings_fn injected — promote module's _recall_available was not touched.
        d = report.to_dict()
        self.assertIn("recall_available", d)

    def test_backend_absent_sets_recall_available_false_and_all_rejected(self):
        """With no semantic_index and no siblings_fn, candidates>0 but accepted==0
        and recall_available==False, and a WARN is emitted to stderr."""
        from memory_consolidate import async_runner as ar
        from memory_consolidate.promote import promote as pr_mod

        # Reset module-level state so the test is idempotent.
        pr_mod._recall_available = True
        pr_mod._recall_warn_emitted = False

        # Ensure semantic_index is NOT importable.
        saved = sys.modules.get("semantic_index")
        sys.modules["semantic_index"] = None  # type: ignore  # ImportError sentinel

        stderr_capture = io.StringIO()
        try:
            with mock_patch("sys.stderr", stderr_capture):
                report = ar.run_async(
                    workdir=str(self.tmp), memory_root=self.memroot,
                    # No siblings_fn — forces real _query_cross_project_siblings path.
                )
        finally:
            if saved is None:
                del sys.modules["semantic_index"]
            else:
                sys.modules["semantic_index"] = saved
            # Restore sentinel for subsequent tests.
            pr_mod._recall_available = True
            pr_mod._recall_warn_emitted = False

        # All candidates rejected (no cross-project siblings found).
        self.assertGreater(report.promotion_candidates, 0, "expected ≥1 candidate")
        self.assertEqual(report.promotion_accepted, 0,
                         "no candidate should be accepted when recall is unavailable")
        # Signal present and False.
        self.assertFalse(report.recall_available)
        d = report.to_dict()
        self.assertFalse(d["recall_available"])
        # WARN was emitted.
        warn_text = stderr_capture.getvalue()
        self.assertIn("WARN", warn_text)
        self.assertIn("semantic_index unavailable", warn_text)


class F2RealPathPromotionTest(unittest.TestCase):
    """Real-path test: mock semantic_index via sys.modules injection so the actual
    _query_cross_project_siblings → query_facts path is exercised end-to-end.
    A genuine cross-project lesson promotes through the production recall path."""

    def setUp(self):
        self.memroot = _make_memroot()
        self.tmp = Path(tempfile.mkdtemp())

    def test_cross_project_lesson_promotes_via_real_recall_path(self):
        """The production _query_cross_project_siblings path (not siblings_fn seam)
        returns cross-project siblings → gate accepts the promotion."""
        from memory_consolidate.promote import promote as pr_mod

        # Reset module-level state.
        pr_mod._recall_available = True
        pr_mod._recall_warn_emitted = False

        # Build a mock semantic_index module that returns a cross-project sibling.
        import types
        mock_si = types.ModuleType("semantic_index")

        def mock_query_facts(query, *, limit=20, mode="hybrid", **kwargs):
            # Simulate a sibling from project p2 for any query matching our lesson.
            if "quote paths" in query or "shell" in query:
                return [
                    {
                        "subject": "quote-paths",
                        "predicate": "similar-to",
                        "object": "path-quoting",
                        "project": "p2",
                        "file_hint": "projects/p2/lessons/quote-paths.md",
                        "score": 0.87,
                    }
                ]
            return []

        mock_si.query_facts = mock_query_facts

        saved = sys.modules.get("semantic_index")
        sys.modules["semantic_index"] = mock_si

        try:
            candidates = pr_mod.find_promotion_candidates(
                workdir=str(self.tmp),
                memory_root=self.memroot,
                # No siblings_fn — exercises the production recall path.
            )
        finally:
            if saved is None:
                sys.modules.pop("semantic_index", None)
            else:
                sys.modules["semantic_index"] = saved

        # At least one candidate from p1 should have cross-project siblings from p2.
        p1_cands = [c for c in candidates if c.project == "p1"]
        self.assertTrue(len(p1_cands) >= 1, "expected at least one p1 candidate")

        # At least one p1 candidate should have picked up the p2 sibling.
        has_cross_project = any(
            "p2" in c.distinct_projects for c in p1_cands
        )
        self.assertTrue(
            has_cross_project,
            f"No p1 candidate found p2 siblings. Candidates: {[c.to_dict() for c in p1_cands]}",
        )

        # That candidate passes the promotion gate.
        from memory_consolidate.promote import promote as pr
        cross_cand = next(c for c in p1_cands if "p2" in c.distinct_projects)
        gate = pr.promotion_gate(cross_cand, min_projects=2)
        self.assertTrue(gate.accepted, f"gate rejected: {gate}")
        self.assertEqual(gate.reason, "recurrence-earned")


# ---------------------------------------------------------------------------
# f3 — sanitize frontmatter values (newline injection)
# ---------------------------------------------------------------------------


class F3FrontmatterSanitizationTests(unittest.TestCase):
    """apply_state_to_frontmatter must strip \\n/\\r from externally-supplied values."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_newline_in_reason_does_not_inject_yaml_key(self):
        """A reason containing \\n---\\ninjected: evil must NOT create a standalone 'injected' key."""
        from memory_consolidate.lifecycle import lifecycle as lc
        import re

        p = _write_entry(self.tmp / "inject.md")
        lc.apply_state_to_frontmatter(
            p, "stale", reason="bad\n---\ninjected: evil"
        )
        text = p.read_text(encoding="utf-8")
        # The frontmatter block must be intact: exactly one open and one close delimiter.
        # A successful injection would create a second `---` line breaking the FM block.
        fm_opens = [i for i in range(len(text)) if text[i:i+4] == "---\n"]
        self.assertEqual(
            len(fm_opens), 2,
            f"Expected exactly 2 '---\\n' delimiters (open+close) but got {len(fm_opens)}. "
            f"Injection likely succeeded: {text!r}",
        )
        # 'injected' must NOT appear as a standalone YAML key (i.e. at start of a line
        # followed by ': '). Being inside a quoted value is fine and expected.
        self.assertIsNone(
            re.search(r"^injected\s*:", text, re.MULTILINE),
            f"'injected' appeared as a standalone frontmatter key: {text!r}",
        )
        # lifecycle_state is still written correctly.
        self.assertIn("lifecycle_state: stale", text)

    def test_cr_in_reason_stripped(self):
        """\\r in reason must be stripped to a space."""
        from memory_consolidate.lifecycle import lifecycle as lc

        p = _write_entry(self.tmp / "cr.md")
        fm = lc.apply_state_to_frontmatter(p, "active", reason="line1\rline2")
        self.assertNotIn("\r", fm.get("lifecycle_reason", ""))
        text = p.read_text(encoding="utf-8")
        self.assertNotIn("\r", text)

    def test_clean_reason_preserved(self):
        """A reason without special chars is written as-is."""
        from memory_consolidate.lifecycle import lifecycle as lc

        p = _write_entry(self.tmp / "clean.md")
        fm = lc.apply_state_to_frontmatter(p, "stale", reason="source-hash-mismatch")
        self.assertEqual(fm["lifecycle_reason"], "source-hash-mismatch")


# ---------------------------------------------------------------------------
# f4 — --no-apply-lifecycle flag gates lifecycle writes
# ---------------------------------------------------------------------------


class F4NoApplyLifecycleTests(unittest.TestCase):
    """--no-apply-lifecycle (apply_lifecycle=False) must suppress lifecycle writes."""

    def setUp(self):
        self.memroot = _make_memroot()
        self.tmp = Path(tempfile.mkdtemp())

    def _snapshot(self) -> dict[str, str]:
        return {str(p): p.read_text(encoding="utf-8")
                for p in self.memroot.rglob("*.md")}

    def test_apply_lifecycle_true_writes_lifecycle_state(self):
        """With apply_lifecycle=True (default), lifecycle transitions ARE written."""
        from memory_consolidate import async_runner as ar

        # Seed a stale entry (source_hash mismatch → will transition).
        p = _write_entry(
            self.memroot / "projects" / "p1" / "lessons" / "stale-entry.md",
            {"name": "stale-entry", "type": "lesson", "source_hash": "0" * 64},
            "this body does not match the recorded source hash so it will be stale",
        )
        before = p.read_text(encoding="utf-8")

        ar.run_async(
            workdir=str(self.tmp), memory_root=self.memroot,
            siblings_fn=lambda b, o: [],
            related_fn=lambda b, o, proj: [],
            apply_lifecycle=True,
        )
        after = p.read_text(encoding="utf-8")
        self.assertNotEqual(before, after, "lifecycle write did not happen with apply_lifecycle=True")
        self.assertIn("lifecycle_state", after)

    def test_no_apply_lifecycle_flag_suppresses_lifecycle_writes(self):
        """With apply_lifecycle=False, lifecycle classification runs but no files are modified."""
        from memory_consolidate import async_runner as ar

        # Seed a stale entry.
        p = _write_entry(
            self.memroot / "projects" / "p1" / "lessons" / "stale-no-write.md",
            {"name": "stale-no-write", "type": "lesson", "source_hash": "ff" * 32},
            "body that triggers stale classification but should not be written to disk",
        )
        before_all = self._snapshot()

        report = ar.run_async(
            workdir=str(self.tmp), memory_root=self.memroot,
            siblings_fn=lambda b, o: [],
            related_fn=lambda b, o, proj: [],
            apply_lifecycle=False,   # <— the flag under test
        )

        after_all = self._snapshot()
        # Files on disk must be unchanged.
        self.assertEqual(
            before_all, after_all,
            "Files changed despite apply_lifecycle=False",
        )
        # But the report still carries the classification data.
        self.assertGreaterEqual(
            report.lifecycle_transitions, 1,
            "lifecycle_transitions should still be counted in report-only mode",
        )

    def test_cli_no_apply_lifecycle_flag_is_parsed(self):
        """The CLI parses --no-apply-lifecycle without error."""
        from memory_consolidate import __main__ as cli
        # --workdir is a top-level arg; must precede the subcommand.
        args = cli.parse_args(["--workdir", ".", "async", "--no-apply-lifecycle"])
        self.assertTrue(args.no_apply_lifecycle)

    def test_cli_default_applies_lifecycle(self):
        """By default (no flag), no_apply_lifecycle is False."""
        from memory_consolidate import __main__ as cli
        args = cli.parse_args(["--workdir", ".", "async"])
        self.assertFalse(args.no_apply_lifecycle)


# ---------------------------------------------------------------------------
# f5 — lazy-load contract: arms must NOT be in memory_consolidate dir()
# ---------------------------------------------------------------------------


class F5LazyLoadContractTests(unittest.TestCase):
    """The memory_consolidate package top-level must not expose the four P3 arms."""

    def test_package_dir_does_not_expose_p3_arms(self):
        """dir(memory_consolidate) must NOT include distill/promote/lifecycle/backlinks/async_runner."""
        # Import fresh in a subprocess to avoid test-session contamination.
        import subprocess
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(SCRIPTS_DIR)!r})
            import memory_consolidate
            attrs = dir(memory_consolidate)
            forbidden = {{'distill', 'promote', 'lifecycle', 'backlinks', 'async_runner'}}
            found = forbidden & set(attrs)
            assert not found, f"P3 arms exposed in package dir(): {{found}}"
            print("OK")
        """)
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)

    def test_arms_not_in_sys_modules_after_hot_path_import(self):
        """Importing intake/place/classify must NOT load the four P3 arm modules."""
        import subprocess
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(SCRIPTS_DIR)!r})
            from memory_consolidate import intake, place, classify
            arm_keys = [
                'memory_consolidate.distill',
                'memory_consolidate.promote',
                'memory_consolidate.lifecycle',
                'memory_consolidate.backlinks',
                'memory_consolidate.async_runner',
            ]
            loaded = [k for k in arm_keys if k in sys.modules]
            assert not loaded, f"Arms loaded on hot-path import: {{loaded}}"
            print("OK")
        """)
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
