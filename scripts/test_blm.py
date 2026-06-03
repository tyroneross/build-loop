#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/blm.py."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import blm  # noqa: E402


class BlmCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.memroot = self.root / "build-loop-memory"
        self.workdir = self.root / "repo"
        self.memroot.mkdir()
        self.workdir.mkdir()
        self._prev_env = {
            "BUILD_LOOP_MEMORY_STORE_ROOT": os.environ.get("BUILD_LOOP_MEMORY_STORE_ROOT"),
            "BUILD_LOOP_MEMORY_ROOT": os.environ.get("BUILD_LOOP_MEMORY_ROOT"),
            "AGENT_MEMORY_ROOT": os.environ.get("AGENT_MEMORY_ROOT"),
            "EMBED_BACKEND_UNAVAILABLE": os.environ.get("EMBED_BACKEND_UNAVAILABLE"),
        }
        os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = str(self.memroot)
        os.environ.pop("BUILD_LOOP_MEMORY_ROOT", None)
        os.environ.pop("AGENT_MEMORY_ROOT", None)
        os.environ["EMBED_BACKEND_UNAVAILABLE"] = "1"
        project_dir = self.memroot / "projects" / "demo"
        (project_dir / "context").mkdir(parents=True)
        (project_dir / "decisions").mkdir()
        (project_dir / "lessons").mkdir()
        (self.memroot / "lessons").mkdir()
        (project_dir / "context" / "CONTEXT.md").write_text(
            "# Context\n\n## Governing Summary\nCLI context works.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        for key, value in self._prev_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def test_context_json(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = blm.main([
                "context",
                "--workdir",
                str(self.workdir),
                "--project",
                "demo",
                "--query",
                "context",
                "--json",
            ])
        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["current"]["project"], "demo")
        self.assertIn("CLI context works", data["current"]["context"]["summary"])

    def test_open_json(self) -> None:
        # Create CURRENT first so the CLI path is exercised like a real caller.
        with redirect_stdout(io.StringIO()):
            blm.main([
                "context",
                "--workdir",
                str(self.workdir),
                "--project",
                "demo",
                "--query",
                "context",
                "--json",
            ])
        out = io.StringIO()
        with redirect_stdout(out):
            code = blm.main([
                "open",
                "--workdir",
                str(self.workdir),
                "--project",
                "demo",
                "--id",
                "context:CONTEXT",
                "--json",
            ])
        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertTrue(data["exists"])
        self.assertIn("CLI context works", data["text"])

    def test_status_json(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = blm.main([
                "status",
                "--workdir",
                str(self.workdir),
                "--project",
                "demo",
                "--json",
            ])
        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["kind"], "build-loop-memory-status")
        self.assertEqual(data["project"], "demo")
        self.assertEqual(data["memory_root"], str(self.memroot.resolve()))
        self.assertIn("fast", data["cli"])
        self.assertEqual(data["api"]["default_host"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
