#!/usr/bin/env python3
"""Contract tests for repo scanning and max-accuracy research packets."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from research_packet import (  # noqa: E402
    build_research_packet,
    scan_repo,
    validate_research_packet,
)


class ResearchPacketTests(unittest.TestCase):
    def test_max_accuracy_packet_has_deep_research_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text(
                "Run `python3 scripts/validate_store.py --strict` after changes.\n",
                encoding="utf-8",
            )
            packet = build_research_packet("evaluate memory retrieval", root, mode="max_accuracy")
            result = validate_research_packet(packet, mode="max_accuracy")
            self.assertFalse(result["valid"], result)
            self.assertIn("Evidence register", result["incomplete"])
            self.assertIn("## Evidence register", packet)
            self.assertIn("## Contradiction log", packet)
            completed = packet.replace("pending", "verified").replace(
                "Populate before calling this deep research", "Recorded primary-source search"
            ).replace("provisional until", "high confidence after")
            self.assertTrue(validate_research_packet(completed, mode="max_accuracy")["valid"])

    def test_scan_reads_repo_native_validation_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "INDEX.md").write_text(
                "python3 scripts/validate_store.py --strict\n",
                encoding="utf-8",
            )
            summary = scan_repo(root, focus_text="memory")
            self.assertEqual(summary["validation_commands"]["check"], "python3 scripts/validate_store.py --strict")


if __name__ == "__main__":
    unittest.main()
