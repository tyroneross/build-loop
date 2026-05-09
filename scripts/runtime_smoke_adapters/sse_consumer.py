#!/usr/bin/env python3
"""SSE-consumer runtime smoke adapter.

Triggered when the diff touches a project with `triggers.runtimeServer == true`
AND the changed files include the server module OR the embedded UI module.
Closes the silent-server / ignored-client class of bug observed in local-smartz
2026-05-08 (27 commits passed pytest, 2 user-facing bugs shipped because the
server emitted SSE event types the embedded UI didn't handle).

Implements the 5-step procedure documented in
`skills/build-loop/SKILL.md` §"Live HTTP/SSE smoke" — restart server, wait for
ready, curl the SSE route, parse handlers in the embedded UI, fail if any
observed event type lacks a UI handler arm.

Exported interface:
    run(changed_files: list[str], workdir: Path, info: dict) -> dict

`info` is the `runtimeServerInfo` envelope written by `detect_runtime_server.py`
to `state.json.runtimeServerInfo`. Required keys:
    - server_module: str (relative path)
    - sse_route: str | None (e.g. "/api/stream")
    - default_port: int | None
    - embedded_ui_module: str | None (relative path; None for API-only services)
    - event_handler_locations: list[str] (relative paths, defaults to [embedded_ui_module])
    - start_command: str | None (overrides the default uv-run shape)

Return envelope shape:
    {
        "status": "pass" | "fail" | "skipped",
        "adapter": "sse_consumer",
        "reason": str,                       # only on skipped/fail-infrastructure
        "checked_route": str,                # the SSE route exercised
        "observed_event_types": [...],
        "handled_event_types": [...],
        "missing_handlers": [...],           # observed but not handled — the bug class
        "findings": [{"event_type": ..., "finding": ...}]
    }

Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

ADAPTER_NAME = "sse_consumer"
BOOT_TIMEOUT_SECONDS = 20
HEALTH_TIMEOUT_SECONDS = 8
SSE_CURL_DURATION_SECONDS = 5
TOTAL_TIMEOUT_SECONDS = 45

_EVENT_TYPE_PATTERN = re.compile(r'"type"\s*:\s*"([^"]+)"')
_HANDLER_PATTERNS = [
    re.compile(r"""(?:d|data|event|msg|m)\.type\s*===?\s*['"]([^'"]+)['"]"""),
    re.compile(r"""case\s+['"]([^'"]+)['"]"""),
    re.compile(r"""['"]([^'"]+)['"]\s*:\s*\([^)]*\)\s*=>\s*\{"""),  # `'foo': (d) => {...}` map shape
]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http_ready(port: int, deadline: float, path: str = "/") -> bool:
    """Poll http://127.0.0.1:port{path} until it returns any HTTP response."""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as resp:
                if resp.status:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    return False


def _read_handlers(workdir: Path, ui_modules: list[str]) -> set[str]:
    """Parse the embedded UI's event-handler switch(es) and extract handled event types."""
    handled: set[str] = set()
    for rel in ui_modules:
        path = workdir / rel
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in _HANDLER_PATTERNS:
            for m in pat.finditer(text):
                handled.add(m.group(1))
    return handled


def _resolve_start_command(info: dict, workdir: Path, port: int) -> list[str]:
    """Build the server start command. Prefer info.start_command; fall back to
    `uv run <package> --serve --port <port>` from pyproject.toml."""
    explicit = info.get("start_command")
    if explicit:
        # Substitute {port} placeholder if present
        cmd = explicit.replace("{port}", str(port))
        return ["bash", "-c", cmd]

    pyproject = workdir / "pyproject.toml"
    package = None
    if pyproject.exists():
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            m = re.match(r'^\s*name\s*=\s*"([^"]+)"', line)
            if m:
                package = m.group(1)
                break
    if package:
        return ["uv", "run", package, "--serve", "--port", str(port)]

    # Last-resort: try python -m on the server module
    server_module = info.get("server_module")
    if server_module:
        module_path = server_module.replace("/", ".").removesuffix(".py")
        return ["python3", "-m", module_path, "--port", str(port)]

    return []


def _curl_sse(port: int, route: str, duration: int, payload: str) -> tuple[set[str], str]:
    """Curl the SSE endpoint for `duration` seconds; return (observed event types, raw body)."""
    cmd = [
        "curl", "-sN", "-X", "POST",
        f"http://127.0.0.1:{port}{route}",
        "-H", "Content-Type: application/json",
        "-d", payload,
        "--max-time", str(duration),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=duration + 5,
        )
        body = result.stdout
        types = set(_EVENT_TYPE_PATTERN.findall(body))
        return types, body
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return set(), ""


def _diff_touches_relevant_files(changed_files: list[str], info: dict) -> bool:
    """Return True if any changed file is the server module, embedded UI module,
    or one of the handler-location modules."""
    relevant = set()
    if info.get("server_module"):
        relevant.add(info["server_module"])
    if info.get("embedded_ui_module"):
        relevant.add(info["embedded_ui_module"])
    for loc in info.get("event_handler_locations") or []:
        relevant.add(loc)
    for f in changed_files:
        f_norm = f.replace("\\", "/")
        if f_norm in relevant:
            return True
        # Also match if the changed file is inside a directory of a tracked module
        for r in relevant:
            if r and f_norm.startswith(r.split("/")[0] + "/"):
                # Same top-level dir — likely related (e.g. tools/web.py touches the server runtime)
                pass  # too permissive; rely on exact match for now
    return False


def run(changed_files: list[str], workdir: Path, info: dict | None = None) -> dict[str, Any]:
    info = info or {}

    # Skip when no SSE route is detected
    sse_route = info.get("sse_route")
    if not sse_route:
        return {
            "status": "skipped",
            "adapter": ADAPTER_NAME,
            "reason": "no_sse_route_detected",
        }

    # Skip when the diff doesn't touch relevant files
    if not _diff_touches_relevant_files(changed_files, info):
        return {
            "status": "skipped",
            "adapter": ADAPTER_NAME,
            "reason": "diff_does_not_touch_runtime_surface",
        }

    port = info.get("default_port") or _find_free_port()
    cmd = _resolve_start_command(info, workdir, port)
    if not cmd:
        return {
            "status": "skipped",
            "adapter": ADAPTER_NAME,
            "reason": "could_not_resolve_start_command",
        }

    log_path = Path("/tmp/buildloop-sse-smoke.log")
    proc: subprocess.Popen | None = None
    total_deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS

    try:
        # Step 1: Restart the server in background
        with log_path.open("w", encoding="utf-8") as logf:
            proc = subprocess.Popen(
                cmd,
                cwd=str(workdir),
                stdout=logf,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PORT": str(port)},
            )

        # Step 2: Wait for ready
        boot_deadline = min(time.monotonic() + BOOT_TIMEOUT_SECONDS, total_deadline)
        if not _wait_for_http_ready(port, boot_deadline):
            return {
                "status": "fail",
                "adapter": ADAPTER_NAME,
                "reason": "server_did_not_become_ready",
                "checked_route": sse_route,
                "log": str(log_path),
            }

        # Step 3: Curl the SSE endpoint
        # Use a minimal probe payload — projects can override via info.smoke_payload
        payload = info.get("smoke_payload") or '{"prompt":"smoke test"}'
        observed, _body = _curl_sse(port, sse_route, SSE_CURL_DURATION_SECONDS, payload)

        if not observed:
            return {
                "status": "fail",
                "adapter": ADAPTER_NAME,
                "reason": "no_sse_events_observed_in_window",
                "checked_route": sse_route,
                "observed_event_types": [],
                "handled_event_types": [],
                "missing_handlers": [],
                "findings": [],
            }

        # Step 4: Parse the embedded UI's event-handler switch(es)
        ui_modules = list(info.get("event_handler_locations") or [])
        if info.get("embedded_ui_module") and info["embedded_ui_module"] not in ui_modules:
            ui_modules.append(info["embedded_ui_module"])

        if not ui_modules:
            # API-only service — no UI to compare. Return observed types as the
            # contract surface; pass since there's no missing-handler class possible.
            return {
                "status": "pass",
                "adapter": ADAPTER_NAME,
                "checked_route": sse_route,
                "observed_event_types": sorted(observed),
                "handled_event_types": [],
                "missing_handlers": [],
                "findings": [],
                "reason": "api_only_no_embedded_ui",
            }

        handled = _read_handlers(workdir, ui_modules)

        # Step 5: Compute missing handlers (observed but not handled)
        missing = sorted(observed - handled)

        findings = [
            {
                "event_type": t,
                "finding": (
                    f"Server emitted SSE event type '{t}' but no handler arm in "
                    f"{', '.join(ui_modules)} — silent client bug class. Add a "
                    f"case to handleEvent (or equivalent) for '{t}'."
                ),
            }
            for t in missing
        ]

        return {
            "status": "fail" if missing else "pass",
            "adapter": ADAPTER_NAME,
            "checked_route": sse_route,
            "observed_event_types": sorted(observed),
            "handled_event_types": sorted(handled),
            "missing_handlers": missing,
            "findings": findings,
        }

    except Exception as exc:  # noqa: BLE001
        return {
            "status": "fail",
            "adapter": ADAPTER_NAME,
            "reason": f"adapter_error: {exc}",
            "checked_route": sse_route,
        }
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


# ---------------------------------------------------------------------------
# CLI surface — for direct invocation and orchestrator interop
# ---------------------------------------------------------------------------

def _cli() -> int:
    import argparse

    p = argparse.ArgumentParser(description="SSE-consumer runtime smoke adapter")
    p.add_argument("--changed-files", required=True, help="comma-separated list")
    p.add_argument("--workdir", required=True)
    p.add_argument("--info-json", help="path to runtimeServerInfo JSON envelope")
    p.add_argument("--state-json", help="path to .build-loop/state.json (extracts runtimeServerInfo)")
    p.add_argument("--json", action="store_true", help="output JSON envelope to stdout")
    args = p.parse_args()

    workdir = Path(args.workdir)
    files = [f for f in args.changed_files.split(",") if f.strip()]
    info: dict[str, Any] = {}
    if args.info_json:
        info = json.loads(Path(args.info_json).read_text(encoding="utf-8"))
    elif args.state_json:
        state = json.loads(Path(args.state_json).read_text(encoding="utf-8"))
        info = state.get("runtimeServerInfo") or {}

    result = run(files, workdir, info)
    if args.json:
        json.dump(result, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))

    # Exit codes: 0 pass, 1 fail, 2 skipped
    if result["status"] == "pass":
        return 0
    if result["status"] == "fail":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
