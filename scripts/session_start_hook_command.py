#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""One SessionStart registration, with native dispatch and bounded recovery.

The fresh native binary aggregates five sub-step contexts. When it is absent,
stale or disabled, --fallback runs the LEGACY commands with their own budgets
and merges their output by the same rule. LEGACY also remains the parity oracle.
No fallback interpreter is started on the fresh native path.

--write / --check generate hooks/hooks.json and hooks/codex.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
FILES = (HOOKS_DIR / "hooks.json", HOOKS_DIR / "codex.json")

_R = '"${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/hooks/'
LEGACY = (
    ("tool-state", f"bash {_R}session-start-tool-state.sh\" 2>/dev/null; exit 0", 2, 0),
    ("architecture", f"bash {_R}session-start-architecture.sh\" </dev/null >/dev/null 2>&1; exit 0", 2, 0),
    ("rally-point", f"bash {_R}session-start-rally-point.sh\" 2>/dev/null; exit 0", 2, 0),
    ("closeout", f"bash {_R}closeout.sh\" session-start </dev/null 2>/dev/null; exit 0", 3, 0),
    ("codex-hook-trust",
     f"bash {_R}session-start-codex-hook-trust.sh\" </dev/null 2>/dev/null || printf '{{}}'", 5, 1),
)

CONTEXT_SEPARATOR = "\n\n"


def context_of(stdout: bytes) -> str | None:
    """What one hook's stdout adds to the session, by the hosts' parse rule.

    One JSON object: its ``hookSpecificOutput.additionalContext`` (a non-empty
    string) or nothing. Text that starts with ``{`` and ends with ``}`` but is
    not one object: a parse failure, nothing added. Any other non-blank text:
    added as plain text.
    """
    text = stdout.decode("utf-8", "replace")
    stripped = text.strip(" \t\r\n")
    if not stripped:
        return None
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            value = json.loads(stripped)
        except ValueError:
            return None
        if not isinstance(value, dict):
            return None
        specific = value.get("hookSpecificOutput")
        context = specific.get("additionalContext") if isinstance(specific, dict) else None
        return context if isinstance(context, str) and context else None
    return stripped


def merge(stdouts: list[bytes]) -> bytes:
    """The single stdout ``bl hook session-start`` prints for these hook
    stdouts, in order: one object, or ``{}`` when no hook adds context."""
    contexts = [c for c in (context_of(s) for s in stdouts) if c is not None]
    if not contexts:
        return b"{}"
    payload = {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                      "additionalContext": CONTEXT_SEPARATOR.join(contexts)}}
    return (json.dumps(payload) + "\n").encode()


# The freshness guard of scripts/trace_hook_command.py: shell builtins only.
GUARD = " ".join([
    'r="${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}";',
    'if [ "${BUILD_LOOP_NATIVE_HOOKS:-}" != off ] && [ -x "$r/bin/native/bl" ]',
    '&& { read -r a < "$r/bin/native/bl.sources"; } 2>/dev/null',
    '&& { read -r b < "$r/crates/SOURCES.digest"; } 2>/dev/null',
    '&& [ -n "$b" ] && [ "$a" = "$b" ]; then',
])
# No `exec`: preserve the observer parent while forwarding host cancellation.
# Wait status, not PID liveness, tells us when the child has been reaped.
CHILD_RUNNER = " ".join([
    '_bl_session_child() { p=""; cancelled=0; interrupted=0;',
    "trap 'cancelled=1; interrupted=1; if [ -n \"$p\" ]; then kill -TERM \"$p\" 2>/dev/null || :; fi' HUP INT TERM;",
    '"$@" <&0 & p=$!;',
    'if [ "$cancelled" = 1 ]; then kill -TERM "$p" 2>/dev/null || :; fi;',
    'while :; do interrupted=0; child_rc=0; wait "$p" || child_rc=$?;',
    'if [ "$child_rc" -le 128 ] || [ "$interrupted" = 0 ]; then break; fi; done;',
    'trap - HUP INT TERM; if [ "$cancelled" = 1 ]; then exit 0; fi; return "$child_rc"; };',
])
RECOVERY = (
    'if command -v python3 >/dev/null 2>&1; then '
    '_bl_session_child python3 "$r/scripts/session_start_hook_command.py" --fallback "$r" 2>/dev/null; '
    "else printf '{}'; fi"
)
NATIVE = CHILD_RUNNER + " " + GUARD + (
    ' _bl_session_child "$r/bin/native/bl" hook session-start --root "$r" 2>/dev/null; rc=$?; '
    # These codes guarantee the hook never read its event. Do not replay after
    # arbitrary failures: a started worker could already have changed state.
    'case "$rc" in 75|126|127) ' + RECOVERY + ';; esac; '
    'else ' + RECOVERY + '; fi; exit 0'
)
# tool-state (2 s host limit today) runs first, then at most the longest
# remaining hook (5 s), plus a second of margin; today's five limits sum to 14.
NATIVE_TIMEOUT = 8


def legacy_command(command: str) -> str:
    """The old command, skipped when the native entry handles the event."""
    return f"{GUARD} exit 0; fi; {command}"


def groups() -> list[dict]:
    """Exactly one host command and one JSON response on either runtime path."""
    return [{"matcher": "", "hooks": [
        {"type": "command", "command": NATIVE, "timeout": NATIVE_TIMEOUT}]}]


def render(text: str) -> str:
    data = json.loads(text)
    known = {NATIVE} | {c for _n, c, _t, _g in LEGACY} | {legacy_command(c) for _n, c, _t, _g in LEGACY}
    for group in data["hooks"].get("SessionStart", []):
        for hook in group["hooks"]:
            if hook.get("command") not in known and "hook session-start" not in hook.get("command", ""):
                raise SystemExit(f"session_start_hook_command: unknown SessionStart hook: {hook.get('command')!r}")
    data["hooks"]["SessionStart"] = groups()
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def fallback(root: str, payload: bytes | None, legacy=None) -> bytes:
    """Recovery-only aggregation, with the same event copied to every command.

    Tool-state completes before coordination starts. Other steps are independent
    and run concurrently, so their original per-hook budgets fit the 8s host limit.
    """
    import concurrent.futures
    import os
    import signal
    import select
    import subprocess
    import threading
    import time

    commands = LEGACY if legacy is None else legacy
    started = time.monotonic()
    raw_limit = os.environ.get("BUILD_LOOP_SESSION_START_DEADLINE_MS", "7500")
    limit = int(raw_limit) if raw_limit.isascii() and raw_limit.isdigit() else 7500
    limit = limit if 100 <= limit <= 7500 else 7500
    overall_until = started + limit / 1000
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=root)
    # Recovery must not skip itself or redispatch the unusable fresh binary.
    env["BUILD_LOOP_NATIVE_HOOKS"] = "off"
    env["BUILD_LOOP_SESSION_START_AGGREGATOR_PARENT_PID"] = str(os.getppid())
    children = set()
    lock = threading.Lock()
    cancelled = threading.Event()

    def stop(proc):
        # TERM reaches the legacy supervisor, which cleans its own child group.
        # Leave enough grace for that supervisor's 70ms bounded cleanup.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                pass
            if sig == signal.SIGTERM:
                time.sleep(0.1)

    def run(item, event):
        _name, command, timeout, _group = item
        with lock:
            if cancelled.is_set():
                return b"{}"
            try:
                proc = subprocess.Popen(["/bin/sh", "-c", command], env=env,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                return b"{}"
            children.add(proc)
        try:
            until = min(time.monotonic() + timeout, overall_until)
            next_input = event
            while not cancelled.is_set() and time.monotonic() < until:
                try:
                    stdout, _ = proc.communicate(next_input, timeout=min(.05, max(.001, until-time.monotonic())))
                    return stdout if not cancelled.is_set() else b"{}"
                except subprocess.TimeoutExpired:
                    next_input = None
            stop(proc)
            try:
                proc.communicate(timeout=0.2)
            except (OSError, subprocess.TimeoutExpired):
                pass
            return b"{}"
        except OSError:
            stop(proc)
            return b"{}"
        finally:
            for pipe in (proc.stdin, proc.stdout):
                if pipe is not None:
                    pipe.close()
            with lock:
                children.discard(proc)

    def cancel(_sig, _frame):
        # A flag avoids interrupting Popen between spawn and child registration.
        cancelled.set()

    previous = {sig: signal.signal(sig, cancel)
                for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(commands)))
    try:
        if not commands:
            return b"{}"
        independent = {i: pool.submit(run, item, b"") for i, item in enumerate(commands)
                       if item[0] in ("architecture", "closeout", "codex-hook-trust")}
        if payload is None:
            # Legacy event readers cap at 64 KiB and the tool-state supervisor
            # gives stdin at most 1.5s. Independent contexts must not wait for EOF.
            data = bytearray()
            read_until = min(overall_until, started + 1.5)
            while len(data) < 65536 and not cancelled.is_set() and time.monotonic() < read_until:
                ready, _, _ = select.select([sys.stdin.fileno()], [], [],
                    min(.05, max(0, read_until-time.monotonic())))
                if ready:
                    chunk = os.read(sys.stdin.fileno(), min(8192, 65536-len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
            payload = bytes(data)
        outputs = [b"{}"] * len(commands)
        dependent = [i for i in range(len(commands)) if i not in independent]
        if dependent and not cancelled.is_set():
            first = dependent.pop(0)
            outputs[first] = run(commands[first], payload)
        futures = {**independent, **{i: pool.submit(run, commands[i], payload) for i in dependent}}
        for i, future in futures.items():
            outputs[i] = future.result()
        return b"{}" if cancelled.is_set() else merge(outputs)
    finally:
        cancelled.set()
        # A worker cannot register a new child after this lock has been taken.
        with lock:
            active = list(children)
        for proc in active:
            stop(proc)
        pool.shutdown(wait=True, cancel_futures=True)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--fallback":
        try:
            sys.stdout.buffer.write(fallback(argv[1], None))
        except (OSError, ValueError):
            sys.stdout.buffer.write(b"{}")
        return 0
    if argv not in (["--write"], ["--check"]):
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    stale = []
    for path in FILES:
        text = path.read_text(encoding="utf-8")
        want = render(text)
        if want != text:
            stale.append(path.name)
            if argv == ["--write"]:
                path.write_text(want, encoding="utf-8")
    if stale and argv == ["--check"]:
        print(f"SessionStart commands differ in {', '.join(stale)}; "
              "run: python3 scripts/session_start_hook_command.py --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
