#!/usr/bin/env python3
"""Next.js dev-server adapter for the runtime smoke gate.

Exported interface:
    run(changed_files: list[str], workdir: Path) -> dict

Return envelope shape:
    {
        "status": "pass" | "fail" | "skipped",
        "adapter": "nextjs",
        "checked_routes": [...],
        "findings": [{"route": ..., "http_status": ..., "render_status": ..., "finding": ...}]
    }

Stdlib only — no third-party dependencies.
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

ADAPTER_NAME = "nextjs"
BOOT_TIMEOUT_SECONDS = 30
TOTAL_TIMEOUT_SECONDS = 60
ROUTE_REQUEST_TIMEOUT_SECONDS = 10

# Patterns that indicate Next.js has started successfully
_READY_PATTERNS = [
    re.compile(r"started server on", re.IGNORECASE),
    re.compile(r"ready\s*[-–]?\s*started", re.IGNORECASE),
    re.compile(r"Local:\s+http://", re.IGNORECASE),
]


def _load_package_json(workdir: Path) -> dict:
    pkg = workdir / "package.json"
    if not pkg.exists():
        return {}
    try:
        return json.loads(pkg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _has_next_dependency(pkg: dict) -> bool:
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    return "next" in deps


def _find_free_port() -> int:
    """Find a free TCP port on 127.0.0.1 by binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _map_files_to_routes(changed_files: list[str]) -> list[str]:
    """Map changed file paths to HTTP routes that should be smoke-tested."""
    routes: list[str] = []
    for f in changed_files:
        # Normalize separators
        f_norm = f.replace("\\", "/")

        # App Router: app/**/page.{tsx,jsx,ts,js}
        m = re.match(r"^app/(.+)/page\.[tj]sx?$", f_norm)
        if m:
            path_segment = m.group(1)
            # Strip dynamic segment brackets for a rough route guess
            route = "/" + path_segment
            routes.append(route)
            continue

        # App Router root page: app/page.{tsx,jsx,ts,js}
        m = re.match(r"^app/page\.[tj]sx?$", f_norm)
        if m:
            routes.append("/")
            continue

        # App Router: app/**/layout.{tsx,jsx,ts,js} — affects all children; smoke root
        m = re.match(r"^app/(.+)/layout\.[tj]sx?$", f_norm)
        if m:
            path_segment = m.group(1)
            routes.append("/" + path_segment)
            continue

        # App Router root layout
        m = re.match(r"^app/layout\.[tj]sx?$", f_norm)
        if m:
            routes.append("/")
            continue

        # App Router API: app/**/route.{ts,js}
        m = re.match(r"^app/(.+)/route\.[tj]s$", f_norm)
        if m:
            path_segment = m.group(1)
            routes.append("/" + path_segment)
            continue

        # Pages Router: pages/**/*.{tsx,jsx,ts,js} — but NOT pages/api/
        m = re.match(r"^pages/(?!api/)(.+)\.[tj]sx?$", f_norm)
        if m:
            path_segment = m.group(1)
            # pages/index -> /
            if path_segment == "index":
                routes.append("/")
            else:
                # pages/foo/bar -> /foo/bar
                routes.append("/" + path_segment)
            continue

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for r in routes:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


def _wait_for_server(proc: subprocess.Popen, port: int, deadline: float) -> bool:
    """Poll for a ready signal or until deadline.

    Primary signal: TCP connect to the bound port. This is the authoritative
    boot signal across all Next.js versions and runtimes. Newer Next versions
    write the ready line to stderr, so a stdout-readline-only path can hang.

    Secondary signal: stdout line scan (best-effort, non-blocking via
    os.set_blocking). Useful when TCP connect is delayed by middleware.
    """
    ready_pattern = re.compile(
        r"(started server on|ready\s*[-–]?\s*started|Local:\s+http://)", re.IGNORECASE
    )
    # Best-effort: switch stdout to non-blocking so readline cannot hang.
    if proc.stdout is not None:
        try:
            os.set_blocking(proc.stdout.fileno(), False)
        except (OSError, ValueError):
            # Not a real fd or already closed — skip the line scan path.
            pass

    while time.monotonic() < deadline:
        # Process died early (crash, port-in-use, missing dep)
        if proc.poll() is not None:
            return False

        # Primary: TCP connect to the bound port
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                # Port is open — server likely up; give it 0.5s to finish startup
                time.sleep(0.5)
                return True
        except OSError:
            pass

        # Secondary: non-blocking stdout line scan
        if proc.stdout is not None:
            try:
                line = proc.stdout.readline()
            except (OSError, BlockingIOError, ValueError):
                line = b""
            if line:
                decoded = line.decode("utf-8", errors="replace").rstrip()
                if ready_pattern.search(decoded):
                    return True

        time.sleep(0.2)
    return False


def _check_route(base_url: str, route: str) -> dict[str, Any]:
    """GET a single route and evaluate the response for error markers."""
    url = base_url + route
    finding: dict[str, Any] = {
        "route": route,
        "http_status": None,
        "render_status": "pass",
        "finding": None,
    }
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "build-loop-smoke/1.0"})
        with urllib.request.urlopen(req, timeout=ROUTE_REQUEST_TIMEOUT_SECONDS) as resp:
            body = resp.read(1024 * 512).decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        finding["http_status"] = e.code
        if e.code >= 500:
            finding["render_status"] = "fail"
            finding["finding"] = f"HTTP {e.code} response"
        return finding
    except (urllib.error.URLError, OSError) as e:
        finding["http_status"] = 0
        finding["render_status"] = "fail"
        finding["finding"] = f"Request error: {e}"
        return finding

    finding["http_status"] = status

    if status >= 500:
        finding["render_status"] = "fail"
        finding["finding"] = f"HTTP {status}"
        return finding

    # Check for Next.js error overlay markers in the body
    if "Application error" in body:
        finding["render_status"] = "fail"
        finding["finding"] = "Body contains 'Application error'"
        return finding

    # Check __NEXT_DATA__ for an err key (Next.js injects this for 500-class render errors)
    next_data_match = re.search(r"<script[^>]+id=['\"]__NEXT_DATA__['\"][^>]*>(.*?)</script>",
                                body, re.DOTALL)
    if next_data_match:
        try:
            next_data = json.loads(next_data_match.group(1))
            if next_data.get("err"):
                finding["render_status"] = "fail"
                finding["finding"] = "__NEXT_DATA__ contains err field"
                return finding
        except (json.JSONDecodeError, AttributeError):
            pass

    return finding


def run(changed_files: list[str], workdir: Path) -> dict:
    """Run Next.js dev-server smoke against changed routes.

    Returns envelope:
        {
            "status": "pass" | "fail" | "skipped",
            "adapter": "nextjs",
            "checked_routes": [...],
            "findings": [{"route", "http_status", "render_status", "finding"}]
        }
    """
    start_time = time.monotonic()
    workdir = Path(workdir)

    # 1. Detect Next.js
    pkg = _load_package_json(workdir)
    if not _has_next_dependency(pkg):
        return {
            "status": "skipped",
            "adapter": ADAPTER_NAME,
            "reason": "next not in dependencies",
            "checked_routes": [],
            "findings": [],
        }

    # 2. Map changed files to routes
    routes = _map_files_to_routes(changed_files)
    if not routes:
        return {
            "status": "skipped",
            "adapter": ADAPTER_NAME,
            "reason": "no_renderable_routes_in_changed_set",
            "checked_routes": [],
            "findings": [],
        }

    # 3. Find a free port
    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    # Determine the dev command
    scripts = pkg.get("scripts", {})
    if "dev" in scripts:
        cmd = ["npm", "run", "dev", "--", "--port", str(port)]
    else:
        cmd = ["npx", "next", "dev", "--port", str(port)]

    proc: subprocess.Popen | None = None
    findings: list[dict] = []

    try:
        # 4a. Spawn the dev server
        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        boot_deadline = time.monotonic() + BOOT_TIMEOUT_SECONDS
        total_deadline = start_time + TOTAL_TIMEOUT_SECONDS

        # 4b. Wait for ready signal or timeout
        ready = _wait_for_server(proc, port, min(boot_deadline, total_deadline))
        if not ready:
            return {
                "status": "fail",
                "adapter": ADAPTER_NAME,
                "reason": "dev_server_timeout",
                "checked_routes": routes,
                "findings": [],
            }

        # 4c-4e. Check each route
        for route in routes:
            if time.monotonic() >= total_deadline:
                findings.append({
                    "route": route,
                    "http_status": None,
                    "render_status": "fail",
                    "finding": "adapter_timeout — ran out of wall-clock budget",
                })
                continue

            finding = _check_route(base_url, route)

            # 4d. Also check stderr for hydration mismatches (captured after GET)
            # Drain any available stderr output for this route
            if proc.stderr and hasattr(proc.stderr, "read1"):
                try:
                    err_chunk = proc.stderr.read1(8192)  # type: ignore[attr-defined]
                    err_text = err_chunk.decode("utf-8", errors="replace") if err_chunk else ""
                    if "Hydration" in err_text or "hydration" in err_text:
                        if finding["render_status"] != "fail":
                            finding["render_status"] = "fail"
                            finding["finding"] = "Hydration mismatch detected in server stderr"
                except OSError:
                    pass

            findings.append(finding)

        # Check total wall-clock
        if time.monotonic() >= total_deadline:
            return {
                "status": "fail",
                "adapter": ADAPTER_NAME,
                "reason": "adapter_timeout",
                "checked_routes": routes,
                "findings": findings,
            }

        # 4g. Evaluate overall status
        any_fail = any(f["render_status"] == "fail" for f in findings)
        return {
            "status": "fail" if any_fail else "pass",
            "adapter": ADAPTER_NAME,
            "checked_routes": routes,
            "findings": findings,
        }

    except Exception as exc:  # noqa: BLE001
        return {
            "status": "fail",
            "adapter": ADAPTER_NAME,
            "reason": f"adapter_error: {exc}",
            "checked_routes": routes,
            "findings": findings,
        }
    finally:
        # 4f + resource hygiene: always kill the dev server
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
