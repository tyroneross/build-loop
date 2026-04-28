#!/usr/bin/env python3
"""
plan_verify.py — deterministic plan verifier for build-loop Phase 2.

Reads a plan markdown file, applies grep-checkable rules, and emits findings
in the Plan Evidence Contract shape. Exits 0 on no BLOCKERs, 1 on BLOCKERs,
2 on verifier error.

Stdlib only: re, pathlib, subprocess, json, argparse, sys.

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


def strip_fenced_blocks(text: str) -> list[tuple[int, str]]:
    """Return list of (1-based line number, line text) with fenced code blocks
    replaced by empty strings (so line numbers stay stable)."""
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
    """Run rg if available else grep -R. Return (found, command_used).
    Excludes .git, node_modules, dist, .build-loop, .navgator, fixture dirs."""
    excludes_grep = [
        "--exclude-dir=.git",
        "--exclude-dir=node_modules",
        "--exclude-dir=dist",
        "--exclude-dir=.build-loop",
        "--exclude-dir=.navgator",
        "--exclude-dir=test-fixtures",
        "--exclude-dir=__pycache__",
    ]
    # Try rg first
    try:
        cmd = ["rg", "-l", "--hidden", "-g", "!.git", "-g", "!node_modules",
               "-g", "!dist", "-g", "!.build-loop", "-g", "!.navgator",
               "-g", "!test-fixtures", "-g", "!__pycache__", pattern, str(repo)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (r.returncode == 0 and bool(r.stdout.strip()), " ".join(cmd))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Fallback to grep
    try:
        cmd = ["grep", "-R", "-l"] + excludes_grep + [pattern, str(repo)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (r.returncode == 0 and bool(r.stdout.strip()), " ".join(cmd))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return (False, "grep:unavailable")


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
# documented atomize-ai failure mode. "callers"/"files"/"routes" routinely
# vary by subject across rows of a table and are NOT internal contradictions.
# The atomize-ai v2.0 leak was orphan-count drift in a totals statement.
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
    return findings


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
