#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""hook_budget_lint.py — deterministic hook timeout-budget lint for build-loop.

A Claude Code hook entry declares an outer ``timeout`` (milliseconds). The
command it runs may itself spawn subprocesses with their OWN timeouts. If any
inner timeout is >= the outer hook timeout, the inner work can still be running
when the harness kills the hook — the hook "fails open" (no result), silently
skipping the check it exists to perform. This is the timeout-inversion defect
class that bit the rally coordination hook twice on 2026-06-09 (commits
2d9e9aa, b104719) and that ``scripts/rally_point/hook_budget.py`` codifies for
the rally path. This lint generalizes the rule across EVERY shipped hook:

    every inner subprocess timeout < its declaring hook entry's timeout.

It also flags hook entries that declare NO ``timeout`` at all (the PostToolUse
Bash entry had none — an unbounded hook can stall a tool call indefinitely).

Rules (all severity=warn — build-loop's "judges route, never stop"):

    HB001 — inner subprocess timeout >= declaring hook's outer timeout
            Reads the referenced script; extracts Python ``timeout=N`` kwargs
            and shell ``timeout N`` invocations (seconds), compares against the
            hook entry's ``timeout`` (ms). Inner_ms = N*1000.
    HB002 — hook entry declares no ``timeout`` field
            An unbounded hook. Default harness timeout is implementation-
            defined; declare one explicitly.

CLI shape::

    python3 scripts/hook_budget_lint.py --hooks hooks/hooks.json [--json]
    python3 scripts/hook_budget_lint.py --self-test

Stdlib only. Exit codes mirror hook_hygiene_lint:
    0  no findings
    1  one or more findings (WARN-level — advisory)
    2  runner error (file not found, JSON parse error, malformed structure)

Style mirrors ``scripts/hook_hygiene_lint.py`` (envelope shape, exit codes,
``--self-test`` mode).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Timeout extraction
# ---------------------------------------------------------------------------

# Python subprocess timeout kwarg: `timeout=5`, `timeout = 2.5`, `timeout=30`.
# Captures the numeric literal only (a dynamic expression like
# `timeout=hook_budget.inner_timeout_seconds(...)` is intentionally NOT matched
# — it is the budget-derived path this lint is steering callers toward).
_PY_TIMEOUT_RE = re.compile(r"\btimeout\s*=\s*(\d+(?:\.\d+)?)")

# Shell `timeout` coreutil: `timeout 5 cmd`, `timeout 2.5s cmd`, `timeout 30s`.
# Captures the duration; an optional trailing unit suffix (s/m/h) is handled.
_SH_TIMEOUT_RE = re.compile(
    r"(?:^|[;&|\n]|\$\(|`)\s*timeout\s+(?:-[a-zA-Z]\s+\S+\s+)?(\d+(?:\.\d+)?)([smh]?)\b"
)

_UNIT_SECONDS = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}

# Reference to an in-repo script inside a hook command string. We resolve
# ${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/<rel> and bare scripts/<rel>.
_SCRIPT_REF_RE = re.compile(
    r"(?:\$\{CLAUDE_PLUGIN_ROOT:-\$CLAUDE_PROJECT_DIR\}/|\$root/|\$\{root\}/|\"\$root\"/)"
    r"([A-Za-z0-9_./-]+\.(?:sh|py))"
)

# A hook command that detaches its work runs it asynchronously: the hook
# returns immediately (typically `printf '{}'` right after), so the detached
# subprocess's own timeout cannot hold the hook past its budget. `nohup ... &`
# is the canonical fire-and-forget pattern; a trailing/embedded job-control `&`
# (not the `&&`/`&>`/`2>&1` operators) also detaches. Such commands are exempt
# from HB001 — their inner timeout is intentionally larger than the hook budget.
_BACKGROUND_RE = re.compile(r"\bnohup\b|(?<![&>])&(?![&>])")


def _is_backgrounded(command: str) -> bool:
    return bool(_BACKGROUND_RE.search(command))


def _seconds_from(value: str, unit: str) -> float:
    return float(value) * _UNIT_SECONDS.get(unit, 1.0)


def _extract_inner_timeouts_seconds(text: str) -> list[tuple[float, str]]:
    """Return (seconds, snippet) for every literal inner timeout in `text`."""
    found: list[tuple[float, str]] = []
    for m in _PY_TIMEOUT_RE.finditer(text):
        found.append((float(m.group(1)), m.group(0)))
    for m in _SH_TIMEOUT_RE.finditer(text):
        found.append((_seconds_from(m.group(1), m.group(2)), f"timeout {m.group(1)}{m.group(2)}"))
    return found


def _referenced_scripts(command: str, root: Path) -> list[Path]:
    """Resolve in-repo scripts a hook command invokes, relative to `root`."""
    out: list[Path] = []
    seen: set[str] = set()
    for m in _SCRIPT_REF_RE.finditer(command):
        rel = m.group(1)
        if rel in seen:
            continue
        seen.add(rel)
        p = (root / rel).resolve()
        if p.is_file():
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Lint core
# ---------------------------------------------------------------------------


def _iter_hook_entries(doc: dict[str, Any]) -> list[tuple[str, str | None, dict[str, Any]]]:
    """Yield (event, matcher, hook_entry) for every command hook in the doc."""
    entries: list[tuple[str, str | None, dict[str, Any]]] = []
    hooks = doc.get("hooks")
    if not isinstance(hooks, dict):
        raise ValueError("hooks.json has no top-level 'hooks' object")
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher")
            for entry in group.get("hooks", []):
                if isinstance(entry, dict) and entry.get("type") == "command":
                    entries.append((event, matcher, entry))
    return entries


def lint_hooks(hooks_path: Path, repo_root: Path | None = None) -> list[dict[str, Any]]:
    """Return a list of finding records for the hooks doc at `hooks_path`."""
    root = repo_root or hooks_path.resolve().parent.parent
    doc = json.loads(hooks_path.read_text(encoding="utf-8"))
    findings: list[dict[str, Any]] = []

    for event, matcher, entry in _iter_hook_entries(doc):
        command = str(entry.get("command", ""))
        outer_ms = entry.get("timeout")
        cmd_trunc = command[:200]

        # HB002 — no declared timeout.
        if outer_ms is None:
            findings.append({
                "rule_id": "HB002",
                "severity": "warn",
                "event": event,
                "matcher": matcher,
                "command": cmd_trunc,
                "script_path": None,
                "message": (
                    "hook entry declares no 'timeout' — an unbounded hook can "
                    "stall the tool call; declare an explicit timeout (ms)."
                ),
                "evidence": {"outer_timeout_ms": None, "inner_timeout_s": None},
            })
            continue

        try:
            outer_ms_val = float(outer_ms)
        except (TypeError, ValueError):
            findings.append({
                "rule_id": "HB002",
                "severity": "warn",
                "event": event,
                "matcher": matcher,
                "command": cmd_trunc,
                "script_path": None,
                "message": f"hook 'timeout' is not numeric: {outer_ms!r}",
                "evidence": {"outer_timeout_ms": outer_ms, "inner_timeout_s": None},
            })
            continue

        # A detached (`nohup ... &`) command returns immediately, so its inner
        # timeouts cannot hold the hook past its budget — exempt from HB001.
        if _is_backgrounded(command):
            continue

        # Collect inner timeouts from the inline command AND any referenced
        # in-repo scripts.
        sources: list[tuple[str, str | None]] = [(command, None)]
        for script in _referenced_scripts(command, root):
            try:
                sources.append((script.read_text(encoding="utf-8"), str(script.relative_to(root))))
            except (OSError, ValueError):
                sources.append((script.read_text(encoding="utf-8"), str(script)))

        for text, script_rel in sources:
            for inner_s, snippet in _extract_inner_timeouts_seconds(text):
                inner_ms = inner_s * 1000.0
                if inner_ms >= outer_ms_val:
                    findings.append({
                        "rule_id": "HB001",
                        "severity": "warn",
                        "event": event,
                        "matcher": matcher,
                        "command": cmd_trunc,
                        "script_path": script_rel,
                        "message": (
                            f"inner timeout {inner_s:g}s ({inner_ms:g}ms) >= hook "
                            f"timeout {outer_ms_val:g}ms — the inner work can still "
                            f"be running when the hook is killed (fail-open). Derive "
                            f"the inner timeout from the hook budget so inner < outer."
                        ),
                        "evidence": {
                            "outer_timeout_ms": outer_ms_val,
                            "inner_timeout_s": inner_s,
                            "snippet": snippet,
                        },
                    })

    return findings


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> int:
    import tempfile

    failures: list[str] = []

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "scripts").mkdir()
        (root / "hooks").mkdir()

        # A script with an inner timeout that EXCEEDS the hook budget.
        bad_script = root / "scripts" / "slow.sh"
        bad_script.write_text("#!/bin/sh\ntimeout 5 git status\n", encoding="utf-8")
        # A script with a safe (smaller) inner timeout.
        good_script = root / "scripts" / "fast.py"
        good_script.write_text("import subprocess\nsubprocess.run(['x'], timeout=1)\n", encoding="utf-8")

        doc = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            # HB001: inner 5s (5000ms) >= outer 2000ms
                            {"type": "command",
                             "command": 'bash "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/scripts/slow.sh"',
                             "timeout": 2000},
                            # OK: inner 1s (1000ms) < outer 2000ms
                            {"type": "command",
                             "command": 'python3 "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/scripts/fast.py"',
                             "timeout": 2000},
                        ],
                    }
                ],
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        # HB002: no timeout field
                        "hooks": [{"type": "command", "command": "echo hi"}],
                    }
                ],
            }
        }
        hp = root / "hooks" / "hooks.json"
        hp.write_text(json.dumps(doc), encoding="utf-8")

        findings = lint_hooks(hp, repo_root=root)
        ids = sorted(f["rule_id"] for f in findings)
        if ids != ["HB001", "HB002"]:
            failures.append(f"expected exactly [HB001, HB002], got {ids}")

        # Inline shell timeout in the command itself should also be caught.
        doc2 = {"hooks": {"Stop": [{"matcher": "", "hooks": [
            {"type": "command", "command": "timeout 30s python3 -c x", "timeout": 5000},
        ]}]}}
        hp2 = root / "hooks" / "hooks2.json"
        hp2.write_text(json.dumps(doc2), encoding="utf-8")
        f2 = lint_hooks(hp2, repo_root=root)
        if not any(x["rule_id"] == "HB001" for x in f2):
            failures.append("inline 'timeout 30s' under 5000ms hook should flag HB001")

        # Budget-derived inner timeout (no numeric literal) must NOT flag.
        good = root / "scripts" / "budget.py"
        good.write_text(
            "from rally_point import hook_budget\n"
            "subprocess.run(['x'], timeout=hook_budget.inner_timeout_seconds())\n",
            encoding="utf-8",
        )
        doc3 = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command",
             "command": 'python3 "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/scripts/budget.py"',
             "timeout": 3000},
        ]}]}}
        hp3 = root / "hooks" / "hooks3.json"
        hp3.write_text(json.dumps(doc3), encoding="utf-8")
        f3 = lint_hooks(hp3, repo_root=root)
        if any(x["rule_id"] == "HB001" for x in f3):
            failures.append("budget-derived inner timeout must not flag HB001")

        # Backgrounded (`nohup ... &`) work must NOT flag HB001 even with a
        # large inner timeout — the hook returns immediately.
        bg = root / "scripts" / "bg.py"
        bg.write_text("subprocess.run(['x'], timeout=60)\n", encoding="utf-8")
        doc4 = {"hooks": {"Stop": [{"matcher": "", "hooks": [
            {"type": "command",
             "command": 'nohup python3 "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/scripts/bg.py" >/dev/null 2>&1 & printf \'{}\'',
             "timeout": 5000},
        ]}]}}
        hp4 = root / "hooks" / "hooks4.json"
        hp4.write_text(json.dumps(doc4), encoding="utf-8")
        f4 = lint_hooks(hp4, repo_root=root)
        if any(x["rule_id"] == "HB001" for x in f4):
            failures.append("backgrounded inner timeout must not flag HB001")

        # ...but `2>&1` / `&&` must NOT be misread as backgrounding.
        fg = root / "scripts" / "fg.py"
        fg.write_text("subprocess.run(['x'], timeout=9)\n", encoding="utf-8")
        doc5 = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command",
             "command": 'python3 "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/scripts/fg.py" 2>&1',
             "timeout": 5000},
        ]}]}}
        hp5 = root / "hooks" / "hooks5.json"
        hp5.write_text(json.dumps(doc5), encoding="utf-8")
        f5 = lint_hooks(hp5, repo_root=root)
        if not any(x["rule_id"] == "HB001" for x in f5):
            failures.append("foreground 2>&1 redirect must still flag HB001 (9s>=5s)")

    if failures:
        for f in failures:
            print(f"SELF-TEST FAIL: {f}", file=sys.stderr)
        return 1
    print("hook_budget_lint self-test: OK")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lint hook timeout budgets.")
    ap.add_argument("--hooks", help="Path to hooks.json")
    ap.add_argument("--json", action="store_true", help="Emit findings as JSON")
    ap.add_argument("--self-test", action="store_true", help="Run built-in self-test and exit")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not args.hooks:
        ap.error("--hooks is required (or use --self-test)")

    hooks_path = Path(args.hooks)
    if not hooks_path.is_file():
        print(f"error: hooks file not found: {hooks_path}", file=sys.stderr)
        return 2

    try:
        findings = lint_hooks(hooks_path)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"hooks": str(hooks_path), "findings": findings}, indent=2))
    else:
        if not findings:
            print(f"hook_budget_lint: OK — no timeout-budget findings in {hooks_path}")
        else:
            for f in findings:
                loc = f["script_path"] or "(inline)"
                print(f"[{f['rule_id']}] {f['event']} {loc}: {f['message']}")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
