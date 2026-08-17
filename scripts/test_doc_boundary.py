#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for `scripts/doc_boundary.py`.

The load-bearing contract under test is the policy's §3 sentence:

    Naming is evidence, not the decision. Review the content and audience.

So the invariant suite below is not "does the regex match" — it is "can a
filename alone ever produce a decided verdict". It cannot.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import doc_boundary as db  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "references" / "public-repository-documentation-boundary.md"

# Five real filenames from the hand-applied agent-rally-point extract
# (build-loop-memory/projects/agent-rally-point/raw/documents/public-repo-extract-2026-08-16/).
ARCHIVED_SAMPLE = (
    "ROOT-CAUSE-REGISTER.md",
    "PLAN-daemon-first-inject-routing.md",
    "RETROSPECTIVE-2026-06-07-rally-protocol-dogfood-lessons.md",
    "ISSUES-2026-07-06-hooks.md",
    "plans/2026-08-08-verified-attribution-and-relevance.handoff.md",
)

ARCHIVE_DIR = (
    ROOT.parent
    / "build-loop-memory"
    / "projects"
    / "agent-rally-point"
    / "raw"
    / "documents"
    / "public-repo-extract-2026-08-16"
)


class NamingIsEvidenceNotTheDecision(unittest.TestCase):
    """A path signal may seed a verdict; only content may decide one."""

    def test_deny_named_file_with_no_content_returns_needs_review(self) -> None:
        for path in ("docs/plans/2026-08-01-migration.md", "docs/RCA-outage.md",
                     "docs/RETROSPECTIVE-sprint-4.md", "docs/HANDOFF-next.md"):
            verdict = db.classify(path, "# Title\n\nA paragraph with no structure.\n")
            self.assertEqual(verdict.bucket, "blocked", path)
            self.assertTrue(verdict.needs_review, path)
            self.assertNotEqual(verdict.confidence, "high", path)

    def test_high_confidence_requires_a_content_signal(self) -> None:
        body = "# Migration\n\n## Deliverables\n\n- ship it\n\n## Phases 1\n"
        verdict = db.classify("docs/plans/2026-08-01-migration.md", body)
        self.assertEqual(verdict.bucket, "blocked")
        self.assertEqual(verdict.confidence, "high")
        self.assertFalse(verdict.needs_review)
        self.assertTrue(any(s["layer"] == "content" and s["kind"] == "deny"
                            for s in verdict.signals))

    def test_policy_named_spec_ambiguity_never_convicts_alone(self) -> None:
        # Policy §3: "A file named `SPEC` can still be a private future plan".
        verdict = db.classify("docs/SPEC-lead-agent.md", "# Lead agent\n\nIt does X.\n")
        self.assertEqual(verdict.bucket, "public_current")
        self.assertFalse(verdict.needs_review)

    def test_content_alone_without_a_path_signal_stays_advisory(self) -> None:
        body = (
            "# Note\n\n## Root cause\n\n## Timeline\n\n## Corrective action\n"
            "Status: draft\n\nrun-id: bl-1\n"
        )
        verdict = db.classify("docs/note.md", body)
        self.assertEqual(verdict.bucket, "blocked")
        self.assertTrue(verdict.needs_review)
        self.assertEqual(verdict.confidence, "low")

    def test_product_surface_caps_a_deny_name_at_needs_review(self) -> None:
        # references/ and skills/ ARE the shipped product in a plugin repo (§2).
        body = "# RCA operating prompt\n\n## Root cause\n\n## Timeline\n"
        verdict = db.classify("references/root-cause-analysis/01-rca.md", body)
        self.assertEqual(verdict.bucket, "blocked")
        self.assertTrue(verdict.needs_review)
        self.assertEqual(verdict.confidence, "low")

    def test_unreadable_content_is_blocked_not_guessed(self) -> None:
        verdict = db.classify("docs/plans/x.md", None)
        self.assertEqual(verdict.bucket, "blocked")
        self.assertTrue(verdict.needs_review)


class AllowList(unittest.TestCase):
    """Policy §2 material stays public_current."""

    def test_entry_documents_stay_public(self) -> None:
        for path in ("README.md", "CONTRIBUTING.md", "CHANGELOG.md",
                     "docs/INSTALL.md", "LICENSE.md"):
            verdict = db.classify(path, "# Title\n\n## Installation\n\n```bash\nuv sync\n```\n")
            self.assertEqual(verdict.bucket, "public_current", path)
            self.assertEqual(verdict.confidence, "high", path)

    def test_agent_contract_stays_public(self) -> None:
        body = "---\nname: thing\ndescription: does a thing\n---\n\n# Thing\n"
        verdict = db.classify("skills/thing/SKILL.md", body)
        self.assertEqual(verdict.bucket, "public_current")

    def test_behavior_guide_named_for_a_denied_class_is_not_convicted(self) -> None:
        # agent-rally-point/docs/HANDOFFS-AND-LAUNCHING-AGENTS.md regression:
        # §2 explicitly allows documenting "handoff behavior".
        body = (
            "# Handoffs & Launching Managed Agents\n\n## TL;DR\n\n```bash\nrally run claude\n```\n"
            "\n## 1. Launching a managed agent\n\n```bash\nrally next\n```\n"
            "\nA handoff that says 'on main' will mislead the next session.\n"
        )
        verdict = db.classify("docs/HANDOFFS-AND-LAUNCHING-AGENTS.md", body)
        self.assertNotEqual(verdict.confidence, "high")
        self.assertTrue(verdict.bucket == "public_current" or verdict.needs_review)


class ExperimentLedgerCategory(unittest.TestCase):
    """Gap C: A/B test records and experiment ledgers are a named deny class."""

    def test_policy_names_the_category(self) -> None:
        text = POLICY.read_text(encoding="utf-8")
        self.assertIn("A/B test records and experiment ledgers", text)
        self.assertIn(".build-loop/experiments/", text)

    def test_ledger_files_are_admitted_as_documents(self) -> None:
        self.assertTrue(db.is_doc(".build-loop/experiments/auto-rollback.jsonl"))
        self.assertTrue(db.is_doc("experiments/discarded.jsonl"))
        self.assertFalse(db.is_doc("src/data.jsonl"))
        self.assertFalse(db.is_doc("scripts/thing.py"))

    def test_ledger_path_is_a_strong_deny_seed(self) -> None:
        verdict = db.classify(".build-loop/experiments/auto-rollback.jsonl",
                              '{"run":1,"metric":0.4}\n')
        self.assertEqual(verdict.bucket, "blocked")
        self.assertIn("path.experiment_ledger",
                      [s["rule"] for s in verdict.signals])

    def test_methodology_and_templates_are_not_the_record(self) -> None:
        # The deny class is the RECORD, not the design doc or the template.
        for path in ("docs/design/ab-experiment.md",
                     "references/experiment-results-template.md"):
            verdict = db.classify(path, "# A/B experiment\n\n## Usage\n\nHow to run one.\n")
            self.assertFalse(verdict.bucket == "blocked" and not verdict.needs_review, path)


class ArchivedCorpusSpotCheck(unittest.TestCase):
    """The 81 hand-archived agent-rally-point files must grade as internal."""

    def test_archived_names_seed_a_deny_verdict(self) -> None:
        for name in ARCHIVED_SAMPLE:
            verdict = db.classify(name, "# Doc\n\nProse.\n")
            self.assertEqual(verdict.bucket, "blocked", name)

    @unittest.skipUnless(ARCHIVE_DIR.is_dir(), "build-loop-memory archive not present")
    def test_archived_files_grade_blocked_on_their_real_content(self) -> None:
        for name in ARCHIVED_SAMPLE:
            source = ARCHIVE_DIR / (name + ".md")
            if not source.is_file():
                self.skipTest(f"archive entry missing: {name}")
            verdict = db.classify(name, source.read_text(encoding="utf-8", errors="replace"))
            self.assertEqual(verdict.bucket, "blocked", name)


class Visibility(unittest.TestCase):
    def test_missing_gh_degrades_to_unknown(self) -> None:
        original = db.subprocess.run

        def boom(*args, **kwargs):
            raise FileNotFoundError("gh")

        db.subprocess.run = boom
        try:
            visibility, source = db.repo_visibility(ROOT)
        finally:
            db.subprocess.run = original
        self.assertEqual(visibility, "unknown")
        self.assertIn("gh", source)

    def test_unparseable_output_degrades_to_unknown(self) -> None:
        original = db.subprocess.run

        class Result:
            returncode = 0
            stdout = "not json"
            stderr = ""

        db.subprocess.run = lambda *a, **k: Result()
        try:
            visibility, _ = db.repo_visibility(ROOT)
        finally:
            db.subprocess.run = original
        self.assertEqual(visibility, "unknown")


class RepoRun(unittest.TestCase):
    """End-to-end over a throwaway git repo, with visibility stubbed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "sample-repo"
        (self.repo / "docs" / "plans").mkdir(parents=True)
        (self.repo / "README.md").write_text(
            "# Sample\n\n## Installation\n\n```bash\nuv sync\n```\n", encoding="utf-8")
        (self.repo / "docs" / "plans" / "2026-08-01-migrate.md").write_text(
            "# Migrate\n\n## Deliverables\n\n- a\n\n## Risks and mitigations\n", encoding="utf-8")
        (self.repo / "docs" / "SPEC-router.md").write_text(
            "# Router\n\nThe router dispatches by intent.\n", encoding="utf-8")
        # Deny-named, content-silent: the needs_review case by construction.
        (self.repo / "docs" / "plans" / "notes.md").write_text(
            "# Notes\n\nJust prose about a thing.\n", encoding="utf-8")
        for args in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                           capture_output=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.email=t@example.com",
             "-c", "user.name=t", "commit", "-qm", "init"],
            check=True, capture_output=True)
        self._original = db.repo_visibility

    def tearDown(self) -> None:
        db.repo_visibility = self._original
        self._tmp.cleanup()

    def _report(self, visibility: str) -> dict:
        db.repo_visibility = lambda repo: (visibility, "stub")
        return db.build_report(self.repo, None, Path(self._tmp.name) / "no-memory")

    def test_public_repo_reports_all_four_policy_buckets(self) -> None:
        report = self._report("public")
        self.assertEqual(
            set(report["buckets"]), {"public_current", "private_archived",
                                     "public_removed", "blocked"})
        paths = {f["path"] for f in report["buckets"]["blocked"]}
        self.assertIn("docs/plans/2026-08-01-migrate.md", paths)
        self.assertIn("README.md", {f["path"] for f in report["buckets"]["public_current"]})
        self.assertEqual(db.exit_code(report, strict=False), 1)

    def test_private_repo_reports_but_never_fails(self) -> None:
        report = self._report("private")
        self.assertTrue(report["counts"]["blocked_decided"] >= 1)
        self.assertEqual(db.exit_code(report, strict=False), 0)
        self.assertEqual(db.exit_code(report, strict=True), 0)

    def test_unknown_visibility_never_assumes(self) -> None:
        report = self._report("unknown")
        self.assertEqual(report["visibility"], "unknown")
        self.assertEqual(db.exit_code(report, strict=False), 0)
        self.assertIn("visibility unresolved", db.render(report, 10))

    def test_strict_escalates_needs_review(self) -> None:
        report = self._report("public")
        self.assertTrue(report["counts"]["blocked_needs_review"] >= 1)
        self.assertEqual(db.exit_code(report, strict=True), 1)

    def test_json_output_round_trips(self) -> None:
        db.repo_visibility = lambda repo: ("public", "stub")
        report = db.build_report(self.repo, None, None)
        json.loads(json.dumps(report))

    def test_rev_mode_reads_from_git(self) -> None:
        db.repo_visibility = lambda repo: ("public", "stub")
        report = db.build_report(self.repo, "HEAD", None)
        self.assertEqual(report["rev"], "HEAD")
        self.assertEqual(report["counts"]["documents"], 4)

    def test_hard_error_on_bad_rev(self) -> None:
        with self.assertRaises(db.HardError):
            db.tracked_docs(self.repo, "no-such-rev")

    def test_cli_missing_repo_exits_two(self) -> None:
        self.assertEqual(db.main(["--repo", str(self.repo / "nope")]), 2)


class ArchiveReceipts(unittest.TestCase):
    def test_receipted_archive_entries_populate_private_archived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "widget"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True,
                           capture_output=True)
            index = root / "memory" / "projects" / "widget" / "raw"
            index.mkdir(parents=True)
            (index / "INDEX.jsonl").write_text(
                json.dumps({"file": "documents/batch/PLAN-x.md.md", "sha256": "abc",
                            "run_id": "r1"}) + "\n"
                + json.dumps({"file": "documents/batch/RCA-y.md.md", "run_id": "r1"}) + "\n",
                encoding="utf-8")
            records = db.archive_records(repo, root / "memory")
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["original_name"], "PLAN-x.md")
            self.assertTrue(records[0]["receipt"])
            self.assertFalse(records[1]["receipt"])

            db_visibility = db.repo_visibility
            db.repo_visibility = lambda r: ("public", "stub")
            try:
                report = db.build_report(repo, None, root / "memory")
            finally:
                db.repo_visibility = db_visibility
            self.assertEqual(report["counts"]["private_archived"], 1)
            self.assertEqual(report["counts"]["public_removed"], 1)
            # The receipt-less entry is `blocked` — policy §4/§5.
            self.assertTrue(any("without a private-memory receipt" in f.get("reason", "")
                                for f in report["buckets"]["blocked"]))


class OracleCorpus(unittest.TestCase):
    """agent-rally-point is the scrubbed regression oracle: zero decided findings."""

    ORACLE = ROOT.parent / "agent-rally-point"

    @unittest.skipUnless((ORACLE / ".git").exists(), "agent-rally-point checkout absent")
    def test_scrubbed_public_repo_has_no_decided_findings(self) -> None:
        original = db.repo_visibility
        db.repo_visibility = lambda repo: ("public", "stub")
        try:
            report = db.build_report(self.ORACLE, None, ROOT.parent / "build-loop-memory")
        finally:
            db.repo_visibility = original
        self.assertEqual(report["counts"]["blocked_decided"], 0,
                         [f["path"] for f in report["buckets"]["blocked"]
                          if not f["needs_review"]])
        self.assertLessEqual(report["counts"]["blocked_needs_review"], 3)
        self.assertGreater(report["counts"]["private_archived"], 70)


if __name__ == "__main__":
    unittest.main()
