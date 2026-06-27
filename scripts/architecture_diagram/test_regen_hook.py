#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the architecture-diagram pre-commit auto-regen hook.

Covers the load-bearing behaviour: glob matching (each source class + the
no-cross-/ rule), fast-skip when no source is staged, real regen+stage in a
throwaway git repo, the BL_ARCH_NO_REGEN opt-out, and the fail-open contract.
"""
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

import regen_hook

REAL_REPO = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Pure glob-matcher tests (no git needed)
# ---------------------------------------------------------------------------

class TestGlobMatcher(unittest.TestCase):
    CASES = [
        ("scripts/**/*.py", "scripts/a/b.py", True),
        ("scripts/**/*.py", "scripts/x.py", True),
        ("scripts/**/*.py", "scripts/architecture_diagram/regen_hook.py", True),
        ("scripts/**/*.py", "docs/x.py", False),
        ("skills/**/SKILL.md", "skills/a/b/SKILL.md", True),
        ("skills/**/SKILL.md", "skills/SKILL.md", True),
        ("skills/**/SKILL.md", "skills/a/notes.md", False),
        ("agents/*.md", "agents/x.md", True),
        ("agents/*.md", "agents/sub/x.md", False),  # * must not cross '/'
        ("hooks/hooks.json", "hooks/hooks.json", True),
        ("hooks/hooks.json", "hooks/other.json", False),
        ("architecture/ARCHITECTURE.md", "architecture/ARCHITECTURE.md", True),
    ]

    def test_glob_cases(self):
        import re
        for glob, path, expected in self.CASES:
            got = bool(re.match(regen_hook._glob_to_re(glob), path))
            self.assertEqual(got, expected, f"{glob} vs {path}: got {got}, want {expected}")

    def test_matches_source_picks_only_sources(self):
        globs = ["agents/*.md", "scripts/**/*.py"]
        staged = ["agents/foo.md", "README.md", "scripts/a/b.py", "docs/x.html"]
        self.assertEqual(
            regen_hook._matches_source(staged, globs),
            ["agents/foo.md", "scripts/a/b.py"],
        )


# ---------------------------------------------------------------------------
# Source-glob derivation: must come from generate.py (single source of truth)
# ---------------------------------------------------------------------------

class TestSourceGlobs(unittest.TestCase):
    def test_globs_derived_from_generate(self):
        globs = regen_hook._source_globs(REAL_REPO)
        # The four auto-discovered inventories + the authored flow doc.
        for expected in ("agents/*.md", "skills/**/SKILL.md", "scripts/**/*.py",
                         "hooks/hooks.json", "architecture/ARCHITECTURE.md"):
            self.assertIn(expected, globs, f"{expected} missing from derived globs")

    def test_outputs_derived_from_generate(self):
        """Staged outputs are the single source of truth in generate.py — not a
        local duplicate (closes the DRY-parity gap on the OUTPUT side)."""
        import sys
        sys.path.insert(0, str(REAL_REPO / "scripts" / "architecture_diagram"))
        import generate
        self.assertEqual(set(regen_hook._generated_outputs()), set(generate.OUTPUTS))


# ---------------------------------------------------------------------------
# Behaviour in a throwaway git repo
# ---------------------------------------------------------------------------

def _run_git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _mk_repo(tmp: Path) -> Path:
    """A minimal repo for testing the HOOK's detect+stage logic.

    generate.py itself is exercised by test_drift_lint.py; here ``_generate`` is
    monkeypatched to a fake that mutates an output, so this fixture only needs
    the source dirs + a seeded output to diff against.
    """
    _run_git(tmp, "init", "-q")
    _run_git(tmp, "config", "user.email", "t@t")
    _run_git(tmp, "config", "user.name", "t")
    (tmp / "agents").mkdir()
    (tmp / "agents" / "foo.md").write_text("---\nname: foo\n---\nbody\n")
    (tmp / "scripts").mkdir()
    (tmp / "architecture").mkdir()
    (tmp / "architecture" / "model.json").write_text('{"v": 1}\n')
    _run_git(tmp, "add", "-A")
    _run_git(tmp, "commit", "-qm", "seed")
    return tmp


def _fake_generate(repo: Path) -> None:
    """Stand-in for generate.py: bump model.json so there's a real diff to stage."""
    (repo / "architecture" / "model.json").write_text('{"v": 2, "regenerated": true}\n')


class TestRegenBehaviour(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.repo = _mk_repo(Path(self._td.name))
        self._real_generate = regen_hook._generate
        regen_hook._generate = _fake_generate

    def tearDown(self):
        regen_hook._generate = self._real_generate
        self._td.cleanup()

    def test_skip_when_no_staged_files(self):
        res = regen_hook.run(self.repo)
        self.assertEqual(res["action"], "skipped")
        self.assertIn("no staged", res["reason"])

    def test_skip_when_non_source_staged(self):
        (self.repo / "README.md").write_text("hi\n")
        _run_git(self.repo, "add", "README.md")
        res = regen_hook.run(self.repo)
        self.assertEqual(res["action"], "skipped")
        self.assertEqual(res["reason"], "no diagram-source change")

    def test_regen_when_agent_changed(self):
        # Mutate a source file and stage it -> the hook should regen + stage outputs.
        (self.repo / "agents" / "bar.md").write_text(
            "---\nname: bar\nmodel: haiku\ndescription: y\n---\nbody\n")
        _run_git(self.repo, "add", "agents/bar.md")
        res = regen_hook.run(self.repo)
        self.assertEqual(res["action"], "regenerated", res)
        self.assertIn("agents/bar.md", res["triggered_by"])
        # model.json must now be staged with the regenerated content.
        staged_now = _run_git(self.repo, "diff", "--cached", "--name-only").splitlines()
        self.assertIn("architecture/model.json", staged_now)

    def test_dry_run_does_not_write(self):
        (self.repo / "scripts" / "new.py").write_text('"""new."""\n')
        _run_git(self.repo, "add", "scripts/new.py")
        before = (self.repo / "architecture" / "model.json").read_text()
        res = regen_hook.run(self.repo, dry_run=True)
        self.assertEqual(res["action"], "would-regen")
        self.assertEqual((self.repo / "architecture" / "model.json").read_text(), before)

    def test_no_regen_env_opt_out(self):
        (self.repo / "agents" / "bar.md").write_text(
            "---\nname: bar\nmodel: haiku\ndescription: y\n---\nbody\n")
        _run_git(self.repo, "add", "agents/bar.md")
        os.environ["BL_ARCH_NO_REGEN"] = "1"
        try:
            res = regen_hook.run(self.repo)
        finally:
            del os.environ["BL_ARCH_NO_REGEN"]
        self.assertEqual(res["action"], "skipped")
        self.assertIn("BL_ARCH_NO_REGEN", res["reason"])

    def test_main_always_exits_zero_on_error(self):
        # Point at a non-repo dir -> internal error -> fail-open exit 0.
        code = regen_hook.main(["--repo", str(self.repo / "does-not-exist")])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
