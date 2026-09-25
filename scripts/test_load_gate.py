#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/load_gate.py.

Never take the real ~/.build-loop lock and never depend on actual machine
load: every test passes an explicit tempdir --lock path and monkeypatches
load_gate.get_loadavg().
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import load_gate  # noqa: E402


class GetJobsEnvTests(unittest.TestCase):
    def test_sets_all_three_job_vars(self) -> None:
        env = load_gate._jobs_env(7)
        self.assertEqual(env["CARGO_BUILD_JOBS"], "7")
        self.assertEqual(env["MAKEFLAGS"], "-j7")
        self.assertEqual(env["CMAKE_BUILD_PARALLEL_LEVEL"], "7")

    def test_preserves_existing_environment(self) -> None:
        with mock.patch.dict(os.environ, {"MY_OWN_VAR": "kept"}):
            env = load_gate._jobs_env(4)
        self.assertEqual(env.get("MY_OWN_VAR"), "kept")


class SplitCommandTests(unittest.TestCase):
    def test_no_separator(self) -> None:
        own, cmd = load_gate._split_command(["run", "--jobs", "2"])
        self.assertEqual(own, ["run", "--jobs", "2"])
        self.assertEqual(cmd, [])

    def test_splits_on_first_double_dash(self) -> None:
        own, cmd = load_gate._split_command(["run", "--jobs", "2", "--", "make", "-j2"])
        self.assertEqual(own, ["run", "--jobs", "2"])
        self.assertEqual(cmd, ["make", "-j2"])


class BuildLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.lock_path = Path(self._tmp.name) / "locks" / "build.lock"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_acquires_and_releases(self) -> None:
        with load_gate.BuildLock(self.lock_path, wait_seconds=5) as lock:
            self.assertTrue(self.lock_path.exists())
            self.assertIsNotNone(lock._fd)
        # released — a fresh lock on the same path acquires immediately
        with load_gate.BuildLock(self.lock_path, wait_seconds=5):
            pass

    def test_times_out_when_already_held(self) -> None:
        # Hold the lock with a separate fd (simulating another process).
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder_fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaises(TimeoutError):
                with load_gate.BuildLock(self.lock_path, wait_seconds=0.3):
                    pass
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)


class CmdRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.lock_path = Path(self._tmp.name) / "locks" / "build.lock"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_runs_command_and_returns_its_exit_code(self) -> None:
        rc = load_gate.main([
            "run", "--lock", str(self.lock_path), "--wait-seconds", "5",
            "--", sys.executable, "-c", "import sys; sys.exit(0)",
        ])
        self.assertEqual(rc, 0)

    def test_propagates_nonzero_exit_code(self) -> None:
        rc = load_gate.main([
            "run", "--lock", str(self.lock_path), "--wait-seconds", "5",
            "--", sys.executable, "-c", "import sys; sys.exit(3)",
        ])
        self.assertEqual(rc, 3)

    def test_missing_command_is_a_usage_error(self) -> None:
        rc = load_gate.main(["run", "--lock", str(self.lock_path)])
        self.assertEqual(rc, 2)

    def test_exit_75_when_lock_busy(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder_fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            rc = load_gate.main([
                "run", "--lock", str(self.lock_path), "--wait-seconds", "0.3",
                "--", sys.executable, "-c", "import sys; sys.exit(0)",
            ])
            self.assertEqual(rc, load_gate.EXIT_LOCK_TIMEOUT)
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

    def test_jobs_env_reaches_the_child_process(self) -> None:
        with tempfile.TemporaryDirectory() as out_dir:
            out_file = Path(out_dir) / "env.json"
            rc = load_gate.main([
                "run", "--lock", str(self.lock_path), "--wait-seconds", "5", "--jobs", "9",
                "--",
                sys.executable, "-c",
                "import json, os, sys; "
                "json.dump({k: os.environ.get(k) for k in "
                "('CARGO_BUILD_JOBS', 'MAKEFLAGS', 'CMAKE_BUILD_PARALLEL_LEVEL')}, "
                f"open(r'{out_file}', 'w'))",
            ])
            self.assertEqual(rc, 0)
            seen = json.loads(out_file.read_text())
        self.assertEqual(seen["CARGO_BUILD_JOBS"], "9")
        self.assertEqual(seen["MAKEFLAGS"], "-j9")
        self.assertEqual(seen["CMAKE_BUILD_PARALLEL_LEVEL"], "9")


class CmdCheckMeasureTests(unittest.TestCase):
    def test_ok_when_below_max(self) -> None:
        with mock.patch.object(load_gate, "get_loadavg", return_value=(2.0, 1.5, 1.0)):
            rc = load_gate.main(["check-measure", "--max-load", "15"])
        self.assertEqual(rc, 0)

    def test_fails_when_at_or_above_max(self) -> None:
        with mock.patch.object(load_gate, "get_loadavg", return_value=(64.0, 40.0, 20.0)):
            rc = load_gate.main(["check-measure", "--max-load", "15"])
        self.assertEqual(rc, 1)

    def test_json_output_shape(self) -> None:
        import contextlib
        import io

        with mock.patch.object(load_gate, "get_loadavg", return_value=(3.0, 2.0, 1.0)):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = load_gate.main(["check-measure", "--max-load", "15", "--json"])
        payload = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(
            payload,
            {"load1": 3.0, "load5": 2.0, "load15": 1.0, "max_load": 15.0, "ok": True},
        )


class CmdRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.perf_file = Path(self._tmp.name) / "perf-measurements.jsonl"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _rows(self) -> list[dict]:
        if not self.perf_file.exists():
            return []
        return [json.loads(line) for line in self.perf_file.read_text().splitlines() if line]

    def test_appends_row_with_load_and_timestamp(self) -> None:
        with mock.patch.object(load_gate, "get_loadavg", return_value=(2.0, 1.5, 1.0)):
            rc = load_gate.main([
                "record", "--metric", "build_seconds", "--value", "12.5",
                "--unit", "s", "--file", str(self.perf_file), "--max-load", "15",
            ])
        self.assertEqual(rc, 0)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["metric"], "build_seconds")
        self.assertEqual(row["value"], 12.5)
        self.assertEqual(row["unit"], "s")
        self.assertEqual(row["load1"], 2.0)
        self.assertEqual(row["load5"], 1.5)
        self.assertEqual(row["load15"], 1.0)
        self.assertIn("ts", row)
        self.assertNotIn("load_gate", row)

    def test_refuses_when_load_too_high(self) -> None:
        with mock.patch.object(load_gate, "get_loadavg", return_value=(64.0, 40.0, 20.0)):
            rc = load_gate.main([
                "record", "--metric", "build_seconds", "--value", "12.5",
                "--file", str(self.perf_file), "--max-load", "15",
            ])
        self.assertEqual(rc, 1)
        self.assertEqual(self._rows(), [])

    def test_force_writes_and_marks_overridden(self) -> None:
        with mock.patch.object(load_gate, "get_loadavg", return_value=(64.0, 40.0, 20.0)):
            rc = load_gate.main([
                "record", "--metric", "build_seconds", "--value", "12.5",
                "--file", str(self.perf_file), "--max-load", "15", "--force",
            ])
        self.assertEqual(rc, 0)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["load_gate"], "overridden")

    def test_appends_without_clobbering_prior_rows(self) -> None:
        with mock.patch.object(load_gate, "get_loadavg", return_value=(1.0, 1.0, 1.0)):
            load_gate.main([
                "record", "--metric", "a", "--value", "1",
                "--file", str(self.perf_file), "--max-load", "15",
            ])
            load_gate.main([
                "record", "--metric", "b", "--value", "2",
                "--file", str(self.perf_file), "--max-load", "15",
            ])
        rows = self._rows()
        self.assertEqual([r["metric"] for r in rows], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
