#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Static tests for the build-loop-memory MCP bridge."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class BlmMcpBridgeTests(unittest.TestCase):
    def test_mcp_tools_are_registered(self) -> None:
        text = (REPO / "src" / "mcp" / "tools.ts").read_text(encoding="utf-8")
        for name in (
            "build_loop_memory_status",
            "build_loop_memory_context",
            "build_loop_memory_open",
        ):
            self.assertIn(f"name: '{name}'", text)
            self.assertIn(f"mcp:{name}", text)

    def test_package_includes_blm_cli_dependencies(self) -> None:
        package = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
        files = set(package["files"])
        required = {
            "scripts/_paths.py",
            "scripts/blm.py",
            "scripts/blm_api.py",
            "scripts/lessons_index",
            "scripts/memory_context",
            "scripts/project_resolver.py",
        }
        self.assertTrue(required.issubset(files), sorted(required - files))


if __name__ == "__main__":
    unittest.main(verbosity=2)
