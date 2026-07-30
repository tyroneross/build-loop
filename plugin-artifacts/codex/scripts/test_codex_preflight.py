#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for codex_preflight.py's current Codex install contract."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import codex_preflight as preflight


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_minimal_repo(root: Path) -> None:
    manifest = {
        "name": "build-loop",
        "version": "1.0.0",
        "skills": "./codex-skills",
    }
    write(root / ".codex-plugin/plugin.json", json.dumps(manifest))
    write(root / ".claude-plugin/plugin.json", json.dumps({"name": "build-loop", "version": "1.0.0"}))
    write(
        root / ".agents/plugins/marketplace.json",
        json.dumps({"name": "build-loop", "version": "1.0.0", "plugins": [{"name": "build-loop", "source": "./plugin-artifacts/codex"}]}),
    )
    write(
        root / "package.json",
        json.dumps({
            "files": [
                ".codex-plugin",
                ".agents/plugins",
                "AGENTS.md",
                "plugin-artifacts/codex",
            ]
        }),
    )
    write(root / "AGENTS.md", "# Agents\n")
    write(root / "plugin-artifacts/codex/.codex-plugin/plugin.json", json.dumps({"name": "build-loop", "version": "1.0.0", "skills": "./skills"}))


class CodexPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_minimal_repo(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_package_files_accepts_agents_plugins_not_agents_root(self) -> None:
        checks = preflight.run_checks(self.root)
        check3 = next(check for check in checks if check["id"] == 3)

        self.assertTrue(check3["pass"], check3)
        self.assertIn("Codex package surfaces", check3["name"])

    def test_absent_mcp_json_passes_when_manifest_declares_no_mcp_servers(self) -> None:
        manifest = json.loads((self.root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))

        ok, reason = preflight.check_mcp_server_path(self.root, manifest)

        self.assertTrue(ok)
        self.assertEqual(reason, "plugin declares no MCP servers")

    def test_declared_mcp_path_still_must_resolve(self) -> None:
        manifest = json.loads((self.root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        manifest["mcpServers"] = "./.mcp.json"

        ok, reason = preflight.check_mcp_server_path(self.root, manifest)

        self.assertFalse(ok)
        self.assertIn("declared MCP config not found", reason)

    def test_cache_sync_uses_declared_codex_artifact_source(self) -> None:
        self.assertEqual(
            preflight.codex_install_source(self.root),
            (self.root / "plugin-artifacts/codex").resolve(),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
