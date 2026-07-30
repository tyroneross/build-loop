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
SCRIPT = HERE / "review_finding_gate.py"
sys.path.insert(0, str(HERE))
from review_finding_gate import evaluate_payloads, normalize_severity  # noqa: E402


class ReviewFindingGateTests(unittest.TestCase):
    def test_legacy_major_maps_to_high_and_blocks(self) -> None:
        result = evaluate_payloads([{"findings": [{"id": "f1", "severity": "major"}]}])
        self.assertFalse(result["pass"])
        self.assertEqual(result["blocking_findings"][0]["normalized_severity"], "high")

    def test_minor_and_info_do_not_block(self) -> None:
        result = evaluate_payloads([{"findings": [
            {"id": "f1", "severity": "minor"},
            {"id": "f2", "severity": "info"},
        ]}])
        self.assertTrue(result["pass"])

    def test_high_closed_without_proof_still_blocks(self) -> None:
        result = evaluate_payloads([{"findings": [{"id": "sec1", "severity": "HIGH", "status": "resolved"}]}])
        self.assertFalse(result["pass"])

    def test_high_closed_with_proof_passes(self) -> None:
        result = evaluate_payloads([{"findings": [{
            "id": "sec1",
            "severity": "HIGH",
            "status": "resolved",
            "closure_proof": "pytest scripts/test_security.py",
        }]}])
        self.assertTrue(result["pass"])

    def test_unknown_severity_fails_conservative(self) -> None:
        self.assertEqual(normalize_severity("surprising"), "high")

    def test_cli_exit_code_blocks_open_high(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"findings": [{"id": "f1", "severity": "critical"}]}, f)
            tmp = f.name
        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--findings-json", tmp, "--json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            Path(tmp).unlink()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["blocking_count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
