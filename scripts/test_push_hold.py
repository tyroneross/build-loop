#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/push_hold.py.

Covers (mandated by the build-loop SELF-MOD SAFETY GATE for any new script):

- ``--set`` writes a marker that ``--status`` reads back.
- ``--release`` removes the marker.
- ``--status`` is no-op when no marker / no blocking verdict.
- ``detect_blocking_verdict`` returns a record for an unresolved
  ``nay`` / ``suggest_correction`` / ``look_again`` / ``block`` in the latest
  run's ``judge_decisions[]``; returns ``None`` for ``yay`` or a verdict
  flagged ``resolved: true``.
- ``evaluate_push``:

  * no-hold + protected push → ``allow``
  * hold + protected push → ``block`` (exit 1)
  * hold + non-protected push → ``allow``
  * bypass env + hold + protected → ``bypass`` (exit 0, logged)
  * malformed marker → ``block`` (fail-safe)
  * empty stdin → ``allow``

- ``evaluate_push`` raises ZERO exceptions — internal errors stay confined to
  the hold-active check; the hook layer is responsible for fail-open on
  exceptions thrown by the importable surface (covered by the hook test).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT = Path(__file__).resolve().parent / "push_hold.py"


def _run_cli(workdir: Path, *args: str, env_extra: dict[str, str] | None = None) -> tuple[int, dict]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir), *args, "--json"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {"_stdout": proc.stdout, "_stderr": proc.stderr}
    return proc.returncode, payload


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.workdir = Path(self._td.name)
        (self.workdir / ".build-loop").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._td.cleanup()


class TestCli(_Base):
    def test_status_no_hold(self):
        rc, payload = _run_cli(self.workdir, "--status")
        self.assertEqual(rc, 0, payload)
        self.assertFalse(payload["active"])
        self.assertEqual(payload["source"], "none")

    def test_set_then_status_then_release(self):
        rc, _ = _run_cli(self.workdir, "--set", "--reason", "briefed: do-not-push", "--source", "orchestrator")
        self.assertEqual(rc, 0)
        marker_path = self.workdir / ".build-loop" / ".push-hold"
        self.assertTrue(marker_path.exists())
        body = json.loads(marker_path.read_text())
        self.assertEqual(body["reason"], "briefed: do-not-push")
        self.assertEqual(body["source"], "orchestrator")
        self.assertIn("set_at", body)

        rc, payload = _run_cli(self.workdir, "--status")
        self.assertEqual(rc, 0)
        self.assertTrue(payload["active"])
        self.assertEqual(payload["source"], "marker")
        self.assertEqual(payload["reason"], "briefed: do-not-push")

        rc, payload = _run_cli(self.workdir, "--release", "--reason", "audit cleared")
        self.assertEqual(rc, 0)
        self.assertTrue(payload["removed"])
        self.assertFalse(marker_path.exists())

        rc, payload = _run_cli(self.workdir, "--release")
        self.assertEqual(rc, 0)
        self.assertFalse(payload["removed"])

    def test_set_requires_reason(self):
        # Direct CLI invocation: --set without --reason exits 2 via argparse.error.
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--workdir", str(self.workdir), "--set", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--reason", proc.stderr)

    def test_set_with_finding_ids(self):
        rc, _ = _run_cli(
            self.workdir,
            "--set",
            "--reason",
            "unresolved suggest_correction",
            "--source",
            "review-a",
            "--auditor-verdict",
            "suggest_correction",
            "--finding-ids",
            "f1,f2,f3",
            "--run-id",
            "run_test_001",
        )
        self.assertEqual(rc, 0)
        body = json.loads((self.workdir / ".build-loop" / ".push-hold").read_text())
        self.assertEqual(body["auditor_verdict"], "suggest_correction")
        self.assertEqual(body["finding_ids"], ["f1", "f2", "f3"])
        self.assertEqual(body["run_id"], "run_test_001")


class TestStateJsonBlockingVerdict(_Base):
    """detect_blocking_verdict + is_hold_active picking up state.json signal."""

    @staticmethod
    def _recent_ts() -> str:
        from datetime import datetime, timezone, timedelta
        return (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _write_state(self, judge_decisions: list[dict]) -> None:
        state = {
            "schema_version": 1,
            "runs": [
                {"run_id": "run_a", "created_at": self._recent_ts(),
                 "judge_decisions": [{"verdict": "yay", "judge": "x"}]},
                {"run_id": "run_b", "created_at": self._recent_ts(),
                 "judge_decisions": judge_decisions},
            ],
        }
        (self.workdir / ".build-loop" / "state.json").write_text(json.dumps(state))

    def test_unresolved_suggest_correction_detected(self):
        sys.path.insert(0, str(SCRIPT.parent))
        try:
            import importlib

            ph = importlib.import_module("push_hold")
            importlib.reload(ph)
            self._write_state(
                [
                    {
                        "verdict": "suggest_correction",
                        "judge": "independent-auditor",
                        "finding_ids": ["f1", "f2"],
                    }
                ]
            )
            v = ph.detect_blocking_verdict(self.workdir)
            self.assertIsNotNone(v)
            self.assertEqual(v["verdict"], "suggest_correction")
            self.assertEqual(v["finding_ids"], ["f1", "f2"])
            active, reason, source = ph.is_hold_active(self.workdir)
            self.assertTrue(active)
            self.assertEqual(source, "state")
            self.assertIn("suggest_correction", reason)
        finally:
            sys.path.remove(str(SCRIPT.parent))

    def test_resolved_verdict_ignored(self):
        sys.path.insert(0, str(SCRIPT.parent))
        try:
            import importlib

            ph = importlib.import_module("push_hold")
            importlib.reload(ph)
            self._write_state(
                [
                    {"verdict": "nay", "judge": "independent-auditor", "resolved": True},
                    {"verdict": "look_again", "judge": "independent-auditor", "status": "resolved"},
                    {"verdict": "yay", "judge": "independent-auditor"},
                ]
            )
            self.assertIsNone(ph.detect_blocking_verdict(self.workdir))
            active, _, source = ph.is_hold_active(self.workdir)
            self.assertFalse(active)
            self.assertEqual(source, "none")
        finally:
            sys.path.remove(str(SCRIPT.parent))

    def test_marker_overrides_state(self):
        """Explicit marker beats state.json signal — marker wins, source=marker."""
        sys.path.insert(0, str(SCRIPT.parent))
        try:
            import importlib

            ph = importlib.import_module("push_hold")
            importlib.reload(ph)
            self._write_state([{"verdict": "nay", "judge": "independent-auditor"}])
            ph.set_marker(self.workdir, reason="manual hold", source="manual")
            active, reason, source = ph.is_hold_active(self.workdir)
            self.assertTrue(active)
            self.assertEqual(source, "marker")
            self.assertEqual(reason, "manual hold")
        finally:
            sys.path.remove(str(SCRIPT.parent))


class TestEvaluatePush(_Base):
    """The path the git hook actually calls."""

    def _ph(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib

        try:
            ph = importlib.import_module("push_hold")
            importlib.reload(ph)
            return ph
        finally:
            sys.path.remove(str(SCRIPT.parent))

    def _push_line(self, remote_ref: str) -> str:
        return f"refs/heads/main aaaaaa {remote_ref} bbbbbb\n"

    def test_no_protected_target_allows(self):
        ph = self._ph()
        r = ph.evaluate_push(
            self.workdir,
            [self._push_line("refs/heads/feature-x")],
            env={},
            protected_branches={"main"},
        )
        self.assertEqual(r["action"], "allow")
        self.assertEqual(r["exit_code"], 0)

    def test_no_hold_allows_protected_push(self):
        """The whole point — autonomous push to main with no hold MUST still work."""
        ph = self._ph()
        r = ph.evaluate_push(
            self.workdir,
            [self._push_line("refs/heads/main")],
            env={},
            protected_branches={"main"},
        )
        self.assertEqual(r["action"], "allow")
        self.assertEqual(r["exit_code"], 0)

    def test_hold_blocks_protected_push(self):
        ph = self._ph()
        ph.set_marker(self.workdir, reason="briefed: do-not-push", source="orchestrator")
        r = ph.evaluate_push(
            self.workdir,
            [self._push_line("refs/heads/main")],
            env={},
            protected_branches={"main"},
        )
        self.assertEqual(r["action"], "block")
        self.assertEqual(r["exit_code"], 1)
        self.assertIn("do-not-push", r["reason"])
        self.assertEqual(r["protected_targets"], ["main"])

    def test_hold_allows_non_protected_push(self):
        ph = self._ph()
        ph.set_marker(self.workdir, reason="briefed: do-not-push", source="orchestrator")
        r = ph.evaluate_push(
            self.workdir,
            [self._push_line("refs/heads/scratch/wip")],
            env={},
            protected_branches={"main"},
        )
        self.assertEqual(r["action"], "allow")

    def test_bypass_env_allows_with_log(self):
        ph = self._ph()
        ph.set_marker(self.workdir, reason="briefed: do-not-push", source="orchestrator")
        r = ph.evaluate_push(
            self.workdir,
            [self._push_line("refs/heads/main")],
            env={"BUILDLOOP_PUSH_HOLD_BYPASS": "1"},
            protected_branches={"main"},
        )
        self.assertEqual(r["action"], "bypass")
        self.assertEqual(r["exit_code"], 0)
        log = self.workdir / ".build-loop" / "audit-log.md"
        self.assertTrue(log.exists())
        self.assertIn("BYPASS", log.read_text())

    def test_malformed_marker_is_held(self):
        """Fail-SAFE on a corrupted marker — prefer blocking one extra push to allowing one."""
        ph = self._ph()
        marker = self.workdir / ".build-loop" / ".push-hold"
        marker.write_text("not json {{{")
        r = ph.evaluate_push(
            self.workdir,
            [self._push_line("refs/heads/main")],
            env={},
            protected_branches={"main"},
        )
        self.assertEqual(r["action"], "block")
        self.assertEqual(r["source"], "marker")

    def test_empty_stdin_allows(self):
        ph = self._ph()
        ph.set_marker(self.workdir, reason="held", source="manual")
        r = ph.evaluate_push(self.workdir, [], env={}, protected_branches={"main"})
        self.assertEqual(r["action"], "allow")
        self.assertEqual(r["protected_targets"], [])

    def test_state_json_blocking_verdict_blocks(self):
        ph = self._ph()
        from datetime import datetime, timezone, timedelta
        recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = {
            "runs": [
                {
                    "run_id": "run_X",
                    "created_at": recent_ts,
                    "judge_decisions": [
                        {
                            "verdict": "suggest_correction",
                            "judge": "independent-auditor",
                            "finding_ids": ["f-blocking-1"],
                        }
                    ],
                }
            ]
        }
        (self.workdir / ".build-loop" / "state.json").write_text(json.dumps(state))
        r = ph.evaluate_push(
            self.workdir,
            [self._push_line("refs/heads/main")],
            env={},
            protected_branches={"main"},
        )
        self.assertEqual(r["action"], "block")
        self.assertEqual(r["source"], "state")
        self.assertIn("suggest_correction", r["reason"])
        self.assertIn("f-blocking-1", r["reason"])


class TestStalenessGuard(_Base):
    """f2: detect_blocking_verdict must ignore verdicts older than MAX_VERDICT_AGE_HOURS."""

    def _ph(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        try:
            ph = importlib.import_module("push_hold")
            importlib.reload(ph)
            return ph
        finally:
            sys.path.remove(str(SCRIPT.parent))

    def _write_state_with_ts(self, ts_iso: str) -> None:
        state = {
            "runs": [
                {
                    "run_id": "run_stale_test",
                    "created_at": ts_iso,
                    "judge_decisions": [
                        {
                            "verdict": "suggest_correction",
                            "judge": "independent-auditor",
                            "finding_ids": ["f-stale"],
                        }
                    ],
                }
            ]
        }
        (self.workdir / ".build-loop" / "state.json").write_text(json.dumps(state))

    def _ts_hours_ago(self, hours: float) -> str:
        from datetime import datetime, timezone, timedelta
        return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_stale_verdict_25h_does_not_block(self):
        """An unresolved blocking verdict timestamped 25h ago must NOT block."""
        ph = self._ph()
        self._write_state_with_ts(self._ts_hours_ago(25))
        v = ph.detect_blocking_verdict(self.workdir)
        self.assertIsNone(v, "25h-old verdict must be treated as stale → None")
        r = ph.evaluate_push(
            self.workdir,
            ["refs/heads/main aaaaaa refs/heads/main bbbbbb\n"],
            env={},
            protected_branches={"main"},
        )
        self.assertEqual(r["action"], "allow", "25h-old verdict must not wedge push")

    def test_recent_verdict_2h_still_blocks(self):
        """An unresolved blocking verdict timestamped 2h ago must still block."""
        ph = self._ph()
        self._write_state_with_ts(self._ts_hours_ago(2))
        v = ph.detect_blocking_verdict(self.workdir)
        self.assertIsNotNone(v, "2h-old verdict must still be active")
        r = ph.evaluate_push(
            self.workdir,
            ["refs/heads/main aaaaaa refs/heads/main bbbbbb\n"],
            env={},
            protected_branches={"main"},
        )
        self.assertEqual(r["action"], "block", "2h-old verdict must block")

    def test_missing_timestamp_does_not_block(self):
        """A run with no timestamp is treated as potentially stale → no block."""
        ph = self._ph()
        state = {
            "runs": [
                {
                    "run_id": "run_no_ts",
                    # no created_at / ts / started_at
                    "judge_decisions": [
                        {"verdict": "suggest_correction", "judge": "independent-auditor"}
                    ],
                }
            ]
        }
        (self.workdir / ".build-loop" / "state.json").write_text(json.dumps(state))
        v = ph.detect_blocking_verdict(self.workdir)
        self.assertIsNone(v, "missing timestamp must be treated as stale → None")

    def test_env_override_max_age(self):
        """BUILDLOOP_PUSH_HOLD_MAX_AGE_H env narrows the staleness window."""
        ph = self._ph()
        # 3h-old verdict; default window is 24h → active; override to 2h → stale
        self._write_state_with_ts(self._ts_hours_ago(3))
        import os
        old = os.environ.get("BUILDLOOP_PUSH_HOLD_MAX_AGE_H")
        try:
            os.environ["BUILDLOOP_PUSH_HOLD_MAX_AGE_H"] = "2"
            # reload to pick up env at module level? No — _max_verdict_age_hours reads
            # os.environ at call time, so no reload needed.
            v = ph.detect_blocking_verdict(self.workdir)
            self.assertIsNone(v, "3h-old verdict with 2h window must be stale")
        finally:
            if old is None:
                os.environ.pop("BUILDLOOP_PUSH_HOLD_MAX_AGE_H", None)
            else:
                os.environ["BUILDLOOP_PUSH_HOLD_MAX_AGE_H"] = old


class TestReleaseIfBriefed(_Base):
    """f1: --release-if-briefed / release_if_briefed() closeout helper."""

    def _ph(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        try:
            ph = importlib.import_module("push_hold")
            importlib.reload(ph)
            return ph
        finally:
            sys.path.remove(str(SCRIPT.parent))

    def _ts_hours_ago(self, hours: float) -> str:
        from datetime import datetime, timezone, timedelta
        return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_no_marker_is_noop(self):
        ph = self._ph()
        result = ph.release_if_briefed(self.workdir)
        self.assertEqual(result["action"], "noop_absent")
        marker = self.workdir / ".build-loop" / ".push-hold"
        self.assertFalse(marker.exists())

    def test_orchestrator_hold_no_blocking_verdict_is_released(self):
        """Briefed orchestrator hold + no blocking findings → marker cleared."""
        ph = self._ph()
        ph.set_marker(self.workdir, reason="briefed: do-not-push", source="orchestrator")
        marker = self.workdir / ".build-loop" / ".push-hold"
        self.assertTrue(marker.exists())
        result = ph.release_if_briefed(self.workdir, reason="run closed, no blocking findings")
        self.assertEqual(result["action"], "released")
        self.assertFalse(marker.exists(), "marker must be gone after release")

    def test_manual_hold_no_blocking_verdict_is_released(self):
        ph = self._ph()
        ph.set_marker(self.workdir, reason="manual hold", source="manual")
        result = ph.release_if_briefed(self.workdir)
        self.assertEqual(result["action"], "released")

    def test_review_a_hold_is_skipped(self):
        """source=review-a holds are owned by Review-A's re-audit path, not closeout."""
        ph = self._ph()
        ph.set_marker(self.workdir, reason="review-a blocking verdict", source="review-a")
        result = ph.release_if_briefed(self.workdir)
        self.assertEqual(result["action"], "noop_review_a")
        marker = self.workdir / ".build-loop" / ".push-hold"
        self.assertTrue(marker.exists(), "review-a marker must remain untouched")

    def test_orchestrator_hold_with_active_blocking_verdict_retained(self):
        """Briefed hold + still-active blocking verdict → hold must stay."""
        ph = self._ph()
        # Write a recent (2h old) blocking verdict.
        state = {
            "runs": [
                {
                    "run_id": "run_active",
                    "created_at": self._ts_hours_ago(2),
                    "judge_decisions": [
                        {"verdict": "suggest_correction", "judge": "independent-auditor"}
                    ],
                }
            ]
        }
        (self.workdir / ".build-loop" / "state.json").write_text(json.dumps(state))
        ph.set_marker(self.workdir, reason="briefed: do-not-push", source="orchestrator")
        result = ph.release_if_briefed(self.workdir)
        self.assertEqual(result["action"], "noop_blocking_verdict")
        marker = self.workdir / ".build-loop" / ".push-hold"
        self.assertTrue(marker.exists(), "marker must stay when blocking verdict is active")

    def test_cli_release_if_briefed(self):
        """CLI --release-if-briefed clears an orchestrator hold with no active verdicts."""
        rc, payload = _run_cli(
            self.workdir, "--set", "--reason", "briefed: do-not-push", "--source", "orchestrator"
        )
        self.assertEqual(rc, 0)
        rc, payload = _run_cli(self.workdir, "--release-if-briefed", "--reason", "run closed")
        self.assertEqual(rc, 0, payload)
        self.assertEqual(payload["action"], "released")
        marker = self.workdir / ".build-loop" / ".push-hold"
        self.assertFalse(marker.exists())


class TestCorruptMarkerMessage(_Base):
    """f4: corrupt marker stderr message must mention 'corrupt' and '--release'."""

    def _installed_hook(self, workdir: Path, hook_source: Path) -> None:
        """Write the hook to .git/hooks/pre-push with the build-loop marker."""
        hooks_dir = workdir / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        body = hook_source.read_text(encoding="utf-8")
        from install_git_hooks import HOOK_MARKER  # type: ignore
        if HOOK_MARKER not in body:
            lines = body.splitlines(keepends=True)
            if lines and lines[0].startswith("#!"):
                lines.insert(1, f"{HOOK_MARKER}\n")
            else:
                lines.insert(0, f"{HOOK_MARKER}\n")
            body = "".join(lines)
        dst = hooks_dir / "pre-push"
        dst.write_text(body, encoding="utf-8")
        import stat
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def test_corrupt_marker_stderr_mentions_release(self):
        """When marker is corrupt, the block message must mention 'corrupt' and '--release'.

        Tests _format_block_message in the pre-push hook directly by loading it
        as a Python module (no .py extension — use importlib with explicit loader).
        """
        import importlib.util
        hook_src = SCRIPT.parent.parent / "hooks" / "git" / "pre-push"

        spec = importlib.util.spec_from_loader(
            "pre_push_hook_mod",
            importlib.util.LazyLoader(
                importlib.machinery.SourceFileLoader("pre_push_hook_mod", str(hook_src))
            ),
        )
        # Simpler: just exec the source directly and grab _format_block_message.
        hook_ns: dict = {}
        exec(compile(hook_src.read_text(encoding="utf-8"), str(hook_src), "exec"), hook_ns)
        _format_block_message = hook_ns["_format_block_message"]

        # A block verdict whose reason indicates an unparseable/corrupt marker.
        verdict = {
            "action": "block",
            "exit_code": 1,
            "reason": "marker present but unparseable",
            "source": "marker",
            "protected_targets": ["main"],
        }
        msg = _format_block_message(verdict)
        self.assertIn("corrupt", msg.lower(), f"block message must mention 'corrupt': {msg!r}")
        self.assertIn("--release", msg, f"block message must mention '--release': {msg!r}")


class TestDeletePushBlocked(_Base):
    """f5: a branch-delete push (zero-sha) is blocked when hold is active."""

    def _ph(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        try:
            ph = importlib.import_module("push_hold")
            importlib.reload(ph)
            return ph
        finally:
            sys.path.remove(str(SCRIPT.parent))

    def test_delete_push_blocked_when_hold_active(self):
        """Delete-push (local_sha=0*40) to main is blocked when hold is active."""
        ph = self._ph()
        ph.set_marker(self.workdir, reason="briefed: do-not-push", source="orchestrator")
        zero_sha = "0" * 40
        r = ph.evaluate_push(
            self.workdir,
            [f"(delete) {zero_sha} refs/heads/main {zero_sha}\n"],
            env={},
            protected_branches={"main"},
        )
        self.assertEqual(r["action"], "block", "delete push to main under hold must be blocked")
        self.assertEqual(r["exit_code"], 1)

    def test_delete_push_allowed_when_no_hold(self):
        """Delete-push to main with no hold must be allowed (no protection gate fires without hold)."""
        ph = self._ph()
        zero_sha = "0" * 40
        r = ph.evaluate_push(
            self.workdir,
            [f"(delete) {zero_sha} refs/heads/main {zero_sha}\n"],
            env={},
            protected_branches={"main"},
        )
        self.assertEqual(r["action"], "allow")

    def test_delete_push_non_protected_branch_allowed(self):
        """Delete-push to a non-protected branch is always allowed."""
        ph = self._ph()
        ph.set_marker(self.workdir, reason="hold", source="orchestrator")
        zero_sha = "0" * 40
        r = ph.evaluate_push(
            self.workdir,
            [f"(delete) {zero_sha} refs/heads/scratch/wip {zero_sha}\n"],
            env={},
            protected_branches={"main"},
        )
        self.assertEqual(r["action"], "allow")


if __name__ == "__main__":
    unittest.main()


class TestParseIsoTsRobust(unittest.TestCase):
    """_parse_iso_ts must accept microseconds/Z/offset or the staleness guard
    silently disables blocking (unparsed ts reads as 'missing -> don't block')."""

    def _ph(self):
        import importlib, push_hold
        return importlib.reload(push_hold)

    def test_microseconds_and_variants_parse(self):
        ph = self._ph()
        for s in (
            "2026-06-04T21:06:46.112690+00:00",
            "2026-06-04T21:06:46.114089Z",
            "2026-06-04T21:06:46Z",
            "2026-06-04T21:06:46+00:00",
            "2026-06-04T21:06:46",
        ):
            self.assertIsNotNone(ph._parse_iso_ts(s), f"must parse {s!r}")
        self.assertIsNone(ph._parse_iso_ts("not-a-timestamp"))
        self.assertIsNone(ph._parse_iso_ts(None))
