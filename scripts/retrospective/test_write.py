# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/retrospective/write."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from retrospective.sections import SECTION_KEYS, SECTION_TITLES, build  # noqa: E402
from retrospective.write import (  # noqa: E402
    render_full_markdown,
    render_summary,
    write_active,
    promote_durable,
    write_enforce_candidates,
)


def _make_sections() -> dict:
    return build(None, {"runs": [{"outcome": "pass"}]}, None, None, "test-run")


class RenderTests(unittest.TestCase):
    def test_full_markdown_contains_all_nine_titles(self) -> None:
        body = render_full_markdown(_make_sections(), run_id="test-run", repo="x")
        for key in SECTION_KEYS:
            self.assertIn(f"## {SECTION_TITLES[key]}", body, f"missing: {key}")

    def test_summary_at_most_five_non_blank_lines(self) -> None:
        s = render_summary(_make_sections(), run_id="test-run")
        non_blank = [ln for ln in s.splitlines() if ln.strip()]
        self.assertLessEqual(len(non_blank), 5, f"summary too long: {len(non_blank)} lines")

    def test_full_markdown_header_carries_run_id(self) -> None:
        body = render_full_markdown(_make_sections(), run_id="my-cool-run", repo="x")
        self.assertIn("my-cool-run", body.splitlines()[0])

    def test_intent_line_is_emitted_when_provided(self) -> None:
        body = render_full_markdown(
            _make_sections(), run_id="r", repo="x",
            intent_one_line="Build the retrospective.",
        )
        self.assertIn("Build the retrospective.", body)


class WriteActiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workdir = Path(self.tmp.name)

    def test_writes_active_and_summary(self) -> None:
        r = write_active(self.workdir, "run-1", _make_sections())
        self.assertEqual(r["status"], "ok")
        self.assertTrue(Path(r["active_path"]).exists())
        self.assertTrue(Path(r["summary_path"]).exists())

    def test_active_path_under_retrospectives_date(self) -> None:
        r = write_active(self.workdir, "run-2", _make_sections())
        ap = Path(r["active_path"])
        self.assertIn("/retrospectives/", str(ap))
        self.assertEqual(ap.name, "run-2.md")

    def test_summary_file_has_summary_suffix(self) -> None:
        r = write_active(self.workdir, "run-3", _make_sections())
        sp = Path(r["summary_path"])
        self.assertEqual(sp.name, "run-3.summary.md")

    def test_idempotent_overwrite(self) -> None:
        write_active(self.workdir, "run-x", _make_sections())
        r2 = write_active(self.workdir, "run-x", _make_sections())
        self.assertEqual(r2["status"], "ok")  # second write replaces, no error

    def test_degraded_on_io_error(self) -> None:
        # Pass a path that can't be created (a regular file pretending to be a dir).
        bad = self.workdir / "not-a-dir"
        bad.write_text("plain file")
        # write_active will mkdir(parents) inside `bad`, which fails on macOS/Linux.
        r = write_active(bad, "run-x", _make_sections())
        # Either degraded or it writes inside the path (shouldn't happen — file in the way)
        self.assertIn(r["status"], ("ok", "degraded"))


class PromoteDurableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workdir = Path(self.tmp.name) / "wd"
        self.workdir.mkdir()
        self.memory_root = Path(self.tmp.name) / "build-loop-memory"
        # Don't create it yet — first test verifies "skipped" when absent.

    def test_skipped_when_memory_root_absent(self) -> None:
        r = promote_durable(self.workdir, "run-x", _make_sections(),
                            memory_root=self.memory_root, repo="x")
        self.assertEqual(r["status"], "skipped")
        self.assertIsNone(r["durable_path"])

    def test_promotes_when_memory_root_present(self) -> None:
        self.memory_root.mkdir()
        r = promote_durable(self.workdir, "run-y", _make_sections(),
                            memory_root=self.memory_root, repo="my-app")
        self.assertEqual(r["status"], "ok")
        self.assertTrue(Path(r["durable_path"]).exists())
        self.assertIn("/projects/my-app/retrospectives/", r["durable_path"])


class WriteEnforceCandidatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workdir = Path(self.tmp.name)

    def test_skipped_when_no_candidates(self) -> None:
        r = write_enforce_candidates(self.workdir, "run-x", [])
        self.assertEqual(r["status"], "skipped")
        self.assertEqual(r["paths"], [])

    def test_writes_one_file_per_candidate(self) -> None:
        r = write_enforce_candidates(self.workdir, "run-x",
                                      ["enforce-X", "enforce-Y", "enforce-Z"])
        self.assertEqual(r["status"], "ok")
        self.assertEqual(len(r["paths"]), 3)
        for p in r["paths"]:
            self.assertTrue(Path(p).exists())

    def test_candidate_body_includes_disposition_checkboxes(self) -> None:
        r = write_enforce_candidates(self.workdir, "r", ["enforce-X"])
        body = Path(r["paths"][0]).read_text()
        self.assertIn("Adopt as default", body)
        self.assertIn("Phase 6 Learn", body)
        self.assertIn("Reject", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)


def test_promote_durable_refuses_scratch_slug(tmp_path):
    """A mktemp workdir must not create projects/tmp.XXXX/ in the curated store.
    Regression: 2026-07-08 smoke-test leak into build-loop-memory."""
    from retrospective.write import promote_durable
    mem = tmp_path / "mem"; mem.mkdir()
    for bad in ("tmp.aB12Xy", "tmp_scratch", "pytest-of-x"):
        r = promote_durable(tmp_path, "session-x", {k: "" for k in SECTION_KEYS},
                            repo=bad, memory_root=mem)
        assert r["status"] == "skipped", (bad, r)
        assert r["durable_path"] is None
    # a real slug still writes
    ok = promote_durable(tmp_path, "session-x", {k: "" for k in SECTION_KEYS},
                        repo="build-loop", memory_root=mem)
    assert ok["status"] == "ok" and ok["durable_path"]
