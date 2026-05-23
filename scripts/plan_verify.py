#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
plan_verify.py — deterministic plan verifier for build-loop Phase 2.

Reads a plan markdown file, applies grep-checkable rules, and emits findings
in the Plan Evidence Contract shape. Exits 0 on no BLOCKERs, 1 on BLOCKERs,
2 on verifier error.

Stdlib only: re, pathlib, subprocess, json, argparse, sys.

Rules: delete-with-callers, numeric-drift, route-change-evidence,
package-state, missing-evidence, scope-split, less-invasive-shim,
tool-without-permission-tier, external-call-without-budget-ceiling,
risk-surface-change-without-threat-model, schema-migration-full-chain,
synthesis-dim-vague-value, risk-reason-invalid-value,
scope-audit-required, parallel-decision-record.

Plan Evidence Contract (per finding):
{
  "claim_text": str,
  "claim_kind": "delete|orphan|zero_callers|route_removed|package_absent|package_present|numeric_count|missing_evidence|scope_split|less_invasive_shim",
  "subject": {"path": str|null, "symbol": str|null, "noun": str|null},
  "verification_command": str|null,
  "evidence": {"file": str|null, "line": int|null, "snippet": str|null},
  "result": "match|no_match|inconclusive",
  "marker": "✅|⚠️|❓|null",
  "severity": "BLOCKER|WARN|INFO",
  "confidence": "high|medium|low",
  "rule_id": str
}
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Markdown parsing helpers
# ---------------------------------------------------------------------------

FENCE_RE = re.compile(r"^\s*```")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def strip_fenced_blocks(text: str) -> list[tuple[int, str]]:
    """Return list of (1-based line number, line text) with fenced code blocks
    AND HTML comments replaced by empty strings (so line numbers stay stable).

    HTML comments are stripped because plan authors commonly use `<!-- ... -->`
    to annotate plans, and comment text containing trigger phrases (e.g.
    "this plan does not add a new tool") would otherwise trip rules like
    `tool-without-permission-tier`."""
    # Strip HTML comments first; preserve line count by replacing with newlines.
    text = HTML_COMMENT_RE.sub(lambda m: "\n" * m.group().count("\n"), text)

    out: list[tuple[int, str]] = []
    in_fence = False
    for i, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out.append((i, ""))  # also blank the fence line itself
            continue
        if in_fence:
            out.append((i, ""))
        else:
            out.append((i, line))
    return out


MARKER_RE = re.compile(r"[✅⚠️❓]")


def has_marker_within(lines: list[tuple[int, str]], idx: int, window: int = 3) -> bool:
    """Check if any line within `window` lines (above or below) of idx contains a status marker."""
    n = len(lines)
    lo = max(0, idx - window)
    hi = min(n, idx + window + 1)
    for j in range(lo, hi):
        if MARKER_RE.search(lines[j][1]):
            return True
    return False


# ---------------------------------------------------------------------------
# Repo grep helper
# ---------------------------------------------------------------------------


def repo_grep(pattern: str, repo: Path) -> tuple[bool, str]:
    """Return (found, command_used).
    Prefers `git grep` (fast, respects gitignore, no exclude-flags drift). Falls back
    to `rg`, then portable Python rglob if neither is available. The previous BSD-grep
    fallback was removed — macOS /usr/bin/grep doesn't honor --exclude-dir reliably and
    traverses .git/, blowing past the 10s timeout on real repos."""
    # Prefer git grep when repo is a git working tree (handles ignore rules for free)
    if (repo / ".git").exists():
        # `:!pattern` is a non-magic pathspec exclude; matches only at repo root.
        # For nested dirs (skills/plan-verify/test-fixtures) we need the glob form.
        cmd = ["git", "-C", str(repo), "grep", "-l", "--untracked", "-e", pattern, "--",
               ":!**/test-fixtures/**", ":!.build-loop", ":!.navgator"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            # git grep exit code: 0=found, 1=not found, 128=error
            if r.returncode in (0, 1):
                return (r.returncode == 0 and bool(r.stdout.strip()), " ".join(cmd))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    # rg fallback (real binary only; shell function won't be found by subprocess)
    try:
        cmd = ["rg", "-l", "--hidden", "-g", "!.git", "-g", "!node_modules",
               "-g", "!dist", "-g", "!.build-loop", "-g", "!.navgator",
               "-g", "!test-fixtures", "-g", "!__pycache__", pattern, str(repo)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (r.returncode == 0 and bool(r.stdout.strip()), " ".join(cmd))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Python-native fallback (no external dep). Scans text files only, skips well-known
    # heavy dirs. Slower than git grep but correct + works without any tools.
    _SKIP = {".git", "node_modules", "dist", ".build-loop", ".navgator", "test-fixtures", "__pycache__"}
    try:
        compiled = re.compile(re.escape(pattern))
    except re.error:
        return (False, "python-fallback:bad-pattern")
    for p in repo.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _SKIP for part in p.parts):
            continue
        try:
            if compiled.search(p.read_text(encoding="utf-8", errors="ignore")):
                return (True, f"python-fallback:rglob (pattern={pattern!r})")
        except OSError:
            continue
    return (False, "python-fallback:rglob (no match)")


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------


def _finding(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "claim_text": "",
        "claim_kind": "",
        "subject": {"path": None, "symbol": None, "noun": None},
        "verification_command": None,
        "evidence": {"file": None, "line": None, "snippet": None},
        "result": "inconclusive",
        "marker": None,
        "severity": "INFO",
        "confidence": "medium",
        "rule_id": "",
    }
    base.update(kw)
    return base


# Patterns that capture a path/symbol after a delete/orphan/zero-callers verb.
# Tightened: require the captured token to look like an actual path or module reference
# (contains `/`, ends in `.py`/`.ts`/`.tsx`/`.js`/`.jsx`/`.swift`/`.rb`/`.go`).
DELETE_PATTERNS = [
    re.compile(r"\b(?:delete|deletes?|deleting|drop|drops?|dropping|remove|removes?|removing)\b[^.\n]*?`([^`\n]+)`", re.IGNORECASE),
    re.compile(r"`([^`\n]+)`[^.\n]*?\bhas\s+(?:0|zero)\s+callers?\b", re.IGNORECASE),
]

PATH_LIKE_RE = re.compile(r"(/|\.(?:py|ts|tsx|js|jsx|swift|rb|go|md|sql)\b)")
# Plan-side hedges that mean the author is NOT contradicting themselves —
# they're either describing prior analysis or explicitly partial.
HEDGE_TOKENS_RE = re.compile(
    r"(\bPARTIAL\b|🔄|🟡|\bKEEP\b|\bkeep\b|"
    r"\bextract\b.*\bdelete\b.*\brest\b|"
    r"\bnot\s+dead\b|\bdo\s+not\s+delete\b|"
    r"\bcan\s+deprecate\s+later\b)",
    re.IGNORECASE,
)


def rule_delete_with_callers(plan_path: Path, lines: list[tuple[int, str]], repo: Path | None) -> list[dict[str, Any]]:
    """BLOCKER if 'delete `path`' grep-disproves against repo.
    Tightened to avoid false positives on plans that *describe* orphan analysis."""
    out: list[dict[str, Any]] = []
    if repo is None or not repo.exists():
        return out
    seen: set[tuple[str, int]] = set()
    for idx, (lineno, line) in enumerate(lines):
        if not line:
            continue
        # Skip plan rows that explicitly mark themselves as PARTIAL / KEEP / etc.
        if HEDGE_TOKENS_RE.search(line):
            continue
        for pat in DELETE_PATTERNS:
            for m in pat.finditer(line):
                target = m.group(1).strip()
                if not target or len(target) > 200:
                    continue
                # Must look like a path or filename, not arbitrary text
                if not PATH_LIKE_RE.search(target):
                    continue
                if target.startswith(("http://", "https://", "#")):
                    continue
                key = (target, lineno)
                if key in seen:
                    continue
                seen.add(key)
                grep_pat = re.escape(target)
                found, cmd = repo_grep(grep_pat, repo)
                if found:
                    out.append(_finding(
                        claim_text=line.strip(),
                        claim_kind="delete",
                        subject={"path": target, "symbol": None, "noun": None},
                        verification_command=cmd,
                        evidence={"file": str(plan_path), "line": lineno, "snippet": line.strip()},
                        result="match",
                        severity="BLOCKER",
                        confidence="high",
                        rule_id="delete-with-callers",
                    ))
    return out


# Numeric drift: only fire on summary-style nouns where a count drift is the
# documented example-app failure mode. "callers"/"files"/"routes" routinely
# vary by subject across rows of a table and are NOT internal contradictions.
# The example-app v2.0 leak was orphan-count drift in a totals statement.
NUM_NOUN_RE = re.compile(
    r"(?:total[^.\n]{0,40}|\bremoved?\b[^.\n]{0,40}|\bremaining\b[^.\n]{0,40}|\bonly\b\s+)?"
    r"\*?\*?\b(\d+)\b\s+(orphans?)\b",
    re.IGNORECASE,
)


def rule_numeric_drift(plan_path: Path, lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """BLOCKER when an aggregate orphan count appears with different values in the same doc."""
    by_noun: dict[str, list[tuple[int, int, str]]] = {}
    for lineno, line in lines:
        if not line:
            continue
        for m in NUM_NOUN_RE.finditer(line):
            count = int(m.group(1))
            noun = m.group(2).lower().rstrip("s")
            by_noun.setdefault(noun, []).append((count, lineno, line.strip()))
    out: list[dict[str, Any]] = []
    for noun, occurrences in by_noun.items():
        counts = {c for c, _, _ in occurrences}
        if len(counts) >= 2:
            first = occurrences[0]
            for count, lineno, snippet in occurrences[1:]:
                if count != first[0]:
                    out.append(_finding(
                        claim_text=snippet,
                        claim_kind="numeric_count",
                        subject={"path": None, "symbol": None, "noun": noun},
                        verification_command=None,
                        evidence={"file": str(plan_path), "line": lineno, "snippet": snippet},
                        result="match",
                        severity="BLOCKER",
                        confidence="high",
                        rule_id="numeric-drift",
                    ))
    return out


# Route change: phrases like "308 redirect", "remove route", "deprecate path", "rewrite to"
ROUTE_PATTERNS = [
    re.compile(r"\b(?:308|301|307|302)\s+redirect\b", re.IGNORECASE),
    re.compile(r"\bremove\s+route\b", re.IGNORECASE),
    re.compile(r"\bdeprecate\s+(?:the\s+)?(?:path|route|endpoint)\b", re.IGNORECASE),
    re.compile(r"\brewrite\s+to\b", re.IGNORECASE),
]
# Phrasing that means the author is critiquing/rejecting the alternative,
# not asserting they will do it. These suppress the BLOCKER.
REJECTION_HINT_RE = re.compile(
    r"(\beliminated\b|\beliminate\b|"
    r"\bavoid\b|\bavoided\b|\bnot\s+chosen\b|\bnot\s+selected\b|"
    r"\binstead\s+of\b|\brejected\b|\bdrawback\b|\bcon\b|\bconsidered\b|"
    r"\bshared\s+handler\b|\bdual\s+mount\b|\balias\s+export\b|"
    r"\bcan\s+break\b|\bcould\s+break\b|\bbreak\s+clients\b|"
    r"\brationale\b)",
    re.IGNORECASE,
)


def rule_route_change_evidence(plan_path: Path, lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """BLOCKER for route-change phrasing without a marker or rejection-hint within 5 lines."""
    out: list[dict[str, Any]] = []
    n = len(lines)
    for idx, (lineno, line) in enumerate(lines):
        if not line:
            continue
        for pat in ROUTE_PATTERNS:
            if pat.search(line):
                # Suppress if a marker is within 5 lines OR the surrounding context
                # signals the author is describing a rejected alternative.
                if has_marker_within(lines, idx, window=5):
                    break
                lo = max(0, idx - 5)
                hi = min(n, idx + 6)
                if any(REJECTION_HINT_RE.search(lines[j][1] or "") for j in range(lo, hi)):
                    break
                out.append(_finding(
                    claim_text=line.strip(),
                    claim_kind="route_removed",
                    subject={"path": None, "symbol": None, "noun": "route"},
                    verification_command=None,
                    evidence={"file": str(plan_path), "line": lineno, "snippet": line.strip()},
                    result="inconclusive",
                    severity="BLOCKER",
                    confidence="medium",
                    rule_id="route-change-evidence",
                ))
                break  # one finding per line
    return out


# Package-state: "<pkg> is unused" / "<pkg> is in package.json"
PKG_UNUSED_RE = re.compile(r"`?([A-Za-z0-9_@/-]+)`?\s+(?:package\s+)?is\s+unused", re.IGNORECASE)
PKG_PRESENT_RE = re.compile(r"`?([A-Za-z0-9_@/-]+)`?\s+is\s+in\s+package\.json", re.IGNORECASE)


def rule_package_state(plan_path: Path, lines: list[tuple[int, str]], repo: Path | None) -> list[dict[str, Any]]:
    """BLOCKER if package.json contradicts the claim."""
    out: list[dict[str, Any]] = []
    if repo is None:
        return out
    pkg_json = repo / "package.json"
    if not pkg_json.exists():
        return out
    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return out
    deps = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps.update((data.get(key) or {}).keys())
    for lineno, line in lines:
        if not line:
            continue
        for m in PKG_UNUSED_RE.finditer(line):
            pkg = m.group(1).strip("`")
            if pkg in deps:
                # claim says unused, manifest still lists it (could be true unused, but
                # without an import-graph check we treat the contradiction as a WARN only
                # if grep finds NO imports anywhere)
                imports_found, cmd = repo_grep(rf"(from\s+['\"]{re.escape(pkg)}['\"]|require\(['\"]{re.escape(pkg)}['\"])", repo)
                if imports_found:
                    out.append(_finding(
                        claim_text=line.strip(),
                        claim_kind="package_absent",
                        subject={"path": None, "symbol": pkg, "noun": "package"},
                        verification_command=cmd,
                        evidence={"file": str(plan_path), "line": lineno, "snippet": line.strip()},
                        result="match",
                        severity="BLOCKER",
                        confidence="high",
                        rule_id="package-state",
                    ))
        for m in PKG_PRESENT_RE.finditer(line):
            pkg = m.group(1).strip("`")
            if pkg not in deps:
                out.append(_finding(
                    claim_text=line.strip(),
                    claim_kind="package_present",
                    subject={"path": None, "symbol": pkg, "noun": "package"},
                    verification_command=f"jq .dependencies {pkg_json}",
                    evidence={"file": str(plan_path), "line": lineno, "snippet": line.strip()},
                    result="no_match",
                    severity="BLOCKER",
                    confidence="high",
                    rule_id="package-state",
                ))
    return out


# Missing-evidence: factual claim with no marker AND no verification source within 3 lines
FACTUAL_RE = re.compile(
    r"\b(?:we\s+(?:never|always|don't|do\s+not)\s+import|"
    r"is\s+unused|"
    r"has\s+(?:0|zero)\s+(?:callers?|imports?|references?)|"
    r"never\s+imported|"
    r"is\s+(?:dead|orphan(?:ed)?))",
    re.IGNORECASE,
)
VERIFY_HINT_RE = re.compile(r"(verified|verify|grep|rg|`\$\s*[a-z]+`|http[s]?://)", re.IGNORECASE)


def rule_missing_evidence(plan_path: Path, lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """WARN if a factual claim has no marker AND no verification hint within 3 lines."""
    out: list[dict[str, Any]] = []
    for idx, (lineno, line) in enumerate(lines):
        if not line:
            continue
        if FACTUAL_RE.search(line):
            if has_marker_within(lines, idx, window=3):
                continue
            # Also accept a verification hint within 3 lines
            n = len(lines)
            lo = max(0, idx - 3)
            hi = min(n, idx + 4)
            has_hint = any(VERIFY_HINT_RE.search(lines[j][1] or "") for j in range(lo, hi))
            if has_hint:
                continue
            out.append(_finding(
                claim_text=line.strip(),
                claim_kind="missing_evidence",
                subject={"path": None, "symbol": None, "noun": None},
                verification_command=None,
                evidence={"file": str(plan_path), "line": lineno, "snippet": line.strip()},
                result="inconclusive",
                severity="WARN",
                confidence="medium",
                rule_id="missing-evidence",
            ))
    return out


PHASE_HEADING_RE = re.compile(r"^#{2,3}\s+Phase\s+\d", re.IGNORECASE)
MILESTONE_RE = re.compile(r"\bmilestones?\b", re.IGNORECASE)


def rule_scope_split(plan_path: Path, lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """INFO when more than 5 Phase headings without a Milestone structure."""
    phase_count = 0
    has_milestone = False
    first_lineno = None
    for lineno, line in lines:
        if not line:
            continue
        if PHASE_HEADING_RE.match(line):
            phase_count += 1
            if first_lineno is None:
                first_lineno = lineno
        if MILESTONE_RE.search(line):
            has_milestone = True
    if phase_count > 5 and not has_milestone:
        return [_finding(
            claim_text=f"{phase_count} Phase headings without Milestone grouping",
            claim_kind="scope_split",
            subject={"path": None, "symbol": None, "noun": "phase"},
            evidence={"file": str(plan_path), "line": first_lineno, "snippet": ""},
            result="match",
            severity="INFO",
            confidence="medium",
            rule_id="scope-split",
        )]
    return []


SHIM_TOKENS = re.compile(r"\b(?:308\s+redirect|rewrite|deprecate)\b", re.IGNORECASE)
ALT_HINT_RE = re.compile(r"\b(?:considered|alternative|shared\s+handler|dual\s+mount|alias\s+export|instead\s+of)\b", re.IGNORECASE)


def rule_less_invasive_shim(plan_path: Path, lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """WARN: shim phrasing without nearby 'considered alternatives' line."""
    out: list[dict[str, Any]] = []
    for idx, (lineno, line) in enumerate(lines):
        if not line:
            continue
        if SHIM_TOKENS.search(line):
            n = len(lines)
            lo = max(0, idx - 5)
            hi = min(n, idx + 6)
            has_alt = any(ALT_HINT_RE.search(lines[j][1] or "") for j in range(lo, hi))
            if not has_alt:
                out.append(_finding(
                    claim_text=line.strip(),
                    claim_kind="less_invasive_shim",
                    subject={"path": None, "symbol": None, "noun": None},
                    evidence={"file": str(plan_path), "line": lineno, "snippet": line.strip()},
                    result="inconclusive",
                    severity="WARN",
                    confidence="low",
                    rule_id="less-invasive-shim",
                ))
    return out


# ---------------------------------------------------------------------------
# Security-surface rules (added with security-methodology skill, 2026-05-02)
# ---------------------------------------------------------------------------

# Rule: tool-without-permission-tier
# A plan that introduces a new tool, MCP server, or agent capability MUST declare
# a permission tier (T0–T5 per agent-builder tool-contract.md). Without a tier,
# the implementer cannot pick the right scanner intensity / approval rule.

NEW_TOOL_RE = re.compile(
    r"\b(?:new|add|introduce|expose|register|create)\b[^.\n]{0,40}\b"
    r"(?:tool|mcp\s+server|mcp|plugin|skill|agent\s+capability|function\s+tool|hosted\s+tool)\b",
    re.IGNORECASE,
)
PERMISSION_TIER_RE = re.compile(r"\bT[0-5]\b|\bpermission_tier\b|\bpermission\s+tier\b", re.IGNORECASE)


def rule_tool_without_permission_tier(plan_path: Path, lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """BLOCKER: new tool / MCP / plugin / skill introduced in plan without a T0–T5
    permission tier or `permission_tier` keyword within 10 lines."""
    out: list[dict[str, Any]] = []
    n = len(lines)
    for idx, (lineno, line) in enumerate(lines):
        if not line:
            continue
        if NEW_TOOL_RE.search(line):
            lo = max(0, idx - 10)
            hi = min(n, idx + 11)
            has_tier = any(PERMISSION_TIER_RE.search(lines[j][1] or "") for j in range(lo, hi))
            if not has_tier:
                out.append(_finding(
                    claim_text=line.strip(),
                    claim_kind="tool_without_permission_tier",
                    subject={"path": None, "symbol": None, "noun": "tool"},
                    verification_command=None,
                    evidence={"file": str(plan_path), "line": lineno, "snippet": line.strip()},
                    result="inconclusive",
                    severity="BLOCKER",
                    confidence="medium",
                    rule_id="tool-without-permission-tier",
                ))
    return out


# Rule: external-call-without-budget-ceiling
# A plan that introduces a new external API call or LLM call MUST declare a
# per-call or per-run budget (token cap, request count, timeout, $ ceiling).
# Without one, the build is a cost-runaway / DoS surface (LLM04).

NEW_EXTCALL_RE = re.compile(
    r"\b(?:new|add|introduce|integrate|wire\s+up|call|invoke)\b[^.\n]{0,40}\b"
    r"(?:external\s+api|third[\s-]?party\s+api|llm\s+call|openai|anthropic|api\s+request|http\s+fetch|webhook)\b",
    re.IGNORECASE,
)
BUDGET_RE = re.compile(
    r"\b(?:budget|max[_\s-]?tokens?|token\s+cap|timeout|rate[_\s-]?limit|ceiling|cost\s+cap|"
    r"\$\s*\d+|per[_\s-]?run\s+limit|per[_\s-]?call\s+limit)\b",
    re.IGNORECASE,
)


def rule_external_call_without_budget_ceiling(plan_path: Path, lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """WARN: new external API or LLM call introduced without a budget / ceiling
    keyword within 10 lines."""
    out: list[dict[str, Any]] = []
    n = len(lines)
    for idx, (lineno, line) in enumerate(lines):
        if not line:
            continue
        if NEW_EXTCALL_RE.search(line):
            lo = max(0, idx - 10)
            hi = min(n, idx + 11)
            has_budget = any(BUDGET_RE.search(lines[j][1] or "") for j in range(lo, hi))
            if not has_budget:
                out.append(_finding(
                    claim_text=line.strip(),
                    claim_kind="external_call_without_budget",
                    subject={"path": None, "symbol": None, "noun": "external_call"},
                    verification_command=None,
                    evidence={"file": str(plan_path), "line": lineno, "snippet": line.strip()},
                    result="inconclusive",
                    severity="WARN",
                    confidence="medium",
                    rule_id="external-call-without-budget-ceiling",
                ))
    return out


# Rule: risk-surface-change-without-threat-model
# A plan that surfaces any risk-surface signal (new tool / MCP / LLM call /
# persistent memory / auth / external API / user-data handling) MUST reference
# a threat-model artifact OR explicitly declare "threat-model: not-applicable: <reason>".

RISK_SURFACE_RE = re.compile(
    r"\b(?:new\s+tool|new\s+mcp|new\s+plugin|new\s+skill|new\s+agent|"
    r"new\s+llm\s+call|llm\s+integration|prompt\s+template|"
    r"persistent\s+memory|vector\s+store|memory\s+store|"
    r"auth(?:entication|orization)?\s+(?:change|flow|gate)|"
    r"identity\s+propagation|permission\s+boundary|"
    r"new\s+(?:external\s+)?api|outbound\s+(?:fetch|request)|"
    r"pii|personal\s+data|credentials|regulated\s+data|user\s+data\s+handling)\b",
    re.IGNORECASE,
)
THREAT_MODEL_RE = re.compile(
    r"\b(?:threat[_\s-]?model|security[_\s-]?review|owasp|asi\d+|llm0\d+|"
    r"security-methodology|security-reviewer|"
    r"threat-model:\s*not[\s-]?applicable)\b",
    re.IGNORECASE,
)


def rule_risk_surface_change_without_threat_model(plan_path: Path, lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """BLOCKER: plan surfaces a risk-surface signal without referencing a
    threat-model artifact within the entire document."""
    # Scan the whole doc once for any threat-model reference
    has_threat_model_doc = any(THREAT_MODEL_RE.search(line) for _, line in lines if line)
    if has_threat_model_doc:
        return []
    # No threat-model reference anywhere — flag the FIRST risk-surface signal
    out: list[dict[str, Any]] = []
    for idx, (lineno, line) in enumerate(lines):
        if not line:
            continue
        if RISK_SURFACE_RE.search(line):
            out.append(_finding(
                claim_text=line.strip(),
                claim_kind="risk_surface_change_without_threat_model",
                subject={"path": None, "symbol": None, "noun": "risk_surface"},
                verification_command=None,
                evidence={"file": str(plan_path), "line": lineno, "snippet": line.strip()},
                result="inconclusive",
                severity="BLOCKER",
                confidence="medium",
                rule_id="risk-surface-change-without-threat-model",
            ))
            break  # one finding per plan; the rule is doc-level
    return out


# ---------------------------------------------------------------------------
# Rule: schema-migration-full-chain (priority 10/11, 2026-05-05)
# ---------------------------------------------------------------------------
# Recurring pattern: writer emits keys X, reader expects keys Y, drift goes
# undetected until runtime. Two instances on this branch alone (Chunk 7
# NavGator-lessons sync; Priority 7 index.json key alias).
#
# Trigger when the plan touches schema/serializer/storage files OR mentions
# changes to `to_dict|to_index|asdict|from_dict|from_index|@dataclass`.
# Require at least one of:
#   (a) matching test fixture file in tests/
#   (b) reader-side file (json.loads/json.load callers — heuristic: any
#       *.py path in the plan that's NOT the writer file)
#   (c) explicit override: `override: schema-migration-full-chain` in the
#       plan markdown
# Severity: WARN by default — false-positive risk on greenfield schemas
# where writer + reader land in the same file. WARN doesn't block the gate.

SCHEMA_FILE_RE = re.compile(
    r"(?:^|[\s`(])"                       # path boundary
    r"((?:[\w./-]*?/)?"                   # optional dir prefix
    r"(?:scripts/migrate_[\w_-]+|"        # migration scripts
    r"src/[\w/-]+/(?:schemas|storage)\.py|"  # schemas.py / storage.py
    r"[\w/-]+/_schema\.py))"              # *_schema.py
    r"(?:[\s`):,]|$)",                    # boundary
    re.IGNORECASE,
)
# Method/decorator names that signal serializer changes.
SCHEMA_METHOD_RE = re.compile(
    r"\b(?:to_dict|to_index|asdict|from_dict|from_index)\b|@dataclass\b",
    re.IGNORECASE,
)
# Paths that look like test fixtures: tests/... or .../test_*.py / *_test.py
TEST_PATH_RE = re.compile(
    r"(?:^|[\s`(])"
    r"((?:[\w./-]*?/)?tests?/[\w/-]+\.py|[\w/-]*?test_[\w_-]+\.py|[\w/-]*?[\w_-]+_test\.py)"
    r"(?:[\s`):,]|$)",
    re.IGNORECASE,
)
# Reader-side hints: any .py path mentioned that calls json.load(s) — we
# can't run the import graph from a markdown plan, so we accept ANY .py path
# in the plan beyond the writer files as evidence the reader side is in
# scope. The keyword "reader" / "json.loads" / "json.load" is also enough.
READER_HINT_RE = re.compile(
    r"\b(?:json\.loads?|reader|consumer|deserialize|deserializ|read[_\s-]?side)\b",
    re.IGNORECASE,
)
# Explicit override marker.
OVERRIDE_RE = re.compile(
    r"override\s*:\s*schema-migration-full-chain", re.IGNORECASE,
)


def rule_schema_migration_full_chain(plan_path: Path, lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """WARN: plan touches schema/serializer/migration files but lacks
    co-changes for the reader side (test fixture, reader file, or explicit
    override).

    The rule scans the entire plan once: if any line names a schema/migration
    path OR a serializer method, AND no line provides co-change evidence, we
    emit ONE finding pointing at the first triggering line. WARN-only — the
    plan author may still be correct on greenfield schemas; the goal is to
    surface the question so the reader-side gets named explicitly."""
    out: list[dict[str, Any]] = []

    # Single-pass scan — collect signals.
    schema_writer_paths: list[tuple[int, str, str]] = []  # (lineno, path, raw_line)
    serializer_methods: list[tuple[int, str]] = []       # (lineno, raw_line)
    test_paths: set[str] = set()
    reader_hints: list[tuple[int, str]] = []              # (lineno, raw_line)
    has_override = False

    for lineno, line in lines:
        if not line:
            continue
        if OVERRIDE_RE.search(line):
            has_override = True
        for m in SCHEMA_FILE_RE.finditer(line):
            schema_writer_paths.append((lineno, m.group(1), line))
        if SCHEMA_METHOD_RE.search(line):
            serializer_methods.append((lineno, line))
        for m in TEST_PATH_RE.finditer(line):
            test_paths.add(m.group(1))
        if READER_HINT_RE.search(line):
            reader_hints.append((lineno, line))

    # No signal → nothing to flag.
    if not (schema_writer_paths or serializer_methods):
        return out
    # Explicit override silences the rule.
    if has_override:
        return out
    # Co-change satisfied by EITHER a test fixture OR a reader hint.
    if test_paths:
        return out
    if reader_hints:
        return out

    # Pick the first triggering line for the finding evidence.
    if schema_writer_paths:
        lineno, path, raw = schema_writer_paths[0]
        subject_path = path
        snippet = raw.strip()
    else:
        lineno, raw = serializer_methods[0]
        subject_path = None
        snippet = raw.strip()

    out.append(_finding(
        claim_text=snippet,
        claim_kind="schema_migration_full_chain",
        subject={"path": subject_path, "symbol": None, "noun": "schema_migration"},
        verification_command=None,
        evidence={"file": str(plan_path), "line": lineno, "snippet": snippet},
        result="inconclusive",
        severity="WARN",
        confidence="medium",
        rule_id="schema-migration-full-chain",
    ))
    return out


# ---------------------------------------------------------------------------
# Rule: risk-reason-invalid-value (2026-05-08) — `risk_reason:` present in
# frontmatter or plan body with a value outside the canonical 5.
# ---------------------------------------------------------------------------
# Matches a `risk_reason:` line followed by a non-empty value.
RISK_REASON_LINE_RE = re.compile(
    r"^\s*risk_reason\s*:\s*(.+)$", re.IGNORECASE
)
# The five and only five canonical values (exact string match after strip).
RISK_REASON_CANONICAL: frozenset[str] = frozenset({
    "security boundary",
    "persistence contract",
    "runtime protocol",
    "deployment",
    "user trust claim",
})


def rule_risk_reason_invalid_value(plan_path: Path, lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """BLOCKER: `risk_reason:` is present but its value is not in the canonical 5.
    Absent `risk_reason:` is always valid. Only fires when a value is given."""
    out: list[dict[str, Any]] = []
    for lineno, line in lines:
        if not line:
            continue
        m = RISK_REASON_LINE_RE.match(line)
        if m:
            value = m.group(1).strip().strip('"').strip("'")
            if value and value not in RISK_REASON_CANONICAL:
                out.append(_finding(
                    claim_text=line.strip(),
                    claim_kind="risk_reason_invalid_value",
                    subject={"path": None, "symbol": None, "noun": "risk_reason"},
                    verification_command=None,
                    evidence={"file": str(plan_path), "line": lineno, "snippet": line.strip()},
                    result="no_match",
                    marker="❌",
                    severity="BLOCKER",
                    confidence="high",
                    rule_id="risk-reason-invalid-value",
                ))
    return out


# ---------------------------------------------------------------------------
# Rule: scope-audit-required (2026-05-08) — `modifies_api: true` without a
# companion `scope_auditor_status:` field in the same plan. WARN-only.
# ---------------------------------------------------------------------------
MODIFIES_API_TRUE_RE = re.compile(
    r"^\s*modifies_api\s*:\s*true\s*$", re.IGNORECASE
)
SCOPE_AUDITOR_STATUS_RE = re.compile(
    r"\bscope_auditor_status\s*:", re.IGNORECASE
)
# Captures the chunk/plan id from adjacent frontmatter. Look for `id:` within
# 20 lines of the `modifies_api: true` line. Falls back to "unknown".
FRONTMATTER_ID_RE = re.compile(r"^\s*id\s*:\s*(.+)$", re.IGNORECASE)


_YAML_KEY_RE = re.compile(r"^\s*[a-z_][a-z0-9_]*\s*:\s*\S")


def _line_has_frontmatter_neighbors(lines: list[tuple[int, str]], idx: int, window: int = 5) -> bool:
    """True when at least two other YAML-shape `key: value` lines appear
    within `window` lines of `idx`. Cheap heuristic that distinguishes a
    genuine frontmatter block from a one-off prose mention."""
    n = len(lines)
    lo = max(0, idx - window)
    hi = min(n, idx + window + 1)
    neighbors = 0
    for j in range(lo, hi):
        if j == idx:
            continue
        text = lines[j][1] or ""
        if _YAML_KEY_RE.match(text):
            neighbors += 1
            if neighbors >= 2:
                return True
    return False


def rule_scope_audit_required(plan_path: Path, lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """WARN: `modifies_api: true` is set in plan/chunk frontmatter but no
    `scope_auditor_status:` field follows in the same plan.
    Severity is WARN because the gate is enforced by the orchestrator at
    dispatch time; this rule only surfaces the missing audit trail."""
    # Scan the whole doc once for scope_auditor_status.
    has_audit_status = any(
        SCOPE_AUDITOR_STATUS_RE.search(line) for _, line in lines if line
    )
    if has_audit_status:
        return []
    # Look for modifies_api: true lines.
    out: list[dict[str, Any]] = []
    n = len(lines)
    for idx, (lineno, line) in enumerate(lines):
        if not line:
            continue
        if MODIFIES_API_TRUE_RE.match(line):
            # Suppress prose false-positives: only fire when the line is
            # surrounded by other YAML-shape keys (i.e. inside a frontmatter
            # block), not when it appears as a one-off mention in prose.
            if not _line_has_frontmatter_neighbors(lines, idx):
                continue
            # Find nearest id: within 20 lines above/below.
            lo = max(0, idx - 20)
            hi = min(n, idx + 21)
            chunk_id = "unknown"
            for j in range(lo, hi):
                mid = FRONTMATTER_ID_RE.match(lines[j][1] or "")
                if mid:
                    chunk_id = mid.group(1).strip().strip('"').strip("'")
                    break
            claim = (
                f"modifies_api: true is set on chunk '{chunk_id}' but no "
                "scope_auditor_status field follows — orchestrator will require "
                "a scope-auditor pass before Phase 3 dispatch."
            )
            out.append(_finding(
                claim_text=claim,
                claim_kind="scope_audit_required",
                subject={"path": None, "symbol": None, "noun": "scope_auditor_status"},
                verification_command=None,
                evidence={"file": str(plan_path), "line": lineno, "snippet": line.strip()},
                result="inconclusive",
                severity="WARN",
                confidence="high",
                rule_id="scope-audit-required",
            ))
    return out


# Rule: synthesis-dim-vague-value (2026-05-07) — flag vague values in the
# `synthesis_dimensions:` block (defeats Opus pre-resolution of synthesis).
SYNTHESIS_VAGUE_RE = re.compile(
    r"\b(appropriate|follow\s+existing|match\s+patterns?|as\s+needed|"
    r"see\s+existing|reasonable)\b", re.IGNORECASE)
SYNTHESIS_HEADER_RE = re.compile(r"^\s*synthesis_dimensions\s*:\s*$", re.IGNORECASE)


def iter_synthesis_dimension_entries(lines: list[tuple[int, str]]):
    """Yield (lineno, raw_line) for each entry inside any `synthesis_dimensions:`
    block in the plan. A block ends at the first non-indented, non-blank line.

    Shared between the deterministic vague-value rule and any caller (e.g.
    Phase 1 routing) that needs to count synthesis dimensions. Fenced code
    blocks and HTML comments are already stripped upstream by
    strip_fenced_blocks(). Multiple synthesis_dimensions blocks in a single
    plan are concatenated."""
    n, i = len(lines), 0
    while i < n:
        lineno, line = lines[i]
        if line and SYNTHESIS_HEADER_RE.match(line):
            j = i + 1
            while j < n:
                lj, lline = lines[j]
                if lline == "":
                    j += 1; continue
                if not re.match(r"^[ \t]+\S", lline):
                    break
                yield lj, lline
                j += 1
            i = j
            continue
        i += 1


def count_synthesis_dimensions(plan_path: Path) -> int:
    """Count entries inside the plan's `synthesis_dimensions:` block(s).

    Used by Phase 1 routing in build-loop's orchestrator: a count > 5
    (i.e. 6 or more entries) signals a synthesis-dense commit that should
    NOT fan out to Sonnet implementers; Phase 3 instead dispatches inline
    at `tier: thinking` (single-context, Opus-class). See
    `agents/build-orchestrator.md` §"Phase 1 routing — synthesis-density
    escalation" and `skills/build-loop/SKILL.md` Phase 1.

    Reuses the same parser as `rule_synthesis_dim_vague_value`; do NOT
    introduce a second parser."""
    text = plan_path.read_text(encoding="utf-8")
    lines = strip_fenced_blocks(text)
    return sum(1 for _ in iter_synthesis_dimension_entries(lines))


def rule_synthesis_dim_vague_value(plan_path: Path, lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """BLOCKER: vague value inside a `synthesis_dimensions:` block.
    Block ends at first non-indented, non-blank line."""
    out: list[dict[str, Any]] = []
    for lj, lline in iter_synthesis_dimension_entries(lines):
        colon_idx = lline.find(":")
        value_part = lline[colon_idx + 1:] if colon_idx >= 0 else lline
        if SYNTHESIS_VAGUE_RE.search(value_part):
            out.append(_finding(
                claim_text=lline.strip(), claim_kind="missing_evidence",
                subject={"path": None, "symbol": None, "noun": "synthesis_dimension"},
                evidence={"file": str(plan_path), "line": lj, "snippet": lline.strip()},
                result="no_match", marker="❌", severity="BLOCKER",
                confidence="high", rule_id="synthesis-dim-vague-value"))
    return out


# ---------------------------------------------------------------------------
# Rule: parallel-decision-record (2026-05-21) — BLOCKER when a plan names
# multiple independent/parallel-safe chunks but omits an explicit dispatch
# decision. This prevents build-loop reports from praising parallelism while
# the actual execution path silently serializes independent work.
# ---------------------------------------------------------------------------

PARALLEL_SIGNAL_RE = re.compile(
    r"\b(independent|parallel[- ]safe|parallelizable|fan[- ]out|concurrent)\b",
    re.IGNORECASE,
)
PARALLEL_DECISION_RE = re.compile(
    r"\b(parallel_batch|parallel_skipped_reason)\b",
    re.IGNORECASE,
)
CHUNK_ID_RE = re.compile(r"\b(?:C|chunk[-_ ]?)(\d+)\b", re.IGNORECASE)


def rule_parallel_decision_record(
    plan_path: Path, lines: list[tuple[int, str]]
) -> list[dict[str, Any]]:
    """BLOCKER if a multi-chunk plan claims independent/parallel-safe work
    without recording the dispatch decision.

    The rule is intentionally opt-in by text signal: it only fires after the
    plan itself says the work is independent/parallel-safe/concurrent. That
    avoids guessing dependencies from file lists while still enforcing the
    high-value case that caused the feedback item.
    """
    chunk_ids: set[str] = set()
    signal_line: tuple[int, str] | None = None
    has_decision = False

    for lineno, line in lines:
        if not line:
            continue
        for match in CHUNK_ID_RE.finditer(line):
            chunk_ids.add(match.group(1))
        if PARALLEL_SIGNAL_RE.search(line) and signal_line is None:
            signal_line = (lineno, line)
        if PARALLEL_DECISION_RE.search(line):
            has_decision = True

    if len(chunk_ids) < 2 or signal_line is None or has_decision:
        return []

    lineno, line = signal_line
    return [_finding(
        claim_text=(
            "Plan identifies multiple independent/parallel-safe chunks but "
            "does not record `parallel_batch` or `parallel_skipped_reason`."
        ),
        claim_kind="parallel_decision_missing",
        subject={"path": None, "symbol": None, "noun": "dispatch_plan"},
        verification_command=None,
        evidence={"file": str(plan_path), "line": lineno, "snippet": line.strip()},
        result="no_match",
        severity="BLOCKER",
        confidence="high",
        rule_id="parallel-decision-record",
    )]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_all(plan_path: Path, repo: Path | None) -> list[dict[str, Any]]:
    text = plan_path.read_text(encoding="utf-8")
    lines = strip_fenced_blocks(text)
    findings: list[dict[str, Any]] = []
    findings.extend(rule_delete_with_callers(plan_path, lines, repo))
    findings.extend(rule_numeric_drift(plan_path, lines))
    findings.extend(rule_route_change_evidence(plan_path, lines))
    findings.extend(rule_package_state(plan_path, lines, repo))
    findings.extend(rule_missing_evidence(plan_path, lines))
    findings.extend(rule_scope_split(plan_path, lines))
    findings.extend(rule_less_invasive_shim(plan_path, lines))
    findings.extend(rule_tool_without_permission_tier(plan_path, lines))
    findings.extend(rule_external_call_without_budget_ceiling(plan_path, lines))
    findings.extend(rule_risk_surface_change_without_threat_model(plan_path, lines))
    findings.extend(rule_schema_migration_full_chain(plan_path, lines))
    findings.extend(rule_synthesis_dim_vague_value(plan_path, lines))
    findings.extend(rule_risk_reason_invalid_value(plan_path, lines))
    findings.extend(rule_scope_audit_required(plan_path, lines))
    findings.extend(rule_task_id_convention(plan_path, lines))
    findings.extend(rule_forbidden_path_conflict(plan_path, lines, repo))
    findings.extend(rule_parallel_decision_record(plan_path, lines))
    return findings


# ---------------------------------------------------------------------------
# Rule: forbidden-path-conflict (2026-05-20) — WARN when a chunk's files_owned
# (YAML `files_owned:` block or `**Files owned:**` bullet) intersects the
# dispatch's forbidden-paths set. Mirrors the protectedBranches plan-verify
# shape (commit 3264983). Same shape ships R5/R1 as markdown additions; this
# is the Python-side companion that catches forbidden-path × required-path
# conflicts at Phase 2 instead of bailing at Sub-step F.
# ---------------------------------------------------------------------------

DEFAULT_FORBIDDEN_PATHS = (
    "project.yml",
    ".github/workflows/*",
    "package-lock.json",
    "pnpm-lock.yaml",
    "Package.resolved",
)
_FILES_OWNED_YAML_RE = re.compile(r"^\s*files_owned\s*:\s*(.*)$", re.IGNORECASE)
_PATH_TOKEN_RE = re.compile(r"[`\"']?([A-Za-z0-9_./\-\*]+\.[A-Za-z0-9]+|\.?[A-Za-z0-9_./\-\*]+/[A-Za-z0-9_./\-\*]+)[`\"']?")


def _load_forbidden_paths(repo: Path | None) -> tuple[str, ...]:
    if repo is None:
        return DEFAULT_FORBIDDEN_PATHS
    cfg = repo / ".build-loop" / "config.json"
    if not cfg.exists():
        return DEFAULT_FORBIDDEN_PATHS
    try:
        data = json.loads(cfg.read_text())
        raw = data.get("dispatch", {}).get("forbiddenPaths")
        if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
            return tuple(x.strip() for x in raw if x.strip())
    except (json.JSONDecodeError, AttributeError):
        pass
    return DEFAULT_FORBIDDEN_PATHS


def rule_forbidden_path_conflict(
    plan_path: Path, lines: list[tuple[int, str]], repo: Path | None
) -> list[dict[str, Any]]:
    """WARN if a chunk's `files_owned:` (YAML inline list, e.g. `[a, b]`)
    intersects the dispatch's forbidden-paths set. Surfaces grep-checkable
    plan/policy conflicts at Phase 2 instead of bailing Phase 3 with
    implementer time already spent."""
    forbidden = _load_forbidden_paths(repo)
    if not forbidden:
        return []
    out: list[dict[str, Any]] = []
    for lineno, line in lines:
        if not line:
            continue
        m = _FILES_OWNED_YAML_RE.match(line)
        if not m:
            continue
        owned = [t.group(1) for t in _PATH_TOKEN_RE.finditer(m.group(1))]
        if not owned:
            continue
        hits = [p for p in owned if any(fnmatch.fnmatch(p, pat) for pat in forbidden)]
        if not hits:
            continue
        out.append(_finding(
            claim_text=(
                f"Chunk requires editing forbidden path(s): {hits}. Either relax "
                "`dispatch.forbiddenPaths` in .build-loop/config.json or rescope "
                "the chunk before Phase 3 dispatch."
            ),
            claim_kind="forbidden_path_conflict",
            subject={"path": None, "symbol": None, "noun": "files_owned"},
            verification_command=None,
            evidence={"file": str(plan_path), "line": lineno, "snippet": line.strip()},
            result="needs_attention",
            severity="WARN",
            confidence="high",
            rule_id="forbidden-path-conflict",
        ))
    return out


# ---------------------------------------------------------------------------
# Rule: task-id-convention (2026-05-13, plan §15.2) — opt-in T-N convention
# for plan tasks. Fires only when at least one T-N appears in the plan.
# Validates uniqueness + sequential. WARN-level — plans without T-N IDs still
# pass (graceful degradation downstream); plans WITH T-N IDs must have them
# correct.
# ---------------------------------------------------------------------------

TASK_ID_RE = re.compile(r"\bT-(\d+)\b")


def rule_task_id_convention(plan_path: Path, lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """WARN: T-N task IDs are used but inconsistently (duplicate or
    non-sequential). Opt-in convention — silent when no T-N IDs appear."""
    ids_with_lineno: list[tuple[int, int, str]] = []  # (lineno, n_value, raw)
    for lineno, line in lines:
        if not line:
            continue
        for m in TASK_ID_RE.finditer(line):
            ids_with_lineno.append((lineno, int(m.group(1)), m.group(0)))

    if not ids_with_lineno:
        return []  # convention not in use; silent

    out: list[dict[str, Any]] = []
    seen_n: dict[int, list[int]] = {}
    for lineno, n, _raw in ids_with_lineno:
        seen_n.setdefault(n, []).append(lineno)

    # Duplicate detection — same N referenced multiple times is usually fine
    # (a table row + a detail heading + a brief mention all use T-3). The
    # WARN fires only when N appears as a DEFINING reference (heading or table
    # row) more than once. Simplest heuristic: count headings only.
    heading_re = re.compile(r"^\s{0,3}#{2,4}\s+T-(\d+)\b")
    heading_counts: dict[int, list[int]] = {}
    for lineno, line in lines:
        if not line:
            continue
        m = heading_re.match(line)
        if m:
            n = int(m.group(1))
            heading_counts.setdefault(n, []).append(lineno)

    for n, linenos in heading_counts.items():
        if len(linenos) > 1:
            out.append(_finding(
                claim_text=f"T-{n} appears as a task heading on {len(linenos)} different lines",
                claim_kind="task_id_duplicate",
                subject={"path": None, "symbol": f"T-{n}", "noun": "task_id"},
                verification_command=None,
                evidence={"file": str(plan_path), "line": linenos[0],
                          "snippet": f"T-{n} heading at lines {linenos}"},
                result="no_match",
                severity="WARN",
                confidence="high",
                rule_id="task-id-convention",
            ))

    # Sequential check — IDs should start at T-1 and have no gaps. Scope to
    # task-defining contexts (headings) only; prose mentions like "see T-5"
    # can otherwise mask real gaps or trigger spurious "doesn't start at T-1"
    # findings when a plan footnotes a task ID from a sibling document.
    defining_ns = sorted(heading_counts.keys())
    if defining_ns and defining_ns[0] != 1:
        out.append(_finding(
            claim_text=f"T-N task IDs should start at T-1 (lowest defining heading: T-{defining_ns[0]})",
            claim_kind="task_id_not_starting_at_one",
            subject={"path": None, "symbol": f"T-{defining_ns[0]}", "noun": "task_id"},
            verification_command=None,
            evidence={"file": str(plan_path), "line": heading_counts[defining_ns[0]][0],
                      "snippet": "first defining T-N is not T-1"},
            result="no_match",
            severity="WARN",
            confidence="high",
            rule_id="task-id-convention",
        ))
    elif defining_ns:
        expected = list(range(1, max(defining_ns) + 1))
        missing = [n for n in expected if n not in heading_counts]
        if missing:
            out.append(_finding(
                claim_text=f"T-N task IDs have gaps in defining headings; missing: {', '.join(f'T-{n}' for n in missing)}",
                claim_kind="task_id_gap",
                subject={"path": None, "symbol": f"T-{missing[0]}", "noun": "task_id"},
                verification_command=None,
                evidence={"file": str(plan_path), "line": ids_with_lineno[0][0],
                          "snippet": f"defining headings: {defining_ns}"},
                result="no_match",
                severity="WARN",
                confidence="high",
                rule_id="task-id-convention",
            ))

    return out


def summarize(findings: list[dict[str, Any]]) -> dict[str, Any]:
    by_severity = {"BLOCKER": 0, "WARN": 0, "INFO": 0}
    by_rule: dict[str, dict[str, int]] = {}
    for f in findings:
        sev = f["severity"]
        by_severity[sev] = by_severity.get(sev, 0) + 1
        rid = f["rule_id"]
        by_rule.setdefault(rid, {"BLOCKER": 0, "WARN": 0, "INFO": 0})[sev] += 1
    return {"by_severity": by_severity, "by_rule_id": by_rule, "total": len(findings)}


def render_human(findings: list[dict[str, Any]], summary: dict[str, Any], plan_path: Path) -> str:
    lines = [f"# plan-verify report — {plan_path.name}", ""]
    lines.append(f"**Total findings:** {summary['total']} "
                 f"(BLOCKER={summary['by_severity']['BLOCKER']}, "
                 f"WARN={summary['by_severity']['WARN']}, "
                 f"INFO={summary['by_severity']['INFO']})")
    lines.append("")
    if not findings:
        lines.append("No findings. ✅")
        return "\n".join(lines)
    # Group by severity
    for sev in ("BLOCKER", "WARN", "INFO"):
        bucket = [f for f in findings if f["severity"] == sev]
        if not bucket:
            continue
        lines.append(f"## {sev} ({len(bucket)})")
        for f in bucket:
            ev = f["evidence"]
            lines.append(f"- **[{f['rule_id']}]** line {ev['line']}: {f['claim_text']}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Deterministic plan verifier (build-loop Phase 2 gate).")
    p.add_argument("plan", help="Path to plan markdown file")
    p.add_argument("--repo", help="Repo root for grep checks (defaults to plan file's parent's git root)")
    p.add_argument("--json", action="store_true", help="Emit findings as JSON")
    p.add_argument("--quiet", action="store_true", help="Suppress human summary on stdout")
    args = p.parse_args(argv)

    plan_path = Path(args.plan).expanduser().resolve()
    if not plan_path.exists():
        print(f"plan-verify: file not found: {plan_path}", file=sys.stderr)
        return 2
    repo = Path(args.repo).expanduser().resolve() if args.repo else None

    try:
        findings = run_all(plan_path, repo)
    except Exception as e:  # noqa: BLE001 — verifier-error -> exit 2
        print(f"plan-verify: error: {e}", file=sys.stderr)
        return 2
    summary = summarize(findings)

    if args.json:
        out = {"plan": str(plan_path), "repo": str(repo) if repo else None,
               "summary": summary, "findings": findings}
        print(json.dumps(out, indent=2))
    elif not args.quiet:
        print(render_human(findings, summary, plan_path))

    return 1 if summary["by_severity"]["BLOCKER"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
