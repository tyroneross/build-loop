#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import unbounded_load_gate as gate  # noqa: E402


class UnboundedLoadGateTests(unittest.TestCase):
    def test_rejects_observed_node_cpu_loop(self) -> None:
        command = "node -e 'while(true){Math.sqrt(Math.random())}' &"
        self.assertIn("bounded supervisor", gate.evaluate(command) or "")

    def test_rejects_python_cpu_loop(self) -> None:
        self.assertIsNotNone(gate.evaluate("python3 -c 'while True: pass'"))

    def test_allows_bounded_or_nonexecuting_mentions(self) -> None:
        self.assertIsNone(gate.evaluate("build-loop-load-probe --workers 2 --duration-seconds 5"))
        self.assertIsNone(gate.evaluate("rg 'while(true)' src"))
        self.assertIsNotNone(gate.evaluate("node -e 'while(true){if(Date.now()>deadline)break}'"))

    def test_trusted_name_does_not_bypass_later_segment(self) -> None:
        command = "echo build-loop-load-probe; node -e 'while(true){}'"
        self.assertIsNotNone(gate.evaluate(command))

    def test_loop_and_break_in_different_segments_remain_rejected(self) -> None:
        command = "node -e 'while(true){}'; echo break"
        self.assertIsNotNone(gate.evaluate(command))

    def test_unreachable_exit_does_not_bypass_gate(self) -> None:
        self.assertIsNotNone(gate.evaluate("node -e 'while(true){}; process.exit(0)'"))


if __name__ == "__main__":
    unittest.main()
