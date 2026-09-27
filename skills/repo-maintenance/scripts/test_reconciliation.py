"""Exercise reconciliation through the real audit CLI and temporary Git repos."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("audit_repo_maintenance.py")


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.test")
        self.commit("core.txt", "base", "base")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], text=True,
                              capture_output=True, check=True).stdout.strip()

    def commit(self, path, body, message):
        (self.repo / path).write_text(body)
        self.git("add", "--", path)
        self.git("commit", "-m", message)

    def branch(self, name, path="feature.txt"):
        self.git("checkout", "-b", name, "main")
        self.commit(path, name, name)
        self.git("checkout", "main")

    def cli(self, *args):
        result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.repo), *args],
                                text=True, capture_output=True)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode, json.loads(result.stdout)

    def draft(self):
        return self.cli("--reconcile")[1]

    def reviewed(self, packet):
        packet = copy.deepcopy(packet)
        packet["comparison_evidence"] = "Inspected candidate implementations and their pinned diffs."
        for row in packet["candidates"]:
            row["ownership"] = "released"
            row["ownership_evidence"] = "Fixture has no active agent or process."
            for unit in row["units"]:
                unit.update(behavior="Adds independent fixture behavior", rationale="Existing behavior retained",
                            disposition="additive", ui="none", evidence=[f"Read {row['source_head']} diff and code"])
        return packet

    def check(self, record):
        path = self.root / "review.json"
        path.write_text(json.dumps(record))
        return self.cli("--review-record", str(path))

    def test_additive_review_requires_evidence_and_never_mutates_refs(self):
        self.branch("feature")
        before = self.git("show-ref")
        packet = self.draft()
        self.assertEqual(packet["candidates"][0]["source_paths"], ["feature.txt"])
        code, unchecked = self.check(packet)
        self.assertEqual(code, 1)
        self.assertFalse(unchecked["review_complete"])
        code, checked = self.check(self.reviewed(packet))
        self.assertEqual(code, 0)
        self.assertEqual(checked["whole_branch_steps"]["feature"], "integration_checks")
        self.assertEqual(self.git("show-ref"), before)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_competing_ui_holds_mixed_branch_but_allows_independent_unit_checks(self):
        self.branch("mixed", "ui.txt")
        self.git("checkout", "mixed")
        self.commit("independent.txt", "new capability", "independent")
        self.git("checkout", "main")
        record = self.reviewed(self.draft())
        row = record["candidates"][0]
        additive = copy.deepcopy(row["units"][0])
        additive["paths"] = ["independent.txt"]
        alternative = copy.deepcopy(additive)
        alternative.update(paths=["ui.txt"], ui="competing")
        row["units"] = [additive, alternative]
        code, result = self.check(record)
        self.assertEqual(code, 0)  # reviewed, but not approved for mutation
        self.assertEqual([a["next_step"] for a in result["actions"]], ["integration_checks", "needs_user_choice"])
        self.assertEqual(result["whole_branch_steps"]["mixed"], "preserve")

    def test_competitors_and_target_changes_are_visible(self):
        self.branch("design-a", "ui.txt")
        self.branch("design-b", "ui.txt")
        self.commit("core.txt", "evolved", "evolve main")
        packet = self.draft()
        self.assertEqual(packet["overlaps"], [{"sources": ["design-a", "design-b"], "paths": ["ui.txt"]}])
        self.assertEqual(packet["candidates"][0]["target_paths"], ["core.txt"])
        record = self.reviewed(packet)
        record["comparison_evidence"] = ""
        self.assertEqual(self.check(record)[0], 1)

    def test_partial_path_coverage_and_source_or_target_drift_reject_review(self):
        self.branch("feature")
        packet = self.reviewed(self.draft())
        partial = copy.deepcopy(packet)
        partial["candidates"][0]["units"][0]["paths"] = []
        self.assertEqual(self.check(partial)[0], 1)
        self.commit("new-main.txt", "changed", "move target")
        self.assertEqual(self.check(packet)[0], 1)
        packet = self.reviewed(self.draft())
        self.git("checkout", "feature")
        self.commit("later.txt", "changed", "move source")
        self.git("checkout", "main")
        self.assertEqual(self.check(packet)[0], 1)

    def test_active_or_dirty_work_is_preserved(self):
        self.branch("feature")
        record = self.reviewed(self.draft())
        record["candidates"][0]["ownership"] = "active"
        result = self.check(record)[1]
        self.assertEqual(result["whole_branch_steps"]["feature"], "preserve")
        (self.repo / "valuable.txt").write_text("unfinished")
        result = self.check(self.reviewed(self.draft()))[1]
        self.assertEqual(result["whole_branch_steps"]["feature"], "preserve")
        self.assertTrue(result["retained_state"]["dirty_worktrees"])

    def test_clean_unfinished_operation_in_other_target_or_source_worktree_is_held(self):
        self.branch("feature")
        self.git("checkout", "-b", "empty", "main")
        self.git("commit", "--allow-empty", "-m", "empty change")
        self.git("checkout", "-b", "audit", "main")
        for branch in ("main", "feature"):
            with self.subTest(branch=branch):
                path = self.root / f"linked-{branch}"
                self.git("worktree", "add", str(path), branch)
                subprocess.run(["git", "-C", str(path), "merge", "--no-ff", "--no-commit", "empty"],
                               capture_output=True, check=True)
                self.assertEqual(subprocess.run(["git", "-C", str(path), "status", "--porcelain"],
                                                capture_output=True, text=True, check=True).stdout, "")
                result = self.check(self.reviewed(self.draft()))[1]
                self.assertEqual(result["whole_branch_steps"]["feature"], "preserve")
                subprocess.run(["git", "-C", str(path), "merge", "--abort"], capture_output=True, check=True)
                self.git("worktree", "remove", str(path))

    def test_patch_equivalence_and_age_never_assign_retirement(self):
        self.branch("feature")
        self.git("cherry-pick", "feature")
        packet = self.draft()
        row = packet["candidates"][0]
        self.assertEqual(row["tip_difference_paths"], [])
        self.assertEqual(row["units"][0]["disposition"], "unverified")
        self.assertEqual(self.check(packet)[0], 1)

    def test_clean_paused_rebase_in_detached_worktree_holds_integration(self):
        self.branch("feature")
        self.commit("target.txt", "target change", "target")
        self.git("checkout", "-b", "audit", "main")
        target = self.root / "target"
        self.git("worktree", "add", str(target), "main")
        result = subprocess.run(["git", "-C", str(target), "rebase", "--exec", "false", "feature"],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(subprocess.run(["git", "-C", str(target), "status", "--porcelain"],
                                        capture_output=True, text=True, check=True).stdout, "")
        packet = self.draft()
        self.assertTrue(packet["retained_state"]["detached_worktrees"])
        result = self.check(self.reviewed(packet))[1]
        self.assertEqual(result["whole_branch_steps"]["feature"], "preserve")

    def test_criss_cross_merge_with_unique_resolution_requires_review(self):
        self.branch("left", "core.txt")
        self.branch("right", "core.txt")
        self.git("checkout", "-b", "candidate", "left")
        result = subprocess.run(["git", "-C", str(self.repo), "merge", "right"], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.commit("core.txt", "unique candidate resolution", "resolve candidate")
        self.git("checkout", "main")
        self.git("merge", "--ff-only", "left")
        result = subprocess.run(["git", "-C", str(self.repo), "merge", "right"], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.commit("core.txt", "different target resolution", "resolve target")
        self.assertEqual(self.git("cherry", "main", "candidate"), "")
        packet = self.draft()
        row = next(r for r in packet["candidates"] if r["source_ref"] == "candidate")
        self.assertIsNone(row["merge_base"])
        self.assertEqual(row["tip_difference_paths"], ["core.txt"])
        result = self.check(self.reviewed(packet))[1]
        self.assertEqual(result["whole_branch_steps"]["candidate"], "preserve")

    def test_single_base_patch_equivalence_does_not_hide_unique_merge_content(self):
        self.branch("left", "left.txt")
        self.branch("right", "right.txt")
        self.git("checkout", "-b", "candidate", "left")
        self.git("merge", "--no-commit", "--no-ff", "right")
        self.commit("unique.txt", "merge-only behavior", "merge with unique behavior")
        self.git("checkout", "main")
        self.git("cherry-pick", "left", "right")
        self.assertTrue(all(line.startswith("-") for line in self.git("cherry", "main", "candidate").splitlines()))
        packet = self.draft()
        row = next(r for r in packet["candidates"] if r["source_ref"] == "candidate")
        self.assertIsNotNone(row["merge_base"])
        self.assertEqual(row["tip_difference_paths"], ["unique.txt"])
        self.assertIn("unique.txt", row["source_paths"])
        result = self.check(packet)[1]
        self.assertEqual(result["whole_branch_steps"]["candidate"], "preserve")
        self.assertTrue(all(a["next_step"] == "needs_review" for a in result["actions"] if a["source_ref"] == "candidate"))

    def test_empty_additive_unit_cannot_request_integration(self):
        self.git("branch", "already-present")
        result = self.check(self.reviewed(self.draft()))[1]
        self.assertFalse(result["review_complete"])
        self.assertEqual(result["whole_branch_steps"]["already-present"], "preserve")

    def test_remote_only_branch_exclusion_is_explicit(self):
        self.git("update-ref", "refs/remotes/origin/remote-only", "HEAD")
        result = self.check(self.draft())[1]
        self.assertIn("remote-only branches", result["review_scope"])

    def test_excessively_nested_record_fails_without_traceback(self):
        path = self.root / "nested.json"
        path.write_text("[" * 2000 + "]" * 2000)
        result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.repo), "--review-record", str(path)], text=True, capture_output=True)
        self.assertIn(result.returncode, (1, 2))  # Decoder depth limits differ by Python version.
        if result.returncode == 1:
            self.assertFalse(json.loads(result.stdout)["review_complete"])
        self.assertNotIn("Traceback", result.stderr)

    def test_stashes_and_detached_work_are_explicitly_retained(self):
        detached = self.root / "detached"
        self.git("worktree", "add", "--detach", str(detached), "HEAD")
        (self.repo / "valuable.txt").write_text("unfinished")
        self.git("stash", "push", "-u", "-m", "preserve")
        packet = self.draft()
        self.assertTrue(packet["retained_state"]["stashes"])
        self.assertTrue(packet["retained_state"]["detached_worktrees"])
        self.assertIn("retained state", self.check(packet)[1]["review_scope"])

    def test_malformed_or_missing_candidate_cannot_pass(self):
        self.branch("feature")
        for record in [[], {"candidates": "bad"}, {**self.reviewed(self.draft()), "candidates": []}]:
            with self.subTest(record=record):
                self.assertEqual(self.check(record)[0], 1)

    def test_invalid_types_and_duplicate_candidates_fail_closed(self):
        self.branch("feature")
        valid = self.reviewed(self.draft())
        for field in ("disposition", "ui", "paths", "evidence"):
            record = copy.deepcopy(valid)
            record["candidates"][0]["units"][0][field] = {"unexpected": True}
            with self.subTest(field=field):
                self.assertEqual(self.check(record)[0], 1)
        duplicate = copy.deepcopy(valid)
        duplicate["candidates"].append(copy.deepcopy(duplicate["candidates"][0]))
        self.assertEqual(self.check(duplicate)[0], 1)


if __name__ == "__main__":
    unittest.main()
