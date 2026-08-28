#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for run_registration_gate.py.

The gate's whole value is WHEN IT STAYS QUIET, so most of these assert silence.
A gate that warns on every commit gets muted, and a muted gate is exactly the
failure the gate exists to prevent.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_registration_gate as gate  # noqa: E402


def _make_repo(root: Path, *, with_state: bool = True) -> Path:
    repo = root / "repo"
    (repo / ".build-loop").mkdir(parents=True, exist_ok=True)
    if with_state:
        (repo / ".build-loop" / "state.json").write_text(json.dumps({"runs": []}))
    return repo


class RepoRootTests(unittest.TestCase):
    def test_finds_state_in_ancestor_not_just_cwd(self):
        """A commit issued from a subdirectory still resolves the repo."""
        with mock.patch.object(gate, "_LINT", Path("/nonexistent")):
            import tempfile

            with tempfile.TemporaryDirectory() as td:
                repo = _make_repo(Path(td))
                nested = repo / "a" / "b"
                nested.mkdir(parents=True)
                # _repo_root resolves on purpose (a stable marker key needs a
                # canonical path), and on macOS /var is a symlink to /private/var
                # — so compare resolved against resolved.
                self.assertEqual(gate._repo_root(str(nested)), repo.resolve())

    def test_returns_none_without_build_loop_state(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            plain = Path(td) / "plain"
            plain.mkdir()
            self.assertIsNone(gate._repo_root(str(plain)))


class SilenceTests(unittest.TestCase):
    """Each of these is a case where firing would be wrong."""

    def setUp(self):
        import tempfile

        self._td = tempfile.TemporaryDirectory()
        self.repo = _make_repo(Path(self._td.name))

    def tearDown(self):
        self._td.cleanup()

    def test_silent_when_not_a_build_loop_repo(self):
        import tempfile

        with tempfile.TemporaryDirectory() as other:
            self.assertIsNone(gate.evaluate(other, "s1", mark=False))

    def test_silent_when_run_is_recorded(self):
        with mock.patch.object(gate, "_lint_status", return_value="recorded"):
            self.assertIsNone(gate.evaluate(str(self.repo), "s1", mark=False))

    def test_silent_when_lint_says_skipped(self):
        """'skipped' means nothing ran here — a fresh repo must not be nagged."""
        with mock.patch.object(gate, "_lint_status", return_value="skipped"):
            self.assertIsNone(gate.evaluate(str(self.repo), "s1", mark=False))

    def test_silent_when_lint_is_unavailable(self):
        """A lint that cannot speak must not be upgraded into a verdict."""
        with mock.patch.object(gate, "_lint_status", return_value=None):
            self.assertIsNone(gate.evaluate(str(self.repo), "s1", mark=False))

    def test_warns_only_once_per_session_and_repo(self):
        with mock.patch.object(gate, "_lint_status", return_value="missing"):
            first = gate.evaluate(str(self.repo), "sess-A")
            second = gate.evaluate(str(self.repo), "sess-A")
        self.assertIsNotNone(first)
        self.assertIsNone(second, "a repeated warning is a warning that gets ignored")

    def test_a_different_session_warns_again(self):
        with mock.patch.object(gate, "_lint_status", return_value="missing"):
            gate.evaluate(str(self.repo), "sess-A")
            other = gate.evaluate(str(self.repo), "sess-B")
        self.assertIsNotNone(other)


class WarningContentTests(unittest.TestCase):
    def test_warning_names_the_repo_and_the_remediation(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td))
            with mock.patch.object(gate, "_lint_status", return_value="missing"):
                msg = gate.evaluate(str(repo), "sess-content")
            self.assertIsNotNone(msg)
            assert msg is not None  # narrows for the type checker
            self.assertIn(str(repo), msg)
            self.assertIn(
                "write_run_entry", msg, "a finding without its fix relocates the guesswork"
            )
            self.assertIn("advisory", msg)


class ProcessContractTests(unittest.TestCase):
    """The dispatcher's contract: always JSON on stdout, always exit 0."""

    def _run(self, payload: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(Path(gate.__file__))],
            input=payload,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_malformed_stdin_fails_open(self):
        proc = self._run("not json at all")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "{}")

    def test_empty_stdin_fails_open(self):
        proc = self._run("")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "{}")

    def test_non_dict_payload_fails_open(self):
        proc = self._run(json.dumps([1, 2, 3]))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "{}")

    def test_never_returns_a_blocking_code(self):
        """rc 2 is the dispatcher's hard-block. This gate must never emit it."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td))
            proc = self._run(json.dumps({"cwd": str(repo), "session_id": "sess-rc"}))
        self.assertEqual(proc.returncode, 0)
        self.assertNotEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "{}")


if __name__ == "__main__":
    unittest.main()
