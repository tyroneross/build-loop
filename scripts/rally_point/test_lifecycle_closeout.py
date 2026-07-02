#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``lifecycle.resolve_addressed_handoffs``.

Stdlib only; no real ``rally`` process is ever invoked — ``subprocess.run``
is mocked in every test. Run from repo root:
    python3 scripts/rally_point/test_lifecycle_closeout.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from rally_point import lifecycle  # noqa: E402


def _completed(returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["rally"], returncode=returncode, stdout="{}", stderr=""
    )


class ResolveAddressedHandoffsTests(unittest.TestCase):
    def setUp(self) -> None:
        # Pin the resolved binary so tests never depend on PATH/discovery.
        self._bin_patch = mock.patch.object(
            lifecycle, "_rally_binary", return_value="rally"
        )
        self._bin_patch.start()

    def tearDown(self) -> None:
        self._bin_patch.stop()

    def test_calls_say_receipt_once_per_id(self):
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            with mock.patch.object(
                lifecycle.subprocess, "run", return_value=_completed(0)
            ) as run_mock:
                result = lifecycle.resolve_addressed_handoffs(
                    repo_root, "claude_code", ["evt-1", "evt-2"]
                )

            self.assertEqual(run_mock.call_count, 2)
            self.assertEqual(result, ["evt-1", "evt-2"])

            for call, expected_ref in zip(run_mock.call_args_list, ["evt-1", "evt-2"]):
                args = call.args[0]
                self.assertEqual(args[0], "rally")
                self.assertIn("say", args)
                self.assertIn("receipt", args)
                self.assertIn("--ref", args)
                self.assertEqual(args[args.index("--ref") + 1], expected_ref)
                self.assertIn("--tool", args)
                self.assertEqual(args[args.index("--tool") + 1], "claude_code")
                self.assertEqual(call.kwargs.get("cwd"), str(repo_root))

    def test_dry_run_makes_zero_subprocess_calls(self):
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            with mock.patch.object(lifecycle.subprocess, "run") as run_mock:
                result = lifecycle.resolve_addressed_handoffs(
                    repo_root,
                    "claude_code",
                    ["evt-1", "evt-2", "evt-3"],
                    dry_run=True,
                )

            run_mock.assert_not_called()
            self.assertEqual(result, ["evt-1", "evt-2", "evt-3"])

    def test_failing_id_excluded_others_still_resolve(self):
        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)

            def _side_effect(args, **kwargs):
                ref = args[args.index("--ref") + 1]
                if ref == "evt-bad-exit":
                    return _completed(1)
                if ref == "evt-raises":
                    raise OSError("boom")
                return _completed(0)

            with mock.patch.object(
                lifecycle.subprocess, "run", side_effect=_side_effect
            ) as run_mock:
                result = lifecycle.resolve_addressed_handoffs(
                    repo_root,
                    "claude_code",
                    ["evt-ok-1", "evt-bad-exit", "evt-raises", "evt-ok-2"],
                )

            self.assertEqual(run_mock.call_count, 4)
            self.assertEqual(result, ["evt-ok-1", "evt-ok-2"])
            self.assertNotIn("evt-bad-exit", result)
            self.assertNotIn("evt-raises", result)


if __name__ == "__main__":
    unittest.main()
