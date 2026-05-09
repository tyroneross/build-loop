"""API-only SSE server fixture — no embedded HTML/JS UI.

Should produce ``runtimeServer: true`` AND ``embedded_ui_module: null``
AND ``event_handler_locations: []``. UI-handler check is skipped for
projects whose UI lives elsewhere (e.g. a separate SPA repo).
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer


def _send_event(handler: BaseHTTPRequestHandler, payload: dict) -> None:
    import json
    handler.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            _send_event(self, {"type": "start"})
            _send_event(self, {"type": "done"})
            return
        self.send_response(404)
        self.end_headers()


def main() -> None:
    HTTPServer(("127.0.0.1", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()
