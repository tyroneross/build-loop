#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the bounded synthetic CPU load supervisor."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "bin" / "build-loop-load-probe.js"


def _receipt_path(run_id: str) -> Path:
    if os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Caches" / "com.rosslabs.build-loop" / "processes" / f"{run_id}.json"
    return Path("/tmp") / f"build-loop-{os.getuid()}" / "processes" / f"{run_id}.json"


def _matching_pids(run_id: str) -> list[int]:
    receipt = _receipt_path(run_id)
    if not receipt.exists():
        return []
    try:
        pids = [int(worker["pid"]) for worker in json.loads(receipt.read_text())["workers"]]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return []
    live = []
    for pid in pids:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], text=True, capture_output=True)
        if result.returncode == 0 and result.stdout.strip().startswith("bl-load-w-"):
            live.append(pid)
    return live


def _child_pids(parent_pid: int) -> list[int]:
    result = subprocess.run(["pgrep", "-P", str(parent_pid)], text=True, capture_output=True)
    return [int(value) for value in result.stdout.split()]


def _wait_for_children(parent_pid: int, *, present: bool, timeout: float) -> list[int]:
    deadline = time.monotonic() + timeout
    latest: list[int] = []
    while time.monotonic() < deadline:
        latest = _child_pids(parent_pid)
        if bool(latest) is present:
            return latest
        time.sleep(0.05)
    return latest


def _wait_for_workers(run_id: str, *, present: bool, timeout: float) -> list[int]:
    deadline = time.monotonic() + timeout
    latest: list[int] = []
    while time.monotonic() < deadline:
        latest = _matching_pids(run_id)
        if bool(latest) is present:
            return latest
        time.sleep(0.05)
    return latest


class LoadProbeTests(unittest.TestCase):
    def test_caps_workers_and_verifies_cleanup(self) -> None:
        completed = subprocess.run(
            ["node", str(PROBE), "--workers", "999", "--duration-seconds", "1", "--json"],
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        result = json.loads(completed.stdout)
        self.assertLessEqual(result["admittedWorkers"], 8)
        self.assertLessEqual(result["admittedWorkers"], max(1, os.cpu_count() - 2))
        self.assertTrue(result["cleanup"]["verifiedZeroSurvivors"])
        self.assertEqual(result["cleanup"]["errors"], [])
        self.assertNotIn("receiptPath", result)
        self.assertEqual(result["receiptId"], result["runId"])

    def test_preserves_target_failure_and_cleans_workers(self) -> None:
        completed = subprocess.run(
            ["node", str(PROBE), "--workers", "2", "--duration-seconds", "2", "--json", "--", "sh", "-c", "exit 7"],
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 7, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["commandExit"], 7)
        self.assertTrue(result["cleanup"]["verifiedZeroSurvivors"])
        self.assertEqual(_matching_pids(result["runId"]), [])

    def test_sigterm_cleans_workers(self) -> None:
        supervisor = subprocess.Popen(["node", str(PROBE), "--workers", "2", "--duration-seconds", "5", "--json"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        try:
            workers = _wait_for_children(supervisor.pid, present=True, timeout=3)
            self.assertTrue(workers)
            supervisor.send_signal(signal.SIGTERM)
            supervisor.wait(timeout=5)
            for pid in workers:
                self.assertNotEqual(subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode, 0)
        finally:
            if supervisor.poll() is None:
                supervisor.kill()

    def test_sigkill_still_allows_worker_self_expiry(self) -> None:
        supervisor = subprocess.Popen(["node", str(PROBE), "--workers", "2", "--duration-seconds", "1", "--json"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        try:
            workers = _wait_for_children(supervisor.pid, present=True, timeout=3)
            self.assertTrue(workers)
            supervisor.kill()
            supervisor.wait(timeout=3)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and any(subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode == 0 for pid in workers):
                time.sleep(0.05)
            self.assertFalse(any(subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode == 0 for pid in workers))
        finally:
            for pid in workers if "workers" in locals() else []:
                if subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode == 0:
                    os.kill(pid, signal.SIGKILL)

    def test_process_title_and_receipt_are_sanitized(self) -> None:
        supervisor = subprocess.Popen(["node", str(PROBE), "--workers", "1", "--duration-seconds", "5", "--json"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        try:
            pids = _wait_for_children(supervisor.pid, present=True, timeout=3)
            self.assertEqual(len(pids), 1)
            deadline = time.monotonic() + 3
            title = ""
            while time.monotonic() < deadline:
                title = subprocess.run(["ps", "-p", str(pids[0]), "-o", "command="], text=True, capture_output=True, check=True).stdout.strip()
                if title.startswith("bl-load-w-"):
                    break
                time.sleep(0.05)
            self.assertRegex(title, r"^bl-load-w-[0-9a-f]{6}$")
            receipt_root = Path.home() / "Library" / "Caches" / "com.rosslabs.build-loop" / "processes"
            receipt_file = next(item for item in receipt_root.glob("*.json") if json.loads(item.read_text()).get("supervisor", {}).get("pid") == supervisor.pid)
            receipt = json.loads(receipt_file.read_text())
            self.assertEqual(receipt["purpose"], "bounded-synthetic-cpu-load")
            self.assertNotIn("command", receipt)
            self.assertNotIn("url", json.dumps(receipt).lower())
            self.assertEqual(receipt_file.stat().st_mode & 0o777, 0o600)
            supervisor.send_signal(signal.SIGTERM)
            supervisor.wait(timeout=5)
        finally:
            if supervisor.poll() is None:
                supervisor.kill()
                supervisor.wait(timeout=3)

    def test_signal_terminates_wrapped_command_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            child_file = Path(tmp) / "child.pid"
            command = f"trap '' TERM; sleep 30 & echo $! > '{child_file}'; wait"
            supervisor = subprocess.Popen(["node", str(PROBE), "--workers", "1", "--duration-seconds", "5", "--", "sh", "-c", command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 3
                while not child_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(child_file.exists())
                descendant_pid = int(child_file.read_text())
                supervisor.send_signal(signal.SIGTERM)
                supervisor.wait(timeout=5)
                deadline = time.monotonic() + 3
                while subprocess.run(["ps", "-p", str(descendant_pid)], capture_output=True).returncode == 0 and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertNotEqual(subprocess.run(["ps", "-p", str(descendant_pid)], capture_output=True).returncode, 0)
            finally:
                if supervisor.poll() is None:
                    supervisor.kill()

    def test_parent_exit_cleans_background_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            child_file = Path(tmp) / "child.pid"
            command = f"sleep 30 & echo $! > '{child_file}'"
            completed = subprocess.run(["node", str(PROBE), "--workers", "1", "--duration-seconds", "5", "--", "sh", "-c", command], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            descendant_pid = int(child_file.read_text())
            self.assertNotEqual(subprocess.run(["ps", "-p", str(descendant_pid)], capture_output=True).returncode, 0)

    def test_supervisor_sigkill_guardian_cleans_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            child_file = Path(tmp) / "child.pid"
            command = f"trap '' TERM; echo $$ > '{child_file}'; while :; do sleep 1; done"
            supervisor = subprocess.Popen(["node", str(PROBE), "--workers", "1", "--duration-seconds", "5", "--", "sh", "-c", command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 3
                while not child_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(child_file.exists())
                target_pid = int(child_file.read_text())
                guardian_pids = [pid for pid in _child_pids(supervisor.pid) if pid != target_pid]
                supervisor.kill()
                supervisor.wait(timeout=3)
                deadline = time.monotonic() + 5
                watched = [target_pid, *guardian_pids]
                while time.monotonic() < deadline and any(subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode == 0 for pid in watched):
                    time.sleep(0.05)
                self.assertFalse(any(subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode == 0 for pid in watched))
            finally:
                if supervisor.poll() is None:
                    supervisor.kill()

    def test_double_signal_waits_for_one_cleanup(self) -> None:
        supervisor = subprocess.Popen(["node", str(PROBE), "--workers", "2", "--duration-seconds", "5"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        workers = _wait_for_children(supervisor.pid, present=True, timeout=3)
        self.assertTrue(workers)
        supervisor.send_signal(signal.SIGTERM)
        supervisor.send_signal(signal.SIGINT)
        supervisor.wait(timeout=5)
        for pid in workers:
            self.assertNotEqual(subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode, 0)

    def test_public_run_id_is_rejected_before_spawning(self) -> None:
        completed = subprocess.run(["node", str(PROBE), "--run-id", "caller-semantic", "--duration-seconds", "1"], text=True, capture_output=True)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("internal", completed.stderr)


if __name__ == "__main__":
    unittest.main()
