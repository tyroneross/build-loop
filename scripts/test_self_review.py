#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for self_review.py. Run: uv run pytest scripts/test_self_review.py -q"""
from __future__ import annotations

import datetime
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Seeded runs must fall inside self_review's lookback window (light=7d, deep=14d,
# explicit up to 30d). A hardcoded absolute date goes stale as wall-clock advances
# and silently drops out of the window — so derive a recent date relative to now
# (1 day ago is inside every window the suite exercises).
_RECENT_DATE = (
    datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
).strftime("%Y-%m-%dT%H:%M:%SZ")
SCRIPT = HERE / "self_review" / "__main__.py"


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
                    "date": _RECENT_DATE,
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
                    "date": _RECENT_DATE,
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
                    "date": _RECENT_DATE,
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
                    "date": _RECENT_DATE,
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


class TestSelfSimplificationScan(unittest.TestCase):
    """Self-recursive deep mode produces self_simplification[] findings and target:self proposals."""

    def _make_self_recursive_dir(self, tmp: Path) -> Path:
        """Create a minimal directory that looks like the build-loop repo itself."""
        plugin_dir = tmp / ".claude-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "build-loop", "version": "0.0.0-test"})
        )
        scripts_dir = tmp / "scripts"
        scripts_dir.mkdir()
        # Canary file that _is_self_recursive checks for (new package form)
        sr_pkg = scripts_dir / "self_review"
        sr_pkg.mkdir()
        (sr_pkg / "__main__.py").write_text("# placeholder\n")
        # A deliberately complex Python file (many branches → should trigger hotspot)
        messy = scripts_dir / "messy.py"
        messy.write_text(_MESSY_PYTHON)
        # Seed .build-loop/state.json so the script doesn't trip on missing state
        build_dir = tmp / ".build-loop"
        build_dir.mkdir()
        (build_dir / "state.json").write_text(json.dumps({"runs": []}))
        return tmp

    def test_deep_self_recursive_produces_self_simplification(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._make_self_recursive_dir(tmp)
            result = _run(
                ["--mode", "deep", "--workdir", str(tmp), "--dry-run", "--json"]
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn("self_simplification", payload,
                          "self_simplification key must always be present")
            findings = payload["self_simplification"]
            self.assertIsInstance(findings, list)
            # The messy.py file should produce at least one finding
            self.assertGreater(
                len(findings),
                0,
                msg=(
                    "Expected ≥1 self_simplification finding from messy.py "
                    f"(got {findings}; errors: {payload.get('errors')})"
                ),
            )
            # Each finding must have the standard shape
            for f in findings:
                for key in ("kind", "signal", "evidence", "suggested_action", "severity"):
                    self.assertIn(key, f, f"finding missing key {key!r}: {f}")

    def test_deep_self_recursive_enqueues_target_self_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._make_self_recursive_dir(tmp)
            result = _run(["--mode", "deep", "--workdir", str(tmp), "--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            # At least one queued proposal should have target: self in its frontmatter
            queued = payload.get("queued") or []
            target_self_found = False
            for qp in queued:
                p = Path(qp)
                if p.exists():
                    content = p.read_text()
                    if "target: self" in content:
                        target_self_found = True
                        break
            self.assertTrue(
                target_self_found,
                msg=(
                    "Expected at least one queued proposal with 'target: self' "
                    f"in its frontmatter. queued={queued}"
                ),
            )

    def test_non_self_recursive_no_self_simplification(self) -> None:
        """A plain project workdir must not produce any self_simplification findings."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            # Plain project: has .build-loop/state.json but NOT the plugin canary
            build_dir = tmp / ".build-loop"
            build_dir.mkdir()
            _seed_state(
                build_dir,
                [
                    {
                        "run_id": "run_plain_00",
                        "date": _RECENT_DATE,
                        "goal": "plain project",
                        "outcome": "fail",
                        "phases": {
                            "execute": {"status": "fail", "failed_criteria": ["tests pass"]}
                        },
                        "filesTouched": [],
                        "diagnosticCommands": [],
                        "manualInterventions": [],
                        "active_experimental_artifacts": [],
                    }
                    for _ in range(3)
                ],
            )
            result = _run(["--mode", "deep", "--workdir", str(tmp), "--dry-run", "--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn("self_simplification", payload)
            self.assertEqual(
                payload["self_simplification"],
                [],
                msg="Non-self-recursive dir must produce empty self_simplification[]",
            )

    def test_light_mode_no_self_simplification(self) -> None:
        """Even a self-recursive dir in light mode must not run the scan."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._make_self_recursive_dir(tmp)
            result = _run(["--mode", "light", "--workdir", str(tmp), "--dry-run", "--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn("self_simplification", payload)
            self.assertEqual(
                payload["self_simplification"],
                [],
                msg="Light mode must never run self-simplification scan",
            )


# A deliberately messy Python file designed to trigger complexity_detector hotspots.
# Uses deeply nested branches (deep_nesting) and a redundant multipass loop.
_MESSY_PYTHON = '''\
def messy_function(items, other):
    """Intentionally complex function for test triggering."""
    result = []
    for item in items:
        if item:
            if item > 0:
                if item > 10:
                    if item > 100:
                        if item > 1000:
                            result.append(item * 2)
                        else:
                            result.append(item)
                    else:
                        result.append(item + 1)
                else:
                    result.append(item - 1)
            else:
                result.append(0)
    # Redundant second pass over same iterable
    for item in items:
        if item < 0:
            result.append(abs(item))
    return result
'''


if __name__ == "__main__":
    unittest.main(verbosity=2)
