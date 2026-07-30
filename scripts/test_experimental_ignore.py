# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""test_experimental_ignore.py — stdlib unittest suite for experimental_ignore.py.

Two layers of assertion:

  * TEXT layer — the managed block is written, is idempotent, preserves
    pre-existing rules, and fails soft on malformed input. No git needed.
  * BEHAVIOUR layer — a real ``git init`` tmp repo plus ``git check-ignore -v``.
    Rule ordering against git's parent-directory exclusion rule is the entire
    risk of this chunk, so it is proven against git itself, not asserted from
    reading the patterns.

The module is loaded via importlib from its explicit file path, matching
test_backlog.py's collision-proof import.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_TARGET = _THIS.parent / "experimental_ignore.py"

# The rooted block backlog.py's `adopt` writes. Reproduced verbatim so this
# suite proves the two managed blocks compose.
_BACKLOG_BLOCK = """
# build-loop backlog (added by `backlog.py adopt` — keep so the backlog travels)
!/.build-loop/
/.build-loop/*
!/.build-loop/backlog/
!/.build-loop/backlog/**
!/BACKLOG.md
"""


def _load():
    spec = importlib.util.spec_from_file_location("_experimental_ignore_under_test", _TARGET)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ei = _load()
_GIT = shutil.which("git")


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "consumer-app"
        self.repo.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_gitignore(self, text: str) -> None:
        (self.repo / ".gitignore").write_text(text, encoding="utf-8")

    def read_gitignore(self) -> str:
        return (self.repo / ".gitignore").read_text(encoding="utf-8")

    def run_cli(self, *args: str) -> tuple[int, dict]:
        """Run main() with --json and return (exit_code, parsed_report)."""
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ei.main(["--workdir", str(self.repo), *args])
        return code, json.loads(buf.getvalue())


class TestTextLayer(_Base):
    def test_apply_on_bare_blanket_ignore_emits_root_and_tier_rules(self) -> None:
        self.write_gitignore("node_modules/\n.build-loop/\n")
        code, report = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(report["action"], "applied")
        self.assertTrue(report["needs_root"], "blanket exclusion requires the root re-open pair")
        body = self.read_gitignore()
        for rule in ei.ROOT_RULES + ei.TIER_RULES:
            self.assertIn(rule, body.splitlines())

    def test_root_rules_omitted_when_backlog_block_already_reopened_root(self) -> None:
        """Emitting /.build-loop/* again would land after !/.build-loop/backlog/."""
        self.write_gitignore(".build-loop/\n" + _BACKLOG_BLOCK)
        code, report = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertFalse(report["needs_root"])
        lines = [ln.strip() for ln in self.read_gitignore().splitlines()]
        marker_at = lines.index(ei.MARKER)
        self.assertNotIn("/.build-loop/*", lines[marker_at:])
        self.assertIn("!/.build-loop/backlog/**", lines[:marker_at])

    def test_idempotent_second_run_adds_nothing(self) -> None:
        self.write_gitignore(".build-loop/\n")
        self.run_cli("--apply")
        first = self.read_gitignore()
        code, report = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(report["action"], "already_compliant")
        self.assertEqual(report["added"], [])
        self.assertEqual(self.read_gitignore(), first)
        self.assertEqual(first.count(ei.MARKER), 1)

    def test_check_is_read_only_and_signals_drift_with_exit_1(self) -> None:
        self.write_gitignore(".build-loop/\n")
        before = self.read_gitignore()
        code, report = self.run_cli("--check")
        self.assertEqual(code, 1)
        self.assertEqual(report["action"], "would_apply")
        self.assertFalse(report["applied"])
        self.assertEqual(self.read_gitignore(), before, "--check must not write")
        self.run_cli("--apply")
        code, report = self.run_cli("--check")
        self.assertEqual(code, 0)
        self.assertEqual(report["action"], "already_compliant")

    def test_preexisting_rules_preserved(self) -> None:
        original = (
            "# OS\n.DS_Store\n\n"
            "node_modules/\n*.pyc\n.build-loop/\n"
            "# >>> NavGator safety guard\n.navgator/dirty.json\n# <<< NavGator safety guard\n"
        )
        self.write_gitignore(original)
        self.run_cli("--apply")
        after = self.read_gitignore()
        for line in original.splitlines():
            if line:
                self.assertIn(line, after.splitlines())
        self.assertTrue(after.index(".DS_Store") < after.index(ei.MARKER))

    def test_missing_gitignore_is_created(self) -> None:
        self.assertFalse((self.repo / ".gitignore").exists())
        code, report = self.run_cli("--check")
        self.assertEqual(code, 1)
        self.assertEqual(report["action"], "would_create")
        self.assertFalse((self.repo / ".gitignore").exists(), "--check must not create")
        code, report = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(report["action"], "created")
        self.assertFalse(report["needs_root"], "nothing excludes the root, so no re-open needed")
        body = self.read_gitignore().splitlines()
        self.assertIn("/.build-loop/skills/experimental/", body)
        self.assertIn("!/.build-loop/agents/active/**", body)

    def test_drift_when_blanket_rule_appended_after_block_is_repaired(self) -> None:
        """A later tool appending its own block must not silently re-exclude us."""
        self.write_gitignore(".build-loop/\n")
        self.run_cli("--apply")
        self.write_gitignore(self.read_gitignore() + _BACKLOG_BLOCK)
        code, _ = self.run_cli("--check")
        self.assertEqual(code, 1, "trailing /.build-loop/* is drift")
        self.run_cli("--apply")
        lines = [ln.strip() for ln in self.read_gitignore().splitlines()]
        self.assertEqual(lines.count(ei.MARKER), 1)
        marker_at = lines.index(ei.MARKER)
        self.assertNotIn("/.build-loop/*", lines[marker_at:], "block must now be last")
        self.assertIn("!/.build-loop/backlog/**", lines[:marker_at], "backlog rules preserved")

    def test_malformed_gitignore_fails_soft(self) -> None:
        (self.repo / ".gitignore").write_bytes(b"\xff\xfe\x00bad\x80bytes\n")
        code, report = self.run_cli("--apply")
        self.assertEqual(code, 0, "soft error must not block a caller")
        self.assertFalse(report["ok"])
        self.assertEqual(report["action"], "error")
        self.assertEqual(report["reason"], "gitignore_unreadable")
        self.assertEqual((self.repo / ".gitignore").read_bytes(), b"\xff\xfe\x00bad\x80bytes\n")

    def test_gitignore_as_directory_fails_soft(self) -> None:
        (self.repo / ".gitignore").mkdir()
        code, report = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "gitignore_not_a_regular_file")

    def test_missing_workdir_fails_soft(self) -> None:
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ei.main(["--workdir", str(self.repo / "nope"), "--apply"])
        report = json.loads(buf.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "workdir_not_a_directory")

    def test_plain_output_renders_without_json(self) -> None:
        import contextlib
        import io

        self.write_gitignore(".build-loop/\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ei.main(["--workdir", str(self.repo), "--apply", "--plain"])
        out = buf.getvalue()
        self.assertIn("applied", out)
        self.assertIn("!/.build-loop/skills/active/**", out)
        self.assertNotIn("{", out)


@unittest.skipIf(_GIT is None, "git not on PATH")
class TestGitBehaviour(_Base):
    """Prove the ordering against git itself, not against our reading of it."""

    def setUp(self) -> None:
        super().setUp()
        subprocess.run([_GIT, "init", "-q", str(self.repo)], check=True, capture_output=True)
        for tier in ("skills", "agents"):
            for state in ("experimental", "active"):
                d = self.repo / ".build-loop" / tier / state / "demo"
                d.mkdir(parents=True)
                (d / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
        items = self.repo / ".build-loop" / "backlog" / "items"
        items.mkdir(parents=True)
        (items / "BUIL-DEMO-1.md").write_text("---\nid: BUIL-DEMO-1\n---\n", encoding="utf-8")
        (self.repo / ".build-loop" / "state.json").write_text("{}", encoding="utf-8")

    def ignored(self, rel: str) -> bool:
        proc = subprocess.run(
            [_GIT, "-C", str(self.repo), "check-ignore", "-q", rel], capture_output=True
        )
        self.assertIn(proc.returncode, (0, 1), proc.stderr.decode())
        return proc.returncode == 0

    def assert_lifecycle(self) -> None:
        for tier in ("skills", "agents"):
            self.assertTrue(
                self.ignored(f".build-loop/{tier}/experimental/demo/SKILL.md"),
                f"{tier}/experimental must be ignored",
            )
            self.assertFalse(
                self.ignored(f".build-loop/{tier}/active/demo/SKILL.md"),
                f"{tier}/active must be TRACKED so promotion shows in git status",
            )

    def test_bare_blanket_ignore_repo(self) -> None:
        self.write_gitignore(".build-loop/\n")
        self.run_cli("--apply")
        self.assert_lifecycle()
        self.assertTrue(self.ignored(".build-loop/state.json"), "other runtime state stays ignored")

    def test_composes_with_backlog_block(self) -> None:
        self.write_gitignore(".build-loop/\n" + _BACKLOG_BLOCK)
        self.run_cli("--apply")
        self.assert_lifecycle()
        self.assertFalse(
            self.ignored(".build-loop/backlog/items/BUIL-DEMO-1.md"),
            "backlog must stay tracked",
        )
        self.assertTrue(self.ignored(".build-loop/state.json"))

    def test_repo_with_no_buildloop_rule_at_all(self) -> None:
        self.write_gitignore("node_modules/\n")
        self.run_cli("--apply")
        self.assert_lifecycle()

    def test_promotion_makes_the_artifact_appear_in_git_status(self) -> None:
        self.write_gitignore(".build-loop/\n" + _BACKLOG_BLOCK)
        self.run_cli("--apply")
        src = self.repo / ".build-loop" / "skills" / "experimental" / "draft"
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text("---\nname: draft\n---\n", encoding="utf-8")

        def status() -> str:
            # -uall: without it git collapses a wholly-untracked directory to a
            # single `?? .build-loop/` entry and the per-file signal is lost.
            return subprocess.run(
                [_GIT, "-C", str(self.repo), "status", "--porcelain", "-uall"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout

        self.assertNotIn("skills/experimental/draft", status())
        dest = self.repo / ".build-loop" / "skills" / "active" / "draft"
        src.rename(dest)
        self.assertIn(".build-loop/skills/active/draft/", status())


if __name__ == "__main__":
    unittest.main()
