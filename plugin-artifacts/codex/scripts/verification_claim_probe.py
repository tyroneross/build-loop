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

Safety: a command is classified BEFORE execution. Anything mutating or
irreversible (rm, git push, git reset --hard, git checkout --, git clean,
git commit, mv, dd, redirection into a file, curl/wget -o, sudo, npm
publish, deploy, ...) is refused and returned as "cited" with a
"not_safely_re-executable: <why>" reason. The probe must never become the
thing that writes 49 rows into a live store.

CLI:
  python3 verification_claim_probe.py --claims-file claims.json
  python3 verification_claim_probe.py --report-file report.md --markdown
  cat report.md | python3 verification_claim_probe.py

Exit codes:
  0 — no contradicted claims (verdict: clean)
  1 — at least one claim contradicted (verdict: contradicted_claims_present)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Safety classification — minimal deny-list.
#
# scripts/classify_action.py answers a different question (SAFE/RISKY/
# PRODUCTION/DECISION for an orchestrator ACTION, gated on production
# deployment targeting). "Is this safe to blindly re-execute as a
# verification probe" is a narrower, stricter question — we deny on
# pattern match alone, regardless of environment target — so we replicate a
# minimal, conservative deny-list here rather than force-fit that API.
# ---------------------------------------------------------------------------

_DENY_CHECKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\b"), "rm (destructive delete)"),
    (re.compile(r"\bgit\s+push\b"), "git push (network mutation)"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git reset --hard (discards work)"),
    (re.compile(r"\bgit\s+checkout\s+--\b"), "git checkout -- (discards work)"),
    (re.compile(r"\bgit\s+clean\b"), "git clean (deletes untracked files)"),
    (re.compile(r"\bgit\s+commit\b"), "git commit (mutates history)"),
    (re.compile(r"\bmv\b"), "mv (moves/overwrites files)"),
    (re.compile(r"\bdd\b"), "dd (raw disk/file write)"),
    (re.compile(r"(?<![>\d])>(?!>)"), "> redirection into a file"),
    (re.compile(r"\bcurl\b[^|;&]*(-o\b|--output\b)"), "curl -o (writes downloaded file)"),
    (re.compile(r"\bwget\b[^|;&]*(-o\b|-O\b|--output-document\b)"), "wget -O (writes downloaded file)"),
    (re.compile(r"\bsudo\b"), "sudo (privilege escalation)"),
    (re.compile(r"\bnpm\s+publish\b"), "npm publish (irreversible release)"),
    (re.compile(r"\bdeploy\b", re.IGNORECASE), "deploy (production-shaped action)"),
)


def _is_safe_to_reexecute(command: str) -> tuple[bool, str]:
    """Return (safe, why). why is empty when safe, else the matched deny reason."""
    cmd = command.strip()
    if not cmd:
        return False, "empty command"
    for pattern, why in _DENY_CHECKS:
        if pattern.search(cmd):
            return False, why
    return True, ""


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
    they'd otherwise match the deny-list (explicit opt-in only; empty by
    default — nothing is auto-allowed).
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
        help="comma-separated list of exact commands pre-approved to run despite a deny-list match",
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

    verdict = "contradicted_claims_present" if counts["contradicted"] > 0 else "clean"

    if args.markdown:
        print(render_markdown(results))
    else:
        print(json.dumps({"claims": results, "counts": counts, "verdict": verdict}, indent=2))

    return 1 if counts["contradicted"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
