#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
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
    python3 native_driver.py resolve --app "MyApp"

    # Scan AX tree of running app
    python3 native_driver.py scan --app "MyApp" --json
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

    # Launch an isolated instance under a private state dir and capture its pid
    # (see SKILL.md "Single-instance PID-scoped verification mode")
    python3 native_driver.py launch --app-path /Applications/MyApp.app \\
        --state-env-var ET_STATE_DIR --state-dir /tmp/myapp-verify --fresh

    # Analyze a captured AX tree for centered-narrow layout gaps
    python3 native_driver.py analyze-layout --stdin < ax-tree.json

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
import time
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────

# This file lives at:
#   <plugin-root>/skills/native-ax-driver/scripts/native_driver.py
# Swift source:
#   <plugin-root>/skills/native-ax-driver/swift/bl-ax-driver/
# Cached binary (in the *consumer* project, NOT the plugin):
#   <consumer-cwd>/.build-loop/bin/bl-ax-driver

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from layout_fill import analyze_layout_fill

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


def _query_gui_processes() -> list[dict]:
    """Query System Events for every foreground GUI process: name, pid, bundle id.

    Raises FileNotFoundError (no osascript) or subprocess.CalledProcessError
    (AppleScript failed) — callers decide how to surface those.
    """
    script = (
        'tell application "System Events" to '
        'get {name, unix id, bundle identifier} of every process '
        "whose background only is false"
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
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
    return apps


def _gui_pids(bundle_id: str | None = None) -> set[int]:
    """Pid set of running GUI processes, optionally filtered to a bundle id.

    Swallows osascript failures and returns an empty set — callers that need
    to distinguish "no processes" from "query failed" should call
    `_query_gui_processes()` directly instead.
    """
    try:
        apps = _query_gui_processes()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return set()
    pids: set[int] = set()
    for app in apps:
        if app["pid"] is None:
            continue
        if bundle_id is not None and app.get("bundleIdentifier") != bundle_id:
            continue
        pids.add(app["pid"])
    return pids


def cmd_apps(_args: argparse.Namespace) -> int:
    """List regular GUI apps with their pid and bundle identifier (no AX needed)."""
    try:
        apps = _query_gui_processes()
    except FileNotFoundError:
        print(json.dumps({"error": "osascript missing"}), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(json.dumps({"error": e.stderr.strip() or "osascript failed"}), file=sys.stderr)
        return 1

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


def select_new_pid(before: set[int], after: set[int]) -> int | None:
    """Return the single new pid in `after - before`, or None if 0 or >1.

    Pure and deterministic — the caller must treat None as "ambiguous, do
    not guess" rather than picking an arbitrary candidate.
    """
    new = after - before
    if len(new) == 1:
        return next(iter(new))
    return None


def build_launch_env(base_env: dict, state_env_var: str | None, state_dir: str | None) -> dict:
    """Return a COPY of base_env with state_env_var=state_dir set, iff both given.

    Never mutates base_env.
    """
    env = dict(base_env)
    if state_env_var and state_dir:
        env[state_env_var] = state_dir
    return env


def build_open_command(
    target: str, *, by_bundle_id: bool, fresh: bool, args: list[str]
) -> list[str]:
    """Build the macOS `open` argv for an isolated-instance launch.

    Always forces a new instance (`-n`). Adds `-F` (ignore saved window/scene
    state) when `fresh` — never wedge into the app's saved state from a prior
    run. Targets by bundle id (`-b`) or by `.app` path. `--args` is only
    appended when there are extra argv to pass through.
    """
    cmd = ["open", "-n"]
    if fresh:
        cmd.append("-F")
    if by_bundle_id:
        cmd += ["-b", target]
    else:
        cmd.append(target)
    if args:
        cmd.append("--args")
        cmd += list(args)
    return cmd


def _derive_bundle_id(app_path: str) -> str | None:
    """Best-effort CFBundleIdentifier lookup from an .app's Info.plist.

    Used only to filter candidate pids and for the launch report — never
    raises; returns None on any failure (missing `defaults`, malformed
    bundle, timeout).
    """
    info_plist_prefix = str(Path(app_path) / "Contents" / "Info")
    try:
        result = subprocess.run(
            ["defaults", "read", info_plist_prefix, "CFBundleIdentifier"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    bundle_id = result.stdout.strip()
    return bundle_id or None


def cmd_launch(args: argparse.Namespace) -> int:
    """Launch an isolated app instance under a private state dir and capture its pid.

    This is the deterministic launch + pid-capture half of the "single-instance
    PID-scoped verification mode" (see SKILL.md). It never assumes which pid
    belongs to the new instance — it snapshots the running GUI pid set before
    launch, launches with `open -n` (always a new instance), and diffs the
    pid set after launch. Ambiguous results (zero or more than one new pid)
    are reported as failure rather than guessed.
    """
    if bool(args.app_path) == bool(args.bundle_id):
        # The argparse mutually-exclusive required group should prevent this;
        # guard defensively so a programmatic caller gets a clean error too.
        print(
            json.dumps(
                {
                    "success": False,
                    "pid": None,
                    "error": "exactly one of --app-path/--bundle-id required",
                }
            ),
            file=sys.stderr,
        )
        return 2

    if args.bundle_id:
        target = args.bundle_id
        by_bundle_id = True
        bundle_id = args.bundle_id
    else:
        target = str(args.app_path)
        by_bundle_id = False
        bundle_id = _derive_bundle_id(target)

    def _fail(error: str) -> int:
        print(
            json.dumps(
                {
                    "success": False,
                    "pid": None,
                    "bundle_id": bundle_id,
                    "state_dir": args.state_dir,
                    "fresh": args.fresh,
                    "error": error,
                    "next": None,
                }
            )
        )
        return 1

    before = _gui_pids()
    env = build_launch_env(dict(os.environ), args.state_env_var, args.state_dir)
    open_argv = build_open_command(
        target, by_bundle_id=by_bundle_id, fresh=args.fresh, args=args.args or []
    )

    try:
        proc = subprocess.run(open_argv, env=env, capture_output=True, text=True, timeout=15)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return _fail(f"`open` failed to run: {e}")

    if proc.returncode != 0:
        return _fail(proc.stderr.strip() or f"open exited {proc.returncode}")

    deadline = time.monotonic() + args.timeout
    pid: int | None = None
    while time.monotonic() < deadline:
        after = _gui_pids(bundle_id=bundle_id)
        pid = select_new_pid(before, after)
        if pid is not None:
            break
        time.sleep(0.25)

    if pid is None:
        return _fail(
            "could not capture a single new pid (zero or ambiguous candidates) "
            "within timeout"
        )

    print(
        json.dumps(
            {
                "success": True,
                "pid": pid,
                "bundle_id": bundle_id,
                "state_dir": args.state_dir,
                "fresh": args.fresh,
                "error": None,
                "next": (
                    f"drive AX scoped to this pid: native_driver.py scan --pid {pid} "
                    f"/ action --pid {pid} ..."
                ),
            }
        )
    )
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


def _strip_window_header(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("WINDOW:"):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def _load_ax_roots(text: str) -> list[dict] | dict:
    return json.loads(_strip_window_header(text))


def _scan_for_layout(args: argparse.Namespace) -> tuple[str | None, str | None]:
    binary = ensure_binary()
    cmd: list[str] = [str(binary)]
    if args.pid is not None:
        cmd += ["--pid", str(args.pid)]
    elif args.app:
        cmd += ["--app", args.app]
    else:
        return None, "analyze-layout requires --from-file, --stdin, --pid, or --app"

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return None, proc.stderr.strip() or f"scan failed with exit {proc.returncode}"
    return proc.stdout, None


def _emit_layout_envelope(
    *,
    status: str,
    artifacts: list[str],
    verification: str,
    findings: list[dict],
    error: str | None = None,
) -> None:
    payload = {
        "status": status,
        "route": "native",
        "verifier": "native-ax-driver",
        "artifacts": artifacts,
        "verification": verification,
        "findings": findings,
    }
    if error:
        payload["error"] = error
    print(json.dumps(payload, indent=2))


def cmd_analyze_layout(args: argparse.Namespace) -> int:
    artifacts: list[str]
    try:
        if args.from_file:
            artifacts = [str(args.from_file)]
            source = Path(args.from_file).read_text()
        elif args.stdin:
            artifacts = ["stdin"]
            source = sys.stdin.read()
        else:
            artifacts = [f"pid:{args.pid}" if args.pid is not None else f"app:{args.app}"]
            source, error = _scan_for_layout(args)
            if error:
                _emit_layout_envelope(
                    status="failed",
                    artifacts=artifacts,
                    verification=f"native-ax-driver analyze-layout failed: {error}",
                    findings=[],
                    error=error,
                )
                return 0

        roots = _load_ax_roots(source)
        raw_findings = analyze_layout_fill(
            roots,
            threshold=args.threshold,
            min_container_px=args.min_container_px,
        )
        findings = [
            {
                "severity": "warning",
                "category": "structure",
                "message": f"layout-fill: {finding.get('detail', '')}",
                "finding": finding,
            }
            for finding in raw_findings
        ]
        count = len(findings)
        plural = "" if count == 1 else "s"
        _emit_layout_envelope(
            status="ran",
            artifacts=artifacts,
            verification=(
                f"native-ax-driver analyze-layout ran; found {count} "
                f"layout-fill finding{plural}."
            ),
            findings=findings,
        )
        return 0
    except Exception as exc:  # Keep analyzer failures data-shaped for bridge callers.
        error = str(exc)
        _emit_layout_envelope(
            status="failed",
            artifacts=artifacts if "artifacts" in locals() else [],
            verification=f"native-ax-driver analyze-layout failed: {error}",
            findings=[],
            error=error,
        )
        return 0


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

    p_launch = sub.add_parser(
        "launch",
        help="Launch an isolated app instance under a private state dir and capture its pid",
    )
    launch_target = p_launch.add_mutually_exclusive_group(required=True)
    launch_target.add_argument("--app-path", help="Path to a .app bundle")
    launch_target.add_argument("--bundle-id", help="Bundle identifier, e.g. com.example.myapp")
    p_launch.add_argument("--state-dir", help="Isolated state directory for this instance")
    p_launch.add_argument(
        "--state-env-var",
        help="Env var name to point at --state-dir, e.g. ET_STATE_DIR",
    )
    p_launch.add_argument(
        "--fresh",
        action="store_true",
        help="Pass -F to `open` (ignore saved window/scene state)",
    )
    p_launch.add_argument(
        "--arg",
        action="append",
        dest="args",
        help="Extra argv passed through to the launched app (repeatable)",
    )
    p_launch.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for the new pid to appear (default 10.0)",
    )

    p_scan = sub.add_parser("scan", help="Scan AX tree of running app")
    p_scan.add_argument("--pid", type=int)
    p_scan.add_argument("--app")
    p_scan.add_argument("--device-name", help="Pin to a specific iOS sim device window")

    p_analyze = sub.add_parser(
        "analyze-layout",
        help="Analyze AX tree for centered-narrow layout-fill gaps",
    )
    source = p_analyze.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-file", type=Path, help="Read Swift scan JSON from a file")
    source.add_argument("--stdin", action="store_true", help="Read Swift scan JSON from stdin")
    source.add_argument("--pid", type=int, help="Scan this pid and analyze the result")
    source.add_argument("--app", help="Scan this app name and analyze the result")
    p_analyze.add_argument("--threshold", type=float, default=0.12)
    p_analyze.add_argument("--min-container-px", type=float, default=50.0)

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
        "launch": cmd_launch,
        "scan": cmd_scan,
        "analyze-layout": cmd_analyze_layout,
        "action": cmd_action,
    }

    try:
        return handlers[args.cmd](args)
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
