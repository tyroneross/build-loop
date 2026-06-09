#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Static tests for the build-loop-memory CLI packaging contract.

The MCP-tool registration test was deleted when the Build Loop MCP surface
itself was removed (commit cef120b "Remove Build Loop MCP surface and harden
hooks"): there is no ``src/mcp/tools.ts`` to assert against, so the old test
could only ever ``FileNotFoundError``. The surviving contract this file guards
is that the blm CLI's Python dependencies ship in the npm package.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class BlmMcpBridgeTests(unittest.TestCase):
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
