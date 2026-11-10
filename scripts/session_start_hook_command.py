#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""The SessionStart hook commands in hooks/hooks.json, from one template.

``LEGACY`` is the five shell hooks as they were registered before the native
path existed: (name, command, host timeout in seconds, matcher group). They
are the fallback for the comparison period and the oracle for
tests/hook_parity/test_session_start_parity.py.

Why six entries and not one. A host reads a hook's stdout as JSON only when
it is exactly one JSON object (Claude Code: code.claude.com/docs/en/hooks
"Exit code 0"; Codex: developers.openai.com/codex/hooks "SessionStart"), and
runs every matching hook concurrently. Five hooks therefore deliver five
separate contexts, and one process can deliver only one object. So:

- one new entry runs ``bin/native/bl hook session-start`` behind the
  freshness guard and prints ONE object whose ``additionalContext`` joins the
  sub-steps' contexts in order (``merge`` below is the rule; ``bl`` implements
  the same rule and the parity suite compares them byte for byte);
- each legacy entry keeps its exact old command behind the NEGATED guard, so
  with the binary absent, stale or switched off the host runs today's five
  commands unchanged, and with a fresh binary each is one shell that starts
  nothing.

Removing the shell hooks later is: delete the five legacy entries (set
``LEGACY = ()``), run ``--write``, delete the hook files.

``python3 scripts/session_start_hook_command.py --write`` rewrites hooks.json
and the Codex copy hooks/codex.json; ``--check`` exits 1 when either differs.
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
# No `exec`: bl's parent stays this shell, as each hook's parent was.
NATIVE = GUARD + ' "$r/bin/native/bl" hook session-start --root "$r" 2>/dev/null; fi; exit 0'
# tool-state (2 s host limit today) runs first, then at most the longest
# remaining hook (5 s), plus a second of margin; today's five limits sum to 14.
NATIVE_TIMEOUT = 8


def legacy_command(command: str) -> str:
    """The old command, skipped when the native entry handles the event."""
    return f"{GUARD} exit 0; fi; {command}"


def groups() -> list[dict]:
    """The SessionStart registration: the native entry, then each legacy
    hook in its original matcher group and order."""
    out: list[dict] = [{"matcher": "", "hooks": [
        {"type": "command", "command": NATIVE, "timeout": NATIVE_TIMEOUT}]}, {"hooks": []}]
    for _name, command, timeout, group in LEGACY:
        out[group]["hooks"].append({"type": "command", "command": legacy_command(command), "timeout": timeout})
    return [g for g in out if g["hooks"]]


def render(text: str) -> str:
    data = json.loads(text)
    known = {NATIVE} | {c for _n, c, _t, _g in LEGACY} | {legacy_command(c) for _n, c, _t, _g in LEGACY}
    for group in data["hooks"].get("SessionStart", []):
        for hook in group["hooks"]:
            if hook.get("command") not in known and "hook session-start" not in hook.get("command", ""):
                raise SystemExit(f"session_start_hook_command: unknown SessionStart hook: {hook.get('command')!r}")
    data["hooks"]["SessionStart"] = groups()
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
        print(f"SessionStart commands differ in {', '.join(stale)}; "
              "run: python3 scripts/session_start_hook_command.py --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
