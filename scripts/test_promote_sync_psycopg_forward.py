#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""F2: _invoke_sync_navgator_lessons forwards the psycopg warn-once line.

sync_navgator_lessons returns rc==0 even when the optional Postgres mirror is
skipped (postgres_unavailable, e.g. psycopg not installed). It emits a one-time
`[sync_navgator_lessons] psycopg not installed …` notice to its stderr. The
caller used to discard child stderr on rc==0, making that actionable hint
invisible on the production path. This test pins the forward.
"""
from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "promote_violation_to_lesson", HERE / "promote_violation_to_lesson.py"
)
assert _SPEC and _SPEC.loader
pv = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("promote_violation_to_lesson", pv)
_SPEC.loader.exec_module(pv)


def _fake_run(stderr_text: str, rc: int = 0):
    def _run(cmd, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr=stderr_text)
    return _run


class PsycopgForwardTests(unittest.TestCase):
    def setUp(self) -> None:
        # The function early-returns False if the sibling script is absent;
        # the real script ships in scripts/, so existence holds.
        self.assertTrue((HERE / "sync_navgator_lessons.py").exists())

    def test_psycopg_warn_forwarded_on_rc0(self) -> None:
        warn = (
            "[sync_navgator_lessons] psycopg not installed — skipping the "
            "optional Postgres mirror (SQLite remains the source of truth)."
        )
        buf = io.StringIO()
        with mock.patch.object(subprocess, "run", _fake_run(warn, rc=0)):
            with redirect_stderr(buf):
                ok = pv._invoke_sync_navgator_lessons(
                    workdir=HERE, lessons_path=HERE / "x.md", dry_run=False
                )
        self.assertTrue(ok)
        self.assertIn("psycopg not installed", buf.getvalue())

    def test_non_psycopg_stderr_not_forwarded_on_rc0(self) -> None:
        """Only psycopg lines forward; unrelated chatter stays quiet on success."""
        buf = io.StringIO()
        with mock.patch.object(
            subprocess, "run", _fake_run("[sync_navgator_lessons] upserted 4 lessons\n", rc=0)
        ):
            with redirect_stderr(buf):
                ok = pv._invoke_sync_navgator_lessons(
                    workdir=HERE, lessons_path=HERE / "x.md", dry_run=False
                )
        self.assertTrue(ok)
        self.assertNotIn("upserted", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
