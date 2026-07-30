# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for intent_freshness.py — advisory per-run intent.md staleness check."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "intent_freshness.py"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import intent_freshness as ifr  # noqa: E402


def _project(tmp: str) -> Path:
    root = Path(tmp)
    (root / ".build-loop").mkdir(parents=True)
    return root


def _write_state(root: Path, *, execution_run_id: str | None = None, runs_run_id: str | None = None) -> None:
    state: dict = {}
    if execution_run_id is not None:
        state["execution"] = {"run_id": execution_run_id}
    if runs_run_id is not None:
        state["runs"] = [{"run_id": runs_run_id}]
    (root / ".build-loop" / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _write_intent(root: Path, body: str) -> None:
    (root / ".build-loop" / "intent.md").write_text(body, encoding="utf-8")


class TestVerdicts(unittest.TestCase):
    def test_fresh_when_stamp_matches_execution_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _write_state(root, execution_run_id="run_X")
            _write_intent(root, f"# Intent\n{ifr.stamp_marker('run_X')}\n## Restated intent\nDo the thing.\n")
            env = ifr.evaluate(root)
            self.assertEqual(env["verdict"], "fresh")
            self.assertFalse(env["stale"])

    def test_stale_when_stamp_is_prior_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _write_state(root, execution_run_id="run_NEW")
            _write_intent(root, f"# Intent\n{ifr.stamp_marker('run_OLD')}\nold prose\n")
            env = ifr.evaluate(root)
            self.assertEqual(env["verdict"], "stale")
            self.assertTrue(env["stale"])
            self.assertIn("run_OLD", env["advice"])
            self.assertIn("run_NEW", env["advice"])

    def test_unstamped_intent_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _write_state(root, execution_run_id="run_X")
            _write_intent(root, "# Intent\n## Restated intent\nNo stamp here.\n")
            env = ifr.evaluate(root)
            self.assertEqual(env["verdict"], "unstamped")
            self.assertTrue(env["stale"])

    def test_no_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _write_state(root, execution_run_id="run_X")
            env = ifr.evaluate(root)
            self.assertEqual(env["verdict"], "no_intent")
            self.assertFalse(env["stale"])  # absence isn't "stale" — it's "must write"

    def test_no_run_id_resolvable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / ".build-loop" / "state.json").write_text("{}", encoding="utf-8")
            _write_intent(root, f"{ifr.stamp_marker('run_X')}\nprose")
            env = ifr.evaluate(root)
            self.assertEqual(env["verdict"], "no_run")
            self.assertFalse(env["stale"])

    def test_falls_back_to_runs_when_no_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _write_state(root, runs_run_id="run_FROM_RUNS")
            _write_intent(root, f"{ifr.stamp_marker('run_FROM_RUNS')}\nprose")
            env = ifr.evaluate(root)
            self.assertEqual(env["verdict"], "fresh")

    def test_execution_takes_precedence_over_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _write_state(root, execution_run_id="run_EXEC", runs_run_id="run_RUNS")
            _write_intent(root, f"{ifr.stamp_marker('run_EXEC')}\nprose")
            env = ifr.evaluate(root)
            self.assertEqual(env["verdict"], "fresh")

    def test_override_current_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _write_state(root, execution_run_id="run_STATE")
            _write_intent(root, f"{ifr.stamp_marker('run_OVERRIDE')}\nprose")
            env = ifr.evaluate(root, override="run_OVERRIDE")
            self.assertEqual(env["verdict"], "fresh")


class TestStampHelper(unittest.TestCase):
    def test_marker_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            p = root / ".build-loop" / "intent.md"
            p.write_text(f"prose\n{ifr.stamp_marker('run_abc123')}\nmore\n", encoding="utf-8")
            self.assertEqual(ifr.read_stamped_run_id(p), "run_abc123")


class TestCli(unittest.TestCase):
    def test_cli_exit_zero_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _write_state(root, execution_run_id="run_X")
            _write_intent(root, f"{ifr.stamp_marker('run_X')}\nok")
            cp = subprocess.run(
                [sys.executable, str(SCRIPT), "--workdir", str(root), "--json"],
                capture_output=True, text=True,
            )
            self.assertEqual(cp.returncode, 0)
            data = json.loads(cp.stdout)
            self.assertEqual(data["verdict"], "fresh")

    def test_cli_stale_still_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _write_state(root, execution_run_id="run_NEW")
            _write_intent(root, f"{ifr.stamp_marker('run_OLD')}\nold")
            cp = subprocess.run(
                [sys.executable, str(SCRIPT), "--workdir", str(root)],
                capture_output=True, text=True,
            )
            self.assertEqual(cp.returncode, 0)  # advisory
            self.assertIn("stale", cp.stdout)


if __name__ == "__main__":
    unittest.main()
