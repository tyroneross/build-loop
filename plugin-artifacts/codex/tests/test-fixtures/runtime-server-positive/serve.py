"""Minimal SSE server fixture for detect_runtime_server tests.

Exercises:
- BaseHTTPRequestHandler substrate
- _send_event emit pattern
- self.path == "/api/research" SSE route paired with text/event-stream
- --port 11435 default
- Embedded HTML+JS UI with handleEvent function
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer

INDEX_HTML = """<!DOCTYPE html>
<html><head><title>SSE demo</title></head>
<body>
  <button id="run">Run</button>
  <pre id="output">Output will appear here...</pre>
  <script>
    function handleEvent(d) {
      const out = document.getElementById('output');
      if (d.type === 'text') out.textContent += d.payload;
      else if (d.type === 'done') out.textContent += '\\nDone';
    }
    document.getElementById('run').addEventListener('click', () => {
      fetch('/api/research', {method:'POST', body:JSON.stringify({prompt:'hi'})})
        .then(r => r.body.getReader())
        .then(reader => {
          // streaming reader omitted for brevity
        });
    });
  </script>
</body></html>
"""


def _send_event(handler: BaseHTTPRequestHandler, payload: dict) -> None:
    """Emit one SSE frame to the open response."""
    import json
    line = f"data: {json.dumps(payload)}\n\n"
    handler.wfile.write(line.encode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(INDEX_HTML.encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/research":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            _send_event(self, {"type": "text", "payload": "hello"})
            _send_event(self, {"type": "done"})
            return
        self.send_response(404)
        self.end_headers()


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=11435)
    args = p.parse_args()
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
