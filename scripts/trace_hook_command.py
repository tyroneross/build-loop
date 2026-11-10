#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""The three tool-trace hook commands in hooks/hooks.json, from one template.

The host runs each with ``/bin/sh -c`` (POSIX sh; bash 3.2 in POSIX mode on
macOS), so the template uses no bashisms. When ``.build-loop/state.json``
exists it starts ``bin/native/bl hook trace`` directly, with no extra shell,
only when the binary is executable, ``BUILD_LOOP_NATIVE_HOOKS`` is not
``off`` and line 1 of ``bin/native/bl.sources`` equals line 1 of
``crates/SOURCES.digest`` (read with builtins). Any guard failure or any
non-zero exit from the binary (75: did nothing; 126/127: could not start; a
crash or signal) runs the Python hook exactly as the command did before the
native path existed. A late fallback cannot duplicate a line: ``bl`` reads
stdin to EOF before its only append, so a Python run after it reads nothing
and writes nothing. The brace group sends the shell's own "Killed: 9" job
message to /dev/null.

``python3 scripts/trace_hook_command.py --write`` rewrites hooks.json and the
Codex copy hooks/codex.json (which must equal hooks.json minus
PostToolUseFailure; scripts/test_hook_repair_deadlines.py asserts it);
``--check`` exits 1 when either differs (also asserted by
tests/hook_parity/test_trace_parity.py).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
HOOKS_JSON = HOOKS_DIR / "hooks.json"
FILES = (HOOKS_JSON, HOOKS_DIR / "codex.json")
EVENTS = {"start": "PreToolUse", "end": "PostToolUse", "error": "PostToolUseFailure"}
MARKER = "/scripts/tool_trace.py"

TEMPLATE = " ".join([
    'if [ -f "${CLAUDE_PROJECT_DIR:-$PWD}/.build-loop/state.json" ]; then',
    'r="${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}"; s=75;',
    'if [ "${BUILD_LOOP_NATIVE_HOOKS:-}" != off ] && [ -x "$r/bin/native/bl" ]',
    '&& { read -r a < "$r/bin/native/bl.sources"; } 2>/dev/null',
    '&& { read -r b < "$r/crates/SOURCES.digest"; } 2>/dev/null',
    '&& [ -n "$b" ] && [ "$a" = "$b" ]; then',
    '{ "$r/bin/native/bl" hook trace --workdir "$CLAUDE_PROJECT_DIR" --phase {phase} >/dev/null 2>&1; s=$?; } 2>/dev/null;',
    'fi;',
    'case $s in 0) ;; *)',
    'python3 "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/scripts/tool_trace.py"'
    ' --workdir "$CLAUDE_PROJECT_DIR" hook --phase {phase} >/dev/null 2>&1 || true;;',
    'esac;',
    'fi',
])


def command(phase: str) -> str:
    return TEMPLATE.replace("{phase}", phase)


def trace_hooks(data: dict) -> dict[str, dict]:
    """phase -> the one entry that runs tool_trace.py for it (events the
    file does not register, such as Codex's PostToolUseFailure, are skipped)."""
    found: dict[str, dict] = {}
    for phase, event in EVENTS.items():
        if event not in data["hooks"]:
            continue
        entries = [hk for group in data["hooks"][event] for hk in group["hooks"] if MARKER in hk.get("command", "")]
        if len(entries) != 1:
            raise SystemExit(f"trace_hook_command: expected one trace hook under {event}, found {len(entries)}")
        found[phase] = entries[0]
    return found


def render(text: str) -> str:
    data = json.loads(text)
    for phase, entry in trace_hooks(data).items():
        entry["command"] = command(phase)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
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
        print(f"trace commands differ in {', '.join(stale)}; run: python3 scripts/trace_hook_command.py --write",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
