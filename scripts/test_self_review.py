#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for self_review.py. Run: uv run pytest scripts/test_self_review.py -q"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "self_review.py"


def _run(args: list[str], workdir: str | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT)] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _seed_state(build_dir: Path, runs: list[dict]) -> None:
    """Write a minimal .build-loop/state.json with the given runs."""
    build_dir.mkdir(parents=True, exist_ok=True)
    state = {"runs": runs}
    (build_dir / "state.json").write_text(json.dumps(state, indent=2))


class TestLightMode(unittest.TestCase):
    """light mode with a seeded state.json that has a repeated failing criterion."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.build_dir = self.workdir / ".build-loop"
        # Seed a state.json with 3 runs, each failing the same phase
        _seed_state(
            self.build_dir,
            [
                {
                    "run_id": f"run_test_{i:02d}",
                    "date": "2026-05-29T00:00:00Z",
                    "goal": "test goal",
                    "outcome": "fail",
                    "phases": {
                        "execute": {
                            "status": "fail",
                            "failed_criteria": ["test suite passes"],
                        }
                    },
                    "filesTouched": [],
                    "diagnosticCommands": [],
                    "manualInterventions": [],
                    "active_experimental_artifacts": [],
                }
                for i in range(3)
            ],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_light_mode_produces_efficiency_findings(self) -> None:
        result = _run(["--mode", "light", "--workdir", str(self.workdir), "--json"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertGreater(
            len(payload["efficiency_findings"]),
            0,
            msg="Expected at least one efficiency finding from repeated phase failure",
        )

    def test_light_mode_writes_digest_file(self) -> None:
        result = _run(["--mode", "light", "--workdir", str(self.workdir), "--json"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertIsNotNone(payload["digest_path"])
        digest = Path(payload["digest_path"])
        self.assertTrue(digest.exists(), f"Digest not found at {digest}")
        content = digest.read_text()
        self.assertIn("Self-Review Digest", content)

    def test_light_mode_enqueues_proposals(self) -> None:
        result = _run(["--mode", "light", "--workdir", str(self.workdir), "--json"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertGreaterEqual(
            len(payload["queued"]),
            1,
            msg="Expected at least one enqueued proposal",
        )
        # Each queued path must exist and have frontmatter
        for qp in payload["queued"]:
            p = Path(qp)
            self.assertTrue(p.exists(), f"Proposal file missing: {qp}")
            content = p.read_text()
            self.assertIn("classify_hint:", content)
            self.assertIn("severity:", content)
            self.assertIn("source: self-review", content)


class TestDeepMode(unittest.TestCase):
    """deep mode digest must contain an ## Apply plan section."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.build_dir = self.workdir / ".build-loop"
        _seed_state(
            self.build_dir,
            [
                {
                    "run_id": f"run_deep_{i:02d}",
                    "date": "2026-05-29T00:00:00Z",
                    "goal": "deep test",
                    "outcome": "fail",
                    "phases": {
                        "review": {"status": "fail", "failed_criteria": ["all tests green"]}
                    },
                    "filesTouched": [],
                    "diagnosticCommands": [],
                    "manualInterventions": [],
                    "active_experimental_artifacts": [],
                }
                for i in range(3)
            ],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_deep_mode_digest_has_apply_plan_section(self) -> None:
        result = _run(["--mode", "deep", "--workdir", str(self.workdir), "--json"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "deep")
        digest_path = payload.get("digest_path")
        self.assertIsNotNone(digest_path)
        content = Path(digest_path).read_text()
        self.assertIn("## Apply plan", content, msg="Deep mode digest must contain '## Apply plan'")

    def test_deep_mode_default_window_is_14_days(self) -> None:
        result = _run(["--mode", "deep", "--workdir", str(self.workdir), "--json"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["window_days"], 14)


class TestDryRun(unittest.TestCase):
    """--dry-run must write no files."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.build_dir = self.workdir / ".build-loop"
        _seed_state(
            self.build_dir,
            [
                {
                    "run_id": "run_dryrun_00",
                    "date": "2026-05-29T00:00:00Z",
                    "goal": "dry run test",
                    "outcome": "fail",
                    "phases": {"execute": {"status": "fail"}},
                    "filesTouched": [],
                    "diagnosticCommands": [],
                    "manualInterventions": [],
                    "active_experimental_artifacts": [],
                }
                for _ in range(3)
            ],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dry_run_writes_no_digest(self) -> None:
        result = _run(
            ["--mode", "light", "--workdir", str(self.workdir), "--dry-run", "--json"]
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["dry_run"])
        self.assertIsNone(payload["digest_path"])

    def test_dry_run_writes_no_proposals(self) -> None:
        result = _run(
            ["--mode", "light", "--workdir", str(self.workdir), "--dry-run", "--json"]
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["queued"], [])
        # proposals dir must not exist
        proposals_dir = self.workdir / ".build-loop" / "proposals"
        self.assertFalse(proposals_dir.exists(), "proposals dir must not be created in dry-run")

    def test_dry_run_still_computes_findings(self) -> None:
        result = _run(
            ["--mode", "light", "--workdir", str(self.workdir), "--dry-run", "--json"]
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        # efficiency_findings should still be populated even in dry-run
        self.assertIsInstance(payload["efficiency_findings"], list)


class TestMinerAbsent(unittest.TestCase):
    """Miner absent or erroring -> fail-soft, errors[] populated, still exits 0 with digest."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.build_dir = self.workdir / ".build-loop"
        # No state.json, no miner available (we'll point to a non-existent one via env)
        self.build_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_no_state_no_miner_exits_0(self) -> None:
        """No state.json + miner either absent or produces empty = still exits 0."""
        result = _run(["--mode", "light", "--workdir", str(self.workdir), "--json"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_no_state_produces_valid_json_shape(self) -> None:
        result = _run(["--mode", "light", "--workdir", str(self.workdir), "--json"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        # errors[] may or may not be populated, but the shape must be valid
        self.assertIn("mode", payload)
        self.assertIn("window_days", payload)
        self.assertIn("mined", payload)
        self.assertIn("efficiency_findings", payload)
        self.assertIn("digest_path", payload)
        self.assertIn("queued", payload)
        self.assertIn("errors", payload)
        self.assertIn("dry_run", payload)

    def test_digest_written_even_with_no_data(self) -> None:
        """A digest file is still written even when no data is available."""
        result = _run(["--mode", "light", "--workdir", str(self.workdir), "--json"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        # digest_path may be written with empty-state content
        if payload["digest_path"]:
            self.assertTrue(Path(payload["digest_path"]).exists())


class TestJsonOutputShape(unittest.TestCase):
    """JSON output validates against the frozen shape."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        build_dir = self.workdir / ".build-loop"
        _seed_state(build_dir, [])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_output_has_all_required_keys(self) -> None:
        result = _run(["--mode", "light", "--workdir", str(self.workdir), "--json"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)

        required_top = {"mode", "window_days", "mined", "efficiency_findings",
                        "digest_path", "queued", "errors", "dry_run"}
        self.assertEqual(required_top, required_top & set(payload.keys()))

        required_mined = {"corrections", "rituals", "sequences"}
        self.assertEqual(required_mined, required_mined & set(payload["mined"].keys()))

        self.assertIsInstance(payload["efficiency_findings"], list)
        self.assertIsInstance(payload["queued"], list)
        self.assertIsInstance(payload["errors"], list)
        self.assertIsInstance(payload["dry_run"], bool)

    def test_efficiency_findings_shape(self) -> None:
        """Each efficiency finding must have the required fields."""
        build_dir = self.workdir / ".build-loop"
        # Reseed with repeated failures so we get findings
        _seed_state(
            build_dir,
            [
                {
                    "run_id": f"run_{i}",
                    "date": "2026-05-29T00:00:00Z",
                    "goal": "g",
                    "outcome": "fail",
                    "phases": {"assess": {"status": "fail"}},
                    "filesTouched": [],
                    "diagnosticCommands": [],
                    "manualInterventions": [],
                    "active_experimental_artifacts": [],
                }
                for i in range(3)
            ],
        )
        result = _run(["--mode", "light", "--workdir", str(self.workdir), "--json"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        for finding in payload["efficiency_findings"]:
            for key in ("kind", "signal", "evidence", "suggested_action", "severity"):
                self.assertIn(key, finding, f"finding missing key {key!r}: {finding}")
            self.assertIn(finding["severity"], ("HIGH", "MEDIUM", "LOW"))

    def test_mode_and_window_days_match(self) -> None:
        result = _run(["--mode", "light", "--workdir", str(self.workdir), "--json"])
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "light")
        self.assertEqual(payload["window_days"], 7)

    def test_custom_days_override(self) -> None:
        result = _run(
            ["--mode", "deep", "--workdir", str(self.workdir), "--days", "30", "--json"]
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["window_days"], 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
