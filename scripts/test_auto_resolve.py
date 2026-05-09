#!/usr/bin/env python3
"""Tests for the Auto-Resolve routing logic (Sub-step F).

Verifies that the autonomy_gate verdicts are consumed correctly by the
orchestrator's report builder — specifically that warn verdicts route to the
Done section with a [warn] prefix rather than Held or Blocked.

Zero external deps. Run: python3 test_auto_resolve.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE_SCRIPT = HERE / "autonomy_gate.py"


def run_gate(workdir: Path, action: str, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--workdir",
            str(workdir),
            "--action",
            action,
            "--command",
            command,
            "--json",
        ],
        capture_output=True,
        text=True,
    )


def _build_done_entry(gate_envelope: dict) -> str:
    """Simulate the orchestrator's Done-entry builder (Sub-step F → Sub-step G).

    For auto verdicts: record the action label in Done with plain evidence.
    For warn verdicts: record in Done with '[warn] <reason>' prefix.
    confirm and block never reach Done — they go to Held/Blocked respectively.

    Returns the Done entry string as the orchestrator would write it.
    """
    action = gate_envelope["action"]
    label = gate_envelope.get("label") or gate_envelope.get("command", "")
    reason = gate_envelope.get("reason", "")

    if action == "auto":
        return f"{label} — {reason}"
    elif action == "warn":
        return f"[warn] {reason} — {label}"
    else:
        raise ValueError(f"Only auto/warn route to Done; got action={action!r}")


class AutoResolveRoutingTests(unittest.TestCase):
    """Verify gate verdict → report-section routing for all 4 verdicts."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_config(self, autonomy: dict) -> None:
        config_path = self.workdir / ".build-loop" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"autonomy": autonomy}))

    def test_auto_routes_to_done(self) -> None:
        """auto verdict → exit 0, entry has no [warn] prefix."""
        result = run_gate(self.workdir, "lint fix", "npx eslint --fix src/")
        self.assertEqual(result.returncode, 0)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["action"], "auto")
        entry = _build_done_entry(envelope)
        self.assertNotIn("[warn]", entry)
        self.assertIn("lint fix", entry)

    def test_confirm_does_not_route_to_done(self) -> None:
        """confirm verdict → exit 1, must NOT appear in Done."""
        result = run_gate(self.workdir, "publish", "npm publish")
        self.assertEqual(result.returncode, 1)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["action"], "confirm")
        # Confirm items go to Held — _build_done_entry raises for them
        with self.assertRaises(ValueError):
            _build_done_entry(envelope)

    def test_block_does_not_route_to_done(self) -> None:
        """block verdict → exit 2, must NOT appear in Done."""
        self._write_config({"blockFor": ["rm -rf *"]})
        result = run_gate(self.workdir, "delete all", "rm -rf /home")
        self.assertEqual(result.returncode, 2)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["action"], "block")
        with self.assertRaises(ValueError):
            _build_done_entry(envelope)

    def test_warn_routes_to_done_with_tag(self) -> None:
        """warn verdict → exit 0, Done entry has [warn] prefix with reason."""
        self._write_config({"warnFor": ["touch-prod-config*"]})
        result = run_gate(self.workdir, "ops", "touch-prod-config /etc/x")
        # warn exits 0 — does not block
        self.assertEqual(
            result.returncode,
            0,
            msg=f"Expected exit 0 for warn, got {result.returncode}. stderr: {result.stderr}",
        )
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["action"], "warn", msg=str(envelope))
        self.assertEqual(envelope["list_source"], "config", msg=str(envelope))
        self.assertEqual(envelope["matched_rule"], "touch-prod-config*", msg=str(envelope))

        # Simulate orchestrator recording in Done
        done_entry = _build_done_entry(envelope)
        self.assertTrue(
            done_entry.startswith("[warn]"),
            msg=f"Done entry must start with [warn], got: {done_entry!r}",
        )
        self.assertIn("ops", done_entry, msg=f"Label must appear in Done entry: {done_entry!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
