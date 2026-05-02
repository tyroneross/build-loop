#!/usr/bin/env python3
"""
Detect Claude Code plugin namesake collisions.

Background: Claude Code 2.1.x registers slash-commands and skills under a single
unified namespace per plugin. When `commands/<name>.md` and `skills/<name>/SKILL.md`
both exist in the same plugin, calling Skill("<plugin>:<name>") returns the
slash-command file's body verbatim (including unrendered Handlebars) instead of
executing the skill. See KNOWN-ISSUES.md.

This script statically detects every such collision across one or more plugin
trees, plus the handful of installed plugin caches Claude Code maintains.

Usage:
    python3 collision_scan.py                       # scan ~/.claude/plugins
    python3 collision_scan.py --path PATH [PATH...] # scan specific dirs
    python3 collision_scan.py --json                # machine-readable
    python3 collision_scan.py --strict              # exit 1 if any collision

Risk model: every detected collision is high-confidence buggy. The Skill resolver
is shape-blind; if the shape exists, the bug exists. The empirical fix pattern
is documented in KNOWN-ISSUES.md (Option 2: rename the slash-command file).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

DEFAULT_ROOTS = [
    Path.home() / ".claude" / "plugins",
]


@dataclass
class Collision:
    plugin_path: str
    plugin_name: str
    collision_name: str
    qualified_name: str
    command_file: str
    skill_file: str

    def pretty(self) -> str:
        return f"  {self.qualified_name:40s}  [{self.plugin_path}]"


def find_plugin_dirs(root: Path) -> list[Path]:
    """A plugin dir contains either a plugin.json/.claude-plugin.json manifest,
    or a `commands/` and/or `skills/` subdirectory at its top level."""
    if not root.is_dir():
        return []
    plugins: list[Path] = []
    seen: set[Path] = set()

    def looks_like_plugin(p: Path) -> bool:
        if not p.is_dir():
            return False
        if (p / ".claude-plugin" / "plugin.json").is_file():
            return True
        if (p / "plugin.json").is_file() or (p / ".claude-plugin.json").is_file():
            return True
        return (p / "commands").is_dir() or (p / "skills").is_dir()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        # skip hidden dirs except top-level .claude-plugin metadata
        dirnames[:] = [n for n in dirnames if not n.startswith(".git")]
        if d in seen:
            continue
        if looks_like_plugin(d):
            plugins.append(d)
            seen.add(d)
            # don't recurse into subdirs of a plugin
            dirnames.clear()
    return plugins


def scan_plugin(plugin_dir: Path) -> list[Collision]:
    out: list[Collision] = []
    cmds_dir = plugin_dir / "commands"
    skills_dir = plugin_dir / "skills"
    if not (cmds_dir.is_dir() and skills_dir.is_dir()):
        return out
    plugin_name = plugin_dir.name
    for manifest in (
        plugin_dir / ".claude-plugin" / "plugin.json",
        plugin_dir / "plugin.json",
        plugin_dir / ".claude-plugin.json",
    ):
        if manifest.is_file():
            try:
                with open(manifest) as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("name"), str):
                    plugin_name = data["name"]
                    break
            except (json.JSONDecodeError, OSError):
                pass

    for cmd in sorted(cmds_dir.glob("*.md")):
        name = cmd.stem
        skill = skills_dir / name / "SKILL.md"
        if skill.is_file():
            out.append(
                Collision(
                    plugin_path=str(plugin_dir),
                    plugin_name=plugin_name,
                    collision_name=name,
                    qualified_name=f"{plugin_name}:{name}",
                    command_file=str(cmd),
                    skill_file=str(skill),
                )
            )
    return out


def scan_roots(roots: list[Path]) -> list[Collision]:
    collisions: list[Collision] = []
    for root in roots:
        for plugin in find_plugin_dirs(root):
            collisions.extend(scan_plugin(plugin))
    return collisions


def render_pretty(collisions: list[Collision]) -> str:
    if not collisions:
        return "No namesake collisions detected.\n"
    # Group by qualified name to show duplicates across cache versions
    by_qname: dict[str, list[Collision]] = {}
    for c in collisions:
        by_qname.setdefault(c.qualified_name, []).append(c)

    lines = [
        f"Found {len(by_qname)} unique collision shape(s) across "
        f"{len(collisions)} plugin location(s):\n"
    ]
    for qname in sorted(by_qname):
        instances = by_qname[qname]
        lines.append(f"  {qname}")
        lines.append(f"    Status: BUGGY (Skill('{qname}') returns slash-command template)")
        lines.append(f"    Locations ({len(instances)}):")
        for c in instances:
            lines.append(f"      - {c.plugin_path}")
        lines.append("")
    lines.append("Fix pattern: rename commands/<name>.md to commands/<other>.md "
                 "(skill name unchanged keeps Skill() callers stable).")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--path",
        action="append",
        type=Path,
        help="scan this directory (repeatable; default: ~/.claude/plugins)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if any collision found (CI gate)",
    )
    args = parser.parse_args(argv)

    roots = args.path or DEFAULT_ROOTS
    collisions = scan_roots(roots)

    if args.json:
        print(json.dumps([asdict(c) for c in collisions], indent=2))
    else:
        print(render_pretty(collisions))

    if args.strict and collisions:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
