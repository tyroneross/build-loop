#!/usr/bin/env python3
"""Tests for MCP server registration in this plugin's .mcp.json.

Stdlib only. Run: python3 test_mcp_registration.py

Checks:
  ConfigShape         — .mcp.json (or referenced file from plugin.json) is
                        valid JSON with the expected `mcpServers` key
  ServerNamingHygiene — server names should be plugin-prefixed to avoid name
                        collisions across installed plugins (build-loop ships
                        its bundled server as `build-loop-debugger` so it does
                        not collide with the standalone `claude-code-debugger`
                        plugin's `debugger` server)
  CommandResolves     — for each server with command:"node" or similar, the
                        referenced script path resolves under CLAUDE_PLUGIN_ROOT
  NoDuplicateNames    — within this plugin, every server name is unique

The ServerNamingHygiene check is informational (skipTest with a hint) rather
than blocking — many plugins use bare names today. Worth flagging though
because two plugins registering the same MCP server name will collide at
runtime; only one wins.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_mcp_config_path() -> Path | None:
    """Return the .mcp.json path declared by plugin.json, or None if not declared."""
    if not PLUGIN_JSON.is_file():
        return None
    data = load_json(PLUGIN_JSON)
    ref = data.get("mcpServers")
    if isinstance(ref, str):
        return (REPO_ROOT / ref).resolve()
    if isinstance(ref, dict):
        return None  # inline config — no separate file
    return None


def load_mcp_servers() -> dict | None:
    """Return the {server_name: server_config, ...} dict, or None if not declared."""
    if not PLUGIN_JSON.is_file():
        return None
    data = load_json(PLUGIN_JSON)
    ref = data.get("mcpServers")
    if isinstance(ref, dict):
        return ref.get("mcpServers", {})
    if isinstance(ref, str):
        path = (REPO_ROOT / ref).resolve()
        if not path.is_file():
            return None
        config = json.loads(path.read_text(encoding="utf-8"))
        return config.get("mcpServers", {})
    return None


def expand_plugin_root(arg: str) -> str:
    """Replace ${CLAUDE_PLUGIN_ROOT} with the actual repo root path."""
    return arg.replace("${CLAUDE_PLUGIN_ROOT}", str(REPO_ROOT))


class ConfigShapeTests(unittest.TestCase):
    def test_plugin_declares_mcp_or_skips(self) -> None:
        if not PLUGIN_JSON.is_file():
            self.skipTest(f"{PLUGIN_JSON} not present")
        data = load_json(PLUGIN_JSON)
        if "mcpServers" not in data:
            self.skipTest("plugin.json has no mcpServers field — nothing to test")
        # If declared, must be string (path) or dict (inline)
        ref = data["mcpServers"]
        self.assertIn(type(ref).__name__, ("str", "dict"))

    def test_referenced_mcp_file_valid_json(self) -> None:
        path = resolve_mcp_config_path()
        if path is None:
            self.skipTest("mcpServers is inline or absent")
        self.assertTrue(path.is_file(), f"referenced mcp file does not exist: {path}")
        config = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("mcpServers", config, "referenced .mcp.json missing mcpServers key")
        self.assertIsInstance(config["mcpServers"], dict)


class ServerNamingHygieneTests(unittest.TestCase):
    """Server names should be plugin-prefixed to avoid global name collisions.

    Multiple plugins each registering server name 'debugger' will collide —
    only one wins at runtime, the others are silently shadowed. Bare names
    aren't blocked by Claude Code, but they are a footgun.

    Build-loop ships its bundled server as `build-loop-debugger` to coexist
    cleanly with the standalone `claude-code-debugger` plugin's `debugger`
    server. Both can be installed; both register; neither shadows the other.
    """

    def test_server_names_avoid_common_unprefixed_names(self) -> None:
        servers = load_mcp_servers()
        if not servers:
            self.skipTest("no MCP servers declared")
        plugin_data = load_json(PLUGIN_JSON)
        plugin_name = plugin_data.get("name", "")
        # Commonly-collisional bare names (likely to clash with another plugin)
        risky = {"debugger", "memory", "search", "auth", "logger", "tracer"}
        # A server is "prefixed" if its name starts with the plugin name OR
        # contains it (e.g., plugin "build-loop" + server "build-loop-debugger").
        risks = [
            n for n in servers
            if n in risky and plugin_name and plugin_name not in n
        ]
        if risks:
            self.skipTest(
                f"server name(s) {risks} are not plugin-prefixed and may collide "
                f"with another plugin registering the same name. Consider "
                f"renaming to e.g. {plugin_name}-{risks[0]!r}. Non-blocking — "
                "many plugins use bare names today."
            )


class CommandResolvesTests(unittest.TestCase):
    def test_command_args_resolve(self) -> None:
        servers = load_mcp_servers()
        if not servers:
            self.skipTest("no MCP servers declared")
        for name, cfg in servers.items():
            with self.subTest(server=name):
                args = cfg.get("args", [])
                # If args reference a script under CLAUDE_PLUGIN_ROOT, that script must exist.
                for a in args:
                    if "${CLAUDE_PLUGIN_ROOT}" in a:
                        path = Path(expand_plugin_root(a))
                        # Only check if it looks like a file path (has an extension)
                        if path.suffix:
                            self.assertTrue(
                                path.exists(),
                                f"server {name!r} references {a!r} which resolves to "
                                f"{path} — not present (run `npm run build` if TS source?)",
                            )


class NoDuplicateNamesTests(unittest.TestCase):
    def test_unique_within_plugin(self) -> None:
        servers = load_mcp_servers()
        if not servers:
            self.skipTest("no MCP servers declared")
        # Dict keys are inherently unique in JSON; this just asserts the load
        # produced sane state.
        self.assertEqual(len(servers), len(set(servers.keys())))


if __name__ == "__main__":
    unittest.main(verbosity=2)
