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


class CrossSlugResolutionTests(unittest.TestCase):
    """The 2026-07-21 defect: a run driven from an orchestrator cwd writes its
    transcript under the ORCHESTRATOR's slug, so the target repo's slug is empty
    and the run gets zero transcript signal.

    Observed on the reporting machine: the target repo's slug dir held 0 jsonl
    while the orchestrator's slug held 150.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake_home = Path(self.tmp.name)
        self.home_patch = patch(
            "retrospective.locate.Path.home", return_value=self.fake_home
        )
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

        # The repo the run TARGETS — its slug dir exists but is empty, exactly
        # like the observed failure.
        self.target = Path("/Users/test/dev/target-repo")
        self.target_dir = self.fake_home / ".claude" / "projects" / "-Users-test-dev-target-repo"
        self.target_dir.mkdir(parents=True)
        # The cwd the session was actually STARTED in.
        self.driver_dir = self.fake_home / ".claude" / "projects" / "-Users-test"
        self.driver_dir.mkdir(parents=True)

    def _write_tx(self, directory: Path, name: str, cwds: list[str],
                  timestamps: list[str]) -> Path:
        """Write a transcript whose records carry the given top-level cwds."""
        f = directory / name
        lines = []
        for i, cwd in enumerate(cwds):
            lines.append(json.dumps({
                "type": "user", "cwd": cwd,
                "timestamp": timestamps[i % len(timestamps)],
                "message": {"role": "user", "content": "hi"},
            }, separators=(",", ":")))
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return f

    # -- Class A mutation test (b): fails against pre-fix code ---------------

    def test_cwd_attested_transcript_under_another_slug_resolves(self) -> None:
        """PRE-FIX: returns (None, marker) — candidates came only from the cwd slug."""
        tx = self._write_tx(
            self.driver_dir, "sess-1.jsonl",
            [str(self.target)] * 20,
            ["2026-07-10T09:00:00Z", "2026-07-10T10:00:00Z"],
        )
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        path, reason = locate.find_transcript_for_run(
            self.target, run_start=ws, run_end=we, run_host="claude_code",
        )
        self.assertEqual(path, tx, "cwd-attested transcript under another slug must resolve")
        self.assertIsNone(reason)

    # -- Class A mutation test (k): the share gate ---------------------------

    def test_minority_attestation_is_rejected(self) -> None:
        """A transcript that is 2.8% this repo must NOT attach to it.

        Calibrated on a real measurement: one transcript carried
        /…/TruePace 2813x (97.2%) and /Users/<user> 81x (2.8%). An existential
        "first match wins" gate would hand that 97%-other-repo transcript to the
        2.8% repo's retrospective — the nearest-but-wrong defect class again.
        """
        minority = Path("/Users/test/dev/other-repo")
        (self.fake_home / ".claude" / "projects" / "-Users-test-dev-other-repo").mkdir()
        # 97 records for target-repo, 3 for other-repo => other-repo share 3%.
        self._write_tx(
            self.driver_dir, "sess-mixed.jsonl",
            [str(self.target)] * 97 + [str(minority)] * 3,
            ["2026-07-10T09:00:00Z"],
        )
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})

        path, _ = locate.find_transcript_for_run(
            minority, run_start=ws, run_end=we, run_host="claude_code",
        )
        self.assertIsNone(path, "3% attestation must not attach the transcript")

        # The dominant repo still resolves from the same file.
        path2, _ = locate.find_transcript_for_run(
            self.target, run_start=ws, run_end=we, run_host="claude_code",
        )
        self.assertIsNotNone(path2, "97% attestation must still attach")

    def test_share_is_measured_not_presence(self) -> None:
        tx = self._write_tx(
            self.driver_dir, "sess-share.jsonl",
            [str(self.target)] * 75 + ["/Users/test/elsewhere"] * 25,
            ["2026-07-10T09:00:00Z"],
        )
        self.assertAlmostEqual(locate.transcript_cwd_share(tx, self.target), 0.75, places=3)
        self.assertAlmostEqual(
            locate.transcript_cwd_share(tx, "/Users/test/elsewhere"), 0.25, places=3)
        self.assertEqual(locate.transcript_cwd_share(tx, "/Users/test/never"), 0.0)

    # -- Class B guards ------------------------------------------------------

    def test_non_attesting_transcript_is_rejected(self) -> None:
        self._write_tx(
            self.driver_dir, "sess-other.jsonl",
            ["/Users/test/dev/unrelated"] * 10,
            ["2026-07-10T09:00:00Z"],
        )
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        path, reason = locate.find_transcript_for_run(
            self.target, run_start=ws, run_end=we, run_host="claude_code",
        )
        self.assertIsNone(path)
        self.assertIn("no transcript for this run", reason)

    def test_attesting_but_out_of_window_is_rejected(self) -> None:
        """Attestation alone is not enough — the time gate still applies."""
        self._write_tx(
            self.driver_dir, "sess-stale.jsonl",
            [str(self.target)] * 20,
            ["2026-06-12T01:04:02Z", "2026-06-20T14:47:07Z"],
        )
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        path, reason = locate.find_transcript_for_run(
            self.target, run_start=ws, run_end=we, run_host="claude_code",
        )
        self.assertIsNone(path)
        self.assertIn("stale by", reason)

    def test_embedded_cwd_in_payload_does_not_attest(self) -> None:
        """A `"cwd":"<path>"` inside a tool payload is not the record's own cwd."""
        f = self.driver_dir / "sess-embedded.jsonl"
        rows = [
            json.dumps({
                "type": "assistant", "cwd": "/Users/test/dev/unrelated",
                "timestamp": "2026-07-10T09:00:00Z",
                # The needle appears, but nested inside tool input — not top-level.
                "toolUse": {"input": {"payload": f'"cwd":"{self.target}"'}},
            }, separators=(",", ":"))
            for _ in range(10)
        ]
        f.write_text("\n".join(rows) + "\n", encoding="utf-8")
        self.assertEqual(locate.transcript_cwd_share(f, self.target), 0.0)


class SessionIdResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake_home = Path(self.tmp.name)
        self.home_patch = patch(
            "retrospective.locate.Path.home", return_value=self.fake_home
        )
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.projects = self.fake_home / ".claude" / "projects"
        self.driver = self.projects / "-Users-test"
        self.driver.mkdir(parents=True)
        (self.projects / "-Users-test-dev-target-repo").mkdir(parents=True)
        self.sid = "82ab7452-e556-48ae-9dcb-31332b50e295"
        self.tx = self.driver / f"{self.sid}.jsonl"
        self.tx.write_text(json.dumps({"type": "user", "timestamp": "2026-07-10T09:00:00Z"}) + "\n")

    def test_exact_session_id_resolves_across_slugs(self) -> None:
        self.assertEqual(locate.find_transcript_by_session_id(self.sid), self.tx)

    def test_rally_tool_id_resolves_via_hex_prefix(self) -> None:
        """A Rally tool id (`fable-82ab7452`) carries the uuid prefix, not the uuid."""
        self.assertEqual(locate.find_transcript_by_session_id("fable-82ab7452"), self.tx)

    def test_short_prefix_is_refused(self) -> None:
        """Uniqueness is not correctness — a 3-char token must never match."""
        self.assertIsNone(locate.find_transcript_by_session_id("82a"))
        self.assertIsNone(locate.find_transcript_by_session_id("bed"))

    def test_ambiguous_prefix_is_refused(self) -> None:
        (self.driver / "82ab7452-0000-0000-0000-000000000000.jsonl").write_text("{}\n")
        self.assertIsNone(locate.find_transcript_by_session_id("fable-82ab7452"))

    def test_glob_metacharacters_are_refused(self) -> None:
        for bad in ("*", "?", "../etc", "a[b]c", "x/y"):
            self.assertIsNone(locate.find_transcript_by_session_id(bad), bad)

    def test_empty_or_none_session_id_is_none(self) -> None:
        self.assertIsNone(locate.find_transcript_by_session_id(None))
        self.assertIsNone(locate.find_transcript_by_session_id("   "))

    def test_explicit_session_id_skips_time_gate(self) -> None:
        """An explicitly-passed id is asserted identity, like `transcript=`."""
        target = Path("/Users/test/dev/target-repo")
        ws, we = tm.run_window({"date": "2027-01-01T00:00:00Z"})  # far from the tx
        path, _ = locate.find_transcript_for_run(
            target, run_start=ws, run_end=we, run_host="claude_code",
            session_id=self.sid, session_id_is_explicit=True,
        )
        self.assertEqual(path, self.tx)

    def test_derived_session_id_still_obeys_time_gate(self) -> None:
        """A state.json-derived id is a HINT: `started_by_session_id` is immutable
        post-generation, so trusting it without a time check would reopen the
        RCA-2026-07-11 stale-substitution defect."""
        target = Path("/Users/test/dev/target-repo")
        ws, we = tm.run_window({"date": "2027-01-01T00:00:00Z"})
        path, reason = locate.find_transcript_for_run(
            target, run_start=ws, run_end=we, run_host="claude_code",
            session_id=self.sid, session_id_is_explicit=False,
        )
        self.assertIsNone(path, "a stale derived id must not attach out-of-window")
        self.assertIsNotNone(reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
