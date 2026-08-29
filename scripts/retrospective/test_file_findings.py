# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""test_file_findings.py — the retrospective filing contract.

Two things are proven here:

1. The GOLDEN FIXTURE acceptance test — the 2026-08-29 retrospective, whose six
   findings were filed by hand into three repos. The dry-run must find the same
   six and propose the same homes.
2. The SEEDED-DEFECT lint test — a retro that names an issue and files nothing
   must FAIL. A lint that has never been shown failing on a planted defect is
   not evidence, so `test_lint_fails_on_seeded_defect` plants one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retrospective import file_findings as ff  # noqa: E402

BUILD_LOOP_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = (Path.home() / "dev" / "git-folder" / "build-loop-memory" / "retrospectives"
          / "2026-08-29-session-ambient-agent-decision-day.md")

# A retro that names findings, in the hand-written shape the fixture uses.
RETRO_WITH_FINDINGS = """# Retrospective — 2026-08-29 sample

## What shipped

| Workstream | Delivered |
|---|---|
| Storage | compression |

## My efficiency failures, ranked by cost

1. **Surface-ownership unchecked before tool dispatch.** Used mockup-gallery when
   another plugin owned the flow. Cost: one full agent run + relaunch. Fix encoded:
   check which plugin owns a surface before dispatching to it.
2. **Guard suite regression in build-loop.** A write clobbered eight tests.
   Cost: a full re-review. Fix: run `git ls-files` before any Write.

## Owner productivity patterns

1. **Decision latency is the bottleneck.** Not a defect — an observation.

## What went well

1. **Evidence-first closeouts.** Auditor re-derived every number.

## Standing debts left open

Checkpoint decision, write-tail latency, C10 baseline pin.
"""


def _write(tmp: Path, name: str, text: str) -> Path:
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return p


class TestExtraction(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_extracts_only_finding_bearing_sections(self):
        retro = _write(self.tmp, "2026-08-29-r.md", RETRO_WITH_FINDINGS)
        findings = ff.extract_findings(retro.read_text(encoding="utf-8"), retro)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(f.section.startswith("My efficiency failures")
                            for f in findings))

    def test_observations_and_wins_are_not_findings(self):
        """`patterns` and `went well` describe the run; filing them would flood
        every target repo and train readers to ignore the filing."""
        retro = _write(self.tmp, "2026-08-29-r.md", RETRO_WITH_FINDINGS)
        titles = [f.title for f in ff.extract_findings(retro.read_text(encoding="utf-8"), retro)]
        self.assertNotIn("Decision latency is the bottleneck", titles)
        self.assertNotIn("Evidence-first closeouts", titles)

    def test_prose_only_section_yields_no_findings(self):
        """"Standing debts left open" is one comma-separated sentence. Filing it
        as a finding would produce one unactionable blob."""
        retro = _write(self.tmp, "2026-08-29-r.md", RETRO_WITH_FINDINGS)
        sections = [f.section for f in ff.extract_findings(retro.read_text(encoding="utf-8"), retro)]
        self.assertNotIn("Standing debts left open", sections)

    def test_segments_derived_from_markers(self):
        retro = _write(self.tmp, "2026-08-29-r.md", RETRO_WITH_FINDINGS)
        f = ff.extract_findings(retro.read_text(encoding="utf-8"), retro)[0]
        self.assertIn("mockup-gallery", f.what_happened)
        self.assertIn("one full agent run", f.impact)
        self.assertIn("check which plugin owns a surface", f.recommendation)
        self.assertEqual(f.observed, "2026-08-29")

    def test_underivable_segment_is_reported_not_invented(self):
        """The tool never fabricates a segment — that is the exact defect it
        exists to fix (a filed item that looks complete and says nothing)."""
        retro = _write(self.tmp, "2026-08-29-r.md", RETRO_WITH_FINDINGS)
        f = ff.extract_findings(retro.read_text(encoding="utf-8"), retro)[0]
        self.assertEqual(f.why, "")
        self.assertIn("why", f.missing_segments())


class TestTargetResolution(unittest.TestCase):
    """Ladder + shadowing, on a synthetic repo root (no real-filesystem coupling)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "git-folder"
        self.root.mkdir(parents=True)
        self.bl = self._repo("build-loop", known_issues=True, backlog=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _repo(self, name: str, *, backlog=False, known_issues=False,
              lessons=False) -> Path:
        repo = self.root / name
        (repo / ".git").mkdir(parents=True)
        if backlog:
            (repo / ".build-loop" / "backlog").mkdir(parents=True)
        if known_issues:
            (repo / "KNOWN-ISSUES.md").write_text("# Known issues\n", encoding="utf-8")
        if lessons:
            (repo / "LESSONS-LEARNED.md").write_text("# Lessons\n", encoding="utf-8")
        return repo

    def _plugin(self, monorepo: Path, target: Path) -> None:
        plugins = monorepo / "plugins"
        plugins.mkdir(exist_ok=True)
        (plugins / target.name).symlink_to(target)

    def _target_for(self, text: str, default_repo=None) -> ff.Target:
        idx = ff.build_repo_index([self.root])
        f = ff.Finding(index=1, section="Issues", title="t", text=text)
        return ff.resolve_target(f, idx, self.bl, default_repo)

    def test_ladder_prefers_backlog(self):
        self._repo("alpha-svc", backlog=True, known_issues=True, lessons=True)
        t = self._target_for("alpha-svc dropped a request")
        self.assertEqual(t.mechanism, "backlog")

    def test_ladder_falls_to_known_issues_then_lessons(self):
        self._repo("beta-svc", known_issues=True, lessons=True)
        self.assertEqual(self._target_for("beta-svc timed out").mechanism, "known-issues")
        self._repo("gamma-svc", lessons=True)
        self.assertEqual(self._target_for("gamma-svc leaked").mechanism, "lessons-learned")

    def test_unknown_surface_falls_back_to_build_loop(self):
        t = self._target_for("something nobody has ever heard of")
        self.assertTrue(t.fallback or t.repo_name == "build-loop")
        self.assertEqual(Path(t.path), self.bl / "KNOWN-ISSUES.md")

    def test_vendored_plugin_does_not_shadow_the_real_repo(self):
        """REGRESSION: RossLabs-AI-Toolkit/plugins/build-loop sorts before the
        real `build-loop` repo, so first-writer-wins sent every build-loop
        finding to the toolkit. Observed on the 2026-08-29 fixture."""
        toolkit = self._repo("AAA-Toolkit", lessons=True)
        self._plugin(toolkit, self.bl)
        idx = ff.build_repo_index([self.root])
        self.assertEqual(idx.tokens["build-loop"][0], self.bl)
        self.assertEqual(self._target_for("build-loop guard suite broke").repo_name,
                         "build-loop")

    def test_walks_outward_to_the_monorepo_that_ships_it(self):
        """A plugin repo with no issue log inherits its monorepo's — which is
        where the human filed the mockup-gallery finding."""
        plugin = self._repo("widget-gallery")          # no filing surface at all
        toolkit = self._repo("ZZZ-Toolkit", lessons=True)
        self._plugin(toolkit, plugin)
        t = self._target_for("widget-gallery showed superseded mockups")
        self.assertEqual(t.repo_name, "ZZZ-Toolkit")
        self.assertEqual(t.mechanism, "lessons-learned")

    def test_actor_names_do_not_attribute_a_finding(self):
        """REGRESSION: "which Codex had to propose" routed a build-loop process
        lesson to agent-rally-point, because a `codex` plugin dir exists there.
        An agent/model name says who did the work, not what broke."""
        self._repo("codex", known_issues=True)
        t = self._target_for("Structural fix reached on round three, "
                             "which Codex had to propose.")
        self.assertTrue(t.fallback)
        self.assertEqual(Path(t.path), self.bl / "KNOWN-ISSUES.md")

    def test_longest_token_wins(self):
        self._repo("gallery", known_issues=True)
        specific = self._repo("widget-gallery", lessons=True)
        idx = ff.build_repo_index([self.root])
        repos, token = idx.match("the widget-gallery run")
        self.assertEqual(token, "widget-gallery")
        self.assertEqual(repos[0], specific)


class TestLint(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_lint_fails_on_seeded_defect(self):
        """SEEDED DEFECT: a retro that names issues and files nothing.

        A lint never shown failing is not evidence. This plants the exact defect
        the lint exists to catch and asserts it is caught — both in-process and
        through the CLI's exit code, since the CLI is what a hook would call.
        """
        retro = _write(self.tmp, "2026-08-29-defect.md", RETRO_WITH_FINDINGS)
        result = ff.lint(retro)
        self.assertFalse(result["ok"])
        self.assertEqual(result["finding_count"], 2)
        self.assertFalse(result["filed_section_present"])
        self.assertIn("Filed findings", result["violations"][0])

        proc = subprocess.run(
            [sys.executable, "-m", "retrospective.file_findings", "lint",
             "--retro", str(retro), "--json"],
            capture_output=True, text=True, cwd=str(BUILD_LOOP_ROOT / "scripts"),
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)

    def test_lint_passes_when_findings_are_filed(self):
        text = RETRO_WITH_FINDINGS + (
            "\n## Filed findings\n\n"
            "| finding | filed as | location |\n"
            "|---|---|---|\n"
            "| Surface-ownership | lessons-learned | `~/x/LESSONS-LEARNED.md` |\n"
            "| Guard suite | BUIL-GUARD-m17dppmtchst | `~/y/items/BUIL-GUARD-m17dppmtchst.md` |\n"
        )
        retro = _write(self.tmp, "2026-08-29-ok.md", text)
        result = ff.lint(retro)
        self.assertTrue(result["ok"], result["violations"])
        self.assertGreaterEqual(result["filed_locations"], 2)

    def test_empty_filed_section_still_fails(self):
        """The heading alone is the failure wearing a passing shape."""
        retro = _write(self.tmp, "2026-08-29-empty.md",
                       RETRO_WITH_FINDINGS + "\n## Filed findings\n\n_TBD_\n")
        result = ff.lint(retro)
        self.assertFalse(result["ok"])
        self.assertTrue(result["filed_section_present"])
        self.assertEqual(result["filed_locations"], 0)

    def test_retro_with_no_findings_passes(self):
        retro = _write(self.tmp, "2026-08-29-clean.md",
                       "# Retro\n\n## What went well\n\n1. **All green.** Nothing to file.\n")
        result = ff.lint(retro)
        self.assertTrue(result["ok"])
        self.assertEqual(result["finding_count"], 0)


class TestApply(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "git-folder"
        self.root.mkdir()
        self.bl = self.root / "build-loop"
        (self.bl / ".git").mkdir(parents=True)
        (self.bl / "KNOWN-ISSUES.md").write_text("# Known issues\n", encoding="utf-8")
        self._old_mem = os.environ.get("BUILD_LOOP_MEMORY_DIR")
        os.environ["BUILD_LOOP_MEMORY_DIR"] = str(self.tmp / "memory")

    def tearDown(self) -> None:
        if self._old_mem is None:
            os.environ.pop("BUILD_LOOP_MEMORY_DIR", None)
        else:
            os.environ["BUILD_LOOP_MEMORY_DIR"] = self._old_mem
        self._tmp.cleanup()

    def test_apply_skips_findings_missing_segments(self):
        retro = _write(self.tmp, "2026-08-29-r.md", RETRO_WITH_FINDINGS)
        result = ff.apply(retro, repo_roots=[self.root], build_loop_root=self.bl,
                          backlog_py=BUILD_LOOP_ROOT / "scripts" / "backlog.py")
        self.assertEqual(result["filed_count"], 0)
        self.assertTrue(all(s["reason"] == "needs_input" for s in result["skipped"]))
        self.assertTrue(all("why" in s["missing"] for s in result["skipped"]))

    def test_apply_writes_markdown_entry_with_five_segments(self):
        text = ("# Retro\n\n## Issues\n\n"
                "1. **Alpha broke.** The alpha-svc worker died. Cost: 2h downtime. "
                "Fix: add a supervisor. Root cause: no restart policy.\n")
        alpha = self.root / "alpha-svc"
        (alpha / ".git").mkdir(parents=True)
        (alpha / "LESSONS-LEARNED.md").write_text("# Lessons\n", encoding="utf-8")
        retro = _write(self.tmp, "2026-08-29-a.md", text)
        result = ff.apply(retro, repo_roots=[self.root], build_loop_root=self.bl,
                          backlog_py=BUILD_LOOP_ROOT / "scripts" / "backlog.py")
        self.assertEqual(result["filed_count"], 1, result["skipped"])
        body = (alpha / "LESSONS-LEARNED.md").read_text(encoding="utf-8")
        for segment in ("What happened", "When", "Impact", "Recommendation", "Why"):
            self.assertIn(f"**{segment}.**", body)
        self.assertIn("2026-08-29", body)

    def test_apply_creates_a_real_backlog_item(self):
        text = ("# Retro\n\n## Issues\n\n"
                "1. **Beta broke.** The beta-svc router misfired. Cost: 2 blocked "
                "dispatches. Fix: add a confidence floor. Root cause: no owner check.\n")
        beta = self.root / "beta-svc"
        (beta / ".git").mkdir(parents=True)
        (beta / ".build-loop" / "backlog").mkdir(parents=True)
        retro = _write(self.tmp, "2026-08-29-b.md", text)
        result = ff.apply(retro, repo_roots=[self.root], build_loop_root=self.bl,
                          backlog_py=BUILD_LOOP_ROOT / "scripts" / "backlog.py")
        self.assertEqual(result["filed_count"], 1, result["skipped"])
        item = Path(result["filed"][0]["path"])
        self.assertTrue(item.exists())
        content = item.read_text(encoding="utf-8")
        self.assertIn("source: retrospective", content)
        self.assertIn(str(retro), content)
        self.assertIn("observed: 2026-08-29", content)
        self.assertIn("## What happened", content)
        self.assertIn("## Why", content)

    def test_apply_is_idempotent(self):
        """REGRESSION: `apply` run twice appended a second copy of every finding.

        A retrospective gets regenerated after a crash, re-narrated by the LLM
        step, or swept again at SessionEnd. Before the fix two runs produced 2
        identical LESSONS-LEARNED entries and 2 identical backlog items, which
        would show up in the theme index as phantom recurrence — the exact
        signal the segmentation view is supposed to make trustworthy.
        """
        text = ("# Retro\n\n## Issues\n\n"
                "1. **Alpha broke.** The alpha-svc worker died. Cost: 2h downtime. "
                "Fix: add a supervisor. Root cause: no restart policy.\n"
                "2. **Beta broke.** The beta-svc router misfired. Cost: 2 blocked "
                "dispatches. Fix: a confidence floor. Root cause: no owner check.\n")
        alpha = self.root / "alpha-svc"
        (alpha / ".git").mkdir(parents=True)
        (alpha / "LESSONS-LEARNED.md").write_text("# Lessons\n", encoding="utf-8")
        beta = self.root / "beta-svc"
        (beta / ".git").mkdir(parents=True)
        (beta / ".build-loop" / "backlog").mkdir(parents=True)
        retro = _write(self.tmp, "2026-08-29-i.md", text)
        kw = dict(repo_roots=[self.root], build_loop_root=self.bl,
                  backlog_py=BUILD_LOOP_ROOT / "scripts" / "backlog.py")

        first = ff.apply(retro, **kw)
        second = ff.apply(retro, **kw)

        self.assertEqual(first["filed_count"], 2, first["skipped"])
        self.assertEqual(second["filed_count"], 0)
        self.assertTrue(all(s["reason"] == "already_filed" for s in second["skipped"]))
        self.assertEqual(
            (alpha / "LESSONS-LEARNED.md").read_text(encoding="utf-8").count("Alpha broke"), 1)
        self.assertEqual(
            len(list((beta / ".build-loop" / "backlog" / "items").glob("*.md"))), 1)

    def test_render_filed_section_names_every_location(self):
        section = ff.render_filed_section([
            {"title": "Alpha", "mechanism": "backlog", "id": "BUIL-A-m17dppm",
             "path": "/x/items/BUIL-A-m17dppm.md"},
        ])
        self.assertIn("## Filed findings", section)
        self.assertIn("BUIL-A-m17dppm", section)
        self.assertIn("/x/items/BUIL-A-m17dppm.md", section)


@unittest.skipUnless(GOLDEN.is_file(), "golden fixture retrospective not present")
class TestGoldenFixture(unittest.TestCase):
    """ACCEPTANCE: the 2026-08-29 retro, filed by hand into three repos."""

    def setUp(self) -> None:
        self.real_bl = Path.home() / "dev" / "git-folder" / "build-loop"
        self.plan = ff.plan(GOLDEN, build_loop_root=self.real_bl)

    def test_identifies_the_six_ranked_findings(self):
        self.assertEqual(self.plan["finding_count"], 6)
        titles = [e["finding"]["title"] for e in self.plan["entries"]]
        self.assertTrue(titles[0].startswith("Surface-ownership unchecked"))
        self.assertTrue(any("Write-without-read" in t for t in titles))
        self.assertTrue(any("Parallel writers" in t for t in titles))

    def test_mockup_gallery_finding_routes_to_the_toolkit_lessons_file(self):
        """Matches the human's filing exactly: RossLabs-AI-Toolkit/LESSONS-LEARNED.md."""
        entry = self.plan["entries"][0]
        self.assertEqual(entry["target"]["matched_token"], "mockup-gallery")
        self.assertEqual(entry["target"]["mechanism"], "lessons-learned")
        self.assertEqual(
            Path(entry["target"]["path"]),
            Path.home() / "dev" / "git-folder" / "RossLabs-AI-Toolkit" / "LESSONS-LEARNED.md",
        )

    def test_build_loop_findings_route_to_the_build_loop_repo(self):
        """Same REPO the human chose. The MECHANISM differs by design: the spec
        ladder puts `.build-loop/backlog/` above KNOWN-ISSUES.md, and build-loop
        has one. The human filed into KNOWN-ISSUES.md because the ladder did not
        exist yet."""
        matched = [e for e in self.plan["entries"]
                   if e["target"]["matched_token"] == "build-loop"]
        self.assertGreaterEqual(len(matched), 2)
        for e in matched:
            self.assertEqual(e["target"]["repo_name"], "build-loop")
            self.assertEqual(e["target"]["mechanism"], "backlog")

    def test_golden_retro_currently_fails_the_lint(self):
        """The fixture names six findings and carries no filed-findings section —
        exactly the gap this workstream closes."""
        result = ff.lint(GOLDEN)
        self.assertFalse(result["ok"])
        self.assertEqual(result["finding_count"], 6)

    def test_dry_run_writes_nothing(self):
        before = (GOLDEN.read_text(encoding="utf-8"),
                  sorted(p.name for p in (self.real_bl / ".build-loop" / "backlog"
                                          / "items").glob("*.md"))
                  if (self.real_bl / ".build-loop" / "backlog" / "items").is_dir() else [])
        ff.plan(GOLDEN, build_loop_root=self.real_bl)
        after = (GOLDEN.read_text(encoding="utf-8"),
                 sorted(p.name for p in (self.real_bl / ".build-loop" / "backlog"
                                         / "items").glob("*.md"))
                 if (self.real_bl / ".build-loop" / "backlog" / "items").is_dir() else [])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
