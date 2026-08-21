#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for atomic_io.py — the shared write/lock primitive.

15 modules import this, so a defect here corrupts state at every call site while
each caller's own tests stay green. The tests below grade the properties that make
the write ATOMIC and DURABLE, not merely "the bytes came back":

  - the temp file is created in the TARGET'S directory. os.replace is only atomic
    within one filesystem; a temp in /tmp silently degrades to a copy that can be
    observed half-written.
  - fsync happens BEFORE replace. Without that ordering the rename can land while
    the data is still in the page cache, and a crash leaves a present-but-empty
    file — the failure this module exists to prevent.
  - a failed write leaves the ORIGINAL intact and no .tmp litter behind.

flock semantics were measured on this platform rather than assumed: a second
exclusive lock raises BlockingIOError both in-process (separate fds) and
cross-process.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import atomic_io  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.target = self.dir / "state.json"


class TestAtomicWrite(Base):
    def test_writes_the_bytes(self):
        atomic_io.atomic_write_bytes(self.target, b'{"a": 1}')
        self.assertEqual(self.target.read_bytes(), b'{"a": 1}')

    def test_creates_missing_parent_directories(self):
        nested = self.dir / "a" / "b" / "c.json"
        atomic_io.atomic_write_bytes(nested, b"x")
        self.assertEqual(nested.read_bytes(), b"x")

    def test_overwrite_replaces_content_entirely(self):
        atomic_io.atomic_write_bytes(self.target, b"aaaaaaaaaa")
        atomic_io.atomic_write_bytes(self.target, b"bb")
        self.assertEqual(self.target.read_bytes(), b"bb", "stale tail survived the overwrite")

    def test_leaves_no_temp_files_behind(self):
        atomic_io.atomic_write_bytes(self.target, b"x")
        leftovers = [p.name for p in self.dir.iterdir() if ".tmp." in p.name]
        self.assertEqual(leftovers, [])

    def test_temp_file_is_created_in_the_target_directory(self):
        """os.replace is atomic only within a filesystem. A temp created elsewhere
        (e.g. /tmp) degrades the rename to a copy that a reader can catch
        half-written — the whole guarantee, silently gone."""
        seen: list[str] = []
        real = tempfile.mkstemp

        def spy(*a, **kw):
            seen.append(kw.get("dir"))
            return real(*a, **kw)

        with mock.patch.object(atomic_io.tempfile, "mkstemp", side_effect=spy):
            atomic_io.atomic_write_bytes(self.target, b"x")
        self.assertEqual(seen, [str(self.target.parent)])

    def test_fsync_runs_before_replace(self):
        """Ordering is the durability guarantee. Replace-then-fsync can leave a
        present-but-empty file after a crash."""
        order: list[str] = []
        with mock.patch.object(atomic_io.os, "fsync", side_effect=lambda fd: order.append("fsync")), \
             mock.patch.object(atomic_io.os, "replace",
                               side_effect=lambda a, b: (order.append("replace"), os.rename(a, b))[0]):
            atomic_io.atomic_write_bytes(self.target, b"x")
        self.assertEqual(order, ["fsync", "replace"], f"bad ordering: {order}")

    def test_failed_write_preserves_the_original(self):
        atomic_io.atomic_write_bytes(self.target, b"ORIGINAL")
        with mock.patch.object(atomic_io.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic_io.atomic_write_bytes(self.target, b"NEW")
        self.assertEqual(self.target.read_bytes(), b"ORIGINAL",
                         "a failed write destroyed the previous good content")

    def test_failed_write_cleans_up_its_temp_file(self):
        with mock.patch.object(atomic_io.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic_io.atomic_write_bytes(self.target, b"NEW")
        leftovers = [p.name for p in self.dir.iterdir() if ".tmp." in p.name]
        self.assertEqual(leftovers, [], "failed write littered a temp file")


class TestLockedFile(Base):
    def test_lock_path_is_the_target_plus_lock(self):
        lf = atomic_io.LockedFile(self.target)
        self.assertEqual(lf.lock_path, self.dir / "state.json.lock")

    def test_acquires_and_releases(self):
        with atomic_io.LockedFile(self.target):
            pass
        with atomic_io.LockedFile(self.target):
            pass  # a leaked lock would make this hang then raise

    def test_second_holder_times_out(self):
        """Measured: a second exclusive flock raises BlockingIOError even in-process."""
        with atomic_io.LockedFile(self.target):
            with self.assertRaises(TimeoutError):
                with atomic_io.LockedFile(self.target, timeout_s=0.1):
                    pass

    def test_timeout_does_not_leak_the_descriptor(self):
        with atomic_io.LockedFile(self.target):
            second = atomic_io.LockedFile(self.target, timeout_s=0.1)
            with self.assertRaises(TimeoutError):
                second.__enter__()
            self.assertIsNone(second._fd, "descriptor leaked after a failed acquire")

    def test_lock_is_released_when_the_body_raises(self):
        with self.assertRaises(ValueError):
            with atomic_io.LockedFile(self.target):
                raise ValueError("boom")
        with atomic_io.LockedFile(self.target, timeout_s=0.1):
            pass  # would time out if the exception path skipped release

    def test_creates_the_lock_directory(self):
        deep = self.dir / "x" / "y" / "state.json"
        with atomic_io.LockedFile(deep):
            self.assertTrue((self.dir / "x" / "y" / "state.json.lock").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
