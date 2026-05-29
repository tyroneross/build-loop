#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "metric_runner.py"
sys.path.insert(0, str(HERE))
from metric_runner import run_simplicity_metrics  # noqa: E402


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


class SimplicityMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, "init")
        git(self.repo, "config", "user.email", "codex@example.test")
        git(self.repo, "config", "user.name", "Codex Test")
        (self.repo / "app.py").write_text("def existing():\n    return 1\n", encoding="utf-8")
        (self.repo / "package.json").write_text('{"dependencies": {}}\n', encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD").strip()

        (self.repo / "app.py").write_text(
            "def existing():\n"
            "    return 1\n\n"
            "class AddedThing:\n"
            "    pass\n\n"
            "def added_function():\n"
            "    return existing()\n",
            encoding="utf-8",
        )
        (self.repo / "package.json").write_text('{"dependencies": {"left-pad": "1.3.0"}}\n', encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "head")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_simplicity_metrics_report_loc_dependency_and_abstractions(self) -> None:
        result = run_simplicity_metrics(self.base, cwd=str(self.repo))
        self.assertGreater(result["net_loc"], 0)
        self.assertIn("package.json", result["dependency_delta"]["manifest_files_changed"])
        names = {entry["name"] for entry in result["new_abstractions"]}
        self.assertIn("AddedThing", names)
        self.assertIn("added_function", names)
        self.assertIsNotNone(result["complexity_delta"])

    def test_cli_emits_simplicity_json(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--simplicity-diff", self.base, "--cwd", str(self.repo)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("net_loc", payload)
        self.assertIn("dependency_delta", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
