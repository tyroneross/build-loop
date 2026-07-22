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
    stamp_durable_in_summary,
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


class DurableSummaryLineTests(unittest.TestCase):
    """BUG 2 (2026-07-21): `wrote_memory` was structurally unreachable.

    `closeout.status._latest_retro_summary` classifies a run as `wrote_memory`
    only on a summary line starting with `durable:`, and `render_summary` never
    emitted one — so no retrospective, however good, and no successful
    `promote_durable` could ever reach that status.
    """

    def test_durable_line_emitted_when_path_supplied(self) -> None:
        s = render_summary(_make_sections(), run_id="r",
                           durable_path="/mem/projects/demo/retrospectives/2026-07-21/r.md")
        lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
        self.assertTrue(
            any(ln.startswith("durable:") for ln in lines),
            f"no line starts with 'durable:': {lines}",
        )

    def test_no_durable_line_when_promotion_skipped(self) -> None:
        """The honest negative: a skipped/failed/queued promotion passes None,
        so `no_durable_lesson` stays reachable and truthful."""
        for missing in (None, ""):
            s = render_summary(_make_sections(), run_id="r", durable_path=missing)
            lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
            self.assertFalse(
                any(ln.startswith("durable:") for ln in lines),
                f"durable line leaked for {missing!r}: {lines}",
            )

    def test_summary_budget_holds_with_durable_line(self) -> None:
        s = render_summary(_make_sections(), run_id="r", durable_path="/mem/x.md")
        non_blank = [ln for ln in s.splitlines() if ln.strip()]
        self.assertLessEqual(len(non_blank), 5, f"summary too long: {non_blank}")

    def test_durable_line_matches_the_readers_grammar(self) -> None:
        """Writer and reader must agree on the literal, not by coincidence."""
        from closeout.status import _latest_retro_summary  # noqa: PLC0415
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        wd = Path(tmp.name)
        want = "/mem/projects/demo/retrospectives/2026-07-21/run-9.md"
        write_active(wd, "run-9", _make_sections(), durable_path=want)
        got = _latest_retro_summary(wd)
        self.assertEqual(got.get("durable_path"), want)


class NoTranscriptBannerTests(unittest.TestCase):
    """A retro built from no transcript must SAY SO in the reader's first glance.

    Observed 2026-07-21: a retrospective ran with transcript_present=false and
    prompt_count=0 for a session with hundreds of tool calls, and still rendered
    eleven confident-looking sections.
    """

    def test_banner_present_when_transcript_absent(self) -> None:
        sections = build(None, {"runs": [{"outcome": "pass"}]}, None, None, "r",
                         transcript_note="no transcript for this run (host=claude_code)")
        body = render_full_markdown(sections, run_id="r", repo="demo")
        self.assertIn("NO TRANSCRIPT", body)
        self.assertIn("zero session evidence", body)
        self.assertIn("no transcript for this run", body)

    def test_banner_absent_when_transcript_present(self) -> None:
        sections = build(None, {"runs": [{"outcome": "pass"}]}, None, None, "r")
        sections["meta"]["transcript_present"] = True
        body = render_full_markdown(sections, run_id="r", repo="demo")
        self.assertNotIn("NO TRANSCRIPT", body)

    def test_banner_does_not_displace_the_title_line(self) -> None:
        sections = build(None, {"runs": [{"outcome": "pass"}]}, None, None, "my-run")
        body = render_full_markdown(sections, run_id="my-run", repo="demo")
        self.assertIn("my-run", body.splitlines()[0])

    def test_summary_headline_flags_missing_transcript(self) -> None:
        sections = build(None, {"runs": [{"outcome": "pass"}]}, None, None, "r")
        self.assertIn("NO TRANSCRIPT", render_summary(sections, run_id="r"))


class StampDurableInSummaryTests(unittest.TestCase):
    """A queued promotion drains LATER — often on a different day. Without the
    stamp, a queued-then-drained promotion could never reach `wrote_memory`."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wd = Path(self.tmp.name)

    def _summary_dir(self, date: str) -> Path:
        d = self.wd / ".build-loop" / "retrospectives" / date
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_stamps_a_summary_written_on_a_different_day(self) -> None:
        """The cross-day case the queue exists to serve. A `_today_iso()` lookup
        would silently no-op here."""
        d = self._summary_dir("2026-01-02")
        (d / "run-x.summary.md").write_text(
            "Retrospective run-x written (2026-01-02).\n"
            "  full file: .build-loop/retrospectives/2026-01-02/run-x.md\n"
        )
        r = stamp_durable_in_summary(self.wd, "run-x", "/mem/p/demo/r/run-x.md")
        self.assertEqual(r["status"], "ok")
        text = Path(r["summary_path"]).read_text()
        self.assertIn("durable: /mem/p/demo/r/run-x.md", text)

    def test_durable_line_lands_before_the_full_file_pointer(self) -> None:
        d = self._summary_dir("2026-01-02")
        (d / "run-x.summary.md").write_text(
            "headline\n  full file: .build-loop/retrospectives/2026-01-02/run-x.md\n"
        )
        r = stamp_durable_in_summary(self.wd, "run-x", "/mem/x.md")
        lines = [ln.strip() for ln in Path(r["summary_path"]).read_text().splitlines() if ln.strip()]
        self.assertLess(
            next(i for i, ln in enumerate(lines) if ln.startswith("durable:")),
            next(i for i, ln in enumerate(lines) if ln.startswith("full file:")),
        )

    def test_replaces_an_existing_durable_line_rather_than_duplicating(self) -> None:
        d = self._summary_dir("2026-01-02")
        (d / "run-x.summary.md").write_text(
            "headline\n  durable: /old/path.md\n  full file: x\n"
        )
        r = stamp_durable_in_summary(self.wd, "run-x", "/new/path.md")
        text = Path(r["summary_path"]).read_text()
        self.assertEqual(sum(1 for ln in text.splitlines() if ln.strip().startswith("durable:")), 1)
        self.assertIn("/new/path.md", text)
        self.assertNotIn("/old/path.md", text)

    def test_missing_summary_is_a_clean_skip_never_a_raise(self) -> None:
        """`promotion_queue.drain` turns any raise into a FAILED record, so a repo
        with no summary tree must be a no-op, not a drain failure."""
        r = stamp_durable_in_summary(self.wd, "never-written", "/mem/x.md")
        self.assertEqual(r["status"], "skipped")
        self.assertIsNone(r["summary_path"])

    def test_empty_durable_path_is_a_skip(self) -> None:
        self.assertEqual(stamp_durable_in_summary(self.wd, "r", "")["status"], "skipped")


def test_promote_durable_refuses_scratch_slug(tmp_path):
    """A mktemp workdir must not create projects/tmp.XXXX/ in the curated store.
    Regression: 2026-07-08 smoke-test leak into build-loop-memory."""
    from retrospective.write import promote_durable
    mem = tmp_path / "mem"; mem.mkdir()
    # shell mktemp (tmp.XXXX), Python tempfile (tmpXXXXXXXX, no dot), pytest,
    # scratch — all must be refused.
    for bad in ("tmp.aB12Xy", "tmp_scratch", "pytest-of-x", "tmpabcd12", "scratchpad", "mktemp123", "run-957538", "run_957538"):
        r = promote_durable(tmp_path, "session-x", {k: "" for k in SECTION_KEYS},
                            repo=bad, memory_root=mem)
        assert r["status"] == "skipped", (bad, r)
        assert r["durable_path"] is None
    # real project slugs — including short tmp-prefixed names — still write.
    for good in ("build-loop", "tmpl", "tmux-tool", "speak-savvy"):
        ok = promote_durable(tmp_path, "session-x", {k: "" for k in SECTION_KEYS},
                            repo=good, memory_root=mem)
        assert ok["status"] == "ok" and ok["durable_path"], good


def test_promote_durable_falls_through_when_enqueue_fails(monkeypatch, tmp_path):
    """f6: if the enqueue itself fails on a busy store, promote_durable must NOT
    claim status=queued (silent loss) — it falls through to the direct write."""
    import sys as _sys
    _scripts = str(Path(__file__).resolve().parent.parent)
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    import promotion_queue as pq
    from retrospective import write as retro_write

    repo = tmp_path / "repo"
    repo.mkdir()
    mem = tmp_path / "mem"
    mem.mkdir()
    (mem / pq.PEER_HOLD_MARKER).write_text("")  # store busy

    # Enqueue fails (returns queued=False).
    monkeypatch.setattr(pq, "enqueue", lambda *a, **k: {"queued": False, "reason": "boom"})
    res = retro_write.promote_durable(
        workdir=repo, run_id="run-f6", sections={"summary": "x"}, repo="demo",
        memory_root=mem,
    )
    # Must have fallen through to the direct write (status ok), not silently "queued".
    assert res["status"] != "queued"
    assert res["status"] == "ok"
    assert (mem / "projects" / "demo").exists()
