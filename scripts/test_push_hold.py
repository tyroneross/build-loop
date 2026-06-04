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

    def _write_state(self, judge_decisions: list[dict]) -> None:
        state = {
            "schema_version": 1,
            "runs": [
                {"run_id": "run_a", "judge_decisions": [{"verdict": "yay", "judge": "x"}]},
                {"run_id": "run_b", "judge_decisions": judge_decisions},
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
        state = {
            "runs": [
                {
                    "run_id": "run_X",
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


if __name__ == "__main__":
    unittest.main()
