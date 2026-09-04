#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""claim_scope_lint.py — does the instrument reach the layer the claim is about?

WHY
---
``report_lint.py``'s ``mechanism-claim-unobserved`` rule grades evidence
STRENGTH and PROVENANCE: did the sentence name an instrument, and is the claim
tagged ``[measured]`` / ``[correlated]`` / ``[reasoned]``. It accepts ``grep``
as a satisfying observation, which is correct — a grep IS a measurement.

What it cannot see is REACH. A grep measures one working tree. It is
structurally blind to other branches, other worktrees, what was built, and what
is serving traffic. So a sentence like

    "no blob dependency — a named grep across the entire repo returns 3 hits"

names its instrument, passes the existing rule, and is still false as a
statement about production.

Observed 2026-09-04: an agent grepped a local checkout of a private app repo that
sat on a day-old branch, reported "there is no blob path, switching source is
not possible", and was contradicted by a blob migration already merged to
``origin/main`` AND deployed to production.

This lint adds the missing axis. Four layers:

    L1 working tree  — this branch, this directory, now
    L2 repository    — origin, all branches, all worktrees
    L3 deployed      — what was built and shipped
    L4 live          — what it does under a real request

A claim at layer N needs an instrument that reaches layer N. Reaching HIGHER is
always fine. Reaching lower is the defect this flags.

WARN only, in the same spirit as the style lint: it never blocks a report. The
blocking half of the contract stays with the action gate — deletes, deploys and
restarts still require ``[measured]``.

CLI
---
::

    claim_scope_lint.py <report.md> [--json] [--quiet] [--min-severity WARN]

Exit 0 when clean, 1 when any finding is at or above ``--fail-on`` (default:
never fail; pass ``--fail-on WARN`` to gate).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

# --- Layers -----------------------------------------------------------------
L1_TREE, L2_REPO, L3_DEPLOYED, L4_LIVE = 1, 2, 3, 4

LAYER_NAMES = {
    L1_TREE: "L1 working tree",
    L2_REPO: "L2 repository",
    L3_DEPLOYED: "L3 deployed",
    L4_LIVE: "L4 live behavior",
}

# What each layer needs, phrased as the command to actually run.
LAYER_REMEDY = {
    L2_REPO: "git fetch --all && git log HEAD..origin/<branch>; git worktree list",
    L3_DEPLOYED: "vercel ls / vercel inspect <url>, or gh run list --branch main",
    L4_LIVE: "curl -D- <url>, a real request, or a live probe of the running system",
}

# --- Instrument reach -------------------------------------------------------
# Ordered most-specific first: `git log HEAD..origin/...` must win over `git log`.
INSTRUMENT_REACH: list[tuple[re.Pattern[str], int, str]] = [
    # L4 — observed behavior under a real request.
    (re.compile(r"\bcurl\b[^.\n]*-[a-zA-Z]*[DI]", re.I), L4_LIVE, "curl with response headers"),
    (re.compile(r"\bhttp/[12](\.\d)?\s+\d{3}\b", re.I), L4_LIVE, "an HTTP response line"),
    (re.compile(r"\b(accept-ranges|content-range|x-vercel-id|set-cookie)\s*:", re.I), L4_LIVE, "a live response header"),
    (re.compile(r"\b(screenshot(ed)?|ibr\s+(scan|start|ask)|ax probe|accessibility tree)\b", re.I), L4_LIVE, "a live UI probe"),
    (re.compile(r"\b(psql|select\s+.*\bfrom\b|live (db|database) query|queried production)\b", re.I), L4_LIVE, "a live data query"),
    (re.compile(r"\bverified live\b|\bagainst production\b|\bobserved at \d", re.I), L4_LIVE, "a stated live observation"),
    # L3 — what was built and shipped.
    (re.compile(r"\bvercel\s+(ls|inspect|deployments?)\b", re.I), L3_DEPLOYED, "vercel deployment listing"),
    (re.compile(r"\bgh\s+run\s+(list|view)\b", re.I), L3_DEPLOYED, "CI run status"),
    (re.compile(r"\b(deploy(ment)?\s+(sha|id|url)|build log|deployed commit)\b", re.I), L3_DEPLOYED, "a deployment identifier"),
    (re.compile(r"\b(fly status|kubectl get|docker ps|systemctl status)\b", re.I), L3_DEPLOYED, "a running-service query"),
    # L2 — the repository as a whole.
    # The gap must allow dots: the canonical form is `git log HEAD..origin/main`.
    # An earlier `[^.\n]*` here silently downgraded that to plain `git log` (L1).
    (re.compile(r"\bgit\s+log\b[^\n]{0,40}?\borigin/", re.I), L2_REPO, "git log against origin"),
    (re.compile(r"\bgit\s+fetch\b", re.I), L2_REPO, "git fetch"),
    (re.compile(r"\bgit\s+worktree\s+list\b", re.I), L2_REPO, "git worktree list"),
    (re.compile(r"\bgit\s+branch\s+-a\b|\bgit\s+ls-remote\b", re.I), L2_REPO, "a remote branch listing"),
    (re.compile(r"\bgh\s+(pr|search)\b", re.I), L2_REPO, "a GitHub query"),
    (re.compile(r"\bacross (all|every) (branch|worktree)", re.I), L2_REPO, "a stated all-branch search"),
    # L1 — this checkout only.
    (re.compile(r"\bgrep(ped|ping)?\b|\brg\b|\bripgrep\b", re.I), L1_TREE, "grep"),
    (re.compile(r"\b(cat|sed|head|tail|awk)\b\s", re.I), L1_TREE, "reading a file"),
    (re.compile(r"\b(read|opened|inspected|glob(bed)?) (the )?(file|source|route|config)", re.I), L1_TREE, "reading a file"),
    (re.compile(r"\bpackage\.json\b|\bin the (working tree|checkout|repo directory)\b", re.I), L1_TREE, "the working tree"),
    (re.compile(r"\bgit\s+(log|status|diff|show)\b", re.I), L1_TREE, "local git state"),
]

# --- Claim layer detection --------------------------------------------------
# A claim's SUBJECT decides its layer, regardless of what was checked.
CLAIM_LAYER: list[tuple[re.Pattern[str], int]] = [
    # L4 — behavior under use.
    (re.compile(
        r"\b(seeking|the (page|player|endpoint|route|button|form)) "
        r"(works|does not work|doesn'?t work|is broken|fails|returns)\b", re.I), L4_LIVE),
    (re.compile(r"\b(users?|visitors?|a listener|a client) (can|cannot|can'?t|see|get)\b", re.I), L4_LIVE),
    (re.compile(r"\b(answers?|returns?|responds? with)\s+(a\s+)?\d{3}\b", re.I), L4_LIVE),
    (re.compile(r"\b(is|are|was|were)\s+(\w+\s+){0,2}in production\b", re.I), L4_LIVE),
    (re.compile(r"\bright now\b|\bcurrently (serving|returning|answering|broken|failing)\b", re.I), L4_LIVE),
    # L3 — shipped state.
    (re.compile(r"\b(is|are|was|were|has been|have been)\s+(not\s+)?(deployed|shipped|released|live|in production)\b", re.I), L3_DEPLOYED),
    (re.compile(r"\b(never|not) (deployed|shipped|released)\b", re.I), L3_DEPLOYED),
    (re.compile(r"\bci (is|was) (red|green|failing|passing)\b", re.I), L3_DEPLOYED),
    (re.compile(r"\bthe (deploy|build) (succeeded|failed|is ready)\b", re.I), L3_DEPLOYED),
    # L2 — repository-wide existence and absence.
    # Absence of a named artifact kind. Covers "is not a dependency", "no
    # consumers", "ZERO callers", "not a migration" — the shape that looks
    # identical whether you searched one branch or all of them.
    (re.compile(
        r"\b(no|zero|not\s+an?|isn'?t\s+an?|has\s+(no|zero)|have\s+(no|zero))\s+"
        r"(\w[\w-]*\s+){0,2}"
        r"(dependenc(y|ies)|migrations?|branch(es)?|commits?|prs?|"
        r"consumers?|callers?|references?|implementations?|usages?|"
        r"import(er|s)?s?|subscribers?|writers?|readers?|copies|copy)\b", re.I), L2_REPO),
    (re.compile(r"\bno (code|path|caller|consumer|reference|implementation)s?\b[^.\n]{0,60}\b(anywhere|in any|in the (repo|codebase|project))\b", re.I), L2_REPO),
    (re.compile(r"\b(does not|doesn'?t|do not|don'?t) exist\b", re.I), L2_REPO),
    (re.compile(r"\bnothing (writes|reads|calls|implements|does|renders|consumes)\b", re.I), L2_REPO),
    (re.compile(r"\b(in any|across (all|every)) (repo|repositor|branch|worktree)", re.I), L2_REPO),
    (re.compile(r"\b(never|nowhere) (implemented|written|added|merged)\b", re.I), L2_REPO),
    (re.compile(r"\bthere is no\b", re.I), L2_REPO),
    # Exclusivity. "lives only in X" / "the only X" asserts that every other
    # possibility was ruled out, which is an absence claim wearing a hat.
    # Exclusivity about a CODE ARTIFACT only. A bare "is the only fix that
    # works" is a judgment, not an existence claim, and flagging it is noise.
    (re.compile(r"\b(lives?|exists?|stored?|held)\s+only\s+(in|on|at)\b", re.I), L2_REPO),
    (re.compile(
        r"\b(is|are)\s+the\s+only\s+"
        r"(place|cop(y|ies)|source|path|caller|consumer|reference|implementation|"
        r"writer|reader|definition|declaration|instance|occurrence)\b", re.I), L2_REPO),
]

# Explicit scope tag satisfies the rule outright, the way [measured] does for
# report_lint. Example: "no blob dependency [L1@3fe5298]".
SCOPE_TAG_RE = re.compile(r"\[L[1-4](@[^\]]+)?\]", re.I)

# A sentence that already carries its own scope qualifier is self-limiting.
SELF_LIMITED_RE = re.compile(
    r"\bin the working tree\b|\bon branch\b|\bat commit\b|\bin this checkout\b|"
    r"\bas of \d|\bat \d{2}:\d{2}\b|\blocally\b", re.I)

# Modal, counterfactual, conditional and prescriptive sentences are not claims
# about what IS. "would have broken every future run", "if X then no consumers",
# "we should deploy" — flagging these is pure noise, and noise trains the reader
# to ignore the gate. Measured on real retrospectives: this suppressor removed
# 5/5 false positives on bl-20260612-buildloop-learn-gates.md.
HYPOTHETICAL_RE = re.compile(
    r"\b(would|could|should|might|may|must|shall|will)\b|"
    r"\b(if|unless|whenever|suppose|assuming|hypothetical(ly)?)\b|"
    r"\bplan(s|ned|ning)? to\b|\bintend(s|ed)? to\b|\bproposal\b|\bTODO\b|"
    r"\b(want|need)s? to\b", re.I)

FENCE_RE = re.compile(r"^\s*```")


def _finding(*, rule_id: str, severity: str, line: int | None,
             snippet: str | None, message: str) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "message": message,
        "evidence": {"line": line, "snippet": snippet},
    }


def _prose_lines(text: str) -> list[tuple[int, str]]:
    """Line numbers and text, skipping fenced code blocks."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for i, raw in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if raw.strip():
            out.append((i, raw))
    return out


def claim_layer(line: str) -> tuple[int, str] | tuple[None, None]:
    """Highest layer this line makes a claim about, and the phrase that says so."""
    best: tuple[int, str] | tuple[None, None] = (None, None)
    for pattern, layer in CLAIM_LAYER:
        m = pattern.search(line)
        if m and (best[0] is None or layer > best[0]):
            best = (layer, m.group(0))
    return best


def instrument_reach(line: str) -> tuple[int, str] | tuple[None, None]:
    """Highest layer any instrument named on this line can actually prove."""
    best: tuple[int, str] | tuple[None, None] = (None, None)
    for pattern, layer, label in INSTRUMENT_REACH:
        if pattern.search(line) and (best[0] is None or layer > best[0]):
            best = (layer, label)
    return best


def lint(text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for lineno, line in _prose_lines(text):
        if (SCOPE_TAG_RE.search(line)
                or SELF_LIMITED_RE.search(line)
                or HYPOTHETICAL_RE.search(line)):
            continue
        layer, claim_phrase = claim_layer(line)
        if layer is None:
            continue
        reach, instrument = instrument_reach(line)

        if reach is None:
            message = (
                f"{LAYER_NAMES[layer]} claim with no instrument named. "
                f"'{claim_phrase}' is a claim about {LAYER_NAMES[layer].split(' ', 1)[1]}. "
                f"Run: {LAYER_REMEDY.get(layer, 'name what you checked')} — "
                f"or tag the scope you actually reached, e.g. [L1@<sha>]."
            )
            findings.append(_finding(
                rule_id="claim-scope-uninstrumented", severity="WARN",
                line=lineno, snippet=line.strip()[:120], message=message))
            continue

        if reach < layer:
            message = (
                f"Instrument cannot reach the claim. '{claim_phrase}' is a "
                f"{LAYER_NAMES[layer]} claim, but the only instrument named is "
                f"{instrument} ({LAYER_NAMES[reach]}). {instrument.capitalize()} is blind to "
                f"{', '.join(LAYER_NAMES[l].split(' ', 1)[1] for l in range(reach + 1, layer + 1))}. "
                f"Either run: {LAYER_REMEDY.get(layer, '')} — or rewrite the claim down "
                f"to {LAYER_NAMES[reach]} and say so in the sentence."
            )
            findings.append(_finding(
                rule_id="claim-scope-exceeds-instrument", severity="WARN",
                line=lineno, snippet=line.strip()[:120], message=message))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Flag claims whose subject layer exceeds the reach of the instrument named.")
    parser.add_argument("report", help="Path to draft report markdown ('-' for stdin)")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--quiet", action="store_true", help="Suppress human output")
    parser.add_argument("--fail-on", choices=["WARN", "never"], default="never",
                        help="Exit 1 when a finding at this severity exists (default: never)")
    args = parser.parse_args(argv)

    text = sys.stdin.read() if args.report == "-" else open(args.report, encoding="utf-8").read()
    findings = lint(text)

    if args.json:
        print(json.dumps({"findings": findings, "count": len(findings)}, indent=2))
    elif not args.quiet:
        if not findings:
            print("claim-scope: clean")
        for f in findings:
            ev = f["evidence"]
            print(f"{ev['line']}: [{f['severity']}] {f['rule_id']}\n    {ev['snippet']}\n    {f['message']}\n")

    if args.fail_on == "WARN" and findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
