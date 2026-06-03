#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/blm_api.py."""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import blm_api  # noqa: E402


class BlmApiTests(unittest.TestCase):
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
            "# Context\n\n## Governing Summary\nHTTP context works.\n",
            encoding="utf-8",
        )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), blm_api.make_handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        for key, value in self._prev_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=3)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        raw_body = json.dumps(body).encode("utf-8") if body is not None else None
        conn.request(method, path, body=raw_body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, json.loads(raw.decode("utf-8"))

    def test_health_and_status(self) -> None:
        status, data = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["service"], "build-loop-memory-api")

        path = f"/status?workdir={self.workdir}&project=demo"
        status, data = self.request("GET", path)
        self.assertEqual(status, 200)
        self.assertEqual(data["kind"], "build-loop-memory-status")
        self.assertEqual(data["project"], "demo")
        self.assertEqual(data["api"]["default_port"], self.port)
        self.assertIn("/context", data["api"]["endpoints"][2])

    def test_get_context_is_read_only_by_default(self) -> None:
        current_path = self.memroot / "projects" / "demo" / "context" / "CURRENT.json"
        self.assertFalse(current_path.exists())

        status, data = self.request("GET", f"/context?workdir={self.workdir}&project=demo&query=context")

        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], "fast")
        self.assertEqual(data["current"]["project"], "demo")
        self.assertIn("HTTP context works", data["current"]["context"]["summary"])
        self.assertEqual(data["written"], {})
        self.assertFalse(current_path.exists())

    def test_post_context_can_write_and_open_evidence(self) -> None:
        current_path = self.memroot / "projects" / "demo" / "context" / "CURRENT.json"

        status, data = self.request(
            "POST",
            "/context",
            {"workdir": str(self.workdir), "project": "demo", "query": "context", "write": True},
        )

        self.assertEqual(status, 200)
        self.assertTrue(current_path.exists())
        self.assertIn("json", data["written"])

        status, data = self.request(
            "POST",
            "/open",
            {"workdir": str(self.workdir), "project": "demo", "id": "context:CONTEXT"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["exists"])
        self.assertIn("HTTP context works", data["text"])

    def test_open_requires_id(self) -> None:
        status, data = self.request("POST", "/open", {"workdir": str(self.workdir), "project": "demo"})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])
        self.assertIn("id is required", data["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
