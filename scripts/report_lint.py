#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""User-facing output style lint for build-loop's Phase 4 Review-G draft.

Orthogonal to ``scripts/build_report_lint.py`` (structural — parallel_batch,
merge_plan, evidence triplets). This script lints **style and jargon** so the
final report the user sees in their terminal is concise, clear, and free of
internal codenames.

Rules
-----
- ``headline-present``: first non-blank line is a complete sentence stating
  what changed. Headings and bare noun phrases fail.
- ``validation-line-present``: at least one validation line names a concrete
  method/command/tool AND carries a status marker (``✅``/``⚠``/``❓``).
- ``jargon-blocklist``: internal tokens (``GAP-1``, ``auditor_status``,
  ``MECE``, ``envelope``, ``sub-step``, verdict-taxonomy words, etc.) that
  must be translated to plain language in user-facing output.
- ``contrastive-pivot``: the ``not X — it's Y`` / ``isn't X, it's Y`` /
  ``not just X but Y`` construction.
- ``length-cap``: report exceeds soft length budget (300 lines default).

Output contract
---------------
- Pure stdlib, no third-party deps.
- Never raises on a malformed file — surfaces as a single ``lint-outage`` finding.
- Exits 0 on success or with findings (warn-mode). Exit 2 only on missing file
  or argparse error.
- ``--json`` for machine consumption (Review-G auto-revise reads this).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

DEFAULT_LENGTH_CAP = 300

FENCE_RE = re.compile(r"^\s*```")
HEADING_RE = re.compile(r"^\s*#{1,6}\s")
BULLET_RE = re.compile(r"^\s*[-*+]\s")
NUMBERED_BULLET_RE = re.compile(r"^\s*\d+\.\s")

# A "complete sentence" headline: starts with a capital letter (or backtick
# delimiter for code-style start), contains at least one verb-bearing word,
# and ends with a sentence terminator. We approximate with: ends with ``.``,
# ``!``, ``?``, or ``;``; length >= 30 chars; not a heading; not a bullet;
# not a noun phrase fragment (no verb-like token).
SENTENCE_TERMINATORS = ".!?;"
# Common English finite verbs / verb markers. Conservative — false-negatives
# on this list mean fewer lints, which is fine; false-positives flag GOOD
# headlines and trigger needless auto-revise, which IS the unsafe side for a
# WARN linter that edits the draft on findings. Extend this list liberally.
VERB_HINT_RE = re.compile(
    r"\b(is|are|was|were|has|have|had|will|would|can|could|should|"
    r"added|added|adds|adding|"
    r"removes?|removed|removing|"
    r"fixes?|fixed|fixing|"
    r"closes?|closed|closing|"
    r"updates?|updated|updating|"
    r"writes?|wrote|written|"
    r"runs?|ran|running|"
    r"emits?|emitted|emitting|"
    r"wires?|wired|wiring|"
    r"enforces?|enforced|enforcing|"
    r"replaces?|replaced|replacing|"
    r"ships?|shipped|shipping|"
    r"introduces?|introduced|introducing|"
    r"captures?|captured|capturing|"
    r"delivers?|delivered|delivering|"
    r"enables?|enabled|enabling|"
    r"improves?|improved|improving|"
    r"creates?|created|creating|"
    r"generates?|generated|generating|"
    r"activates?|activated|activating|"
    r"triggers?|triggered|triggering|"
    r"migrates?|migrated|migrating|"
    r"installs?|installed|installing|"
    r"now|then|today|"
    r"made|makes|making|"
    r"land(s|ed|ing)?|"
    r"commit(s|ted|ting)?)\b",
    re.IGNORECASE,
)

VALIDATION_STATUS_RE = re.compile(r"[✅⚠❓]|⚠️")
# A method/command/tool pattern: bare-word command + path-like or .py/.sh
# extension, or "pytest", "curl", "ran", "passing test", "scan", etc.
VALIDATION_METHOD_RE = re.compile(
    r"\b("
    r"verified by|validated by|tested via|tested by|"
    r"python3?\s+\S+|"
    r"pytest|"
    r"npm\s+\S+|"
    r"curl|"
    r"ran\s+(the\s+)?\S+|"
    r"passing test|"
    r"ibr scan|"
    r"native[- ]?ax|"
    r"\bcommand:\s*\S+|"
    r"observed by|observer:"
    r")",
    re.IGNORECASE,
)

# Jargon blocklist — internal tokens that should be translated for the user.
# Each entry is ``(compiled_pattern, message)``. Patterns compile case-insensitive
# so "MECE"/"mece" and "Sub-step"/"sub-step" hit the same rule.
_JARGON_RAW: list[tuple[str, str]] = [
    # gap codenames
    (r"\bGAP[-_ ]?\d+\b", "gap codename — name the actual gap"),
    # auditor_status enum values
    (r"\bauditor_status\b", "auditor_status — describe the result in words"),
    (r"\bnot-run:parent-must-dispatch\b", "auditor-status enum — say 'auditor not run'"),
    (r"\bran:dispatched-agent\b", "auditor-status enum — say 'auditor ran'"),
    (r"\bran:peer-host\b", "auditor-status enum — say 'auditor ran on peer'"),
    (r"\bcross-vendor-deferred\b", "auditor-status enum — say 'cross-tool audit deferred'"),
    # phase / sub-step codenames
    (r"\bsub-?step\s*[A-G]\b", "sub-step codename — describe what the step does"),
    (r"\bPhase\s*[1-6][A-Ga-g]?\b", "phase codename — describe the step in words"),
    # process acronyms
    (r"\bMECE\b", "MECE — say 'ownership split' or 'one owner per file'"),
    (r"\bC-(HEAL|RCA|FLOW|SUPPLY|JUDGE)[A-Z]*(/[a-z_]+)?\b", "internal constitution codename — describe the behavior"),
    # data-shape jargon
    (r"\benvelope\b", "envelope — say 'result' or 'return data'"),
    (r"\bscope\s*=\s*build\b", "scope=build — say 'full build review' or omit"),
    (r"\bstate\.json\.runs\[\]", "state.json.runs[] — say 'run record'"),
    # verdict taxonomy
    (r"\bsuggest_correction\b", "verdict enum — say 'needs change'"),
    (r"\blook_again\b", "verdict enum — say 'needs another look'"),
    (r"\byay\s*\(approve\)", "verdict enum — say 'approve'"),
    (r"\bnay\s*\(reject\)", "verdict enum — say 'reject'"),
    (r"\b(yay|nay)\s+verdict\b", "verdict enum — say approve/reject"),
]
JARGON_BLOCKLIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pat, re.IGNORECASE), msg) for pat, msg in _JARGON_RAW
]

# Contrastive pivot: "not X — it's Y", "isn't X, it's Y", "not just X but Y".
CONTRASTIVE_PIVOT_RES = [
    re.compile(r"\bnot\s+[^.\n]{1,40}\s*[—-]\s*it'?s\s+", re.IGNORECASE),
    re.compile(r"\bisn'?t\s+[^.\n]{1,40},\s*it'?s\s+", re.IGNORECASE),
    re.compile(r"\bnot\s+just\s+[^.\n]{1,40}\s+but\s+", re.IGNORECASE),
    re.compile(r"\brather than\s+[^.\n]{1,40}\s*[—,]\s*", re.IGNORECASE),
]


def _finding(
    *,
    rule_id: str,
    severity: str,
    line: int | None,
    snippet: str | None,
    message: str,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "message": message,
        "evidence": {"line": line, "snippet": snippet},
    }


def _strip_fenced_blocks(text: str) -> list[tuple[int, str]]:
    """Return ``(lineno, line)`` pairs with fenced code blocks blanked out.

    Fenced examples illustrate good/bad style — they must not trigger lint
    findings against themselves.
    """
    out: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out.append((lineno, ""))
            continue
        out.append((lineno, "" if in_fence else line))
    return out


def _first_nonblank(lines: list[tuple[int, str]]) -> tuple[int, str] | None:
    for lineno, line in lines:
        if line.strip():
            return lineno, line
    return None


def lint_headline(lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """First non-blank line must be a plain sentence stating what changed."""
    first = _first_nonblank(lines)
    if first is None:
        return [_finding(
            rule_id="headline-present",
            severity="WARN",
            line=None,
            snippet=None,
            message="Report is empty — needs a one-sentence headline.",
        )]
    lineno, line = first
    stripped = line.strip()
    if HEADING_RE.match(stripped):
        return [_finding(
            rule_id="headline-present",
            severity="WARN",
            line=lineno,
            snippet=stripped,
            message="First line is a Markdown heading; needs a plain sentence headline above the sections.",
        )]
    if BULLET_RE.match(stripped) or NUMBERED_BULLET_RE.match(stripped):
        return [_finding(
            rule_id="headline-present",
            severity="WARN",
            line=lineno,
            snippet=stripped,
            message="First line is a bullet; needs a plain-sentence headline first.",
        )]
    # Length + sentence-terminator + verb-hint check.
    if len(stripped) < 30:
        return [_finding(
            rule_id="headline-present",
            severity="WARN",
            line=lineno,
            snippet=stripped,
            message="Headline is too short — write a full sentence stating what changed.",
        )]
    if stripped[-1] not in SENTENCE_TERMINATORS:
        return [_finding(
            rule_id="headline-present",
            severity="WARN",
            line=lineno,
            snippet=stripped,
            message="Headline is missing a sentence terminator — write a full sentence.",
        )]
    if not VERB_HINT_RE.search(stripped):
        return [_finding(
            rule_id="headline-present",
            severity="WARN",
            line=lineno,
            snippet=stripped,
            message="Headline reads like a noun phrase — write what changed with a verb.",
        )]
    return []


def lint_validation_line(lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """At least one line must name a method/tool AND carry a status marker."""
    for lineno, line in lines:
        if VALIDATION_STATUS_RE.search(line) and VALIDATION_METHOD_RE.search(line):
            return []
    # Fallback: a status marker on a line that mentions a `.py` / `.sh` /
    # `.json` artifact also counts as a validation line.
    for lineno, line in lines:
        if VALIDATION_STATUS_RE.search(line) and re.search(r"\.(py|sh|json|md|jsonl)\b", line):
            return []
    return [_finding(
        rule_id="validation-line-present",
        severity="WARN",
        line=None,
        snippet=None,
        message=(
            "No validation line found. Add one line with a status marker "
            "(✅/⚠️/❓) naming the method/command/tool that verified the work."
        ),
    )]


def lint_jargon(lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for lineno, line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        for pattern, message in JARGON_BLOCKLIST:
            match = pattern.search(stripped)
            if match:
                findings.append(_finding(
                    rule_id="jargon-blocklist",
                    severity="WARN",
                    line=lineno,
                    snippet=stripped,
                    message=f"Jargon `{match.group(0)}` — {message}.",
                ))
                # One finding per line is enough; move on.
                break
    return findings


def lint_contrastive_pivot(lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for lineno, line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in CONTRASTIVE_PIVOT_RES:
            match = pattern.search(stripped)
            if match:
                findings.append(_finding(
                    rule_id="contrastive-pivot",
                    severity="WARN",
                    line=lineno,
                    snippet=stripped,
                    message=(
                        f"Contrastive-pivot construction `{match.group(0).strip()}` — "
                        "state the point directly without the negation."
                    ),
                ))
                break
    return findings


# --- Direct language: clear verb, clear outcome ------------------------------------------
# The doctrine already lives in references/output-style.md ("Precision and Brevity": strong
# verb + specific outcome; adverbs replaced by data). It was TAUGHT but never ENFORCED, so it
# was routinely ignored. These rules close that gap. Sourced from that doc's own verb table so
# doctrine and enforcement cannot drift apart.

# Weak verb / nominalization -> the strong verb it is hiding.
WEAK_VERBS = {
    r"had an impact on": "changed",
    r"was responsible for": "caused",
    r"made improvements to": "improved",
    r"experienced delays": "slipped",
    r"provided support for": "supported",
    r"made a decision": "decided",
    r"performed an analysis of": "analyzed",
    r"conducted a review of": "reviewed",
    r"took steps to": "(name the step)",
    r"is able to": "can",
    r"in order to": "to",
    r"has the ability to": "can",
    r"gave consideration to": "considered",
    r"reached a conclusion": "concluded",
}
WEAK_VERB_RES = [(re.compile(rf"\b{p}\b", re.IGNORECASE), r) for p, r in WEAK_VERBS.items()]

# Filler openers: they carry no information and delay the verb.
FILLER_OPENER_RE = re.compile(
    r"^\s*(?:Now,|Basically,|Essentially,|Of course,|As you know,|It'?s worth noting|"
    r"I'?ll now|Let me (?:now )?|First of all,|To be clear,)",
    re.IGNORECASE,
)

# Hedges that add no calibration. A line already carrying a status marker or an explicit
# confidence word IS calibrated, so it is exempt (that hedging is required, not padding).
HEDGE_RE = re.compile(
    r"\b(?:I think|I believe|perhaps|kind of|sort of|somewhat|arguably|"
    r"it seems like|fairly confident|pretty much)\b",
    re.IGNORECASE,
)
CALIBRATED_RE = re.compile(r"[✅⚠❓]|\bunverified\b|\bassumed\b|\binferred\b|\buncertain\b", re.IGNORECASE)

EM_DASH_RE = re.compile(r"\u2014")
EM_DASH_MAX = 2  # an occasional em dash is fine; leaning on them is not


def lint_direct_language(lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """Clear verb, clear outcome. Flags weak verbs, filler, uncalibrated hedges, em dashes."""
    findings: list[dict[str, Any]] = []
    for lineno, line in lines:
        stripped = line.strip()
        if not stripped or HEADING_RE.match(line):
            continue

        for pattern, strong in WEAK_VERB_RES:
            m = pattern.search(stripped)
            if m:
                findings.append(_finding(
                    rule_id="weak-verb", severity="warn", line=lineno, snippet=m.group(0),
                    message=f"Weak verb {m.group(0)!r} hides the action. Use {strong!r}.",
                ))
                break

        m = FILLER_OPENER_RE.match(stripped)
        if m:
            findings.append(_finding(
                rule_id="filler-opener", severity="warn", line=lineno, snippet=m.group(0),
                message=f"Filler opener {m.group(0).strip()!r} delays the verb. Start with the action.",
            ))

        if not CALIBRATED_RE.search(stripped):
            m = HEDGE_RE.search(stripped)
            if m:
                findings.append(_finding(
                    rule_id="hedge", severity="warn", line=lineno, snippet=m.group(0),
                    message=f"Hedge {m.group(0)!r} adds no calibration. State it, or mark confidence "
                            f"(✅ verified / ⚠️ untested / ❓ uncertain).",
                ))

    # Em dashes: DENSITY, not per-line. An occasional one is fine; leaning on them is not.
    total = sum(len(EM_DASH_RE.findall(l)) for _, l in lines)
    if total > EM_DASH_MAX:
        findings.append(_finding(
            rule_id="em-dash", severity="warn", line=None, snippet=None,
            message=f"{total} em dashes (limit {EM_DASH_MAX}). Leaning on em dashes. "
                    f"Use periods, colons, or commas.",
        ))
    return findings


def lint_length(
    lines: list[tuple[int, str]], cap: int = DEFAULT_LENGTH_CAP
) -> list[dict[str, Any]]:
    total = len(lines)
    if total <= cap:
        return []
    return [_finding(
        rule_id="length-cap",
        severity="WARN",
        line=None,
        snippet=None,
        message=(
            f"Report is {total} lines, exceeding soft cap of {cap}. "
            "Tighten — say it once, omit empty sections."
        ),
    )]


def lint_context_density(workdir: Path | None = None) -> list[dict[str, Any]]:
    """Emit a WARN-level finding when pointer_density_findings is non-empty.

    Reads ``.build-loop/context/index.json`` from *workdir* (defaults to cwd).
    Advisory-checks-are-automated: surfaces density findings in the Phase-4G
    report so they appear without operator intervention.  Never blocks — missing
    or unreadable index → no finding, no error.
    """
    import json as _json  # already imported at module level; local ref for clarity
    base = Path(workdir) if workdir is not None else Path.cwd()
    index_path = base / ".build-loop" / "context" / "index.json"
    try:
        data = _json.loads(index_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, _json.JSONDecodeError):
        return []
    density = data.get("pointer_density_findings")
    if not density:
        return []
    joined = "; ".join(str(d) for d in density)
    return [_finding(
        rule_id="context-density",
        severity="WARN",
        line=None,
        snippet=None,
        message=f"context/current.md density findings: {joined}",
    )]


def run_lint(
    report_path: Path,
    length_cap: int = DEFAULT_LENGTH_CAP,
    workdir: Path | None = None,
) -> dict[str, Any]:
    text = report_path.read_text(encoding="utf-8")
    lines = _strip_fenced_blocks(text)
    findings: list[dict[str, Any]] = []
    findings.extend(lint_headline(lines))
    findings.extend(lint_validation_line(lines))
    findings.extend(lint_jargon(lines))
    findings.extend(lint_contrastive_pivot(lines))
    findings.extend(lint_direct_language(lines))
    findings.extend(lint_length(lines, cap=length_cap))
    findings.extend(lint_context_density(workdir))
    summary = {
        "total": len(findings),
        "by_severity": {
            "WARN": sum(1 for f in findings if f["severity"] == "WARN"),
            "INFO": sum(1 for f in findings if f["severity"] == "INFO"),
        },
        "by_rule": {},
    }
    for f in findings:
        summary["by_rule"][f["rule_id"]] = summary["by_rule"].get(f["rule_id"], 0) + 1
    return {"report": str(report_path), "summary": summary, "findings": findings}


def render_human(result: dict[str, Any]) -> str:
    summary = result["summary"]
    out = [
        f"# report-lint — {Path(result['report']).name}",
        "",
        f"Total findings: {summary['total']} (WARN={summary['by_severity']['WARN']})",
    ]
    if not result["findings"]:
        out.append("No findings.")
        return "\n".join(out)
    out.append("")
    for f in result["findings"]:
        line = f["evidence"]["line"]
        line_s = f"line {line}" if line is not None else "(no line)"
        out.append(f"- [{f['severity']}] {f['rule_id']} {line_s}: {f['message']}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lint build-loop final user-facing report for style/jargon."
    )
    parser.add_argument("report", help="Path to draft report markdown")
    parser.add_argument(
        "--length-cap",
        type=int,
        default=DEFAULT_LENGTH_CAP,
        help=f"Soft line-count cap (default {DEFAULT_LENGTH_CAP})",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--quiet", action="store_true", help="Suppress human output")
    parser.add_argument("--workdir", default=None, help="Workdir for context-density check (default: cwd)")
    args = parser.parse_args(argv)

    report_path = Path(args.report).expanduser().resolve()
    if not report_path.exists():
        print(f"report-lint: file not found: {report_path}", file=sys.stderr)
        return 2

    workdir = Path(args.workdir).expanduser().resolve() if args.workdir else None
    try:
        result = run_lint(report_path, length_cap=args.length_cap, workdir=workdir)
    except Exception as exc:  # noqa: BLE001 — verifier outage maps to lint-outage finding
        result = {
            "report": str(report_path),
            "summary": {"total": 1, "by_severity": {"WARN": 1, "INFO": 0}, "by_rule": {"lint-outage": 1}},
            "findings": [{
                "rule_id": "lint-outage",
                "severity": "WARN",
                "message": f"Lint script error: {exc}",
                "evidence": {"line": None, "snippet": None},
            }],
        }

    if args.json:
        print(json.dumps(result, indent=2))
    elif not args.quiet:
        print(render_human(result))

    # Warn-mode: never block. Exit 0 even with findings — orchestrator reads
    # JSON and decides whether to auto-revise.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
