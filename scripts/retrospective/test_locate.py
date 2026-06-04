# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/retrospective/locate."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from retrospective.locate import cwd_to_slug, find_transcript_for_cwd, sessions_root  # noqa: E402


class CwdToSlugTests(unittest.TestCase):
    def test_basic_slug(self) -> None:
        self.assertEqual(
            cwd_to_slug("/Users/tyroneross/dev/git-folder/build-loop"),
            "-Users-tyroneross-dev-git-folder-build-loop",
        )

    def test_relative_path_resolves_to_absolute(self) -> None:
        # cwd_to_slug calls .resolve() so a relative arg becomes absolute first.
        s = cwd_to_slug(".")
        self.assertTrue(s.startswith("-"))
        self.assertIn("-", s)

    def test_no_trailing_slash(self) -> None:
        s1 = cwd_to_slug("/a/b/c")
        s2 = cwd_to_slug("/a/b/c/")
        self.assertEqual(s1, s2)


class FindTranscriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake_home = Path(self.tmp.name)
        # Patch Path.home so sessions_root() resolves into the tmp tree.
        self.home_patch = patch(
            "retrospective.locate.Path.home", return_value=self.fake_home
        )
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        # The cwd we'll pretend belongs to this run.
        self.cwd = Path("/Users/test/proj")
        self.slug = "-Users-test-proj"
        self.proj_dir = self.fake_home / ".claude" / "projects" / self.slug
        self.proj_dir.mkdir(parents=True)

    def test_returns_none_when_no_dir(self) -> None:
        # Remove the dir to simulate no-transcript-yet.
        for f in self.proj_dir.iterdir():
            f.unlink()
        self.proj_dir.rmdir()
        self.assertIsNone(find_transcript_for_cwd(self.cwd))

    def test_returns_none_when_dir_empty(self) -> None:
        self.assertIsNone(find_transcript_for_cwd(self.cwd))

    def test_returns_most_recent_jsonl(self) -> None:
        older = self.proj_dir / "uuid-1.jsonl"
        newer = self.proj_dir / "uuid-2.jsonl"
        older.write_text("{}\n")
        time.sleep(0.05)  # ensure mtime differs
        newer.write_text("{}\n")
        # Touch newer just to be safe.
        now = time.time()
        os.utime(older, (now - 100, now - 100))
        os.utime(newer, (now, now))
        result = find_transcript_for_cwd(self.cwd)
        self.assertEqual(result, newer)

    def test_ignores_non_jsonl_files(self) -> None:
        (self.proj_dir / "uuid.jsonl").write_text("{}\n")
        (self.proj_dir / "readme.md").write_text("ignored")
        result = find_transcript_for_cwd(self.cwd)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "uuid.jsonl")

    def test_never_raises_on_oserror(self) -> None:
        # Pass a path that resolves to something nonsensical; locate must return None.
        try:
            r = find_transcript_for_cwd("/nonexistent/probably/never/exists/zzz")
        except Exception as e:  # noqa: BLE001
            self.fail(f"locate raised: {e!r}")
        # Slug-derivation for a missing path is still valid; the proj dir won't exist.
        self.assertIsNone(r)


class SessionsRootTests(unittest.TestCase):
    def test_sessions_root_under_home(self) -> None:
        r = sessions_root()
        self.assertEqual(r, Path.home() / ".claude" / "projects")


if __name__ == "__main__":
    unittest.main(verbosity=2)
