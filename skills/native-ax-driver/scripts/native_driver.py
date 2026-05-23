#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""
Build-loop native AX driver — Python launcher.

Wraps the vendored Swift extractor (`swift/bl-ax-driver/`). Compiles on first
use, caches the binary at `.build-loop/bin/bl-ax-driver`, rebuilds when the
Swift source is newer than the binary.

All actions go through macOS Accessibility APIs. The driver does NOT inject
synthetic mouse or keyboard events — the user's hardware cursor is never moved.

Usage (from a build-loop run, project cwd):

    # Resolve a running app to its pid (no AX permission needed for this)
    python3 native_driver.py resolve --app "Secrets Vault"

    # Scan AX tree of running app
    python3 native_driver.py scan --app "Secrets Vault" --json
    python3 native_driver.py scan --pid 12345 --json

    # Perform an AX action on a specific element
    python3 native_driver.py action --pid 12345 \\
        --element-path 0,2,1 --action press

    # Set a text-field value
    python3 native_driver.py action --pid 12345 \\
        --element-path 0,4,0 --action setValue --value "secret"

    # Pre-flight permission check (returns 0 if AX granted, 2 if not)
    python3 native_driver.py preflight

    # List running regular GUI apps (no AX permission needed)
    python3 native_driver.py apps

The launcher is intentionally stdlib-only (no pip deps). Calling code can shell
out from any orchestrator phase or skill.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────

# This file lives at:
#   <plugin-root>/skills/native-ax-driver/scripts/native_driver.py
# Swift source:
#   <plugin-root>/skills/native-ax-driver/swift/bl-ax-driver/
# Cached binary (in the *consumer* project, NOT the plugin):
#   <consumer-cwd>/.build-loop/bin/bl-ax-driver

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent  # skills/native-ax-driver
SWIFT_SOURCE_DIR = SKILL_DIR / "swift" / "bl-ax-driver"
SWIFT_MAIN = SWIFT_SOURCE_DIR / "Sources" / "main.swift"
SWIFT_PACKAGE = SWIFT_SOURCE_DIR / "Package.swift"
SWIFT_BUILT_BINARY = SWIFT_SOURCE_DIR / ".build" / "release" / "bl-ax-driver"


def consumer_bin_dir() -> Path:
    """Directory the compiled binary is cached in, in the consumer project."""
    cwd_bl = Path.cwd() / ".build-loop" / "bin"
    return cwd_bl


def cached_binary_path() -> Path:
    return consumer_bin_dir() / "bl-ax-driver"


# ─── Build the Swift extractor ────────────────────────────────────────────────


def _is_fresh(binary: Path) -> bool:
    """Binary is fresh iff it's newer than both Swift sources."""
    if not binary.exists():
        return False
    try:
        bin_mtime = binary.stat().st_mtime
        src_mtime = max(SWIFT_MAIN.stat().st_mtime, SWIFT_PACKAGE.stat().st_mtime)
        return bin_mtime >= src_mtime
    except FileNotFoundError:
        return False


def _build_swift() -> None:
    if not SWIFT_PACKAGE.exists() or not SWIFT_MAIN.exists():
        raise RuntimeError(
            f"Swift sources missing under {SWIFT_SOURCE_DIR}. "
            "The native-ax-driver skill is not vendored correctly."
        )
    if shutil.which("swift") is None:
        raise RuntimeError(
            "`swift` not found on PATH. Install Xcode Command Line Tools: "
            "`xcode-select --install`"
        )
    try:
        subprocess.run(
            ["swift", "build", "-c", "release"],
            cwd=SWIFT_SOURCE_DIR,
            check=True,
            timeout=120,
        )
    except subprocess.CalledProcessError:
        # Some sandboxed shells need --disable-sandbox
        subprocess.run(
            ["swift", "build", "-c", "release", "--disable-sandbox"],
            cwd=SWIFT_SOURCE_DIR,
            check=True,
            timeout=120,
        )


def ensure_binary() -> Path:
    """Compile-on-first-use, cache in consumer's .build-loop/bin/. Idempotent."""
    cached = cached_binary_path()
    if _is_fresh(cached):
        return cached

    if not _is_fresh(SWIFT_BUILT_BINARY):
        _build_swift()

    if not SWIFT_BUILT_BINARY.exists():
        raise RuntimeError(
            f"Swift build reported success but binary missing at {SWIFT_BUILT_BINARY}"
        )

    consumer_bin_dir().mkdir(parents=True, exist_ok=True)
    shutil.copy2(SWIFT_BUILT_BINARY, cached)
    cached.chmod(0o755)
    return cached


# ─── Subcommand implementations ───────────────────────────────────────────────


def cmd_preflight(_args: argparse.Namespace) -> int:
    """Quickest possible AX-permission probe."""
    if shutil.which("osascript") is None:
        print(json.dumps({"granted": False, "error": "osascript not found"}))
        return 2
    try:
        # The least-surprising AX op: read frontmost process name.
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get the name of '
                "(first process whose frontmost is true)",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        granted = result.returncode == 0 and bool(result.stdout.strip())
        payload = {"granted": granted}
        if not granted:
            payload["error"] = (
                result.stderr.strip()
                or "Grant Accessibility permission in System Settings > Privacy & Security."
            )
        print(json.dumps(payload))
        return 0 if granted else 2
    except subprocess.TimeoutExpired:
        print(json.dumps({"granted": False, "error": "AX preflight timed out"}))
        return 2


def cmd_apps(_args: argparse.Namespace) -> int:
    """List regular GUI apps with their pid and bundle identifier (no AX needed)."""
    script = (
        'tell application "System Events" to '
        'get {name, unix id, bundle identifier} of every process '
        "whose background only is false"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except FileNotFoundError:
        print(json.dumps({"error": "osascript missing"}), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(json.dumps({"error": e.stderr.strip() or "osascript failed"}), file=sys.stderr)
        return 1

    raw = result.stdout.strip()
    # AppleScript returns a flat list: name1, name2, ..., pid1, pid2, ..., bundle1, ...
    parts = [p.strip() for p in raw.split(",")]
    n = len(parts) // 3 if len(parts) % 3 == 0 else 0
    apps = []
    for i in range(n):
        apps.append(
            {
                "name": parts[i],
                "pid": int(parts[n + i]) if parts[n + i].isdigit() else None,
                "bundleIdentifier": parts[2 * n + i] if parts[2 * n + i] != "missing value" else None,
            }
        )
    print(json.dumps(apps, indent=2))
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    binary = ensure_binary()
    proc = subprocess.run(
        [str(binary), "--resolve-app", args.app],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return proc.returncode
    sys.stdout.write(proc.stdout)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    binary = ensure_binary()
    cmd: list[str] = [str(binary)]
    if args.pid is not None:
        cmd += ["--pid", str(args.pid)]
    elif args.app:
        cmd += ["--app", args.app]
    elif args.device_name:
        cmd += ["--device-name", args.device_name]
    else:
        print(
            json.dumps({"error": "scan requires --pid, --app, or --device-name"}),
            file=sys.stderr,
        )
        return 2

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


VALID_ACTIONS = {
    "press",
    "setValue",
    "increment",
    "decrement",
    "showMenu",
    "confirm",
    "cancel",
    "focus",
    "scrollToVisible",
}


def cmd_action(args: argparse.Namespace) -> int:
    if args.action not in VALID_ACTIONS:
        print(
            json.dumps(
                {
                    "success": False,
                    "error": f"invalid --action {args.action!r}; one of: {sorted(VALID_ACTIONS)}",
                }
            ),
            file=sys.stderr,
        )
        return 2
    if args.pid is None:
        print(
            json.dumps({"success": False, "error": "--pid required"}),
            file=sys.stderr,
        )
        return 2
    if not args.element_path:
        print(
            json.dumps({"success": False, "error": "--element-path required (e.g. '0,2,1')"}),
            file=sys.stderr,
        )
        return 2

    binary = ensure_binary()
    cmd = [
        str(binary),
        "--pid",
        str(args.pid),
        "--action",
        args.action,
        "--element-path",
        args.element_path,
    ]
    if args.value is not None:
        cmd += ["--value", args.value]
    if args.device_name:
        cmd += ["--device-name", args.device_name]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


# ─── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="native_driver.py",
        description="Build-loop native macOS AX driver (cursor-free).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preflight", help="Check AX permission")
    sub.add_parser("apps", help="List running regular GUI apps")

    p_resolve = sub.add_parser("resolve", help="Resolve an app name to its pid")
    p_resolve.add_argument("--app", required=True)

    p_scan = sub.add_parser("scan", help="Scan AX tree of running app")
    p_scan.add_argument("--pid", type=int)
    p_scan.add_argument("--app")
    p_scan.add_argument("--device-name", help="Pin to a specific iOS sim device window")

    p_action = sub.add_parser("action", help="Perform AX action on element by index path")
    p_action.add_argument("--pid", type=int, required=True)
    p_action.add_argument(
        "--element-path",
        required=True,
        help="Comma-separated indices, e.g. '0,2,1' from window root",
    )
    p_action.add_argument("--action", required=True, choices=sorted(VALID_ACTIONS))
    p_action.add_argument("--value", help="Required for setValue")
    p_action.add_argument("--device-name", help="iOS sim variant")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "preflight": cmd_preflight,
        "apps": cmd_apps,
        "resolve": cmd_resolve,
        "scan": cmd_scan,
        "action": cmd_action,
    }

    try:
        return handlers[args.cmd](args)
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
