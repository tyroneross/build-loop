#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for plan_verify.py. Stdlib only. Run: python3 test_plan_verify.py"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "plan_verify.py"
FIXTURES = HERE.parent / "skills" / "plan-verify" / "test-fixtures"
REPO_ROOT = HERE.parent  # build-loop repo root


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True, text=True, timeout=30,
    )


class ContractShapeTests(unittest.TestCase):
    """Verifier output must match the Plan Evidence Contract."""

    def test_empty_plan_produces_no_findings(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("# Empty plan\n\nThis plan does nothing.\n")
            tmp = f.name
        try:
            r = run([tmp, "--json"])
            self.assertEqual(r.returncode, 0, r.stderr)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["summary"]["by_severity"]["BLOCKER"], 0)
        finally:
            Path(tmp).unlink()

    def test_finding_has_required_keys(self) -> None:
        text = "## Phase 1\n\nDelete `scripts/optimize_loop.py` immediately.\n"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--repo", str(REPO_ROOT), "--json"])
            payload = json.loads(r.stdout)
            for f_obj in payload["findings"]:
                for k in ("claim_text", "claim_kind", "subject", "verification_command",
                          "evidence", "result", "marker", "severity", "confidence", "rule_id"):
                    self.assertIn(k, f_obj, f"missing key {k} in finding: {f_obj}")
                self.assertIn(f_obj["severity"], ("BLOCKER", "WARN", "INFO"))
        finally:
            Path(tmp).unlink()

    def test_exit_code_2_on_missing_file(self) -> None:
        r = run(["/nonexistent/plan.md"])
        self.assertEqual(r.returncode, 2)


class FencedCodeExclusionTests(unittest.TestCase):
    """Claims inside fenced code blocks must be ignored."""

    def test_delete_inside_fenced_block_is_ignored(self) -> None:
        text = (
            "# Plan\n\n"
            "```\n"
            "delete `scripts/optimize_loop.py`\n"
            "```\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--repo", str(REPO_ROOT), "--json"])
            self.assertEqual(r.returncode, 0)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["summary"]["by_severity"]["BLOCKER"], 0)
        finally:
            Path(tmp).unlink()


class NumericDriftTests(unittest.TestCase):
    def test_drift_in_orphan_count(self) -> None:
        text = "We removed **6 orphans** from Phase 1. Later: only **5 orphans** are left.\n"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--json"])
            payload = json.loads(r.stdout)
            rules = payload["summary"]["by_rule_id"]
            self.assertGreaterEqual(rules.get("numeric-drift", {}).get("BLOCKER", 0), 1)
            self.assertEqual(r.returncode, 1)
        finally:
            Path(tmp).unlink()

    def test_no_drift_when_counts_match(self) -> None:
        text = "We have **6 orphans**. Phase 2 also reports **6 orphans**.\n"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--json"])
            self.assertEqual(r.returncode, 0)
        finally:
            Path(tmp).unlink()


class RouteChangeTests(unittest.TestCase):
    def test_308_redirect_without_marker_is_blocker(self) -> None:
        text = "## Routes\n\nWe use a 308 redirect from /old to /new.\n"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--json"])
            payload = json.loads(r.stdout)
            rules = payload["summary"]["by_rule_id"]
            self.assertGreaterEqual(rules.get("route-change-evidence", {}).get("BLOCKER", 0), 1)
            self.assertEqual(r.returncode, 1)
        finally:
            Path(tmp).unlink()

    def test_308_redirect_with_marker_within_3_lines_passes(self) -> None:
        text = "## Routes\n\nWe use a 308 redirect from /old to /new.\n✅ verified by reading nginx.conf line 42.\n"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--json"])
            payload = json.loads(r.stdout)
            rules = payload["summary"]["by_rule_id"]
            self.assertEqual(rules.get("route-change-evidence", {}).get("BLOCKER", 0), 0)
        finally:
            Path(tmp).unlink()


class MissingEvidenceTests(unittest.TestCase):
    def test_unmarked_unused_claim_is_warn(self) -> None:
        text = "## Packages\n\nThe recharts package is unused; remove it.\n"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--json"])
            payload = json.loads(r.stdout)
            rules = payload["summary"]["by_rule_id"]
            self.assertGreaterEqual(rules.get("missing-evidence", {}).get("WARN", 0), 1)
            # WARN does not block
            self.assertEqual(r.returncode, 0)
        finally:
            Path(tmp).unlink()


class FixtureABTests(unittest.TestCase):
    """Run against the three committed fixtures and check expectations."""

    def _payload(self, fixture: str, with_repo: bool = True) -> dict:
        plan = FIXTURES / fixture
        args = [str(plan), "--json"]
        if with_repo:
            args += ["--repo", str(REPO_ROOT)]
        r = run(args)
        return {"rc": r.returncode, "payload": json.loads(r.stdout) if r.stdout else None,
                "stderr": r.stderr}

    def test_v20_fails_loudly(self) -> None:
        res = self._payload("example-app-v20.md")
        self.assertEqual(res["rc"], 1, f"v2.0 should exit 1; stderr={res['stderr']}")
        rules = res["payload"]["summary"]["by_rule_id"]
        # Per goal.md criterion 2:
        self.assertGreaterEqual(rules.get("delete-with-callers", {}).get("BLOCKER", 0), 1,
                                f"v2.0 needs ≥1 delete-with-callers BLOCKER; got {rules}")
        self.assertGreaterEqual(rules.get("numeric-drift", {}).get("BLOCKER", 0), 1,
                                f"v2.0 needs ≥1 numeric-drift BLOCKER; got {rules}")
        self.assertGreaterEqual(rules.get("route-change-evidence", {}).get("BLOCKER", 0), 1,
                                f"v2.0 needs ≥1 route-change-evidence BLOCKER; got {rules}")
        self.assertGreaterEqual(rules.get("missing-evidence", {}).get("WARN", 0), 1,
                                f"v2.0 needs ≥1 missing-evidence WARN; got {rules}")

    def test_v22_passes(self) -> None:
        res = self._payload("example-app-v22.md")
        self.assertEqual(res["rc"], 0,
                         f"v2.2 should exit 0; got {res['rc']}; "
                         f"BLOCKERs={res['payload']['summary']['by_severity']['BLOCKER']}; "
                         f"by_rule={res['payload']['summary']['by_rule_id']}")

    def test_unrelated_passes(self) -> None:
        res = self._payload("unrelated-good-plan.md")
        self.assertEqual(res["rc"], 0,
                         f"unrelated plan should exit 0; "
                         f"BLOCKERs={res['payload']['summary']['by_severity']['BLOCKER']}; "
                         f"by_rule={res['payload']['summary']['by_rule_id']}")


class ForbiddenPathConflictTests(unittest.TestCase):
    """rule_forbidden_path_conflict — Phase 2 catch for plan × dispatch-forbidden-paths conflicts."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.plan = self.repo / "plan.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, plan_text: str, config: dict | None = None) -> list[dict]:
        self.plan.write_text(plan_text, encoding="utf-8")
        if config is not None:
            cfg = self.repo / ".build-loop" / "config.json"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(json.dumps(config))
        sys.path.insert(0, str(HERE))
        try:
            from plan_verify import run_all  # type: ignore  # noqa: PLC0415
        finally:
            sys.path.pop(0)
        return [f for f in run_all(self.plan, self.repo) if f["rule_id"] == "forbidden-path-conflict"]

    def test_default_forbidden_project_yml_in_files_owned_is_warn(self) -> None:
        findings = self._run("## Chunk c1\nfiles_owned: [project.yml, scripts/foo.py]\n")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "WARN")
        self.assertIn("project.yml", findings[0]["claim_text"])

    def test_no_overlap_is_silent(self) -> None:
        findings = self._run("## Chunk c1\nfiles_owned: [scripts/foo.py, tests/test_foo.py]\n")
        self.assertEqual(findings, [])

    def test_wildcard_forbidden_path_matches(self) -> None:
        findings = self._run("## Chunk c1\nfiles_owned: [.github/workflows/ci.yml]\n")
        self.assertEqual(len(findings), 1)

    def test_config_override_relaxes_default(self) -> None:
        """User can opt-in to allowing project.yml edits by setting an empty forbiddenPaths."""
        findings = self._run(
            "## Chunk c1\nfiles_owned: [project.yml]\n",
            config={"dispatch": {"forbiddenPaths": []}},
        )
        self.assertEqual(findings, [])

    def test_fenced_code_block_excluded(self) -> None:
        """Plans showing example files_owned in a code fence should NOT trigger."""
        findings = self._run(
            "## Example\n```yaml\nfiles_owned: [project.yml]\n```\n",
        )
        self.assertEqual(findings, [])


class ParallelDecisionRecordTests(unittest.TestCase):
    """rule_parallel_decision_record — Phase 2 dispatch decision enforcement."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.plan = self.repo / "plan.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _findings(self, text: str) -> list[dict]:
        self.plan.write_text(text, encoding="utf-8")
        sys.path.insert(0, str(HERE))
        try:
            from plan_verify import run_all  # type: ignore  # noqa: PLC0415
        finally:
            sys.path.pop(0)
        return [
            f for f in run_all(self.plan, self.repo)
            if f["rule_id"] == "parallel-decision-record"
        ]

    def test_parallel_safe_multi_chunk_plan_requires_decision(self) -> None:
        findings = self._findings(
            "## C1\nfiles_owned: [scripts/a.py]\n\n"
            "## C2\nfiles_owned: [scripts/b.py]\n\n"
            "C1 and C2 are independent and parallel-safe.\n"
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "BLOCKER")

    def test_parallel_batch_satisfies_rule(self) -> None:
        findings = self._findings(
            "## C1\nfiles_owned: [scripts/a.py]\n\n"
            "## C2\nfiles_owned: [scripts/b.py]\n\n"
            "C1 and C2 are independent and parallel-safe.\n"
            "parallel_batch: [[C1, C2]]\n"
        )
        self.assertEqual(findings, [])

    def test_single_chunk_parallel_word_is_silent(self) -> None:
        findings = self._findings(
            "## C1\nfiles_owned: [scripts/a.py]\n\n"
            "C1 can run in parallel with nothing else.\n"
        )
        self.assertEqual(findings, [])


class ApproachLensesMissingTests(unittest.TestCase):
    """rule_approach_lenses_missing — clean-sheet vs constrained-path prompt."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.plan = self.repo / "plan.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _findings(self, text: str) -> list[dict]:
        self.plan.write_text(text, encoding="utf-8")
        sys.path.insert(0, str(HERE))
        try:
            from plan_verify import run_all  # type: ignore  # noqa: PLC0415
        finally:
            sys.path.pop(0)
        return [
            f for f in run_all(self.plan, self.repo)
            if f["rule_id"] == "approach-lenses-missing"
        ]

    def test_architecture_plan_without_approach_lenses_warns(self) -> None:
        findings = self._findings(
            "## Plan\n\n"
            "Recommended architecture: move the API contract into a shared module.\n"
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "WARN")

    def test_approach_lenses_section_satisfies_rule(self) -> None:
        findings = self._findings(
            "## Approach Lenses\n\n"
            "**Clean-sheet best approach:** shared typed contract.\n"
            "**Current-constraints approach:** keep adapter until migration completes.\n\n"
            "## Plan\n\n"
            "Recommended architecture: move the API contract into a shared module.\n"
        )
        self.assertEqual(findings, [])

    def test_explicit_na_satisfies_rule(self) -> None:
        findings = self._findings(
            "Approach Lenses: n/a - narrow fix.\n\n"
            "## Plan\n\n"
            "Update the endpoint label only.\n"
        )
        self.assertEqual(findings, [])


class NoStopLanguageTests(unittest.TestCase):
    """rule_no_stop_language — catches stop/halt/ask phrasing that violates build-loop autonomy policy."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.plan = self.repo / "plan.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _findings(self, text: str) -> list[dict]:
        self.plan.write_text(text, encoding="utf-8")
        sys.path.insert(0, str(HERE))
        try:
            from plan_verify import run_all  # type: ignore  # noqa: PLC0415
        finally:
            sys.path.pop(0)
        return [f for f in run_all(self.plan, self.repo) if f["rule_id"] == "no-stop-language"]

    def test_pause_for_user_confirmation_is_flagged(self) -> None:
        """Plain stop phrasing with no exempt keyword → WARN."""
        findings = self._findings(
            "## Phase 2\n\nPause for user confirmation before proceeding.\n"
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "WARN")
        self.assertIn("pause", findings[0]["claim_text"].lower())

    def test_confirm_before_production_deploy_is_not_flagged(self) -> None:
        """Production-push exemption: 'production' keyword suppresses the finding."""
        findings = self._findings(
            "## Deploy\n\nConfirm before production deploy to avoid downtime.\n"
        )
        self.assertEqual(findings, [])

    def test_delete_table_after_confirming_is_not_flagged(self) -> None:
        """Destructive-delete exemption: 'delete' keyword suppresses the finding."""
        findings = self._findings(
            "## Cleanup\n\nAsk the user before we delete the staging table.\n"
        )
        self.assertEqual(findings, [])

    def test_clean_step_is_silent(self) -> None:
        """No stop phrasing → no finding."""
        findings = self._findings(
            "## Phase 1\n\nRun the migration script and verify the output.\n"
        )
        self.assertEqual(findings, [])


class ReadsDependencyTests(unittest.TestCase):
    """rule_reads-from-dependency — Phase 1/2 gate for unmet read dependencies."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.plan = self.repo / "plan.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _findings(self, text: str) -> list[dict]:
        self.plan.write_text(text, encoding="utf-8")
        sys.path.insert(0, str(HERE))
        try:
            from plan_verify import run_all  # type: ignore  # noqa: PLC0415
        finally:
            sys.path.pop(0)
        return [f for f in run_all(self.plan, self.repo) if f["rule_id"] == "reads-from-dependency"]

    def test_code_plan_missing_section_is_blocker(self) -> None:
        """A plan that names a code path but omits Depends-on (reads-from) → BLOCKER."""
        findings = self._findings(
            "## Plan\n\n"
            "Edit `scripts/report_generator.py` to read security findings.\n"
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "BLOCKER")
        self.assertIn("reads_from_missing", findings[0]["claim_kind"])

    def test_unverified_entry_in_section_is_blocker(self) -> None:
        """A Depends-on section with an `unverified` entry on a code plan → BLOCKER."""
        findings = self._findings(
            "## Plan\n\n"
            "Edit `scripts/report_generator.py` to read security findings.\n\n"
            "## Depends-on (reads-from)\n\n"
            "- `state.json.runs[].security_findings[]` — unverified\n"
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "BLOCKER")
        self.assertIn("reads_from_unverified", findings[0]["claim_kind"])

    def test_all_verified_entries_pass(self) -> None:
        """A Depends-on section with all entries `verified` → no findings."""
        findings = self._findings(
            "## Plan\n\n"
            "Edit `scripts/report_generator.py` to read security findings.\n\n"
            "## Depends-on (reads-from)\n\n"
            "- `state.json.runs[].security_findings[]` — verified\n"
            "- `.build-loop/goal.md` — verified\n"
        )
        self.assertEqual(findings, [])

    def test_doc_only_plan_exempt(self) -> None:
        """A plan with no code paths is exempt even without the section."""
        findings = self._findings(
            "## Plan\n\n"
            "Update the README and CLAUDE.md to document the new feature.\n"
        )
        self.assertEqual(findings, [])

    def test_override_silences_rule(self) -> None:
        """Explicit override suppresses the rule even on a code-shipping plan."""
        findings = self._findings(
            "override: reads-from-dependency\n\n"
            "## Plan\n\n"
            "Edit `scripts/report_generator.py` to read security findings.\n"
        )
        self.assertEqual(findings, [])


class DecisionWithoutFalsifierTests(unittest.TestCase):
    """rule_decision_without_falsifier — doctrine rule 8 (WP-C), advisory WARN."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.plan = self.repo / "plan.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _findings(self, text: str) -> list[dict]:
        self.plan.write_text(text, encoding="utf-8")
        sys.path.insert(0, str(HERE))
        try:
            from plan_verify import run_all  # type: ignore  # noqa: PLC0415
        finally:
            sys.path.pop(0)
        return [f for f in run_all(self.plan, self.repo) if f["rule_id"] == "decision-without-falsifier"]

    def test_decision_heading_without_falsifier_warns(self) -> None:
        findings = self._findings(
            "# Plan\n## Decision record\nStore the charter in memory; mirror to repo.\n"
            "Chosen because memory reads are reliable.\n"
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "WARN")

    def test_decision_with_falsifier_is_silent(self) -> None:
        findings = self._findings(
            "# Plan\n## Decision record\nStore the charter in memory; mirror to repo.\n"
            "Falsifier: if memory reads miss >5% of runs, revert to repo-canonical.\n"
        )
        self.assertEqual(findings, [])

    def test_decision_with_revisit_trigger_is_silent(self) -> None:
        findings = self._findings(
            "# Plan\n### Decision\nUse OpenRouter for the provider registry.\n"
            "Revisit trigger: a second provider needs a different call shape.\n"
        )
        self.assertEqual(findings, [])

    def test_prose_decide_without_heading_does_not_fire(self) -> None:
        # The word "decide" in prose must NOT trip the rule (only a Decision heading does).
        findings = self._findings(
            "# Plan\nWe will decide the storage layer during execution based on profiling.\n"
        )
        self.assertEqual(findings, [])


class TierSanityTests(unittest.TestCase):
    """rule_tier_sanity — doctrine rule 12 (WP-B item 3), advisory WARN."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.plan = self.repo / "plan.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _findings(self, text: str) -> list[dict]:
        self.plan.write_text(text, encoding="utf-8")
        sys.path.insert(0, str(HERE))
        try:
            from plan_verify import run_all  # type: ignore  # noqa: PLC0415
        finally:
            sys.path.pop(0)
        return [f for f in run_all(self.plan, self.repo) if f["rule_id"].startswith("tier-sanity")]

    def test_judgment_task_on_script_warns(self) -> None:
        findings = self._findings(
            "# Plan\n## Chunk 1\nAssess and decide the right rollback strategy.\n"
            "dispatch_tier: script\n"
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "WARN")
        self.assertEqual(findings[0]["rule_id"], "tier-sanity-judgment-on-script")

    def test_mechanical_task_on_opus_warns(self) -> None:
        findings = self._findings(
            "# Plan\n## Chunk 2\nRename the helper across all call sites.\n"
            "dispatch_tier: opus\n"
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule_id"], "tier-sanity-mechanical-on-opus")

    def test_mechanical_task_on_script_is_silent(self) -> None:
        # The aligned case — a rote rename on `script` — must not fire.
        findings = self._findings(
            "# Plan\n## Chunk 3\nRename the helper across all call sites.\n"
            "dispatch_tier: script\n"
        )
        self.assertEqual(findings, [])

    def test_judgment_task_on_opus_is_silent(self) -> None:
        # The aligned case — a judgment task on `opus` — must not fire.
        findings = self._findings(
            "# Plan\n## Chunk 4\nAssess and decide the rollback strategy.\n"
            "dispatch_tier: opus\n"
        )
        self.assertEqual(findings, [])

    def test_no_dispatch_tier_is_silent(self) -> None:
        findings = self._findings(
            "# Plan\n## Chunk 5\nRename the helper across all call sites.\n"
        )
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
