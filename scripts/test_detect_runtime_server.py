#!/usr/bin/env python3
"""Tests for detect_runtime_server.py. Zero deps. Run: python3 test_detect_runtime_server.py"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from detect_runtime_server import detect, _is_test_path  # noqa: E402


# Minimal server fixture: BaseHTTPRequestHandler + _send_event + inline UI.
REAL_SERVER = '''\
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/api/research":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            _send_event(self.wfile, {"type": "status", "msg": "ok"})

def _send_event(wfile, payload):
    wfile.write(b"data:" + repr(payload).encode() + b"\\n\\n")

INDEX_HTML = """<!DOCTYPE html>
<html><body>
<script>
const es = new EventSource('/api/research');
function handleEvent(d) {
  if (d.type === 'status') console.log(d);
  if (d.type === 'done') console.log('end');
}
</script>
</body></html>
"""

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 11435), H).serve_forever()
'''

# Test-file fixture that imports BaseHTTPRequestHandler for mock-server purposes.
# This shape is what caused local-smartz to be misclassified before commit 1.
TEST_FIXTURE = '''\
from http.server import BaseHTTPRequestHandler

class MockH(BaseHTTPRequestHandler):
    def do_GET(self):
        _send_event(self.wfile, {"type":"x"})

def _send_event(w, p):
    w.write(b"data:")
'''


class TestIsTestPath(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_tests_dir_anywhere(self):
        for rel in ("tests/foo.py", "src/tests/bar.py", "a/tests/b/c.py"):
            p = self.tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("")
            self.assertTrue(_is_test_path(p, self.tmp), f"failed on {rel}")

    def test_test_underscore_prefix(self):
        p = self.tmp / "src/test_foo.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
        self.assertTrue(_is_test_path(p, self.tmp))

    def test_underscore_test_suffix(self):
        p = self.tmp / "src/foo_test.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
        self.assertTrue(_is_test_path(p, self.tmp))

    def test_jest_test_suffix(self):
        p = self.tmp / "src/foo.test.js"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
        self.assertTrue(_is_test_path(p, self.tmp))

    def test_non_test_path(self):
        p = self.tmp / "src/serve.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
        self.assertFalse(_is_test_path(p, self.tmp))

    def test_top_level_test_dir(self):
        p = self.tmp / "test/foo.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
        self.assertTrue(_is_test_path(p, self.tmp))


class TestServerModulePriority(unittest.TestCase):
    """Regression test for the local-smartz 2026-05-11 misclassification."""

    def test_real_server_wins_over_test_fixture(self):
        """When tests/ and src/ both match substrate+emit, src/ wins."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real = root / "src" / "myapp" / "serve.py"
            real.parent.mkdir(parents=True, exist_ok=True)
            real.write_text(REAL_SERVER)
            mock = root / "tests" / "test_sse_cancel.py"
            mock.parent.mkdir(parents=True, exist_ok=True)
            mock.write_text(TEST_FIXTURE)

            env = detect(root)
            self.assertTrue(env["runtimeServer"])
            self.assertEqual(env["server_module"], "src/myapp/serve.py")
            # Inline UI detected because real server ships HTML + handlers.
            self.assertEqual(env["embedded_ui_module"], "src/myapp/serve.py")
            # SSE route extracted from the real server, not the mock.
            self.assertEqual(env["sse_route"], "/api/research")

    def test_test_fixture_alone_still_negative(self):
        """When only a test fixture matches, server_module is set to it but
        detection still fires — keeps prior behavior so we don't false-negative
        on projects whose only server-shape file IS a test scaffold."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mock = root / "tests" / "test_sse_cancel.py"
            mock.parent.mkdir(parents=True, exist_ok=True)
            mock.write_text(TEST_FIXTURE)

            env = detect(root)
            # Test file still wins when it's the only candidate.
            self.assertTrue(env["runtimeServer"])
            self.assertEqual(env["server_module"], "tests/test_sse_cancel.py")


if __name__ == "__main__":
    unittest.main()
