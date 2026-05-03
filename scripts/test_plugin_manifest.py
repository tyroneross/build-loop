#!/usr/bin/env python3
"""Tests for plugin manifest correctness and version sync.

Stdlib only. Run: python3 test_plugin_manifest.py

Checks:
  ManifestExists       — .claude-plugin/plugin.json present and valid JSON
  RequiredFields       — name, version, description, author all present + non-empty
  VersionShape         — semver (`X.Y.Z[-pre]`) and matches across plugin.json
                          and marketplace.json (catches the v0.4.0/v0.3.2 drift
                          that hit this repo before)
  McpServersReference  — if plugin.json declares `mcpServers`, the referenced
                          file/path must exist and be valid JSON
  SkillNameUniqueness  — every SKILL.md frontmatter `name:` is unique within
                          this plugin (would catch silent skill-shadowing)
  CommandSchema        — every commands/*.md has frontmatter (description at
                          minimum); files without frontmatter break the resolver

The version-sync check would have caught the 0.4.0-vs-0.3.2 drift in
build-loop's marketplace.json that lingered from 0.3.2 era.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"
SKILLS_DIR = REPO_ROOT / "skills"
COMMANDS_DIR = REPO_ROOT / "commands"

REQUIRED_PLUGIN_FIELDS = ("name", "version", "description", "author")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[A-Za-z0-9.-]+)?$")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
DESCRIPTION_RE = re.compile(r"^description:", re.MULTILINE)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_skill_name(skill_md: Path) -> str | None:
    text = skill_md.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    nm = NAME_RE.search(m.group(1))
    if not nm:
        return None
    name = nm.group(1).strip().strip('"').strip("'")
    return name.split(":", 1)[1] if ":" in name else name


def has_frontmatter(md: Path) -> bool:
    text = md.read_text(encoding="utf-8")
    return FRONTMATTER_RE.match(text) is not None


def has_description(md: Path) -> bool:
    text = md.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    return bool(m and DESCRIPTION_RE.search(m.group(1)))


class ManifestExistsTests(unittest.TestCase):
    def test_plugin_json_present(self) -> None:
        self.assertTrue(PLUGIN_JSON.is_file(), f"missing {PLUGIN_JSON}")

    def test_plugin_json_valid(self) -> None:
        data = load_json(PLUGIN_JSON)
        self.assertIsInstance(data, dict)


class RequiredFieldsTests(unittest.TestCase):
    def test_required_fields_present(self) -> None:
        data = load_json(PLUGIN_JSON)
        for f in REQUIRED_PLUGIN_FIELDS:
            self.assertIn(f, data, f"plugin.json missing {f!r}")
            self.assertTrue(data[f], f"plugin.json {f!r} is empty")

    def test_author_has_name(self) -> None:
        data = load_json(PLUGIN_JSON)
        author = data["author"]
        self.assertIsInstance(author, dict)
        self.assertIn("name", author)
        self.assertTrue(author["name"])

    def test_description_is_substantive(self) -> None:
        data = load_json(PLUGIN_JSON)
        self.assertGreaterEqual(
            len(data["description"]), 40,
            "plugin.json description too short — Claude Code uses it for skill triggering",
        )


class VersionShapeTests(unittest.TestCase):
    def test_plugin_version_is_semver(self) -> None:
        data = load_json(PLUGIN_JSON)
        self.assertRegex(data["version"], SEMVER_RE, f"plugin.json version {data['version']!r} not semver")

    def test_marketplace_versions_match_plugin(self) -> None:
        if not MARKETPLACE_JSON.is_file():
            self.skipTest(f"{MARKETPLACE_JSON} not present (no self-hosted marketplace)")
        plugin_v = load_json(PLUGIN_JSON)["version"]
        market = load_json(MARKETPLACE_JSON)
        meta_v = market.get("metadata", {}).get("version")
        if meta_v is not None:
            self.assertEqual(
                meta_v, plugin_v,
                f"marketplace.json metadata.version {meta_v!r} != plugin.json version {plugin_v!r}",
            )
        for entry in market.get("plugins", []):
            if entry.get("name") == load_json(PLUGIN_JSON)["name"]:
                self.assertEqual(
                    entry.get("version"), plugin_v,
                    f"marketplace.json plugins[name={entry['name']!r}].version "
                    f"{entry.get('version')!r} != plugin.json version {plugin_v!r}",
                )


class McpServersReferenceTests(unittest.TestCase):
    def test_mcp_reference_resolves(self) -> None:
        data = load_json(PLUGIN_JSON)
        ref = data.get("mcpServers")
        if ref is None:
            self.skipTest("plugin.json has no mcpServers field")
        if isinstance(ref, str):
            # Path reference — must resolve to an existing JSON file.
            mcp_path = (REPO_ROOT / ref).resolve()
            self.assertTrue(mcp_path.is_file(), f"mcpServers path does not exist: {ref}")
            mcp_data = json.loads(mcp_path.read_text(encoding="utf-8"))
            self.assertIn("mcpServers", mcp_data, "referenced .mcp.json missing mcpServers key")
        elif isinstance(ref, dict):
            # Inline servers object — basic shape check
            self.assertIn("mcpServers", ref, "inline mcpServers missing key")
        else:
            self.fail(f"mcpServers should be string-path or dict, got {type(ref).__name__}")


class SkillNameUniquenessTests(unittest.TestCase):
    def test_no_duplicate_skill_names(self) -> None:
        if not SKILLS_DIR.is_dir():
            self.skipTest(f"{SKILLS_DIR} not present")
        names: dict[str, list[str]] = {}
        for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            name = read_skill_name(skill_md)
            if name is None:
                continue
            names.setdefault(name, []).append(str(skill_md.relative_to(REPO_ROOT)))
        dupes = {n: paths for n, paths in names.items() if len(paths) > 1}
        self.assertEqual(dupes, {}, f"duplicate skill names: {dupes}")


class CommandSchemaTests(unittest.TestCase):
    def test_every_command_has_frontmatter(self) -> None:
        if not COMMANDS_DIR.is_dir():
            self.skipTest(f"{COMMANDS_DIR} not present")
        missing = [
            str(p.relative_to(REPO_ROOT))
            for p in sorted(COMMANDS_DIR.glob("*.md"))
            if not has_frontmatter(p)
        ]
        self.assertEqual(missing, [], f"commands without frontmatter: {missing}")

    def test_every_command_has_description(self) -> None:
        if not COMMANDS_DIR.is_dir():
            self.skipTest(f"{COMMANDS_DIR} not present")
        missing = [
            str(p.relative_to(REPO_ROOT))
            for p in sorted(COMMANDS_DIR.glob("*.md"))
            if not has_description(p)
        ]
        self.assertEqual(missing, [], f"commands without description: {missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
