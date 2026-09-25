# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Stranger-test enforcement (incident 2026-09-25, eval 2026-09-25).

Two controls, each with regression fixtures from the eval:
  1. Commit hook: a HIGH-strength release-surface marker on changed Swift lines
     blocks (exit 2) unless a REASONED bypass is given, which is logged.
     Medium/low stay WARN-only; a clean or non-Swift commit prints nothing.
  2. Verdict schema: a security-reviewer (or an auditor that records one)
     verdict on a gated-surface change without a filled `stranger_test` does
     not count as review-complete.

Fixtures (tests/fixtures/stranger_eval/): the SpeakSavvy a6d72c5f TOFU owner
gate (must flag `establishOwnerAnchor`) and the held-out `#if DEBUG` developer
menu (must be clean).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import audit_before_commit as abc  # noqa: E402
import stranger_test_check as stc  # noqa: E402

SCRIPT = HERE / "audit_before_commit.py"
FIXTURES = HERE.parent / "tests" / "fixtures" / "stranger_eval"
TOFU_PATCH = FIXTURES / "speaksavvy-a6d72c5f-tofu.patch"
NEGATIVE_PATCH = FIXTURES / "heldout-negative-debug-menu.patch"


def _files(patch: str) -> list[str]:
    return re.findall(r"^diff --git a/.+? b/(.+)$", patch, re.MULTILINE)


def _post_image_reader(patch: str):
    """Reconstruct each file's post-image from hunks (context + added lines)."""
    images: dict[str, str] = {}
    for path, section in abc._diff_by_file(patch).items():
        lines: dict[int, str] = {}
        n = 0
        in_hunk = False
        for ln in section.splitlines():
            m = abc._HUNK_RE.match(ln)
            if m:
                n, in_hunk = int(m.group(1)), True
                continue
            if not in_hunk or ln.startswith("-") or ln.startswith("\\"):
                continue
            lines[n] = ln[1:] if ln[:1] in ("+", " ") else ln
            n += 1
        top = max(lines) if lines else 0
        images[path] = "\n".join(lines.get(i, "") for i in range(1, top + 1)) + "\n"
    return images.get


def _new_file_text(patch: str, path: str) -> str:
    section = abc._diff_by_file(patch)[path]
    body = section.split("\n@@", 1)[1].split("\n", 1)[1]
    return "\n".join(ln[1:] for ln in body.splitlines() if ln.startswith("+")) + "\n"


class FixtureScanTests(unittest.TestCase):
    def test_tofu_fixture_blocks_on_establish_owner_anchor(self) -> None:
        patch = TOFU_PATCH.read_text(encoding="utf-8")
        rs = abc.release_surface_findings(_files(patch), patch, _post_image_reader(patch))
        self.assertTrue(rs["block"], rs)
        self.assertTrue(
            any("establishOwnerAnchor" in b["snippet"] for b in rs["block"]),
            [b["snippet"] for b in rs["block"]],
        )
        self.assertTrue(all(b["file"].endswith("AdminGate.swift") or b["file"].endswith("ProfileView.swift")
                            for b in rs["block"]))

    def test_debug_menu_fixture_is_clean(self) -> None:
        patch = NEGATIVE_PATCH.read_text(encoding="utf-8")
        rs = abc.release_surface_findings(_files(patch), patch, _post_image_reader(patch))
        self.assertEqual(rs["block"], [], rs)
        self.assertEqual(rs["warn"], [], rs)

    def test_only_changed_lines_are_reported(self) -> None:
        # Pre-existing `adminUnlockTaps` is a context line in the fixture, not added.
        patch = TOFU_PATCH.read_text(encoding="utf-8")
        rs = abc.release_surface_findings(_files(patch), patch, _post_image_reader(patch))
        self.assertFalse(any("private let adminUnlockTaps" in b["snippet"] for b in rs["block"]))


class _Repo(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self.home = self.repo / "_home"
        self.home.mkdir()
        for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "T"]):
            self._git(args)
        (self.repo / "README.md").write_text("init\n")
        self._git(["add", "README.md"])
        self._git(["commit", "-q", "-m", "init"])

    def _git(self, args: list[str]) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout

    def _stage(self, rel: str, text: str) -> None:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        self._git(["add", rel])

    def _hook(self, env_extra: dict | None = None, command: str = ""):
        env = dict(os.environ)
        for k in ("BUILDLOOP_AUDIT_BYPASS", "BUILDLOOP_ENFORCE_RISK_AUDIT", abc.RELEASE_SURFACE_BYPASS_VAR):
            env.pop(k, None)
        env["HOME"] = str(self.home)
        env.update(env_extra or {})
        stdin = json.dumps({"tool_input": {"command": command}}) if command else ""
        return subprocess.run([sys.executable, str(SCRIPT)], cwd=self.repo, env=env, input=stdin,
                              capture_output=True, text=True, timeout=60)


class CommitHookTests(_Repo):
    def _stage_tofu(self) -> None:
        patch = TOFU_PATCH.read_text(encoding="utf-8")
        self._stage("SpeakSavvy/Services/AdminGate.swift",
                    _new_file_text(patch, "SpeakSavvy/Services/AdminGate.swift"))

    def test_high_signal_blocks_with_remedies(self) -> None:
        self._stage_tofu()
        r = self._hook()
        self.assertEqual(r.returncode, 2, r.stderr[-2000:])
        self.assertIn("RELEASE-SURFACE BLOCK", r.stderr)
        self.assertIn("AdminGate.swift:", r.stderr)
        self.assertIn("#if DEBUG", r.stderr)
        self.assertIn("verified server-side", r.stderr)

    def test_bypass_without_reason_is_refused(self) -> None:
        self._stage_tofu()
        for val in ("1", "true", ""):
            r = self._hook({abc.RELEASE_SURFACE_BYPASS_VAR: val})
            self.assertEqual(r.returncode, 2, val)
        r = self._hook({"BUILDLOOP_AUDIT_BYPASS": "1"})
        self.assertEqual(r.returncode, 2, "blanket bypass must not waive a release surface")

    def test_bypass_with_reason_passes_and_is_logged(self) -> None:
        self._stage_tofu()
        r = self._hook({abc.RELEASE_SURFACE_BYPASS_VAR: "owner id pinned server-side in PR 42"})
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("BYPASSED", r.stderr)
        self.assertIn("MUST report it", r.stderr)
        log = (self.repo / abc.RELEASE_SURFACE_BYPASS_LOG).read_text().splitlines()
        entry = json.loads(log[-1])
        self.assertEqual(entry["reason"], "owner id pinned server-side in PR 42")
        self.assertEqual(entry["source"], "environment")
        self.assertTrue(entry["findings"])

    def test_agent_issued_bypass_in_command_is_logged_as_agent(self) -> None:
        self._stage_tofu()
        cmd = f'{abc.RELEASE_SURFACE_BYPASS_VAR}="fixture only, not shipped" git commit -m x'
        r = self._hook(command=cmd)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        entry = json.loads((self.repo / abc.RELEASE_SURFACE_BYPASS_LOG).read_text().splitlines()[-1])
        self.assertIn("agent-issued", entry["source"])

    def test_medium_signal_warns_only(self) -> None:
        self._stage("App/Views/Settings.swift",
                    "struct Settings: View {\n    var body: some View { AdminPanelView() }\n}\n")
        r = self._hook()
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("Release-surface scan (advisory)", r.stderr)
        self.assertNotIn("RELEASE-SURFACE BLOCK", r.stderr)

    def test_debug_fixture_commit_is_clean(self) -> None:
        patch = NEGATIVE_PATCH.read_text(encoding="utf-8")
        self._stage("Trailmark/Debug/TileCacheDebugView.swift",
                    _new_file_text(patch, "Trailmark/Debug/TileCacheDebugView.swift"))
        r = self._hook()
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertNotIn("RELEASE-SURFACE", r.stderr)
        self.assertNotIn("Release-surface scan", r.stderr)

    def test_non_swift_commit_is_silent(self) -> None:
        self._stage("scripts/tool.py", "def claimOwnership():\n    return 1\n")
        r = self._hook()
        self.assertNotIn("RELEASE-SURFACE", r.stderr)
        self.assertNotIn("Release-surface scan", r.stderr)


class SelfAssessmentStripTests(unittest.TestCase):
    def test_reviewer_claims_removed(self) -> None:
        for s in ("abc123 feat: admin gate (security-reviewer PASS)",
                  "def456 fix: owner (auditor: approved)", "789 LGTM ship it"):
            self.assertIn("[author self-assessment removed]", abc._strip_self_assessment(s), s)
        self.assertEqual(abc._strip_self_assessment("abc feat: add review screen"),
                         "abc feat: add review screen")


GOOD_ST = {"applies": True, "answer": "a fresh install claims owner via Face ID",
           "in_release": "yes — no #if around AdminGate.swift:30"}


class VerdictSchemaTests(unittest.TestCase):
    def test_missing_stranger_test_rejected_for_security_reviewer(self) -> None:
        kept, rej = stc.filter_decisions([{"judge_id": "security-reviewer", "verdict": "pass"}], True)
        self.assertEqual(kept, [])
        self.assertIn("no `stranger_test`", rej[0]["reason"])

    def test_empty_answer_rejected(self) -> None:
        d = {"judge_id": "security-reviewer", "verdict": "pass",
             "stranger_test": {"applies": True, "answer": " ", "in_release": "yes"}}
        _, rej = stc.filter_decisions([d], True)
        self.assertIn("`answer`", rej[0]["reason"])
        d2 = {"judge_id": "independent-auditor", "verdict": "yay",
              "stranger_test": {"applies": True, "answer": "x", "in_release": ""}}
        _, rej2 = stc.filter_decisions([d2], True)
        self.assertIn("`in_release`", rej2[0]["reason"])

    def test_non_gated_change_unaffected(self) -> None:
        ds = [{"judge_id": "security-reviewer", "verdict": "pass"},
              {"judge_id": "independent-auditor", "verdict": "yay"}]
        kept, rej = stc.filter_decisions(ds, False)
        self.assertEqual((kept, rej), (ds, []))

    def test_complete_verdict_passes(self) -> None:
        d = {"judge_id": "security-reviewer", "verdict": "pass", "stranger_test": GOOD_ST}
        self.assertEqual(stc.filter_decisions([d], True), ([d], []))

    def test_auditor_without_key_is_not_retroactively_rejected(self) -> None:
        d = {"judge_id": "independent-auditor", "verdict": "yay"}
        self.assertEqual(stc.filter_decisions([d], True), ([d], []))


class VerdictSchemaIntegrationTests(_Repo):
    """gated detection from a real range + run_close_lint wiring."""

    def _setup_run(self, rel: str, text: str, decision: dict) -> str:
        base = self._git(["rev-parse", "HEAD"]).strip()
        self._stage(rel, text)
        self._git(["commit", "-q", "--no-verify", "-m", "change"])
        head = self._git(["rev-parse", "HEAD"]).strip()
        rng = f"{base}..{head}"
        bl = self.repo / ".build-loop"
        bl.mkdir(exist_ok=True)
        (bl / "judge-decisions.json").write_text(json.dumps([{**decision, "run_id": "r1", "diff_range": rng}]))
        (bl / "state.json").write_text(json.dumps({"runs": [{"run_id": "r1", "filesTouched": [rel]}]}))
        return rng

    def test_gated_range_missing_field_is_incomplete(self) -> None:
        patch = TOFU_PATCH.read_text(encoding="utf-8")
        self._setup_run("SpeakSavvy/Services/AdminGate.swift",
                        _new_file_text(patch, "SpeakSavvy/Services/AdminGate.swift"),
                        {"judge_id": "security-reviewer", "verdict": "pass"})
        res = stc.check_run(self.repo, "r1")
        self.assertEqual(res["status"], "incomplete", res)
        self.assertEqual(stc.main(["--workdir", str(self.repo), "--run-id", "r1"]), 1)

    def test_gated_range_complete_verdict_ok(self) -> None:
        patch = TOFU_PATCH.read_text(encoding="utf-8")
        self._setup_run("SpeakSavvy/Services/AdminGate.swift",
                        _new_file_text(patch, "SpeakSavvy/Services/AdminGate.swift"),
                        {"judge_id": "security-reviewer", "verdict": "pass", "stranger_test": GOOD_ST})
        self.assertEqual(stc.check_run(self.repo, "r1")["status"], "complete")

    def test_ungated_range_missing_field_ok(self) -> None:
        self._setup_run("src/util.py", "def add(a, b):\n    return a + b\n",
                        {"judge_id": "security-reviewer", "verdict": "pass"})
        self.assertEqual(stc.check_run(self.repo, "r1")["status"], "complete")

    def test_run_close_lint_marks_review_owed(self) -> None:
        import run_close_lint as rcl

        env = {"status": "recorded", "run_id": "r1"}
        patch = TOFU_PATCH.read_text(encoding="utf-8")
        self._setup_run("SpeakSavvy/Services/AdminGate.swift",
                        _new_file_text(patch, "SpeakSavvy/Services/AdminGate.swift"),
                        {"judge_id": "security-reviewer", "verdict": "pass"})
        out = rcl._apply_stranger_test(self.repo, dict(env))
        self.assertEqual(out["status"], "review_owed")
        self.assertIn("stranger_test", out["reason"])
        self.assertIn("stranger_test_check.py", out["remediation"])

    def test_owed_auditor_debt_stays_open_on_empty_stranger_test(self) -> None:
        import owed_verification as ov

        patch = TOFU_PATCH.read_text(encoding="utf-8")
        empty = {"judge_id": "independent-auditor", "verdict": "yay", "status": "verdict_recorded",
                 "stranger_test": {"applies": True, "answer": "", "in_release": ""}}
        rng = self._setup_run("SpeakSavvy/Services/AdminGate.swift",
                              _new_file_text(patch, "SpeakSavvy/Services/AdminGate.swift"), empty)
        debt = {"verifier": "independent-auditor", "run_id": "r1", "diff_range": rng}
        res = ov.evaluate_debt(self.repo, debt, record={"run_id": "r1"})
        self.assertFalse(res["satisfied"], res)
        self.assertTrue(any("stranger_test" in r["reason"] for r in res["rejected_evidence"]), res)
        # Same verdict with a filled stranger test discharges it.
        (self.repo / ".build-loop" / "judge-decisions.json").write_text(json.dumps(
            [{**empty, "stranger_test": GOOD_ST, "run_id": "r1", "diff_range": rng}]))
        self.assertTrue(ov.evaluate_debt(self.repo, debt, record={"run_id": "r1"})["satisfied"])

    def test_check_never_raises_on_garbage(self) -> None:
        bl = self.repo / ".build-loop"
        bl.mkdir(exist_ok=True)
        (bl / "judge-decisions.json").write_text("{not json")
        self.assertIn(stc.check_run(self.repo, "r1")["status"], ("complete", "skipped"))


if __name__ == "__main__":
    unittest.main()
