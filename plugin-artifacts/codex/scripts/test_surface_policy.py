#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/surface_policy.py."""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

# Ensure scripts/ is importable when run directly via pytest <file>
sys.path.insert(0, str(Path(__file__).resolve().parent))

import exposure_policy  # noqa: E402
from surface_policy import (  # noqa: E402
    DEFAULT_PUBLIC,
    HIDDEN,
    PUBLIC_JUSTIFIED,
    PUBLIC_UNJUSTIFIED,
    SKILL_CLASSES,
    build_report,
    classify_skill,
    discover_commands,
    discover_skills,
    main,
    parse_frontmatter,
    render_plain,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

HIDDEN_SKILL = """---
name: worker
description: A helper skill.
user-invocable: false
---

# Worker
"""

PUBLIC_JUSTIFIED_SKILL = """---
name: entry
description: The front door.
user-invocable: true
public-justification: Sole human entrypoint; users type this by name.
---

# Entry
"""

PUBLIC_UNJUSTIFIED_SKILL = """---
name: loose
description: Public with no stated reason.
user-invocable: true
---

# Loose
"""

NO_FIELD_SKILL = """---
name: ghost
description: Declares nothing about visibility.
---

# Ghost
"""

UNRECOGNIZED_FLAG_SKILL = """---
name: odd
description: A flag this repo does not recognize, plus a reason.
user-invocable: yes
public-justification: nonsense flag
---

# Odd
"""

MALFORMED_SKILL = """name: broken
user-invocable: false
this file never opens a frontmatter fence

# Broken
"""


def write_skill(root: Path, slug: str, body: str) -> Path:
    path = root / "skills" / slug / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def write_command(root: Path, rel: str) -> Path:
    path = root / "commands" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# command\n", encoding="utf-8")
    return path


class TmpPluginTest(unittest.TestCase):
    """Base: each test gets a scratch plugin directory."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


# ---------------------------------------------------------------------------
# The four skill classifications
# ---------------------------------------------------------------------------

class TestSkillClassification(TmpPluginTest):
    def test_hidden(self) -> None:
        path = write_skill(self.root, "worker", HIDDEN_SKILL)
        rec = classify_skill(path, self.root)
        self.assertEqual(rec["class"], HIDDEN)
        self.assertFalse(rec["public"])
        self.assertEqual(rec["user_invocable"], "false")

    def test_public_justified(self) -> None:
        path = write_skill(self.root, "entry", PUBLIC_JUSTIFIED_SKILL)
        rec = classify_skill(path, self.root)
        self.assertEqual(rec["class"], PUBLIC_JUSTIFIED)
        self.assertTrue(rec["public"])
        self.assertIn("Sole human entrypoint", rec["public_justification"])

    def test_public_unjustified(self) -> None:
        path = write_skill(self.root, "loose", PUBLIC_UNJUSTIFIED_SKILL)
        rec = classify_skill(path, self.root)
        self.assertEqual(rec["class"], PUBLIC_UNJUSTIFIED)
        self.assertTrue(rec["public"])
        self.assertIsNone(rec["public_justification"])

    def test_absent_field_is_public_by_harness_default(self) -> None:
        """`userInvocable ?? true` — no field means PUBLIC, never hidden."""
        path = write_skill(self.root, "ghost", NO_FIELD_SKILL)
        rec = classify_skill(path, self.root)
        self.assertEqual(rec["class"], DEFAULT_PUBLIC)
        self.assertTrue(rec["public"], "an absent user-invocable field must classify as PUBLIC")
        self.assertIsNone(rec["user_invocable"])

    def test_unrecognized_flag_is_never_justified(self) -> None:
        """An unparseable flag is exposed, but it is not a deliberate opt-in.

        `test_agent_surface_policy.surface_violation` has always rejected
        `user-invocable: maybe` + a justification; this script used to accept the
        same file. Both now read the one rule in `exposure_policy`.
        """
        path = write_skill(self.root, "odd", UNRECOGNIZED_FLAG_SKILL)
        rec = classify_skill(path, self.root)
        self.assertEqual(rec["class"], PUBLIC_UNJUSTIFIED)
        self.assertTrue(rec["public"])
        self.assertEqual(rec["user_invocable"], "yes")

    def test_classes_come_from_the_shared_policy_module(self) -> None:
        """One definition of the four classes, imported — never re-declared."""
        self.assertIs(SKILL_CLASSES, exposure_policy.EXPOSURE_CLASSES)
        self.assertIs(HIDDEN, exposure_policy.HIDDEN)
        self.assertIs(DEFAULT_PUBLIC, exposure_policy.DEFAULT_PUBLIC)
        self.assertIs(PUBLIC_JUSTIFIED, exposure_policy.PUBLIC_JUSTIFIED)
        self.assertIs(PUBLIC_UNJUSTIFIED, exposure_policy.PUBLIC_UNJUSTIFIED)

    def test_name_falls_back_to_directory(self) -> None:
        path = write_skill(self.root, "nameless", "---\nuser-invocable: false\n---\n")
        self.assertEqual(classify_skill(path, self.root)["name"], "nameless")

    def test_namespaced_name_is_stripped(self) -> None:
        path = write_skill(self.root, "x", "---\nname: build-loop:run\nuser-invocable: false\n---\n")
        self.assertEqual(classify_skill(path, self.root)["name"], "run")

    def test_path_is_relative_to_workdir(self) -> None:
        path = write_skill(self.root, "worker", HIDDEN_SKILL)
        self.assertEqual(classify_skill(path, self.root)["path"], "skills/worker/SKILL.md")


# ---------------------------------------------------------------------------
# Malformed frontmatter
# ---------------------------------------------------------------------------

class TestMalformedFrontmatter(TmpPluginTest):
    def test_parse_frontmatter_returns_none(self) -> None:
        self.assertIsNone(parse_frontmatter(MALFORMED_SKILL))

    def test_malformed_is_flagged_and_treated_as_public(self) -> None:
        """Fail-safe: an unparseable file yields no field, so it reads as PUBLIC."""
        path = write_skill(self.root, "broken", MALFORMED_SKILL)
        rec = classify_skill(path, self.root)
        self.assertTrue(rec["malformed_frontmatter"])
        self.assertEqual(rec["class"], DEFAULT_PUBLIC)
        self.assertTrue(rec["public"])

    def test_malformed_fails_check(self) -> None:
        write_skill(self.root, "broken", MALFORMED_SKILL)
        self.assertFalse(build_report(self.root)["ok"])

    def test_empty_file_does_not_crash(self) -> None:
        path = write_skill(self.root, "empty", "")
        rec = classify_skill(path, self.root)
        self.assertEqual(rec["class"], DEFAULT_PUBLIC)


# ---------------------------------------------------------------------------
# Discovery — empty dir, no skills dir, scoping
# ---------------------------------------------------------------------------

class TestDiscovery(TmpPluginTest):
    def test_no_skills_dir_returns_empty(self) -> None:
        self.assertEqual(discover_skills(self.root), [])

    def test_no_commands_dir_returns_empty(self) -> None:
        self.assertEqual(discover_commands(self.root), [])

    def test_empty_dir_reports_zero_and_passes(self) -> None:
        report = build_report(self.root)
        self.assertEqual(report["counts"]["skills"], 0)
        self.assertEqual(report["counts"]["commands"], 0)
        self.assertTrue(report["ok"])

    def test_empty_skills_dir_returns_empty(self) -> None:
        (self.root / "skills").mkdir()
        self.assertEqual(discover_skills(self.root), [])

    def test_nested_skills_are_found(self) -> None:
        path = self.root / "skills" / "architecture" / "scan" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(HIDDEN_SKILL, encoding="utf-8")
        self.assertEqual(len(discover_skills(self.root)), 1)

    def test_skills_outside_the_skills_dir_are_ignored(self) -> None:
        """A worktree copy under .build-loop/ is not part of the loaded surface."""
        stray = self.root / ".build-loop" / "worktrees" / "run-1" / "skills" / "ghost" / "SKILL.md"
        stray.parent.mkdir(parents=True)
        stray.write_text(NO_FIELD_SKILL, encoding="utf-8")
        write_skill(self.root, "worker", HIDDEN_SKILL)
        skills = discover_skills(self.root)
        self.assertEqual([s["name"] for s in skills], ["worker"])

    def test_worktree_copies_nested_under_skills_are_excluded(self) -> None:
        """A worktree copy is another agent's checkout, not this plugin's surface.

        Directory scoping alone missed a worktree NESTED under `skills/`, so the
        gate could fail on a file the harness never loads. Same exclusion list
        `skill_index.py` walks with, so both scripts see one set of files.
        """
        for rel in (
            ".build-loop/worktrees/run-1/skills/copy",
            ".claude/worktrees/run-2/skills/copy",
            "node_modules/pkg",
            "plugin-artifacts/codex",
        ):
            stray = self.root / "skills" / rel / "SKILL.md"
            stray.parent.mkdir(parents=True, exist_ok=True)
            stray.write_text(NO_FIELD_SKILL, encoding="utf-8")
        write_skill(self.root, "worker", HIDDEN_SKILL)
        self.assertEqual([s["name"] for s in discover_skills(self.root)], ["worker"])
        self.assertTrue(build_report(self.root)["ok"])

    def test_a_skill_named_after_worktrees_is_kept(self) -> None:
        write_skill(self.root, "data-plane-worktrees", HIDDEN_SKILL)
        self.assertEqual(len(discover_skills(self.root)), 1)

    def test_commands_are_discovered_with_namespaced_names(self) -> None:
        write_command(self.root, "run.md")
        write_command(self.root, "sub/deep.md")
        names = [c["name"] for c in discover_commands(self.root)]
        self.assertEqual(sorted(names), ["run", "sub:deep"])


# ---------------------------------------------------------------------------
# Report shape — counts, json vs plain
# ---------------------------------------------------------------------------

class TestReportShape(TmpPluginTest):
    def setUp(self) -> None:
        super().setUp()
        write_skill(self.root, "worker", HIDDEN_SKILL)
        write_skill(self.root, "entry", PUBLIC_JUSTIFIED_SKILL)
        write_skill(self.root, "loose", PUBLIC_UNJUSTIFIED_SKILL)
        write_skill(self.root, "ghost", NO_FIELD_SKILL)
        write_command(self.root, "run.md")

    def test_counts_cover_all_four_classes(self) -> None:
        counts = build_report(self.root)["counts"]
        self.assertEqual(counts["skills"], 4)
        self.assertEqual(counts["commands"], 1)
        self.assertEqual(counts[HIDDEN], 1)
        self.assertEqual(counts[PUBLIC_JUSTIFIED], 1)
        self.assertEqual(counts[PUBLIC_UNJUSTIFIED], 1)
        self.assertEqual(counts[DEFAULT_PUBLIC], 1)

    def test_class_counts_sum_to_total(self) -> None:
        counts = build_report(self.root)["counts"]
        total = sum(counts[k] for k in (HIDDEN, PUBLIC_JUSTIFIED, PUBLIC_UNJUSTIFIED, DEFAULT_PUBLIC))
        self.assertEqual(total, counts["skills"])

    def test_violations_are_the_two_undeclared_classes(self) -> None:
        violations = build_report(self.root)["violations"]
        self.assertEqual(sorted(v["name"] for v in violations), ["ghost", "loose"])

    def test_no_stored_policy_file_is_read_or_written(self) -> None:
        """The report is derived. A stored policy file must be irrelevant to it."""
        before = build_report(self.root)["counts"]
        (self.root / "surface-policy.json").write_text(
            json.dumps({"public": ["everything"]}), encoding="utf-8"
        )
        after = build_report(self.root)
        self.assertEqual(after["counts"], before)
        self.assertFalse((self.root / ".surface-policy.json").exists())

    def test_plain_output_names_the_harness_default_class(self) -> None:
        text = render_plain(build_report(self.root))
        self.assertIn("PUBLIC BY HARNESS DEFAULT", text)
        self.assertIn("userInvocable ?? true", text)
        self.assertIn("ghost", text)

    def test_plain_output_is_a_string_not_json(self) -> None:
        text = render_plain(build_report(self.root))
        with self.assertRaises(json.JSONDecodeError):
            json.loads(text)

    def test_json_report_has_the_documented_keys(self) -> None:
        payload = json.loads(self._run_ok(["report", "--workdir", str(self.root), "--json"]))
        for key in ("workdir", "plugin", "commands", "skills", "counts", "violations", "ok"):
            self.assertIn(key, payload)

    def test_json_report_lists_hidden_skill_names(self) -> None:
        payload = json.loads(self._run_ok(["report", "--workdir", str(self.root), "--json"]))
        hidden = [s["name"] for s in payload["skills"] if s["class"] == HIDDEN]
        self.assertEqual(hidden, ["worker"])

    def _run_ok(self, argv: list[str]) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        self.assertEqual(code, 0)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------

class TestCliExitCodes(TmpPluginTest):
    def _run(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_check_passes_when_all_hidden(self) -> None:
        write_skill(self.root, "worker", HIDDEN_SKILL)
        code, out = self._run(["check", "--workdir", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("OK", out)

    def test_check_passes_when_public_is_justified(self) -> None:
        write_skill(self.root, "entry", PUBLIC_JUSTIFIED_SKILL)
        self.assertEqual(self._run(["check", "--workdir", str(self.root)])[0], 0)

    def test_check_fails_on_public_without_justification(self) -> None:
        write_skill(self.root, "loose", PUBLIC_UNJUSTIFIED_SKILL)
        code, out = self._run(["check", "--workdir", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("loose", out)

    def test_check_fails_on_missing_field(self) -> None:
        write_skill(self.root, "ghost", NO_FIELD_SKILL)
        code, out = self._run(["check", "--workdir", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("PUBLIC BY HARNESS DEFAULT", out)

    def test_check_passes_on_empty_dir(self) -> None:
        self.assertEqual(self._run(["check", "--workdir", str(self.root)])[0], 0)

    def test_report_exits_zero_even_with_violations(self) -> None:
        write_skill(self.root, "ghost", NO_FIELD_SKILL)
        self.assertEqual(self._run(["report", "--workdir", str(self.root)])[0], 0)

    def test_check_json_shape(self) -> None:
        write_skill(self.root, "ghost", NO_FIELD_SKILL)
        code, out = self._run(["check", "--workdir", str(self.root), "--json"])
        payload = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual([v["name"] for v in payload["violations"]], ["ghost"])

    def test_plain_flag_forces_text_over_json(self) -> None:
        write_skill(self.root, "worker", HIDDEN_SKILL)
        _, out = self._run(["report", "--workdir", str(self.root), "--json", "--plain"])
        self.assertIn("Surface report", out)


# ---------------------------------------------------------------------------
# Portability — runs against the two real plugin repos when present
# ---------------------------------------------------------------------------

class TestRealPluginDirs(unittest.TestCase):
    def test_build_loop_itself_is_clean(self) -> None:
        repo = Path(__file__).resolve().parent.parent
        report = build_report(repo)
        self.assertGreater(report["counts"]["skills"], 0)
        self.assertEqual(report["counts"][DEFAULT_PUBLIC], 0)
        self.assertTrue(report["ok"], f"violations: {[v['name'] for v in report['violations']]}")


if __name__ == "__main__":
    unittest.main()
