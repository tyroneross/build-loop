#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for inject_readiness."""
from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from rally_point import inject_readiness as ir


class InjectReadinessTests(unittest.TestCase):
    def _executable(self, directory: Path, name: str) -> Path:
        path = directory / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_fresh_laptop_simulation_degrades_to_handoff(self):
        with tempfile.TemporaryDirectory(prefix="inject-fresh-") as td:
            home = Path(td) / "home"
            empty_bin = Path(td) / "bin"
            home.mkdir()
            empty_bin.mkdir()
            result = ir.probe(env={"PATH": str(empty_bin), "HOME": str(home)})

        self.assertEqual(result, {
            "tmux": False,
            "ptyd_socket_live": False,
            "ptyd_bin": False,
            "inject_available": False,
            "recommended_backend": "handoff",
        })

    def test_tmux_on_path_recommends_tmux(self):
        with tempfile.TemporaryDirectory(prefix="inject-tmux-") as td:
            root = Path(td)
            self._executable(root, "tmux")
            result = ir.probe(env={"PATH": str(root), "HOME": td})

        self.assertTrue(result["tmux"])
        self.assertTrue(result["inject_available"])
        self.assertEqual(result["recommended_backend"], "tmux")

    def test_ptyd_binary_without_socket_recommends_ptyd(self):
        with tempfile.TemporaryDirectory(prefix="inject-ptyd-bin-") as td:
            root = Path(td)
            ptyd = self._executable(root, "ptyd")
            result = ir.probe(env={
                "PATH": "",
                "HOME": td,
                "RALLY_PTYD_BIN": str(ptyd),
            })

        self.assertFalse(result["tmux"])
        self.assertFalse(result["ptyd_socket_live"])
        self.assertTrue(result["ptyd_bin"])
        self.assertTrue(result["inject_available"])
        self.assertEqual(result["recommended_backend"], "ptyd")

    def test_live_ptyd_socket_recommends_ptyd(self):
        with tempfile.TemporaryDirectory(prefix="inject-ptyd-sock-") as td:
            socket_path = str(Path(td) / "ptyd.sock")
            ready = threading.Event()

            def serve_once() -> None:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                    server.bind(socket_path)
                    server.listen(1)
                    ready.set()
                    conn, _ = server.accept()
                    with conn:
                        conn.recv(4096)
                        conn.sendall(
                            json.dumps({
                                "id": "test",
                                "result": {"panes": []},
                            }).encode("utf-8") + b"\n"
                        )

            thread = threading.Thread(target=serve_once, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(2.0))
            result = ir.probe(
                env={"PATH": "", "HOME": td},
                socket_path=socket_path,
            )
            thread.join(timeout=2.0)

        self.assertTrue(result["ptyd_socket_live"])
        self.assertTrue(result["inject_available"])
        self.assertEqual(result["recommended_backend"], "ptyd")

    def test_cli_prints_stable_json_shape(self):
        with tempfile.TemporaryDirectory(prefix="inject-cli-") as td:
            env = {
                **os.environ,
                "PATH": "",
                "HOME": td,
                "RALLY_PTYD_SOCKET": str(Path(td) / "missing.sock"),
            }
            proc = subprocess.run(
                [
                    sys.executable,
                    str(HERE / "inject_readiness.py"),
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                env=env,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(
            set(result),
            {
                "tmux",
                "ptyd_socket_live",
                "ptyd_bin",
                "inject_available",
                "recommended_backend",
            },
        )
        self.assertEqual(result["recommended_backend"], "handoff")


if __name__ == "__main__":
    unittest.main()
