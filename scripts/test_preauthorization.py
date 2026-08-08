#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for preauthorization.py. Zero deps. Run: python3 test_preauthorization.py"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "preauthorization.py"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import preauthorization  # noqa: E402


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def envelope(result: subprocess.CompletedProcess) -> dict:
    return json.loads(result.stdout)


DEPLOY_CONTRAST_GATE = json.dumps(
    {
        "id": "deploy-contrast",
        "action": "deploy",
        "metric": "contrast_ratio",
        "op": ">=",
        "threshold": 4.5,
        "measurement_source": "computed from rendered fg/bg",
        "on_fail": "skip_and_record",
    }
)


def _record_deploy_contrast(workdir: Path) -> subprocess.CompletedProcess:
    return run_cli(
        "record",
        "--workdir", str(workdir),
        "--run-id", "run_deploy_contrast",
        "--unattended",
        "--repo-scope", str(workdir),
        "--irreversible-policy", "skip_and_record",
        "--stop-rule-failures", "5",
        "--stop-rule-hours", "8",
        "--gate", DEPLOY_CONTRAST_GATE,
        "--json",
    )


class RecordTests(unittest.TestCase):
    """record writes .build-loop/preauthorization.json atomically."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_record_writes_file(self) -> None:
        result = _record_deploy_contrast(self.workdir)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        path = self.workdir / ".build-loop" / "preauthorization.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data["run_id"], "run_deploy_contrast")
        self.assertTrue(data["unattended"])
        self.assertEqual(data["irreversible_policy"], "skip_and_record")
        self.assertEqual(len(data["conditional_gates"]), 1)
        self.assertEqual(data["conditional_gates"][0]["id"], "deploy-contrast")

    def test_gate_without_measurement_source_is_rejected_at_record(self) -> None:
        """Case 5: an authorization whose evidence source is unnamed is REJECTED
        at record time (exit 2) and writes NO file."""
        bad_gate = json.dumps(
            {
                "id": "deploy-contrast",
                "action": "deploy",
                "metric": "contrast_ratio",
                "op": ">=",
                "threshold": 4.5,
                "measurement_source": "",
                "on_fail": "skip_and_record",
            }
        )
        result = run_cli(
            "record",
            "--workdir", str(self.workdir),
            "--run-id", "run_bad_gate",
            "--unattended",
            "--repo-scope", str(self.workdir),
            "--irreversible-policy", "skip_and_record",
            "--stop-rule-failures", "5",
            "--stop-rule-hours", "8",
            "--gate", bad_gate,
            "--json",
        )
        self.assertEqual(result.returncode, 2, msg=f"stdout: {result.stdout}\nstderr: {result.stderr}")
        path = self.workdir / ".build-loop" / "preauthorization.json"
        self.assertFalse(path.exists(), msg="a rejected gate must not produce a file")

    def test_gate_missing_measurement_source_key_is_rejected_at_record(self) -> None:
        """Same rejection when the key is absent entirely, not just empty."""
        bad_gate = json.dumps(
            {
                "id": "deploy-contrast",
                "action": "deploy",
                "metric": "contrast_ratio",
                "op": ">=",
                "threshold": 4.5,
                "on_fail": "skip_and_record",
            }
        )
        result = run_cli(
            "record",
            "--workdir", str(self.workdir),
            "--run-id", "run_missing_key",
            "--unattended",
            "--repo-scope", str(self.workdir),
            "--irreversible-policy", "skip_and_record",
            "--stop-rule-failures", "5",
            "--stop-rule-hours", "8",
            "--gate", bad_gate,
            "--json",
        )
        self.assertEqual(result.returncode, 2, msg=f"stdout: {result.stdout}\nstderr: {result.stderr}")
        path = self.workdir / ".build-loop" / "preauthorization.json"
        self.assertFalse(path.exists())


class EvaluateGateTests(unittest.TestCase):
    """Case 1-3: evaluate is the load-bearing surface — no epsilon slack."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        setup = _record_deploy_contrast(self.workdir)
        self.assertEqual(setup.returncode, 0, msg=setup.stderr)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_authorized_deploy_refuses_when_measurement_falls_short(self) -> None:
        """THE CONVICTION: pre-authorized deploy still refuses at 4.2045 < 4.5.

        The action was pre-authorized and the gate still refused, because the
        authorization was bound to evidence, not to approval.
        """
        result = run_cli(
            "evaluate",
            "--workdir", str(self.workdir),
            "--gate", "deploy-contrast",
            "--measured", "4.2045",
            "--json",
        )
        data = envelope(result)
        self.assertFalse(data["authorized"], msg=str(data))
        self.assertEqual(data["verdict"], "skip_and_record", msg=str(data))
        self.assertEqual(result.returncode, 1, msg=f"stdout: {result.stdout}")
        self.assertIn("4.2045", data["reason"], msg=str(data))
        self.assertIn("4.5", data["reason"], msg=str(data))

    def test_measurement_at_threshold_authorizes(self) -> None:
        """Boundary exactness: 4.5 >= 4.5 authorizes, no epsilon slack."""
        result = run_cli(
            "evaluate",
            "--workdir", str(self.workdir),
            "--gate", "deploy-contrast",
            "--measured", "4.5",
            "--json",
        )
        data = envelope(result)
        self.assertTrue(data["authorized"], msg=str(data))
        self.assertEqual(data["verdict"], "auto", msg=str(data))
        self.assertEqual(result.returncode, 0, msg=f"stdout: {result.stdout}")

    def test_measurement_above_threshold_authorizes(self) -> None:
        result = run_cli(
            "evaluate",
            "--workdir", str(self.workdir),
            "--gate", "deploy-contrast",
            "--measured", "7.1",
            "--json",
        )
        data = envelope(result)
        self.assertTrue(data["authorized"], msg=str(data))
        self.assertEqual(data["verdict"], "auto", msg=str(data))
        self.assertEqual(result.returncode, 0, msg=f"stdout: {result.stdout}")

    def test_unknown_gate_id_never_authorizes(self) -> None:
        result = run_cli(
            "evaluate",
            "--workdir", str(self.workdir),
            "--gate", "no-such-gate",
            "--measured", "99",
            "--json",
        )
        data = envelope(result)
        self.assertFalse(data["authorized"], msg=str(data))
        self.assertEqual(result.returncode, 1)


class CheckActionTests(unittest.TestCase):
    """Case 4 + 6: a gate existing is not itself the authorization."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_conditional_gate_without_measurement_is_never_auto(self) -> None:
        setup = _record_deploy_contrast(self.workdir)
        self.assertEqual(setup.returncode, 0, msg=setup.stderr)

        result = run_cli("check", "--workdir", str(self.workdir), "--action", "deploy", "--json")
        data = envelope(result)
        self.assertFalse(data["covered"], msg=str(data))
        self.assertEqual(data["verdict"], "confirm", msg=str(data))
        self.assertNotEqual(data["verdict"], "auto")

    def test_absent_preauthorization_authorizes_nothing(self) -> None:
        """load() returns None with no file; check_action never yields covered=True."""
        self.assertIsNone(preauthorization.load(self.workdir))

        direct = preauthorization.check_action(None, "deploy")
        self.assertFalse(direct["covered"])
        self.assertNotEqual(direct["verdict"], "auto")

        result = run_cli("check", "--workdir", str(self.workdir), "--action", "deploy", "--json")
        data = envelope(result)
        self.assertFalse(data["covered"], msg=str(data))
        self.assertNotEqual(data["verdict"], "auto", msg=str(data))


class ScopeCheckTests(unittest.TestCase):
    """Case 7: out-of-scope paths (including traversal escapes) refuse."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo_a = self.root / "repoA"
        self.repo_b = self.root / "repoB"
        self.repo_a.mkdir()
        self.repo_b.mkdir()

        result = run_cli(
            "record",
            "--workdir", str(self.repo_a),
            "--run-id", "run_scope",
            "--unattended",
            "--repo-scope", str(self.repo_a),
            "--irreversible-policy", "skip_and_record",
            "--stop-rule-failures", "5",
            "--stop-rule-hours", "8",
            "--json",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_out_of_scope_path_refused(self) -> None:
        result = run_cli(
            "scope-check",
            "--workdir", str(self.repo_a),
            "--path", str(self.repo_b / "secret.txt"),
        )
        self.assertEqual(result.returncode, 1, msg=result.stdout)

    def test_traversal_escape_refused(self) -> None:
        traversal_path = str(self.repo_a / ".." / "repoB" / "secret.txt")
        result = run_cli(
            "scope-check",
            "--workdir", str(self.repo_a),
            "--path", traversal_path,
        )
        self.assertEqual(result.returncode, 1, msg=result.stdout)

    def test_in_scope_path_allowed(self) -> None:
        result = run_cli(
            "scope-check",
            "--workdir", str(self.repo_a),
            "--path", str(self.repo_a / "file.txt"),
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout)


class EvaluateGateGuardedTests(unittest.TestCase):
    """F3: evaluate_gate_guarded is the door the block guard actually lives on."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_blocked_action_is_never_authorized_even_with_a_satisfied_measurement(self) -> None:
        """THE CONVICTION: a satisfied measurement must not launder a blocked
        action into an auto. `rm -rf /` is a DEFAULT_BLOCK_FOR pattern in
        autonomy_gate.py, so classify() returns 'block' for it regardless of
        any recorded conditional_gate or measurement."""
        gate = json.dumps(
            {
                "id": "delete-guard",
                "action": "rm -rf /",
                "metric": "confidence",
                "op": ">=",
                "threshold": 0.5,
                "measurement_source": "n/a",
                "on_fail": "skip_and_record",
            }
        )
        setup = run_cli(
            "record",
            "--workdir", str(self.workdir),
            "--run-id", "run_evaluate_block_guard",
            "--unattended",
            "--repo-scope", str(self.workdir),
            "--irreversible-policy", "skip_and_record",
            "--stop-rule-failures", "5",
            "--stop-rule-hours", "8",
            "--gate", gate,
            "--json",
        )
        self.assertEqual(setup.returncode, 0, msg=setup.stderr)

        # Measurement clears the threshold with room to spare (0.99 >= 0.5) —
        # if the block guard were missing, this would authorize.
        result = run_cli(
            "evaluate",
            "--workdir", str(self.workdir),
            "--gate", "delete-guard",
            "--measured", "0.99",
            "--json",
        )
        data = envelope(result)
        self.assertFalse(data["authorized"], msg=str(data))
        self.assertEqual(data["verdict"], "block", msg=str(data))
        self.assertNotEqual(result.returncode, 0, msg=f"stdout: {result.stdout}")

        # Also true at the importable-function layer, bypassing the CLI.
        config = preauthorization.load(self.workdir)
        direct = preauthorization.evaluate_gate_guarded(self.workdir, config, "delete-guard", 0.99)
        self.assertFalse(direct["authorized"], msg=str(direct))
        self.assertEqual(direct["verdict"], "block", msg=str(direct))

    def test_out_of_scope_path_refused_at_evaluate(self) -> None:
        repo_a = self.workdir / "repoA"
        repo_b = self.workdir / "repoB"
        repo_a.mkdir()
        repo_b.mkdir()

        gate = json.dumps(
            {
                "id": "deploy-contrast",
                "action": "deploy",
                "metric": "contrast_ratio",
                "op": ">=",
                "threshold": 4.5,
                "measurement_source": "computed from rendered fg/bg",
                "on_fail": "skip_and_record",
            }
        )
        setup = run_cli(
            "record",
            "--workdir", str(repo_a),
            "--run-id", "run_evaluate_scope",
            "--unattended",
            "--repo-scope", str(repo_a),
            "--irreversible-policy", "skip_and_record",
            "--stop-rule-failures", "5",
            "--stop-rule-hours", "8",
            "--gate", gate,
            "--json",
        )
        self.assertEqual(setup.returncode, 0, msg=setup.stderr)

        # A satisfied measurement (7.1 >= 4.5) with an out-of-scope --path
        # must still refuse.
        result = run_cli(
            "evaluate",
            "--workdir", str(repo_a),
            "--gate", "deploy-contrast",
            "--measured", "7.1",
            "--path", str(repo_b / "secret.txt"),
            "--json",
        )
        data = envelope(result)
        self.assertFalse(data["authorized"], msg=str(data))
        self.assertEqual(data["verdict"], "block", msg=str(data))
        self.assertNotEqual(result.returncode, 0, msg=f"stdout: {result.stdout}")

        # `..` traversal resolving outside repo_scope is refused the same way.
        traversal_path = str(repo_a / ".." / "repoB" / "secret.txt")
        result_traversal = run_cli(
            "evaluate",
            "--workdir", str(repo_a),
            "--gate", "deploy-contrast",
            "--measured", "7.1",
            "--path", traversal_path,
            "--json",
        )
        data_traversal = envelope(result_traversal)
        self.assertFalse(data_traversal["authorized"], msg=str(data_traversal))
        self.assertEqual(data_traversal["verdict"], "block", msg=str(data_traversal))
        self.assertNotEqual(result_traversal.returncode, 0, msg=f"stdout: {result_traversal.stdout}")

        # An in-scope path with the same satisfied measurement still authorizes.
        result_in_scope = run_cli(
            "evaluate",
            "--workdir", str(repo_a),
            "--gate", "deploy-contrast",
            "--measured", "7.1",
            "--path", str(repo_a / "file.txt"),
            "--json",
        )
        data_in_scope = envelope(result_in_scope)
        self.assertTrue(data_in_scope["authorized"], msg=str(data_in_scope))
        self.assertEqual(result_in_scope.returncode, 0, msg=f"stdout: {result_in_scope.stdout}")

    def test_docstring_claim_matches_code(self) -> None:
        """Reflection: `_cmd_evaluate` must call `evaluate_gate_guarded`, not
        the bare `evaluate_gate`, so the guard cannot be silently unwired by a
        future edit without this test catching it."""
        import inspect

        source = inspect.getsource(preauthorization._cmd_evaluate)
        self.assertIn(
            "evaluate_gate_guarded(",
            source,
            msg="_cmd_evaluate must call evaluate_gate_guarded(), the guarded surface",
        )
        self.assertNotIn(
            "= evaluate_gate(",
            source,
            msg="_cmd_evaluate must not call the bare, unguarded evaluate_gate() directly",
        )

        # Belt-and-suspenders: patch evaluate_gate_guarded and confirm the CLI
        # path actually invokes it (not just that the source text mentions it).
        from unittest import mock

        sentinel = {
            "gate": "x",
            "metric": None,
            "op": None,
            "threshold": None,
            "measured": 1.0,
            "authorized": True,
            "verdict": "auto",
            "reason": "sentinel",
        }
        with mock.patch.object(preauthorization, "evaluate_gate_guarded", return_value=sentinel) as spy:
            args = argparse.Namespace(
                workdir=str(self.workdir), gate="x", measured=1.0, path=None, emit_json=True
            )
            preauthorization._cmd_evaluate(args)
        spy.assert_called_once()


class BlockGuardTests(unittest.TestCase):
    """Case 8: block is never relaxed by any recorded policy."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_block_is_never_relaxed(self) -> None:
        # Record a standing authorization that even names the destructive
        # action as an explicit conditional_gate — this is the strongest
        # version of the test: a recorded policy tries to cover it, and the
        # block guard must still win.
        gate = json.dumps(
            {
                "id": "delete-guard",
                "action": "rm -rf /",
                "metric": "n/a",
                "op": ">=",
                "threshold": 0,
                "measurement_source": "n/a",
                "on_fail": "skip_and_record",
            }
        )
        setup = run_cli(
            "record",
            "--workdir", str(self.workdir),
            "--run-id", "run_block_guard",
            "--unattended",
            "--repo-scope", str(self.workdir),
            "--irreversible-policy", "skip_and_record",
            "--stop-rule-failures", "5",
            "--stop-rule-hours", "8",
            "--gate", gate,
            "--json",
        )
        self.assertEqual(setup.returncode, 0, msg=setup.stderr)

        result = run_cli("check", "--workdir", str(self.workdir), "--action", "rm -rf /", "--json")
        data = envelope(result)
        self.assertEqual(data["verdict"], "block", msg=str(data))
        self.assertFalse(data["covered"], msg=str(data))
        self.assertNotEqual(data["verdict"], "auto")

        # Also true at the importable-function layer, bypassing the CLI.
        config = preauthorization.load(self.workdir)
        direct = preauthorization.check_action_guarded(self.workdir, config, "rm -rf /")
        self.assertEqual(direct["verdict"], "block")
        self.assertNotEqual(direct["verdict"], "auto")


if __name__ == "__main__":
    unittest.main(verbosity=2)
