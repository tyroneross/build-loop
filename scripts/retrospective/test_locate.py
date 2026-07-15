# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/retrospective/locate."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

sys.path.insert(0, str(HERE.parent.parent))  # scripts/ for temporal_membership
from retrospective import locate  # noqa: E402
from retrospective.locate import cwd_to_slug, find_transcript_for_cwd, sessions_root  # noqa: E402
import temporal_membership as tm  # noqa: E402


class CwdToSlugTests(unittest.TestCase):
    def test_basic_slug(self) -> None:
        self.assertEqual(
            cwd_to_slug("/Users/devuser/dev/git-folder/build-loop"),
            "-Users-devuser-dev-git-folder-build-loop",
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


class FindTranscriptForRunTests(unittest.TestCase):
    """RCA 2026-07-11: the run-scoped locator must attach ONLY a temporally +
    host-matching transcript, and emit an explicit absence marker otherwise
    (never substitute the nearest-in-time-but-wrong transcript)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake_home = Path(self.tmp.name)
        self.home_patch = patch(
            "retrospective.locate.Path.home", return_value=self.fake_home
        )
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.cwd = Path("/Users/test/proj")
        self.slug = "-Users-test-proj"
        self.proj_dir = self.fake_home / ".claude" / "projects" / self.slug
        self.proj_dir.mkdir(parents=True)

    def _write_tx(self, name: str, timestamps: list[str]) -> Path:
        f = self.proj_dir / name
        lines = [
            json.dumps({"type": "user", "timestamp": ts,
                        "message": {"role": "user", "content": "hi"}})
            for ts in timestamps
        ]
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return f

    def test_right_window_transcript_attaches(self) -> None:
        self._write_tx("s1.jsonl", ["2026-07-10T09:00:00Z", "2026-07-10T10:00:00Z"])
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        path, reason = locate.find_transcript_for_run(
            self.cwd, run_start=ws, run_end=we, run_host="claude_code",
        )
        self.assertIsNotNone(path)
        self.assertIsNone(reason)

    def test_wrong_window_rejected_with_marker(self) -> None:
        # The observed stale span: 2026-06-12 .. 2026-06-20; run is 2026-07-10.
        self._write_tx("stale.jsonl", ["2026-06-12T01:04:02Z", "2026-06-20T14:47:07Z"])
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        path, reason = locate.find_transcript_for_run(
            self.cwd, run_start=ws, run_end=we, run_host="claude_code",
        )
        self.assertIsNone(path)
        self.assertIn("no transcript for this run", reason)
        self.assertIn("stale by", reason)

    def test_codex_host_run_with_claude_transcript_is_absence(self) -> None:
        # A time-overlapping Claude transcript EXISTS, but the run is codex-hosted.
        # Host mismatch → explicit absence, ZERO substitution.
        self._write_tx("s.jsonl", ["2026-07-10T09:00:00Z"])
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        path, reason = locate.find_transcript_for_run(
            self.cwd, run_start=ws, run_end=we, run_host="codex",
        )
        self.assertIsNone(path)
        self.assertIn("host=codex", reason)

    def test_no_transcript_dir_is_absence_marker(self) -> None:
        # Different cwd → no slug dir at all.
        other = Path("/Users/test/other")
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        path, reason = locate.find_transcript_for_run(
            other, run_start=ws, run_end=we, run_host="claude_code",
        )
        self.assertIsNone(path)
        self.assertIn("no transcript for this run", reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
