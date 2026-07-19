# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/audit_before_commit — the temporal-membership guard on the hook-path
judge-decision write (RCA 2026-07-11 site 3).

_record_runs_judge_entry must NOT attach a commit's audit packet to a stale runs[-1] whose
window doesn't contain the trigger time — it opens a fresh hook-run entry instead — while
still appending to runs[-1] on the normal same-day path.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import audit_before_commit as abc  # noqa: E402


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class RecordRunsJudgeEntryMembershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".build-loop").mkdir(parents=True)

    def _write_state(self, runs: list) -> None:
        (self.root / ".build-loop" / "state.json").write_text(
            json.dumps({"runs": runs}), encoding="utf-8"
        )

    def _read_runs(self) -> list:
        return json.loads(
            (self.root / ".build-loop" / "state.json").read_text(encoding="utf-8")
        )["runs"]

    def test_stale_last_run_gets_fresh_hook_entry(self) -> None:
        """A month-stale runs[-1] must NOT absorb today's packet — a fresh hook_ run opens."""
        self._write_state([
            {"run_id": "old", "date": "2026-06-01T00:00:00Z", "judge_decisions": []},
        ])
        abc._record_runs_judge_entry(self.root, "abc1234", "packet_emitted", "2 files staged")
        runs = self._read_runs()
        self.assertEqual(len(runs), 2, runs)
        self.assertEqual(runs[0]["run_id"], "old")
        self.assertEqual(runs[0]["judge_decisions"], [], "stale run must stay untouched")
        self.assertTrue(runs[-1]["run_id"].startswith("hook_"), runs[-1])
        targets = [d.get("target") for d in runs[-1]["judge_decisions"]]
        self.assertIn("abc1234", targets)

    def test_in_window_last_run_receives_packet(self) -> None:
        """A same-day runs[-1] is the correct owner — no fresh run, packet appends there."""
        self._write_state([
            {"run_id": "current", "date": _now_iso(), "judge_decisions": []},
        ])
        abc._record_runs_judge_entry(self.root, "def5678", "packet_emitted", "1 file staged")
        runs = self._read_runs()
        self.assertEqual(len(runs), 1, "same-day run must not spawn a fresh hook run")
        self.assertEqual(runs[0]["run_id"], "current")
        targets = [d.get("target") for d in runs[0]["judge_decisions"]]
        self.assertIn("def5678", targets)

    def test_no_runs_creates_hook_entry(self) -> None:
        """Preserved behavior: empty runs[] → one fresh hook run with the packet."""
        self._write_state([])
        abc._record_runs_judge_entry(self.root, "aaa0000", "packet_emitted", "x")
        runs = self._read_runs()
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0]["run_id"].startswith("hook_"))

    def test_missing_state_is_noop(self) -> None:
        """Fail-soft: no state.json → returns without raising, writes nothing."""
        empty = Path(self._tmp.name) / "nostate"
        empty.mkdir()
        abc._record_runs_judge_entry(empty, "zzz", "packet_emitted", "x")  # must not raise
        self.assertFalse((empty / ".build-loop" / "state.json").exists())


# ---------------------------------------------------------------------------
# Risk classification (learn/risk-gated-commit-audit) — pure unit tests.
#
# _classify_risk(files, diff_body) is deliberately pure w.r.t. its two
# arguments (it does not re-shell out to git for the synthetic diff under
# test), so these run without a real git repo.


class ClassifyRiskTests(unittest.TestCase):
    def test_persisted_data_write_is_high(self) -> None:
        files = ["app/models/user.py"]
        diff = (
            "diff --git a/app/models/user.py b/app/models/user.py\n"
            "new file mode 100644\n"
            "index 0000000..1111111\n"
            "--- /dev/null\n"
            "+++ b/app/models/user.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+class User(models.Model):\n"
            "+    name = models.CharField(max_length=100)\n"
        )
        risk = abc._classify_risk(files, diff)
        self.assertEqual(risk["level"], "high")
        self.assertTrue(any("persisted-data" in r for r in risk["reasons"]), risk["reasons"])
        self.assertIn("app/models/user.py", risk["risky_files"])

    def test_docs_only_diff_is_low(self) -> None:
        files = ["docs/readme.md"]
        diff = (
            "diff --git a/docs/readme.md b/docs/readme.md\n"
            "index 1111111..2222222 100644\n"
            "--- a/docs/readme.md\n"
            "+++ b/docs/readme.md\n"
            "@@ -1 +1,2 @@\n"
            " # Title\n"
            "+Some prose about the project.\n"
        )
        risk = abc._classify_risk(files, diff)
        self.assertEqual(risk["level"], "low")
        self.assertEqual(risk["reasons"], [])
        self.assertEqual(risk["risky_files"], [])

    def test_many_files_diff_is_high(self) -> None:
        files = [f"scripts/file_{i}.py" for i in range(10)]
        risk = abc._classify_risk(files, "")
        self.assertEqual(risk["level"], "high")
        self.assertTrue(any("large changeset" in r for r in risk["reasons"]), risk["reasons"])
        self.assertTrue(risk["risky_files"], "file-count-only high must still cite files")

    def test_new_ui_presentation_surface_is_high(self) -> None:
        files = ["ios/Views/NewFeatureSheet.swift"]
        diff = (
            "diff --git a/ios/Views/NewFeatureSheet.swift b/ios/Views/NewFeatureSheet.swift\n"
            "new file mode 100644\n"
            "index 0000000..3333333\n"
            "--- /dev/null\n"
            "+++ b/ios/Views/NewFeatureSheet.swift\n"
            "@@ -0,0 +1,3 @@\n"
            "+struct NewFeatureSheet: View {\n"
            '+    var body: some View { Text("hi") }\n'
            "+}\n"
        )
        risk = abc._classify_risk(files, diff)
        self.assertEqual(risk["level"], "high")
        self.assertTrue(any("UI presentation surface" in r for r in risk["reasons"]), risk["reasons"])
        self.assertIn("ios/Views/NewFeatureSheet.swift", risk["risky_files"])

    def test_module_boundary_crossing_is_high(self) -> None:
        files = ["ios/App/Feature.swift", "web/src/component.tsx", "docs/notes.md"]
        risk = abc._classify_risk(files, "")
        self.assertEqual(risk["level"], "high")
        self.assertTrue(any("crosses module boundary" in r for r in risk["reasons"]), risk["reasons"])

    def test_auth_keyword_diff_reuses_infer_risk_surface_as_medium(self) -> None:
        """A diff with no high-risk signal but a constitution-keyword hit (e.g. auth)
        classifies as medium, by calling infer_risk_surface.evaluate() directly —
        not by re-deriving keyword risk logic in this module."""
        files = ["scripts/auth_check.py"]
        diff = (
            "diff --git a/scripts/auth_check.py b/scripts/auth_check.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/scripts/auth_check.py\n"
            "+++ b/scripts/auth_check.py\n"
            "@@ -1 +1,2 @@\n"
            " def check():\n"
            "+    auth = validate_session(token)\n"
        )
        risk = abc._classify_risk(files, diff)
        self.assertEqual(risk["level"], "medium")
        self.assertTrue(risk["reasons"])
        self.assertEqual(risk["risky_files"], [])

    def test_nextjs_app_router_page_is_high(self) -> None:
        """Lowercase `page.tsx` (Next.js app-router convention) is a new-UI-surface signal."""
        files = ["app/dashboard/page.tsx"]
        diff = (
            "diff --git a/app/dashboard/page.tsx b/app/dashboard/page.tsx\n"
            "new file mode 100644\n"
            "index 0000000..4444444\n"
            "--- /dev/null\n"
            "+++ b/app/dashboard/page.tsx\n"
            "@@ -0,0 +1,3 @@\n"
            "+export default function Dashboard() {\n"
            "+  return <div>hi</div>;\n"
            "+}\n"
        )
        risk = abc._classify_risk(files, diff)
        self.assertEqual(risk["level"], "high")
        self.assertTrue(any("UI presentation surface" in r for r in risk["reasons"]), risk["reasons"])
        self.assertIn("app/dashboard/page.tsx", risk["risky_files"])

    def test_nextjs_app_router_route_is_high(self) -> None:
        """Lowercase `route.ts` (Next.js app-router route handler) is a new-UI-surface signal."""
        files = ["app/api/widgets/route.ts"]
        diff = (
            "diff --git a/app/api/widgets/route.ts b/app/api/widgets/route.ts\n"
            "new file mode 100644\n"
            "index 0000000..5555555\n"
            "--- /dev/null\n"
            "+++ b/app/api/widgets/route.ts\n"
            "@@ -0,0 +1,3 @@\n"
            "+export async function GET() {\n"
            "+  return Response.json({});\n"
            "+}\n"
        )
        risk = abc._classify_risk(files, diff)
        self.assertEqual(risk["level"], "high")
        self.assertTrue(any("UI presentation surface" in r for r in risk["reasons"]), risk["reasons"])
        self.assertIn("app/api/widgets/route.ts", risk["risky_files"])

    def test_sqlalchemy_model_is_high(self) -> None:
        files = ["app/orm/user.py"]
        diff = (
            "diff --git a/app/orm/user.py b/app/orm/user.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/app/orm/user.py\n"
            "+++ b/app/orm/user.py\n"
            "@@ -1 +1,3 @@\n"
            " # existing\n"
            "+Base = declarative_base()\n"
            "+class User(Base):\n"
            "+    id = Column(Integer, primary_key=True)\n"
        )
        risk = abc._classify_risk(files, diff)
        self.assertEqual(risk["level"], "high")
        self.assertTrue(any("persisted-data" in r for r in risk["reasons"]), risk["reasons"])

    def test_drizzle_table_is_high(self) -> None:
        files = ["src/db/widgets.ts"]
        diff = (
            "diff --git a/src/db/widgets.ts b/src/db/widgets.ts\n"
            "index 1111111..2222222 100644\n"
            "--- a/src/db/widgets.ts\n"
            "+++ b/src/db/widgets.ts\n"
            "@@ -1 +1,2 @@\n"
            " // existing\n"
            "+export const widgets = pgTable('widgets', { id: serial('id') });\n"
        )
        risk = abc._classify_risk(files, diff)
        self.assertEqual(risk["level"], "high")
        self.assertTrue(any("persisted-data" in r for r in risk["reasons"]), risk["reasons"])

    def test_mongoose_schema_is_high(self) -> None:
        files = ["server/models/widget.js"]
        diff = (
            "diff --git a/server/models/widget.js b/server/models/widget.js\n"
            "index 1111111..2222222 100644\n"
            "--- a/server/models/widget.js\n"
            "+++ b/server/models/widget.js\n"
            "@@ -1 +1,2 @@\n"
            " // existing\n"
            "+const widgetSchema = new Schema({ name: String });\n"
        )
        risk = abc._classify_risk(files, diff)
        self.assertEqual(risk["level"], "high")
        self.assertTrue(any("persisted-data" in r for r in risk["reasons"]), risk["reasons"])

    def test_localstorage_write_is_high(self) -> None:
        files = ["web/src/session.ts"]
        diff = (
            "diff --git a/web/src/session.ts b/web/src/session.ts\n"
            "index 1111111..2222222 100644\n"
            "--- a/web/src/session.ts\n"
            "+++ b/web/src/session.ts\n"
            "@@ -1 +1,2 @@\n"
            " // existing\n"
            "+localStorage.setItem('token', token);\n"
        )
        risk = abc._classify_risk(files, diff)
        self.assertEqual(risk["level"], "high")
        self.assertTrue(any("persisted-data" in r for r in risk["reasons"]), risk["reasons"])

    def test_userdefaults_write_is_high(self) -> None:
        files = ["ios/App/SessionStore.swift"]
        diff = (
            "diff --git a/ios/App/SessionStore.swift b/ios/App/SessionStore.swift\n"
            "index 1111111..2222222 100644\n"
            "--- a/ios/App/SessionStore.swift\n"
            "+++ b/ios/App/SessionStore.swift\n"
            "@@ -1 +1,2 @@\n"
            " // existing\n"
            "+UserDefaults.standard.set(token, forKey: \"token\")\n"
        )
        risk = abc._classify_risk(files, diff)
        self.assertEqual(risk["level"], "high")
        self.assertTrue(any("persisted-data" in r for r in risk["reasons"]), risk["reasons"])

    def test_docs_only_diff_stays_low_with_broadened_patterns(self) -> None:
        """Broadening the classifier must not over-classify plain markdown/comments into high."""
        files = ["docs/architecture.md"]
        diff = (
            "diff --git a/docs/architecture.md b/docs/architecture.md\n"
            "index 1111111..2222222 100644\n"
            "--- a/docs/architecture.md\n"
            "+++ b/docs/architecture.md\n"
            "@@ -1 +1,3 @@\n"
            " # Architecture\n"
            "+We use a database and a schema, plus a page in the app for settings.\n"
            "+See the route users take through the UI.\n"
        )
        risk = abc._classify_risk(files, diff)
        self.assertEqual(risk["level"], "low", risk["reasons"])
        self.assertEqual(risk["reasons"], [])
        self.assertEqual(risk["risky_files"], [])

    def test_medium_reasons_come_from_infer_risk_surface_directly(self) -> None:
        """Cross-check: _classify_risk's medium-tier output matches calling
        infer_risk_surface.evaluate() directly on the same inputs — proving reuse,
        not reimplementation."""
        import infer_risk_surface as irs

        files = ["scripts/auth_check.py"]
        diff = "+    auth = validate_session(token)\n"
        direct = irs.evaluate(diff, files, set())
        risk = abc._classify_risk(files, diff)
        self.assertEqual(risk["level"], "medium")
        for rule_id in direct["matched_rules"]:
            self.assertTrue(
                any(rule_id in r for r in risk["reasons"]),
                f"expected {rule_id} surfaced in {risk['reasons']}",
            )


# ---------------------------------------------------------------------------
# Escalated packet + opt-in hard block + regression — CLI integration tests.
#
# These drive the real script as a subprocess against a real temp git repo,
# so packet text and exit codes are exercised end-to-end exactly as the
# PreToolUse hook invokes it.

SCRIPT = HERE / "audit_before_commit.py"


class _GitRepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self._git(["init", "-q"])
        self._git(["config", "user.email", "test@example.com"])
        self._git(["config", "user.name", "Test"])
        (self.repo / "README.md").write_text("init\n", encoding="utf-8")
        self._git(["add", "README.md"])
        self._git(["commit", "-q", "-m", "init"])

    def _git(self, args: list[str]) -> None:
        subprocess.run(["git"] + args, cwd=self.repo, check=True, capture_output=True, text=True)

    def _write_and_stage(self, rel_path: str, content: str) -> None:
        path = self.repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._git(["add", rel_path])

    def _run_hook(self, extra_env: dict | None = None):
        env = dict(os.environ)
        env.pop("BUILDLOOP_AUDIT_BYPASS", None)
        env.pop("BUILDLOOP_ENFORCE_RISK_AUDIT", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=self.repo,
            env=env,
            input="",
            capture_output=True,
            text=True,
            timeout=30,
        )


class EscalatedPacketTests(_GitRepoCase):
    def test_high_risk_packet_is_mandatory_and_cites_files(self) -> None:
        self._write_and_stage(
            "ios/Views/NewFeatureSheet.swift",
            'struct NewFeatureSheet: View {\n    var body: some View { Text("hi") }\n}\n',
        )
        result = self._run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)  # default: never blocks on risk alone
        self.assertIn("HIGH-RISK", result.stderr)
        self.assertIn("REQUIRED", result.stderr)
        self.assertIn("ios/Views/NewFeatureSheet.swift", result.stderr)

    def test_low_risk_packet_is_unchanged_advisory(self) -> None:
        self._write_and_stage("docs/notes.md", "Some prose about the project.\n")
        result = self._run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("HIGH-RISK", result.stderr)
        self.assertNotIn("RISK GATE BLOCK", result.stderr)
        self.assertIn("yay (approve)", result.stderr)


class OptInBlockTests(_GitRepoCase):
    def _stage_persisted_model(self) -> None:
        self._write_and_stage(
            "app/models/Widget.py",
            "class Widget(models.Model):\n    name = models.CharField(max_length=100)\n",
        )

    def test_flag_off_default_never_blocks_on_high_risk(self) -> None:
        self._stage_persisted_model()
        result = self._run_hook()  # no env, no config.json — default OFF
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("RISK GATE BLOCK", result.stderr)

    def test_flag_on_high_risk_no_matching_verdict_blocks(self) -> None:
        self._stage_persisted_model()
        result = self._run_hook(extra_env={"BUILDLOOP_ENFORCE_RISK_AUDIT": "1"})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("RISK GATE BLOCK", result.stderr)
        self.assertIn("app/models/Widget.py", result.stderr)

    def test_flag_on_high_risk_with_matching_verdict_passes(self) -> None:
        self._stage_persisted_model()
        now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        state_path = self.repo / ".build-loop" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({
                "runs": [{
                    "run_id": "r1",
                    "judge_decisions": [{
                        "judge_id": "independent-auditor-hook",
                        "verdict": "yay",
                        "verdict_ts": now,
                        "risky_files": ["app/models/Widget.py"],
                    }],
                }],
            }),
            encoding="utf-8",
        )
        result = self._run_hook(extra_env={"BUILDLOOP_ENFORCE_RISK_AUDIT": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("RISK GATE BLOCK", result.stderr)

    def test_flag_on_high_risk_recorded_nay_still_blocks(self) -> None:
        """FIX #3: a recorded REJECTION verdict must NOT satisfy the opt-in block —
        only an explicit 'yay' approval may unblock a high-risk commit."""
        self._stage_persisted_model()
        now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        state_path = self.repo / ".build-loop" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({
                "runs": [{
                    "run_id": "r1",
                    "judge_decisions": [{
                        "judge_id": "independent-auditor-hook",
                        "verdict": "nay",
                        "verdict_ts": now,
                        "risky_files": ["app/models/Widget.py"],
                    }],
                }],
            }),
            encoding="utf-8",
        )
        result = self._run_hook(extra_env={"BUILDLOOP_ENFORCE_RISK_AUDIT": "1"})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("RISK GATE BLOCK", result.stderr)
        self.assertIn("app/models/Widget.py", result.stderr)

    def test_flag_on_high_risk_recorded_suggest_still_blocks(self) -> None:
        """A 'suggest correction' verdict is also not an approval — must still block."""
        self._stage_persisted_model()
        now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        state_path = self.repo / ".build-loop" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({
                "runs": [{
                    "run_id": "r1",
                    "judge_decisions": [{
                        "judge_id": "independent-auditor-hook",
                        "verdict": "suggest",
                        "verdict_ts": now,
                        "risky_files": ["app/models/Widget.py"],
                    }],
                }],
            }),
            encoding="utf-8",
        )
        result = self._run_hook(extra_env={"BUILDLOOP_ENFORCE_RISK_AUDIT": "1"})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("RISK GATE BLOCK", result.stderr)

    def test_config_flag_alone_also_enables_block(self) -> None:
        self._stage_persisted_model()
        config_path = self.repo / ".build-loop" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({"sessionPrefs": {"enforceRiskAudit": True}}), encoding="utf-8"
        )
        result = self._run_hook()  # no env var — config.json alone
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("RISK GATE BLOCK", result.stderr)


class RegressionTests(_GitRepoCase):
    def test_secrets_file_still_blocks(self) -> None:
        self._write_and_stage("config/id_rsa", "-----BEGIN RSA PRIVATE KEY-----\nfake\n")
        result = self._run_hook()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("DETERMINISTIC BLOCK", result.stderr)

    def test_conflict_markers_still_block(self) -> None:
        self._write_and_stage(
            "docs/notes.md",
            "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n",
        )
        result = self._run_hook()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("DETERMINISTIC BLOCK", result.stderr)

    def test_bypass_still_exits_zero(self) -> None:
        self._stage_persisted_model_inline()
        result = self._run_hook(extra_env={
            "BUILDLOOP_AUDIT_BYPASS": "1",
            "BUILDLOOP_ENFORCE_RISK_AUDIT": "1",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BYPASS active", result.stderr)

    def _stage_persisted_model_inline(self) -> None:
        self._write_and_stage(
            "app/models/Widget.py",
            "class Widget(models.Model):\n    name = models.CharField(max_length=100)\n",
        )

    def test_low_risk_unchanged_by_enforce_flag(self) -> None:
        self._write_and_stage("docs/notes.md", "Some prose about the project.\n")
        result = self._run_hook(extra_env={"BUILDLOOP_ENFORCE_RISK_AUDIT": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("RISK GATE BLOCK", result.stderr)
        self.assertNotIn("HIGH-RISK", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
