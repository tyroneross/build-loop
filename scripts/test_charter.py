#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for charter.py — canonical/mirror/hash-drift sync (WP-F/F3).

Stdlib only. Isolates the memory root by monkeypatching the resolver so no test
touches the real build-loop-memory store.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import charter  # noqa: E402


class CharterSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workdir = self.root / "repo"
        self.mem = self.root / "memory"
        (self.workdir / ".build-loop").mkdir(parents=True)
        # Patch the path resolver to point at the temp memory + repo, slug=demo.
        self._orig = charter._resolve_paths
        canonical = self.mem / "projects" / "demo" / "charter.md"
        mirror = self.workdir / ".build-loop" / "charter.md"
        charter._resolve_paths = lambda wd, slug=None: (canonical, mirror)  # type: ignore
        self.canonical = canonical
        self.mirror = mirror

    def tearDown(self) -> None:
        charter._resolve_paths = self._orig  # type: ignore
        self.tmp.cleanup()

    def test_noop_when_no_charter(self) -> None:
        r = charter.sync(self.workdir)
        self.assertEqual(r["action"], "noop_no_charter")
        self.assertFalse(self.mirror.exists())

    def test_sync_from_canonical_writes_mirror_with_pointer(self) -> None:
        self.canonical.parent.mkdir(parents=True)
        self.canonical.write_text("# Charter\nNorth Star: ship reliably.\n", encoding="utf-8")
        r = charter.sync(self.workdir)
        self.assertEqual(r["action"], "synced_from_canonical")
        mtext = self.mirror.read_text(encoding="utf-8")
        self.assertIn("North Star: ship reliably.", mtext)
        self.assertIn("charter-sync canonical=", mtext)
        self.assertIn("hash=", mtext)

    def test_sync_is_idempotent(self) -> None:
        self.canonical.parent.mkdir(parents=True)
        self.canonical.write_text("# Charter\nbody\n", encoding="utf-8")
        charter.sync(self.workdir)
        first = self.mirror.read_text(encoding="utf-8")
        charter.sync(self.workdir)
        self.assertEqual(first, self.mirror.read_text(encoding="utf-8"))

    def test_user_edit_to_mirror_promotes_to_canonical(self) -> None:
        self.canonical.parent.mkdir(parents=True)
        self.canonical.write_text("# Charter\noriginal canonical body\n", encoding="utf-8")
        charter.sync(self.workdir)  # writes mirror with pointer+hash
        # User hand-edits the mirror body (hash now mismatches the recorded one).
        mtext = self.mirror.read_text(encoding="utf-8")
        edited = mtext.replace("original canonical body", "USER EDITED body")
        self.mirror.write_text(edited, encoding="utf-8")
        r = charter.sync(self.workdir)
        self.assertEqual(r["action"], "promoted_user_edit")
        canon = self.canonical.read_text(encoding="utf-8")
        self.assertIn("USER EDITED body", canon)
        self.assertIn("authored_by: user", canon)

    def test_status_reports_user_edit(self) -> None:
        self.canonical.parent.mkdir(parents=True)
        self.canonical.write_text("# Charter\nbody\n", encoding="utf-8")
        charter.sync(self.workdir)
        mtext = self.mirror.read_text(encoding="utf-8")
        self.mirror.write_text(mtext.replace("body", "tampered"), encoding="utf-8")
        st = charter.status(self.workdir)
        self.assertTrue(st["mirror_user_edited"])

    def test_create_seeds_from_template_when_missing(self) -> None:
        if not charter.CHARTER_TEMPLATE.is_file():
            self.skipTest("charter template absent")
        r = charter.create(self.workdir)
        self.assertEqual(r["action"], "created")
        self.assertTrue(self.canonical.is_file())
        self.assertIn("Project Charter", self.canonical.read_text(encoding="utf-8"))

    def test_create_is_noop_when_exists(self) -> None:
        self.canonical.parent.mkdir(parents=True)
        self.canonical.write_text("# Charter\n", encoding="utf-8")
        r = charter.create(self.workdir)
        self.assertEqual(r["action"], "exists")


if __name__ == "__main__":
    unittest.main(verbosity=2)
