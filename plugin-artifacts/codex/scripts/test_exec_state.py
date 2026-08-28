#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for exec_state.py — the item_iteration telemetry producer CLI.

Focus: the row records BOTH tier and the resolved model, so a tiered surface is
auditable after the fact. The headline case is `--tier frontier` resolving to
the tier's default with no config — i.e. the instrument actually captures which
model served the tier rather than leaving it null capacity. That default was
`fable` until 2026-07-28, when Opus 5 took T1; the test reads it from the
resolver so a future swap touches the taxonomy only.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "exec_state.py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "rally_point"))

from write_run_entry import update_execution_state  # type: ignore  # noqa: E402
from model_overrides import resolve_model  # type: ignore  # noqa: E402


def _frontier_default(workdir: Path) -> str:
    """Resolve through the same override stack exercised by ``exec_state.py``."""
    return str(resolve_model(tier="frontier", workdir=workdir).get("model"))


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


class ExecStateItemIterationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".build-loop").mkdir(parents=True)
        self.state_path = self.root / ".build-loop" / "state.json"
        # item_iteration requires an existing execution block (run start first).
        update_execution_state(
            self.state_path,
            "start",
            run_id="bl-test-run",
            queued_chunks=["c0"],
            file_ownership={"c0": ["a.py"]},
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _last_attempt(self, item_id: str) -> dict:
        state = json.loads(self.state_path.read_text())
        return state["execution"]["item_iterations"][item_id][-1]

    def test_tier_frontier_resolves_to_tier_default(self) -> None:
        """--tier frontier with no config records tier=frontier + the T1 default.

        That default was `fable` until 2026-07-28, when Opus 5 took T1. The
        durable assertion is that the recorded model is whatever the frontier
        tier resolves to — not a hardcoded token.
        """
        r = run_cli(
            "item-iteration", "--workdir", str(self.root),
            "--item-id", "q-7", "--status", "passed",
            "--validator", "independent-auditor", "--tier", "frontier",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        row = self._last_attempt("q-7")
        self.assertEqual(row["tier"], "frontier")
        self.assertEqual(row["model"], _frontier_default(self.root))
        self.assertEqual(row["validator"], "independent-auditor")
        self.assertEqual(row["status"], "passed")

    def test_explicit_model_skips_resolution(self) -> None:
        """--model is recorded verbatim; tier omitted when not passed."""
        r = run_cli(
            "item-iteration", "--workdir", str(self.root),
            "--item-id", "q-8", "--model", "claude-opus-4-7",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        row = self._last_attempt("q-8")
        self.assertEqual(row["model"], "claude-opus-4-7")
        self.assertNotIn("tier", row)

    def test_config_override_wins_over_tier_default(self) -> None:
        """A repo config.json modelOverride for the tier is what gets recorded."""
        cfg = self.root / ".build-loop" / "config.json"
        cfg.write_text(json.dumps({"modelOverrides": {"code": "gpt-5-codex"}}))
        r = run_cli(
            "item-iteration", "--workdir", str(self.root),
            "--item-id", "q-9", "--tier", "code",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        row = self._last_attempt("q-9")
        self.assertEqual(row["tier"], "code")
        self.assertEqual(row["model"], "gpt-5-codex")

    def test_attempts_increment_per_item(self) -> None:
        for _ in range(2):
            run_cli("item-iteration", "--workdir", str(self.root), "--item-id", "q-10", "--tier", "code")
        state = json.loads(self.state_path.read_text())
        attempts = state["execution"]["item_iterations"]["q-10"]
        self.assertEqual([a["attempt"] for a in attempts], [1, 2])


class ExecStateStartIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".build-loop").mkdir(parents=True)
        self.state_path = self.root / ".build-loop" / "state.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_start_enriches_phase1_identity_without_losing_recovery_pointers(self) -> None:
        identity = {
            "build_loop_id": "bl-test-run",
            "started_at": "2026-08-27T17:47:01Z",
            "started_by_tool": "codex",
            "started_by_session_id": "session-1",
            "current_session_id": "session-1",
            "run_label": "codex#test 2026-08-27T17:47:01Z",
            "run_worktree_path": str(self.root / ".build-loop/worktrees/run-test"),
            "run_worktree_branch": "bl/run-test",
            "data_manifest_path": str(self.root / ".build-loop/data-manifests/test.json"),
            "data_root": str(self.root / ".build-loop/data/test"),
            "crashed_at": "2026-08-27T18:18:30Z",
            "crash_signal": "stop_hook",
        }
        self.state_path.write_text(json.dumps({"execution": identity}))

        execution = update_execution_state(
            self.state_path,
            "start",
            run_id="bl-test-run",
            queued_chunks=["voice-review"],
            file_ownership={"voice-review": ["SpeakSavvy/Services/RealtimeVoiceService.swift"]},
        )

        for key in (
            "build_loop_id",
            "started_at",
            "started_by_tool",
            "started_by_session_id",
            "current_session_id",
            "run_label",
            "run_worktree_path",
            "run_worktree_branch",
            "data_manifest_path",
            "data_root",
        ):
            self.assertEqual(execution[key], identity[key])
        self.assertEqual(execution["schema_version"], 1)
        self.assertEqual(execution["run_id"], "bl-test-run")
        self.assertEqual(execution["phase"], "execute")
        self.assertEqual(execution["queued_chunks"], ["voice-review"])
        self.assertIsNone(execution["crashed_at"])
        self.assertNotIn("crash_signal", execution)

    def test_start_rejects_a_different_active_identity(self) -> None:
        self.state_path.write_text(json.dumps({
            "execution": {"build_loop_id": "bl-existing-run"}
        }))

        with self.assertRaisesRegex(ValueError, "does not match"):
            update_execution_state(
                self.state_path,
                "start",
                run_id="bl-different-run",
                queued_chunks=[],
                file_ownership={},
            )


if __name__ == "__main__":
    unittest.main()
