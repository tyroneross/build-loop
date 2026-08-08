#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Re-execute the literal command behind a subagent verification claim.

Build-loop relays subagent verification claims ("Fixed — verified by
reproducing the auditor's exact attack (exit 2, store still 0)") straight
into the Phase 4G Report. Nothing re-runs them. Observed failure: a claim
of "exit 2, store still 0" shipped while the real command exited 0 and
wrote 49 rows into the user's live store.

This script closes that gap by literally re-executing the command named in
a claim (when it is safe to do so) and comparing the actual outcome against
the stated expectation. Each claim ends up labeled:

  executed      — ran, and every stated expectation held
  contradicted  — ran, and at least one stated expectation failed
  cited         — not run (unsafe to re-execute, or no expectation to check)
  error         — attempted to run but the harness itself failed (timeout, OSError)

Input: a subagent report/envelope, via --claims-file (JSON array of claim
objects), --report-file (free markdown/text to extract claims from), or
stdin (JSON array or free text, auto-detected).

Claim object shape:
  {"claim": "<headline text>",
   "command": "<literal shell command>",
   "expected": {"returncode": N} | {"stdout_contains": "..."} | {"stderr_contains": "..."}}

Safety: a command is classified BEFORE execution, by an ALLOWLIST. The input
is LLM-authored prose, so nothing about it can be enumerated in advance — a
deny-list is structurally unable to be correct here. Three gates, in order:

  1. Any shell metacharacter or redirection (> >> < | ; & && || ` $( newline)
     refuses the command outright. This script runs with shell=True, so a
     metacharacter is arbitrary shell.
  2. A `git` head delegates to scripts/audit_git.py `classify()` — one table
     governs both scripts, so they cannot drift. If that import fails, the
     git command is refused (never a permissive fallback).
  3. Otherwise the command head must appear in _ALLOWED_HEADS, a fixed set of
     verification-shaped commands (pytest, python3, cargo test, npm test, ...).
     Multi-word heads are matched as a unit: `npm test` is allowed, `npm` is
     not, so `npm install` is refused.

Anything refused is returned as "cited" with a "not_safely_re-executable:
<why>" reason and is NEVER executed. The probe must never become the thing
that writes 49 rows into a live store.

--allow is the explicit operator escape hatch: exact-string match against the
whole command, empty by default.

CLI:
  python3 verification_claim_probe.py --claims-file claims.json
  python3 verification_claim_probe.py --report-file report.md --markdown
  cat report.md | python3 verification_claim_probe.py

Exit codes:
  0 — at least one claim executed and no claim contradicted (verdict: clean)
  1 — at least one claim contradicted (verdict: contradicted_claims_present)
  2 — nothing was actually executed (verdict: nothing_executed): no claims
      extracted, or every claim refused / lacked a checkable expectation. A
      run we could not observe is not a pass, so it must not exit 0.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Safety classification — ALLOWLIST.
#
# History: this gate used to be a 13-pattern DENY-list. Its input is command
# text EXTRACTED FROM AN LLM-AUTHORED REPORT, and you cannot enumerate what a
# model might emit — so the deny-list let through, among others,
# `pytest -q >> ~/.zshrc` (the redirection regex deliberately exempted the
# append form), `git checkout main`, `git reset`, `git stash`,
# `truncate -s 0 f`, `chmod 777 /`, `psql -c "drop table t"`,
# `docker compose down -v`, and `npm install`. Deny-lists are refuted by this
# threat model; the gate is now default-refuse.
#
# scripts/classify_action.py answers a different question (SAFE/RISKY/
# PRODUCTION/DECISION for an orchestrator ACTION, gated on production
# deployment targeting), which is why it is not reused here. The git arm IS
# shared: it delegates to scripts/audit_git.py, so the two scripts cannot
# disagree about whether `git checkout` is safe.
# ---------------------------------------------------------------------------

# Ordered longest-first so the refusal reason names the right operator
# (`>>` before `>`, `&&` before `&`, `||` before `|`).
_SHELL_METACHARACTERS: tuple[tuple[str, str], ...] = (
    (">>", "append redirection (>>) — writes into a file"),
    (">", "output redirection (>) — writes into a file"),
    ("<", "input redirection (<)"),
    ("&&", "command chaining (&&)"),
    ("||", "command chaining (||)"),
    ("|", "pipe (|)"),
    (";", "command separator (;)"),
    ("&", "background/chaining (&)"),
    ("`", "command substitution (backtick)"),
    ("$(", "command substitution ($(...))"),
    ("\n", "newline (multiple commands)"),
    ("\r", "carriage return (multiple commands)"),
)

# Verification-shaped command heads. Tuples are matched as a unit against the
# leading tokens, longest first: ("npm", "test") is allowed but ("npm",) is
# not, so `npm install` is refused. Adding a bare head here widens the gate to
# every subcommand of that tool — do that only for tools with no write mode.
_ALLOWED_HEADS: frozenset[tuple[str, ...]] = frozenset({
    ("pytest",),
    ("python",),
    ("python3",),
    ("node",),
    ("npm", "test"),
    ("npm", "run", "test"),
    ("pnpm", "test"),
    ("yarn", "test"),
    ("cargo", "test"),
    ("cargo", "build"),
    ("cargo", "check"),
    ("go", "test"),
    ("swift", "test"),
    ("ruff",),
    ("mypy",),
    ("tsc",),
    ("eslint",),
    ("jest",),
    ("vitest",),
    ("jq",),
    ("shellcheck",),
    ("make", "test"),
})

_MAX_HEAD_TOKENS = max(len(h) for h in _ALLOWED_HEADS)

_PYTHON_ALIAS_RE = re.compile(r"^python3(?:\.\d+)?$")
_PYTHON2_ALIAS_RE = re.compile(r"^python(?:\.\d+)?$")


def _normalize_head(token: str) -> str:
    """Strip any directory prefix and fold versioned python names.

    `/opt/homebrew/bin/python3.13` and `python3` are the same head. Everything
    else is compared by basename so an absolute path cannot smuggle a
    non-allowlisted tool past the set membership test.
    """
    name = Path(token).name
    if _PYTHON_ALIAS_RE.match(name):
        return "python3"
    if _PYTHON2_ALIAS_RE.match(name):
        return "python"
    return name


def _classify_git(git_args: list[str]) -> tuple[bool, str]:
    """Delegate a git command to scripts/audit_git.py `classify()`.

    One table governs both scripts. If audit_git cannot be loaded or raises,
    the git command is REFUSED — never a permissive fallback.
    """
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import audit_git
    except Exception as exc:  # pragma: no cover - import failure is environmental
        return False, f"git refused — audit_git allowlist unavailable ({type(exc).__name__}: {exc})"
    try:
        verdict = audit_git.classify(git_args)
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"git refused — audit_git.classify raised {type(exc).__name__}: {exc}"
    if not isinstance(verdict, dict) or not verdict.get("allowed"):
        reason = ""
        if isinstance(verdict, dict):
            reason = verdict.get("reason") or verdict.get("reason_code") or ""
        return False, f"git refused by audit_git allowlist: {reason or 'not on the read-only allowlist'}"
    return True, ""


def _is_safe_to_reexecute(command: str) -> tuple[bool, str]:
    """Return (safe, why). why is empty when safe, else the refusal reason.

    Default-refuse. A command is safe only if it carries no shell
    metacharacter AND (its head is on _ALLOWED_HEADS OR audit_git allows the
    git form).
    """
    cmd = command.strip()
    if not cmd:
        return False, "empty command"

    for token, why in _SHELL_METACHARACTERS:
        if token in cmd:
            return False, why

    try:
        tokens = shlex.split(cmd)
    except ValueError as exc:
        return False, f"unparseable command ({exc})"
    if not tokens:
        return False, "empty command"

    head = _normalize_head(tokens[0])

    if head == "git":
        return _classify_git(tokens[1:])

    normalized = [head] + tokens[1 : _MAX_HEAD_TOKENS]
    for width in range(min(_MAX_HEAD_TOKENS, len(normalized)), 0, -1):
        if tuple(normalized[:width]) in _ALLOWED_HEADS:
            return True, ""

    return False, (
        f"{head!r} is not on the verification allowlist "
        "(only verification-shaped commands are re-executed)"
    )


# ---------------------------------------------------------------------------
# Extraction — pull command-shaped verification claims out of free text.
# ---------------------------------------------------------------------------

_VERB_RE = re.compile(
    r"\b(verified(?:\s+by)?|confirmed|reproduced|\bran\b|tested|proved|passed"
    r"|exit\s+\d+|exit\s+code\s+\d+|returncode\s+\d+|\d+\s+passed)\b",
    re.IGNORECASE,
)

_INLINE_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_FENCE_RE = re.compile(r"```(?:\w*\n)?(.*?)```", re.DOTALL)

_EXPECTED_PATTERNS: tuple[tuple[re.Pattern[str], Any], ...] = (
    (re.compile(r"exit\s+code\s+(\d+)", re.IGNORECASE), lambda m: {"returncode": int(m.group(1))}),
    (re.compile(r"returncode\s+(\d+)", re.IGNORECASE), lambda m: {"returncode": int(m.group(1))}),
    (re.compile(r"exit\s+(\d+)", re.IGNORECASE), lambda m: {"returncode": int(m.group(1))}),
    (re.compile(r"(\d+)\s+passed", re.IGNORECASE), lambda m: {"stdout_contains": f"{m.group(1)} passed"}),
    (re.compile(r"(\d+)\s+failed", re.IGNORECASE), lambda m: {"stdout_contains": f"{m.group(1)} failed"}),
)


def _extract_expected(window: str) -> dict[str, Any] | None:
    for pattern, builder in _EXPECTED_PATTERNS:
        m = pattern.search(window)
        if m:
            return builder(m)
    return None


def extract_claims(text: str) -> list[dict[str, Any]]:
    """Pull command-shaped verification claims out of free markdown/text.

    Conservative: a command with no verification verb (verified, confirmed,
    reproduced, ran, tested, proved, passed, "exit N", "N passed", ...)
    within a ~200-char window is NOT considered a claim.
    """
    claims: list[dict[str, Any]] = []

    # Inline single-backtick commands, scanned line by line (source_line matters).
    lines = text.splitlines()
    for lineno, line in enumerate(lines, start=1):
        for m in _INLINE_BACKTICK_RE.finditer(line):
            command = m.group(1).strip()
            if not command:
                continue
            window_start = max(0, m.start() - 200)
            window_end = min(len(line), m.end() + 200)
            window = line[window_start:window_end]
            if not _VERB_RE.search(window):
                continue
            entry: dict[str, Any] = {
                "claim": line.strip(),
                "command": command,
                "source_line": lineno,
            }
            expected = _extract_expected(window)
            if expected is not None:
                entry["expected"] = expected
            claims.append(entry)

    # Triple-backtick fenced blocks, scanned against the whole text (they can
    # span lines) — only when the paragraph around the fence carries a verb.
    for m in _FENCE_RE.finditer(text):
        body = m.group(1).strip()
        if not body:
            continue
        first_line = body.splitlines()[0].strip()
        if not first_line:
            continue
        window_start = max(0, m.start() - 200)
        window_end = min(len(text), m.end() + 200)
        window = text[window_start:window_end]
        if not _VERB_RE.search(window):
            continue
        lineno = text.count("\n", 0, m.start()) + 1
        entry = {
            "claim": window.strip().splitlines()[0] if window.strip() else first_line,
            "command": first_line,
            "source_line": lineno,
        }
        expected = _extract_expected(window)
        if expected is not None:
            entry["expected"] = expected
        claims.append(entry)

    return claims


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _check_expected(expected: dict[str, Any], proc: subprocess.CompletedProcess[str]) -> tuple[bool, str]:
    reasons: list[str] = []
    if "returncode" in expected:
        if proc.returncode != expected["returncode"]:
            reasons.append(f"expected returncode {expected['returncode']}, got {proc.returncode}")
    if "stdout_contains" in expected:
        needle = expected["stdout_contains"]
        if needle not in (proc.stdout or ""):
            reasons.append(f"stdout did not contain {needle!r}")
    if "stderr_contains" in expected:
        needle = expected["stderr_contains"]
        if needle not in (proc.stderr or ""):
            reasons.append(f"stderr did not contain {needle!r}")
    if reasons:
        return False, "; ".join(reasons)
    return True, ""


def probe(
    claims: list[dict[str, Any]],
    cwd: str = ".",
    timeout: int = 120,
    allow: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Re-execute each claim's command (when safe) and grade it against `expected`.

    allow: literal command strings pre-approved by the caller to run even if
    the allowlist would refuse them (exact whole-command match; explicit
    operator opt-in only; empty by default — nothing is auto-allowed).
    """
    allow_set = set(allow or [])
    results: list[dict[str, Any]] = []

    for c in claims:
        claim_text = c.get("claim", "")
        command = c.get("command", "")
        expected = c.get("expected") or {}

        if not command:
            results.append({
                "claim": claim_text,
                "command": command,
                "status": "cited",
                "expected": expected,
                "actual": None,
                "reason": "no_command_to_execute",
            })
            continue

        safe, why = _is_safe_to_reexecute(command)
        if not safe and command not in allow_set:
            results.append({
                "claim": claim_text,
                "command": command,
                "status": "cited",
                "expected": expected,
                "actual": None,
                "reason": f"not_safely_re-executable: {why}",
            })
            continue

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            results.append({
                "claim": claim_text,
                "command": command,
                "status": "error",
                "expected": expected,
                "actual": None,
                "reason": f"timeout after {timeout}s",
            })
            continue
        except OSError as exc:
            results.append({
                "claim": claim_text,
                "command": command,
                "status": "error",
                "expected": expected,
                "actual": None,
                "reason": f"execution error: {exc}",
            })
            continue

        actual = {
            "returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-800:],
            "stderr_tail": (proc.stderr or "")[-800:],
        }

        if not expected:
            results.append({
                "claim": claim_text,
                "command": command,
                "status": "cited",
                "expected": expected,
                "actual": actual,
                "reason": "no_stated_expectation",
            })
            continue

        ok, mismatch_reason = _check_expected(expected, proc)
        results.append({
            "claim": claim_text,
            "command": command,
            "status": "executed" if ok else "contradicted",
            "expected": expected,
            "actual": actual,
            "reason": None if ok else mismatch_reason,
        })

    return results


# ---------------------------------------------------------------------------
# Rendering + CLI
# ---------------------------------------------------------------------------

def render_markdown(results: list[dict[str, Any]]) -> str:
    lines = ["| Status | Claim | Command | Reason |", "|---|---|---|---|"]
    for r in results:
        status = str(r.get("status", "error"))
        claim = (r.get("claim") or "").replace("|", "\\|").replace("\n", " ")[:100]
        command = (r.get("command") or "").replace("|", "\\|")[:80]
        reason = (r.get("reason") or "").replace("|", "\\|")[:100]
        lines.append(f"| {status}: | {claim} | `{command}` | {reason} |")
    return "\n".join(lines)


def _load_claims_from_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.claims_file:
        data = json.loads(Path(args.claims_file).read_text())
        if not isinstance(data, list):
            raise ValueError("--claims-file must contain a JSON array of claim objects")
        return data
    if args.report_file:
        text = Path(args.report_file).read_text()
        return extract_claims(text)

    text = sys.stdin.read()
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return data
    return extract_claims(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--claims-file", default="", help="JSON array of claim objects")
    parser.add_argument("--report-file", default="", help="free markdown/text to extract claims from")
    parser.add_argument("--cwd", default=".", help="working directory to run commands in")
    parser.add_argument("--timeout", type=int, default=120, help="per-claim timeout in seconds")
    parser.add_argument("--markdown", action="store_true", help="render a compact markdown table instead of JSON")
    parser.add_argument(
        "--allow",
        default="",
        help="comma-separated list of exact commands pre-approved to run despite an allowlist refusal",
    )
    args = parser.parse_args(argv)

    try:
        claims = _load_claims_from_args(args)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    allow = [a.strip() for a in args.allow.split(",") if a.strip()]

    results = probe(claims, cwd=args.cwd, timeout=args.timeout, allow=allow)

    counts = {"executed": 0, "contradicted": 0, "cited": 0, "error": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    # A run we could not observe is not a pass. Zero claims extracted, every
    # claim refused, or every claim run with no checkable expectation all mean
    # nothing was verified — that must not read as exit 0 to an orchestrator.
    if counts["contradicted"] > 0:
        verdict, exit_code = "contradicted_claims_present", 1
    elif counts["executed"] == 0:
        verdict, exit_code = "nothing_executed", 2
    else:
        verdict, exit_code = "clean", 0

    if args.markdown:
        print(render_markdown(results))
    else:
        print(json.dumps({"claims": results, "counts": counts, "verdict": verdict}, indent=2))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
