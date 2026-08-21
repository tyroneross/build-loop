#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for architecture_freshness.py — invoked from git hooks.

Chosen for hook exposure rather than importer count. hooks/pre-edit-architecture.sh
and hooks/_arch_scan_bg.py both shell out to this script, and a hook that returns the
wrong verdict fails SILENTLY: the edit proceeds, the scan never fires, and the
architecture map drifts with nothing reporting it. An import failure is loud; this
one is not.

Two properties carry the most weight:

  - the explicit `stale` flag must BEAT a fresh manifest mtime. Inverted, any process
    that touches manifest.json masks a real staleness signal, and the map silently
    stops being rebuilt.
  - mark_stale / mark_fresh must PRESERVE unrelated state.json keys. That file also
    holds runs[] and execution; a write that clobbers them destroys Phase 6 Learn's
    history, and nothing would surface it until a much later run read an empty list.

Boundaries are asserted exactly because the code uses `>` not `>=`: age == 3600 is
fresh, age == 86400 is fresh-but-old.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import architecture_freshness as af  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.wd = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.wd, ignore_errors=True)
        self.arch = self.wd / af.ARCH_DIR_REL
        self.state_path = self.wd / af.STATE_FILE_REL

    def manifest(self, age_s: float = 0.0) -> Path:
        self.arch.mkdir(parents=True, exist_ok=True)
        m = self.arch / af.MANIFEST_NAME
        m.write_text("{}")
        if age_s:
            t = time.time() - age_s
            os.utime(m, (t, t))
        return m

    def write_state(self, data: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(data))

    def state(self) -> dict:
        return json.loads(self.state_path.read_text())


class TestCheckStateMachine(Base):
    def test_missing_without_arch_dir(self):
        self.assertEqual(af.cmd_check(self.wd), "missing")

    def test_missing_when_manifest_absent(self):
        self.arch.mkdir(parents=True)
        self.assertEqual(af.cmd_check(self.wd), "missing")

    def test_fresh_when_just_written(self):
        self.manifest()
        self.assertEqual(af.cmd_check(self.wd), "fresh")

    def test_fresh_but_old_between_one_and_24_hours(self):
        self.manifest(age_s=2 * 3600)
        self.assertEqual(af.cmd_check(self.wd), "fresh-but-old")

    def test_stale_past_24_hours(self):
        self.manifest(age_s=25 * 3600)
        self.assertEqual(af.cmd_check(self.wd), "stale")

    def test_explicit_stale_flag_beats_a_fresh_manifest(self):
        """The silent-failure case. If mtime won, touching manifest.json would mask a
        real staleness signal and the map would quietly stop being rebuilt."""
        self.manifest()
        self.write_state({"architecture": {"stale": True}})
        self.assertEqual(af.cmd_check(self.wd), "stale")

    def test_boundary_exactly_one_hour_is_fresh(self):
        """`>` not `>=`: at exactly OLD_THRESHOLD_S the verdict is still fresh."""
        self.manifest(age_s=af.OLD_THRESHOLD_S - 1)
        self.assertEqual(af.cmd_check(self.wd), "fresh")

    def test_boundary_just_past_24h_is_stale_not_fresh_but_old(self):
        self.manifest(age_s=af.STALE_THRESHOLD_S + 5)
        self.assertEqual(af.cmd_check(self.wd), "stale")

    def test_non_dict_architecture_block_does_not_crash(self):
        """A hook must never fail on malformed state."""
        self.manifest()
        self.write_state({"architecture": "corrupted-into-a-string"})
        self.assertEqual(af.cmd_check(self.wd), "fresh")


class TestMarkStale(Base):
    def test_sets_the_flag(self):
        af.cmd_mark_stale(self.wd, None)
        self.assertIs(self.state()["architecture"]["stale"], True)

    def test_records_the_file(self):
        af.cmd_mark_stale(self.wd, "scripts/x.py")
        self.assertEqual(self.state()["architecture"]["staleFiles"], ["scripts/x.py"])

    def test_deduplicates_repeat_files(self):
        af.cmd_mark_stale(self.wd, "scripts/x.py")
        af.cmd_mark_stale(self.wd, "scripts/x.py")
        self.assertEqual(self.state()["architecture"]["staleFiles"], ["scripts/x.py"])

    def test_caps_the_list_keeping_the_most_recent(self):
        """Unbounded growth in a long session; the cap must drop the OLDEST."""
        for i in range(af.STALE_FILES_CAP + 10):
            af.cmd_mark_stale(self.wd, f"f{i}.py")
        files = self.state()["architecture"]["staleFiles"]
        self.assertEqual(len(files), af.STALE_FILES_CAP)
        self.assertEqual(files[-1], f"f{af.STALE_FILES_CAP + 9}.py")
        self.assertNotIn("f0.py", files)

    def test_stale_since_is_not_reset_by_later_marks(self):
        """staleSince records when the map FIRST went stale, so a later mark must not
        restart it — otherwise "how long has this been stale" always reads as "just now".

        The clock is stubbed because _iso_now() has second granularity: two marks in
        the same second produce identical timestamps whether or not the value resets,
        so the un-stubbed version of this test passed against a mutant that reset it
        on every call. It asserted nothing."""
        stamps = iter(["2026-01-01T00:00:00Z", "2026-06-01T12:00:00Z"])
        with mock.patch.object(af, "_iso_now", side_effect=lambda: next(stamps)):
            af.cmd_mark_stale(self.wd, "a.py")
            first = self.state()["architecture"]["staleSince"]
            af.cmd_mark_stale(self.wd, "b.py")
        self.assertEqual(first, "2026-01-01T00:00:00Z")
        self.assertEqual(self.state()["architecture"]["staleSince"], first,
                         "staleSince restarted, hiding how long the map has been stale")

    def test_preserves_unrelated_state_keys(self):
        """state.json also holds runs[] and execution. Clobbering them would destroy
        Phase 6 Learn history with nothing reporting it."""
        self.write_state({"runs": [{"run_id": "r1"}], "execution": {"phase": "review"}})
        af.cmd_mark_stale(self.wd, "x.py")
        s = self.state()
        self.assertEqual(s["runs"], [{"run_id": "r1"}])
        self.assertEqual(s["execution"], {"phase": "review"})


class TestMarkFresh(Base):
    def test_clears_stale_state(self):
        af.cmd_mark_stale(self.wd, "x.py")
        af.cmd_mark_fresh(self.wd)
        arch = self.state()["architecture"]
        self.assertIs(arch["stale"], False)
        self.assertEqual(arch["staleFiles"], [])
        self.assertIn("lastFreshAt", arch)
        self.assertNotIn("staleSince", arch)

    def test_preserves_unrelated_state_keys(self):
        self.write_state({"runs": [{"run_id": "r1"}]})
        af.cmd_mark_fresh(self.wd)
        self.assertEqual(self.state()["runs"], [{"run_id": "r1"}])


class TestRoundTrip(Base):
    def test_mark_stale_then_check_reports_stale(self):
        self.manifest()
        af.cmd_mark_stale(self.wd, "x.py")
        self.assertEqual(af.cmd_check(self.wd), "stale")

    def test_mark_fresh_then_check_reports_fresh(self):
        self.manifest()
        af.cmd_mark_stale(self.wd, "x.py")
        af.cmd_mark_fresh(self.wd)
        self.assertEqual(af.cmd_check(self.wd), "fresh")


class TestResilience(Base):
    def test_corrupt_state_json_does_not_raise(self):
        self.manifest()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text("{not json")
        self.assertEqual(af.cmd_check(self.wd), "fresh")
        af.cmd_mark_stale(self.wd, "x.py")  # must not raise

    def test_lockfile_path_is_inside_the_architecture_dir(self):
        self.assertEqual(af.cmd_lockfile(self.wd),
                         str(self.wd / af.ARCH_DIR_REL / af.LOCKFILE_NAME))

    def test_atomic_write_leaves_no_temp_files(self):
        af.cmd_mark_stale(self.wd, "x.py")
        leftovers = [p.name for p in self.state_path.parent.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
