#!/usr/bin/env python3
"""Tests for skill resolution shape — catches the namesake-collision bug class.

Stdlib only. Run: python3 test_skill_resolution.py

The Skill tool resolver in Claude Code 2.1.x treats commands/<name>.md and
skills/<name>/SKILL.md within the same plugin as a single namespace per qualified
name <plugin>:<name>. When both files exist, calling Skill("<plugin>:<name>")
returns the slash-command template instead of executing the skill. This was the
bug that shipped through build-loop 0.4.0 and claude-code-debugger 1.8.1
undetected — that's the bug shape this suite exists to prevent shipping again.

Tests:
  ContractShape — collision_scan.py output structure is stable
  ZeroNewCollisions — no collision in build-loop other than the 3 accepted
                      latent siblings (optimize, research, plan-verify)
  FrontmatterMatchesDir — every skill's frontmatter `name:` matches its
                          directory name (drift would break Skill() lookups)
  StrictExitCode — --strict flag exits 1 on any collision (CI gate)

If you intentionally rename one of the accepted siblings (Option-2 fix),
remove it from ACCEPTED_SIBLINGS below and the test will then enforce
zero collisions going forward.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "collision_scan.py"
REPO_ROOT = HERE.parent  # build-loop repo root
SKILLS_DIR = REPO_ROOT / "skills"

# Latent-risk collisions intentionally retained per KNOWN-ISSUES.md
# (user explicitly accepted these on 2026-05-01 — UX cost of rename
# deemed not worth the unverified risk). Remove an entry here ONLY when
# the corresponding command/skill pair has actually been renamed.
ACCEPTED_SIBLINGS: set[str] = {
    "build-loop:optimize",
    "build-loop:plan-verify",
    "build-loop:research",
}

FINDING_KEYS = (
    "plugin_path",
    "plugin_name",
    "collision_name",
    "qualified_name",
    "command_file",
    "skill_file",
)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)


def run_scanner(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--path", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def parse_json_findings(stdout: str) -> list[dict]:
    return json.loads(stdout)


def read_skill_name(skill_md: Path) -> str | None:
    """Return the `name:` from a SKILL.md's YAML frontmatter, or None if absent."""
    text = skill_md.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    nm = NAME_RE.search(m.group(1))
    if not nm:
        return None
    name = nm.group(1).strip().strip('"').strip("'")
    # Handle qualified-name form (`name: build-loop:foo`) — return the bare skill name
    if ":" in name:
        return name.split(":", 1)[1]
    return name


class ContractShapeTests(unittest.TestCase):
    """Scanner output structure must remain stable for downstream consumers."""

    def test_json_is_list_of_findings(self) -> None:
        r = run_scanner("--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        findings = parse_json_findings(r.stdout)
        self.assertIsInstance(findings, list)
        for f in findings:
            for k in FINDING_KEYS:
                self.assertIn(k, f, f"missing key {k} in finding: {f}")

    def test_qualified_name_format(self) -> None:
        r = run_scanner("--json")
        for f in parse_json_findings(r.stdout):
            self.assertEqual(f["qualified_name"], f"{f['plugin_name']}:{f['collision_name']}")

    def test_files_exist_on_disk(self) -> None:
        r = run_scanner("--json")
        for f in parse_json_findings(r.stdout):
            self.assertTrue(Path(f["command_file"]).is_file(), f["command_file"])
            self.assertTrue(Path(f["skill_file"]).is_file(), f["skill_file"])


class ZeroNewCollisionsTests(unittest.TestCase):
    """Build-loop must not introduce new namesake collisions beyond accepted ones."""

    def test_only_accepted_siblings_present(self) -> None:
        r = run_scanner("--json")
        actual = {f["qualified_name"] for f in parse_json_findings(r.stdout)}
        new = actual - ACCEPTED_SIBLINGS
        self.assertEqual(
            new,
            set(),
            f"NEW namesake collisions detected (not in ACCEPTED_SIBLINGS): {sorted(new)}. "
            "Either rename the slash-command file (Option 2 in KNOWN-ISSUES.md) or, "
            "if intentional, add to ACCEPTED_SIBLINGS in this test.",
        )

    def test_accepted_siblings_still_exist(self) -> None:
        # Soft cross-check: if an accepted sibling was renamed away (good!), this
        # test will help us notice the ACCEPTED_SIBLINGS list can be trimmed.
        r = run_scanner("--json")
        actual = {f["qualified_name"] for f in parse_json_findings(r.stdout)}
        gone = ACCEPTED_SIBLINGS - actual
        if gone:
            self.skipTest(
                f"accepted siblings no longer collide (rename succeeded?): {sorted(gone)}. "
                "Consider removing them from ACCEPTED_SIBLINGS to enforce zero collisions."
            )


class FrontmatterMatchesDirTests(unittest.TestCase):
    """Every skill's `name:` frontmatter must match its directory name.

    Drift between dir name and frontmatter name will break Skill() lookups
    even without a slash-command collision — qualified resolution uses the
    frontmatter, but the on-disk path uses the directory.
    """

    def test_every_skill_name_matches_dir(self) -> None:
        if not SKILLS_DIR.is_dir():
            self.skipTest(f"{SKILLS_DIR} not present")
        mismatches: list[str] = []
        missing: list[str] = []
        for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            dir_name = skill_md.parent.name
            name = read_skill_name(skill_md)
            if name is None:
                missing.append(str(skill_md.relative_to(REPO_ROOT)))
                continue
            if name != dir_name:
                mismatches.append(
                    f"{skill_md.relative_to(REPO_ROOT)}: dir={dir_name!r} "
                    f"frontmatter name={name!r}"
                )
        self.assertEqual(missing, [], f"SKILL.md missing `name:` frontmatter: {missing}")
        self.assertEqual(mismatches, [], f"frontmatter/dir drift: {mismatches}")


class StrictExitCodeTests(unittest.TestCase):
    """--strict must exit 1 when any collision exists (CI gate behaviour)."""

    def test_strict_exits_1_with_accepted_siblings(self) -> None:
        # Today the accepted siblings still collide → strict exits 1.
        r = run_scanner("--strict")
        if ACCEPTED_SIBLINGS:
            self.assertEqual(r.returncode, 1, r.stderr or r.stdout[:200])
        else:
            # If we ever fix all siblings, strict should pass.
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout[:200])

    def test_default_exits_0(self) -> None:
        r = run_scanner()
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
