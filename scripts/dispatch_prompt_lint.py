#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Flag a dispatch prompt that prohibits without a fallback, cheaply.

WHY: a 2026-07-25 A/B test (build-loop-memory/retrospectives/
2026-07-25-session-plugin-underuse-and-statusline-rca.md §7) found hand-written
subagent prompts missed one gap class 3/3 times — a PROHIBITION WITHOUT A
FALLBACK aimed at failable external apparatus: (A) "copy the token block
VERBATIM" and "4.5:1 contrast in both themes", two binding rules with no
priority; (B) "drive the browser, do not read the code", no fallback if the
browser step is blocked; (C) "do not review from HTML source", no fallback if
screenshots fail — the agent then silently reads source anyway, or critiques
screens it never captured, and the output looks identical to a real review.
Verdict: do NOT run prompt-builder on every dispatch — most dispatch prompts
are throwaway. Instead run this cheap lint on every dispatch prompt; only a
hit escalates to the prompt-builder optimize pass.

GATED ON A FAILABLE RESOURCE: rule (a) only runs when the prompt names a
runtime resource from rule (b)'s list. Ungated, it hit `plan-critic.md`'s
"## What you must NOT do" scope list ("Do not write to files.") — a real
prohibition with nothing failable to fall back from, so not the retro's gap
class; ungated, nearly every agent prompt hits and "optimize only on hits" collapses.

DOES NOT CATCH: prompt A's failure mode above (two binding rules, no
priority order) — needs semantic judgment, not regex. A clean result means
this gap class is absent, not that the prompt is safe. Judge-role (rule c)
is an amplifier only, never a hit alone, and intentionally generous (flags
"verify the output" even when benign).

    dispatch_prompt_lint.py <prompt.md|->      # exit 1 hit, 0 clean, 2 unreadable
    dispatch_prompt_lint.py --json <prompt.md>

Stdlib only. No network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

__version__ = "1.0.0"

#: Prohibition markers. "no " + verb is deliberately excluded — too noisy.
_PROHIBITION_RE = re.compile(r"\b(do not|don't|never|must not|avoid)\b", re.IGNORECASE)
_UPPER_NOT_RE = re.compile(r"(?<![A-Za-z])NOT(?![A-Za-z])")

#: Fallback markers. Presence anywhere in the checked window clears a hit.
_FALLBACK_RE = re.compile(
    r"\b(instead|otherwise|fall\s*back|fallback|unless|stop and report|if you cannot)\b"
    r"|\bif\b[^.\n]{0,100}\b(fails?|is blocked|is unavailable|cannot|can't|errors?)\b",
    re.IGNORECASE,
)

#: Failable runtime resources: (label, pattern). Matched once per prompt since
#: rule (b) gates on whole-prompt fallback presence, not per-match proximity.
_RESOURCE_PATTERNS = (
    ("url", re.compile(r"https?://")),
    ("localhost", re.compile(r"\b(localhost|127\.0\.0\.1)\b", re.IGNORECASE)),
    ("dev-server", re.compile(r"\bdev(?:elopment)?\s+server\b", re.IGNORECASE)),
    ("live-server", re.compile(r"\blive\s+server\b", re.IGNORECASE)),
    ("browser", re.compile(r"\bbrowser\b", re.IGNORECASE)),
    ("screenshot", re.compile(r"\bscreenshots?\b", re.IGNORECASE)),
    ("simulator", re.compile(r"\bsimulator\b", re.IGNORECASE)),
    ("skill-load", re.compile(r"\bload the\b[^.\n]{0,60}\bskill\b", re.IGNORECASE)),
    ("mcp", re.compile(r"\bMCP\b")),
    ("playwright", re.compile(r"\bplaywright\b", re.IGNORECASE)),
    ("ibr", re.compile(r"\bIBR\b")),
)

#: Evaluator-role assignment. Amplifier only — never a hit by itself.
_JUDGE_ROLE_RE = re.compile(
    r"\byou are (?:a|an|the)?\s*(?:\w+\s+){0,3}(reviewer|auditor|judge|critic|evaluator)s?\b"
    r"|\b(review|audit|judge|critique|grade|score|verify|evaluate)\s+(?:the|this|that|your|a|an)\b",
    re.IGNORECASE,
)

_SEVERITY_RANK = {"none": 0, "normal": 1, "high": 2}
_NEXT_HIT = "run the prompt-builder optimize pass before dispatch (prompt-builder:prompt-builder skill / /prompt-builder:optimize)"
_NEXT_CLEAN = "no unguarded failure mode detected; dispatch the inline prompt as written"

def _split_sentences(text: str) -> list[tuple[int, str]]:
    """(line_no, sentence) pairs. Splits within a line only — under-splits a
    paragraph that wraps without terminal punctuation; never over-splits."""
    sentences: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        sentences.extend((line_no, p) for p in re.split(r"(?<=[.!?])\s+", stripped) if p)
    return sentences

def check(text: str) -> dict:
    """Lint one prompt's text. Returns the --json result shape. Gated on a
    failable resource being present (see module docstring, "GATED ON")."""
    matched_resources = [(kind, m) for kind, pattern in _RESOURCE_PATTERNS
                          for m in [pattern.search(text)] if m]
    if not matched_resources:
        return {"hit": False, "severity": "none", "findings": [], "next": _NEXT_CLEAN}

    findings: list[dict] = []
    sentences = _split_sentences(text)
    for i, (line_no, sent) in enumerate(sentences):
        if not (_PROHIBITION_RE.search(sent) or _UPPER_NOT_RE.search(sent)):
            continue
        window = " ".join(s for _, s in sentences[i:i + 3])
        if _FALLBACK_RE.search(window):
            continue
        findings.append({
            "rule": "prohibition-without-fallback", "line": line_no,
            "excerpt": sent.strip()[:160],
            "message": "prohibition has no fallback in this sentence or the next "
                       "two — what should the agent do if it can't comply?",
        })

    if not _FALLBACK_RE.search(text):
        for kind, m in matched_resources:
            findings.append({
                "rule": "unguarded-runtime-dependency",
                "line": text.count("\n", 0, m.start()) + 1, "excerpt": m.group(0),
                "message": f"prompt depends on {kind}, a failable runtime resource, "
                           f"with no fallback marker anywhere in the prompt — say "
                           f"what happens if it's unreachable or blocked.",
            })

    hit = any(f["rule"] in ("prohibition-without-fallback", "unguarded-runtime-dependency")
              for f in findings)
    severity = "none"
    if hit:
        severity = "normal"
        judge_match = _JUDGE_ROLE_RE.search(text)
        if judge_match:
            severity = "high"
            findings.append({
                "rule": "judge-role", "excerpt": judge_match.group(0),
                "line": text.count("\n", 0, judge_match.start()) + 1,
                "message": "agent is assigned an evaluator role — require it to state "
                           "what evidence it actually captured versus inferred; "
                           "substituted evidence is undetectable in a judge's output.",
            })

    return {"hit": hit, "severity": severity, "findings": findings,
            "next": _NEXT_HIT if hit else _NEXT_CLEAN}

def _read(prompt: str) -> str:
    if prompt == "-":
        return sys.stdin.read()
    with open(prompt, encoding="utf-8") as fh:
        return fh.read()

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompts", nargs="+", help="prompt file path(s), or - for stdin")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    multi = len(args.prompts) > 1
    all_findings: list[dict] = []
    overall_hit = False
    overall_severity = "none"
    for prompt in args.prompts:
        try:
            text = _read(prompt)
        except OSError as exc:
            print(f"dispatch_prompt_lint: cannot read {prompt}: {exc}", file=sys.stderr)
            return 2
        result = check(text)
        for finding in result["findings"]:
            all_findings.append(dict(finding, file=prompt) if multi else finding)
        overall_hit = overall_hit or result["hit"]
        if _SEVERITY_RANK[result["severity"]] > _SEVERITY_RANK[overall_severity]:
            overall_severity = result["severity"]

    output = {"hit": overall_hit, "severity": overall_severity,
              "findings": all_findings, "next": _NEXT_HIT if overall_hit else _NEXT_CLEAN}
    if args.json:
        print(json.dumps(output, indent=2))
    elif overall_hit:
        print("dispatch_prompt_lint: unguarded failure mode in this prompt\n", file=sys.stderr)
        for f in all_findings:
            loc = f"{f.get('file', '')}:{f['line']}" if multi else f"line {f['line']}"
            print(f"  [{f['rule']}] {loc}: {f['excerpt']}", file=sys.stderr)
            print(f"    {f['message']}", file=sys.stderr)
        print(f"\nnext: {output['next']}", file=sys.stderr)

    return 1 if overall_hit else 0

if __name__ == "__main__":
    raise SystemExit(main())
