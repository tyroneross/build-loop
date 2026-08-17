#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Classify a repository's tracked documentation against the public-repository
documentation boundary (`references/public-repository-documentation-boundary.md`).

The policy is the spec. This script grades a docs tree against it and reports in
the policy's own §5 vocabulary: ``public_current`` / ``private_archived`` /
``public_removed`` / ``blocked``.

The policy's binding constraint on any automated grader is §3:

    Naming is evidence, not the decision. Review the content and audience.

So every path pattern here only SEEDS a verdict. A file is convicted at high
confidence only when a path signal and a content signal agree. A path signal on
its own returns ``needs_review`` — an honest "a human or an LLM has to read
this" — never a verdict. ``needs_review`` findings are advisory (exit 0) unless
``--strict``; only high-confidence ``blocked`` findings fail the run.

Usage::

    python3 scripts/doc_boundary.py --repo <path> [--json] [--rev <rev>] [--strict]

Exit codes: 0 clean (or private repo, or advisory-only), 1 findings, 2 hard error.

Stdlib only. No network beyond an optional `gh repo view` for visibility.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

DOC_SUFFIXES = {".md", ".markdown", ".rst", ".adoc", ".txt"}

# Experiment ledgers are data, not prose, so they need their own admission rule
# (policy §3, "A/B test records and experiment ledgers").
LEDGER_SUFFIXES = {".jsonl", ".ndjson", ".csv"}
LEDGER_PATH = re.compile(r"(^|/)(experiments?|ab-tests?|bakeoffs?|benchmarks?)/", re.I)

MAX_READ_BYTES = 400_000


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rule:
    rule_id: str
    kind: str  # "deny" | "allow"
    layer: str  # "path" | "content"
    weight: int
    pattern: re.Pattern
    note: str


def _p(expr: str) -> re.Pattern:
    # MULTILINE matters: every content rule anchors on `^` at a heading or a
    # list marker. Without it the anchors only match byte 0 of the file.
    return re.compile(expr, re.I | re.M)


# Path rules SEED only. Weight 2 = strong seed (policy §3 names the class
# explicitly); weight 1 = weak seed that can never convict on its own.
PATH_RULES: tuple[Rule, ...] = (
    # --- deny seeds, strong (each maps to a named §3 bullet) ---
    # NARROWED 2026-08-17: a bare case-insensitive `PLAN` matched *ex-plan-ation*
    # and convicted build-loop's `docs/01-simple-explanation.md`. Word-anchored now.
    # `\b` treats `_` as a word character, so `\bplan\b` missed `PLAN_2026-05-17`.
    # Letter-only lookarounds keep `explanation`/`planning` out while admitting
    # every separator a filename actually uses.
    Rule("path.plans", "deny", "path", 2, _p(r"(^|/)plans?/|(?<![a-z])plans?(?![a-z])"),
         "§3 build plans / future plans"),
    Rule("path.roadmap", "deny", "path", 2, _p(r"roadmap|deferred|workstream"),
         "§3 roadmaps, deferred-work lists, workstreams"),
    Rule("path.proposal", "deny", "path", 2, _p(r"proposal|rfc[-_/]"),
         "§3 proposals"),
    Rule("path.backlog", "deny", "path", 2, _p(r"(^|/)backlog"),
         "§3 deferred-work lists"),
    Rule("path.rca", "deny", "path", 2,
         _p(r"(?<![a-z])rca(?![a-z])|root[-_]?cause|postmortem|post[-_]mortem|incident"),
         "§3 root-cause analyses and incident reports"),
    Rule("path.assessment", "deny", "path", 2, _p(r"assessment|\baudit\b|triage"),
         "§3 assessments and audit working papers"),
    Rule("path.retro", "deny", "path", 2, _p(r"retro|lessons"),
         "§3 retrospectives and lessons-learned source documents"),
    Rule("path.handoff", "deny", "path", 2, _p(r"handoff|hand[-_]off|closeout|merge[-_]back"),
         "§3 handoff notes for unfinished implementation"),
    Rule("path.issues", "deny", "path", 2, _p(r"(^|/)issues?[-_/]|(^|/)ISSUES"),
         "§3 internal status"),
    Rule("path.cockpit", "deny", "path", 2, _p(r"cockpit|(^|/)dashboards?/|operating[-_]board"),
         "§3 cockpit or maintainer dashboards"),
    Rule("path.buildloop_state", "deny", "path", 2, _p(r"(^|/)\.build-loop/"),
         "§3 Build Loop working state under .build-loop/"),
    Rule("path.experiment_ledger", "deny", "path", 2,
         _p(r"(^|/)(experiments?|ab-tests?|bakeoffs?)/|discarded\.jsonl$|bake[-_]?off|a[-_/]b[-_]test"),
         "§3 A/B test records and experiment ledgers"),
    Rule("path.perf_capture", "deny", "path", 2, _p(r"(^|/)perf/|(^|/)bench(marks?)?[-_/]|\bbench_"),
         "§3 performance captures"),
    Rule("path.release_runbook", "deny", "path", 2, _p(r"RELEASING|release[-_]rehearsal|deploy(ment)?[-_]runbook"),
         "§3 maintainer-specific release or deployment instructions"),
    # --- deny seeds, weak (policy explicitly warns these names are ambiguous) ---
    Rule("path.spec", "deny", "path", 1, _p(r"(^|/)specs?/|(^|/)SPEC[-_]"),
         "§3 note: a file named SPEC can still be a private future plan"),
    Rule("path.design", "deny", "path", 1, _p(r"(^|/)designs?/|DESIGN"),
         "§3 future architecture considerations vs §2 current design rationale"),
    Rule("path.draft", "deny", "path", 1, _p(r"draft|\bwip\b|scratch|_inbox|notes?[-_]20\d\d"),
         "§3 response drafts / unfinished notes"),
    Rule("path.dated", "deny", "path", 1, _p(r"20\d\d-\d\d-\d\d|_20\d\d[-_]\d\d"),
         "date-stamped working record (weak seed only)"),
    # DROPPED 2026-08-17 — `path.option_study` (`migration|tradeoffs|recommendations|
    # option-study|position-`) for §3 "option studies, migration plans". Measured:
    # +2 recall on the 81-file agent-rally-point extract, but it raised a fresh
    # false alarm on the scrubbed oracle (`docs/DESIGN-TRADEOFFS.md`, a §2 design
    # rationale) and changed build-loop's decided count by 0. A rule that dirties
    # the clean corpus to catch two hard cases is a net loss. §3's option-study and
    # migration-plan classes stay a human read.
    # --- allow seeds, strong (policy §2) ---
    Rule("path.entry_doc", "allow", "path", 3,
         _p(r"(^|/)(README|READ_ME|INSTALL(ATION)?|GETTING[-_]STARTED|QUICKSTART|ONBOARDING"
            r"|CONTRIBUTING|LICENSE|LICENCE|NOTICE|COPYING|CHANGELOG|CODE_OF_CONDUCT"
            r"|SECURITY|USAGE|FAQ)(\.[a-z]+)?$"),
         "§2 README, installation, onboarding, contribution, license, changelog"),
    Rule("path.agent_contract", "allow", "path", 3,
         _p(r"(^|/)(SKILL|AGENTS|CLAUDE)\.md$|(^|/)(agents|commands)/[^/]+\.md$"),
         "§2 agent-facing contracts needed to use or extend the product"),
    Rule("path.schema", "allow", "path", 3, _p(r"(^|/)schemas?/|(^|/)api/|(^|/)templates?/|[-_]template\."),
         "§2 protocols, schemas, and reusable contracts"),
    # --- allow seeds, medium ---
    # ADDED 2026-08-17 after measurement: every decided false positive on the
    # build-loop corpus lived under `references/` or `skills/` — in a plugin repo
    # that tree IS the shipped product (§2 agent-facing contracts), so a §3 name
    # there can no longer convict on its own; it caps at needs_review.
    Rule("path.product_surface", "allow", "path", 2,
         _p(r"(^|/)(references|skills|agents|commands|templates|test-fixtures|fixtures)/"),
         "§2 shipped product documentation surface"),
)

# Content rules DECIDE. A high-confidence verdict needs at least one of these.
CONTENT_RULES: tuple[Rule, ...] = (
    Rule("content.rca_shape", "deny", "content", 2,
         _p(r"^#{1,4}\s*(root cause|timeline|corrective action|contributing factors|"
            r"detection|blast radius|what escaped)\b"),
         "root-cause / incident report structure"),
    Rule("content.retro_shape", "deny", "content", 2,
         _p(r"^#{1,4}\s*(what went (well|wrong)|lessons learned|what to change)\b"),
         "retrospective structure"),
    # DEMOTED 2026-08-17: the original rule also matched the free prose
    # `next session` / `handing off to`, which convicted agent-rally-point's
    # `docs/HANDOFFS-AND-LAUNCHING-AGENTS.md` — a §2 "handoff behavior" guide.
    # Heading-anchored only now: a handoff RECORD headers its own state.
    Rule("content.handoff_shape", "deny", "content", 2,
         _p(r"^#{1,4}\s*(handoff|next session|pick up here|state at handoff|"
            r"for the next agent|remaining work|where i left off)\b"),
         "handoff record structure"),
    Rule("content.future_plan", "deny", "content", 2,
         _p(r"^#{1,4}\s*(deliverables|phases?\s*\d|implementation plan|rollout|"
            r"open questions|risks and mitigations|acceptance criteria|out of scope|non-goals)\b"),
         "unexecuted build-plan structure"),
    Rule("content.status_marker", "deny", "content", 2,
         _p(r"^\s*(?:[-*]\s*)?(?:\*\*)?status(?:\*\*)?\s*[:=]\s*"
            r"(draft|proposed|in[- ]progress|deferred|blocked|pending|not started|planned)\b"),
         "declares itself unfinished internal work"),
    Rule("content.open_tasks", "deny", "content", 1, _p(r"^\s*[-*]\s*\[ \]\s+\S"),
         "unchecked task list (deferred work)"),
    Rule("content.maintainer_cmd", "deny", "content", 2,
         _p(r"\bgit push\s+(origin|upstream)\b|\bgh release create\b|\bnpm publish\b|"
            r"\bcargo publish\b|\bxcrun altool\b|\bnotarytool submit\b"),
         "§3 maintainer-specific push / release / deployment instructions"),
    Rule("content.local_path", "deny", "content", 1, _p(r"/Users/[a-z0-9._-]+/|/home/[a-z0-9._-]+/"),
         "maintainer machine path (internal working record)"),
    Rule("content.run_record", "deny", "content", 2,
         _p(r"\brun[-_ ]?id\b\s*[:=]|^\s*(?:\*\*)?(verdict|judge decision)(?:\*\*)?\s*:"),
         "per-run working record"),
    # --- allow (content) ---
    Rule("content.install", "allow", "content", 2,
         _p(r"^#{1,4}\s*(install(ation|ing)?|quick ?start|getting started|usage|"
            r"how to use|configuration|options|commands)\b"),
         "§2 install / operate / use documentation"),
    Rule("content.frontmatter_contract", "allow", "content", 2,
         _p(r"\A---\r?\n(?:.*\r?\n)*?description:\s*\S"),
         "§2 declared agent-facing contract (skill/agent frontmatter)"),
    Rule("content.command_guide", "allow", "content", 2,
         _p(r"^```(bash|sh|shell|console|zsh|json|yaml)"),
         "§2 operational guidance: repeated runnable command/config blocks"),
)


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


@dataclass
class Verdict:
    path: str
    bucket: str
    confidence: str
    needs_review: bool
    signals: list[dict] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "bucket": self.bucket,
            "confidence": self.confidence,
            "needs_review": self.needs_review,
            "reason": self.reason,
            "signals": self.signals,
        }


def _match_path_rules(path: str) -> list[Rule]:
    return [rule for rule in PATH_RULES if rule.pattern.search(path)]


def _match_content_rules(text: str) -> list[tuple[Rule, int]]:
    hits: list[tuple[Rule, int]] = []
    for rule in CONTENT_RULES:
        count = len(rule.pattern.findall(text)) if rule.pattern.flags & re.M else 0
        if not count:
            count = 1 if rule.pattern.search(text) else 0
        if count:
            hits.append((rule, count))
    return hits


def _signal(rule: Rule, count: int = 1) -> dict:
    return {
        "rule": rule.rule_id,
        "kind": rule.kind,
        "layer": rule.layer,
        "weight": rule.weight,
        "count": count,
        "note": rule.note,
    }


def classify(path: str, text: str | None) -> Verdict:
    """Classify one documentation path. `text` may be None when unreadable."""
    path_hits = _match_path_rules(path)
    path_deny = sum(r.weight for r in path_hits if r.kind == "deny")
    path_allow = sum(r.weight for r in path_hits if r.kind == "allow")
    strong_allow = max((r.weight for r in path_hits if r.kind == "allow"), default=0) >= 3

    content_hits: list[tuple[Rule, int]] = []
    if text:
        content_hits = _match_content_rules(text)
    # `content.open_tasks` is weight 1 per hit but only meaningful in bulk.
    content_deny = 0
    for rule, count in content_hits:
        if rule.kind != "deny":
            continue
        if rule.rule_id == "content.open_tasks":
            content_deny += 2 if count >= 3 else 0
        else:
            content_deny += rule.weight
    content_allow = 0
    for rule, count in content_hits:
        if rule.kind != "allow":
            continue
        if rule.rule_id == "content.command_guide":
            content_allow += 2 if count >= 2 else 0
        else:
            content_allow += rule.weight

    signals = [_signal(r) for r in path_hits] + [_signal(r, c) for r, c in content_hits]

    def out(bucket: str, confidence: str, needs_review: bool, reason: str) -> Verdict:
        return Verdict(path, bucket, confidence, needs_review, signals, reason)

    if text is None:
        return out("blocked", "low", True,
                   "content unreadable; audience unresolved (policy §5 blocked)")

    # A named §2 entry document stays public unless its own content says otherwise.
    if strong_allow and content_deny == 0:
        return out("public_current", "high", False,
                   "§2 allow-list path and no internal-record content signal")

    if path_deny >= 2:
        if path_allow >= 2:
            return out("blocked", "low", True,
                       "§3 deny-class name inside the §2 product documentation surface "
                       "— only a content read resolves the audience")
        if content_deny >= 2:
            return out("blocked", "high", False,
                       "§3 deny-class path confirmed by content structure")
        if content_deny >= 1:
            # An allow signal does NOT cancel a deny signal here: internal plans
            # routinely carry runnable commands. Only a silent document is unsure.
            return out("blocked", "medium", False,
                       "§3 deny-class path with a supporting content signal")
        if strong_allow or content_allow >= 2:
            return out("blocked", "low", True,
                       "path names a §3 class but content reads as §2 product documentation "
                       "— naming is evidence, not the decision")
        return out("blocked", "medium", True,
                   "§3 deny-class path with no decisive content signal — read it")

    if path_deny == 1:
        if content_deny >= 2 and content_allow == 0:
            return out("blocked", "medium", True,
                       "weak path seed plus internal-record content structure — read it")
        return out("public_current", "medium", False,
                   "weak path seed only; a name alone never convicts (policy §3)")

    if content_deny >= 4 and path_allow == 0 and content_allow == 0:
        return out("blocked", "low", True,
                   "no path signal, but content reads as an internal working record — read it")

    return out("public_current", "high" if content_allow else "medium", False,
               "no §3 signal")


# --------------------------------------------------------------------------- #
# Git + visibility
# --------------------------------------------------------------------------- #


class HardError(Exception):
    pass


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise HardError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def repo_visibility(repo: Path) -> tuple[str, str]:
    """Return (visibility, source). Never guesses: unknown stays unknown."""
    try:
        proc = subprocess.run(
            ["gh", "repo", "view", "--json", "visibility"],
            cwd=str(repo), capture_output=True, text=True, timeout=20, check=False,
        )
    except FileNotFoundError:
        return "unknown", "gh not installed"
    except (subprocess.TimeoutExpired, OSError) as exc:
        return "unknown", f"gh unavailable ({type(exc).__name__})"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return "unknown", f"gh failed: {detail[0] if detail else 'no detail'}"
    try:
        value = json.loads(proc.stdout)["visibility"]
    except (ValueError, KeyError, TypeError):
        return "unknown", "gh returned unparseable visibility"
    return str(value).lower(), "gh repo view"


def is_doc(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix in DOC_SUFFIXES:
        return True
    if suffix in LEDGER_SUFFIXES and LEDGER_PATH.search(path):
        return True
    return False


def tracked_docs(repo: Path, rev: str | None) -> list[str]:
    if rev:
        out = _git(repo, "ls-tree", "-r", "--name-only", rev)
    else:
        out = _git(repo, "ls-files")
    return sorted(p for p in out.splitlines() if p and is_doc(p))


def read_doc(repo: Path, rev: str | None, path: str) -> str | None:
    try:
        if rev:
            proc = subprocess.run(
                ["git", "-C", str(repo), "show", f"{rev}:{path}"],
                capture_output=True, check=False,
            )
            if proc.returncode != 0:
                return None
            raw = proc.stdout[:MAX_READ_BYTES]
        else:
            target = repo / path
            if not target.is_file():
                return None
            with target.open("rb") as handle:
                raw = handle.read(MAX_READ_BYTES)
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Private-memory archive (policy §4 receipts)
# --------------------------------------------------------------------------- #


def _normalize_archived_name(name: str) -> str:
    # The canonical writer appends its own suffix, producing `FOO.md.md`.
    while name.endswith(".md.md"):
        name = name[:-3]
    return name


def archive_records(repo: Path, memory_root: Path | None) -> list[dict]:
    """Read archival receipts written under build-loop-memory for this repo."""
    root = memory_root or (repo.parent / "build-loop-memory")
    index = root / "projects" / repo.name / "raw" / "INDEX.jsonl"
    if not index.is_file():
        return []
    records: list[dict] = []
    for line in index.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        rel = entry.get("file", "")
        if not rel.startswith("documents/"):
            continue
        records.append({
            "memory_file": rel,
            "original_name": _normalize_archived_name(Path(rel).name),
            "receipt": bool(entry.get("sha256")),
            "run_id": entry.get("run_id"),
            "source_workdir": entry.get("source_workdir"),
        })
    return records


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def build_report(repo: Path, rev: str | None, memory_root: Path | None) -> dict:
    visibility, visibility_source = repo_visibility(repo)
    docs = tracked_docs(repo, rev)

    buckets: dict[str, list[dict]] = {
        "public_current": [], "private_archived": [], "public_removed": [], "blocked": [],
    }
    for path in docs:
        verdict = classify(path, read_doc(repo, rev, path))
        buckets[verdict.bucket].append(verdict.to_dict())

    tracked_names = {Path(p).name for p in docs}
    for record in archive_records(repo, memory_root):
        entry = {
            "path": record["memory_file"],
            "original_name": record["original_name"],
            "receipt": record["receipt"],
            "run_id": record["run_id"],
        }
        if record["receipt"]:
            buckets["private_archived"].append(entry)
            if record["original_name"] not in tracked_names:
                buckets["public_removed"].append(entry)
        else:
            buckets["blocked"].append({
                "path": record["memory_file"],
                "bucket": "blocked",
                "confidence": "high",
                "needs_review": False,
                "reason": "archived without a private-memory receipt (policy §4)",
                "signals": [],
            })

    blocked = buckets["blocked"]
    decided = [f for f in blocked if not f.get("needs_review")]
    review = [f for f in blocked if f.get("needs_review")]

    return {
        "repo": str(repo),
        "rev": rev or "worktree",
        "visibility": visibility,
        "visibility_source": visibility_source,
        "policy": "references/public-repository-documentation-boundary.md",
        "counts": {
            "documents": len(docs),
            "public_current": len(buckets["public_current"]),
            "private_archived": len(buckets["private_archived"]),
            "public_removed": len(buckets["public_removed"]),
            "blocked": len(blocked),
            "blocked_decided": len(decided),
            "blocked_needs_review": len(review),
        },
        "buckets": buckets,
    }


def exit_code(report: dict, strict: bool) -> int:
    if report["visibility"] != "public":
        # The boundary binds public repositories only (policy §1).
        return 0
    counts = report["counts"]
    if counts["blocked_decided"]:
        return 1
    if strict and counts["blocked_needs_review"]:
        return 1
    return 0


def render(report: dict, limit: int) -> str:
    counts = report["counts"]
    lines = [
        f"doc-boundary  repo={report['repo']}  rev={report['rev']}",
        f"visibility={report['visibility']} ({report['visibility_source']})",
        f"documents={counts['documents']}  public_current={counts['public_current']}  "
        f"private_archived={counts['private_archived']}  public_removed={counts['public_removed']}  "
        f"blocked={counts['blocked']} "
        f"(decided={counts['blocked_decided']} needs_review={counts['blocked_needs_review']})",
    ]
    if report["visibility"] == "unknown":
        lines.append("NOTE: visibility unresolved — reporting only, no verdict on publication (policy §1).")
    elif report["visibility"] != "public":
        lines.append("NOTE: non-public repository — the boundary does not bind; reporting only (policy §1).")

    decided = [f for f in report["buckets"]["blocked"] if not f.get("needs_review")]
    review = [f for f in report["buckets"]["blocked"] if f.get("needs_review")]
    for title, rows in (("blocked (decided)", decided), ("blocked (needs_review)", review)):
        if not rows:
            continue
        lines.append("")
        lines.append(f"{title}: {len(rows)}")
        for row in rows[:limit]:
            sig = ",".join(s["rule"] for s in row.get("signals", []) if s["kind"] == "deny")
            lines.append(f"  [{row.get('confidence','?')}] {row['path']}  <- {sig or row.get('reason','')}")
        if len(rows) > limit:
            lines.append(f"  ... {len(rows) - limit} more (use --json)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--repo", default=".", help="repository to classify")
    parser.add_argument("--rev", default=None, help="classify a git rev instead of the worktree")
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument("--strict", action="store_true",
                        help="also fail on needs_review findings")
    parser.add_argument("--memory-root", default=None,
                        help="path to build-loop-memory (default: sibling of --repo)")
    parser.add_argument("--limit", type=int, default=25, help="rows per bucket in text output")
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        print(f"doc_boundary: no such directory: {repo}", file=sys.stderr)
        return 2
    memory_root = Path(args.memory_root).expanduser().resolve() if args.memory_root else None

    try:
        report = build_report(repo, args.rev, memory_root)
    except HardError as exc:
        print(f"doc_boundary: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2) if args.json else render(report, args.limit))
    return exit_code(report, args.strict)


if __name__ == "__main__":
    sys.exit(main())
