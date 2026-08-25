#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "build-loop-debugger.js"


class NativeDebuggerTests(unittest.TestCase):
    def run_cli(self, *args: str) -> dict:
        result = subprocess.run(
            ["node", str(CLI), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_store_and_search_use_the_same_project_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp)
            payload_path = workdir / "incident.json"
            payload_path.write_text(
                json.dumps(
                    {
                        "symptom": "TypeError: release vendor was null in brief generation",
                        "root_cause": {
                            "description": "A nullable database vendor reached a lowercase call without normalization.",
                            "category": "logic",
                            "confidence": 0.99,
                        },
                        "fix": "Normalize nullable vendor values before release deduplication.",
                        "verification": "verified",
                        "tags": ["build-loop", "typescript", "null"],
                        "files_changed": ["lib/brief-data.ts"],
                    }
                ),
                encoding="utf-8",
            )

            stored = self.run_cli("store", "--input", str(payload_path), "--workdir", str(workdir))
            memory_root = (workdir / ".claude" / "memory").resolve()
            self.assertEqual(Path(stored["memory_root"]), memory_root)
            self.assertTrue(Path(stored["file_path"]).is_file())

            found = self.run_cli(
                "search",
                "release vendor null brief",
                "--threshold",
                "0.1",
                "--workdir",
                str(workdir),
            )
            self.assertEqual(Path(found["memory_root"]), memory_root)
            ids = [item["id"] for item in found["verdict"]["incidents"]]
            self.assertIn(stored["incident_id"], ids)
            self.assertEqual(found["debugger_core_version"], "1.9.0")


if __name__ == "__main__":
    unittest.main()
