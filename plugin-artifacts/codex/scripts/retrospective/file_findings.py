#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""file_findings.py — file a retrospective's issues to their RELEVANT location.

Owner directive (2026-08-29): "Every retrospective should automatically log and
capture issues or recommendations in relevant location." Before this, a retro
named issues in prose and a human decided — per retro, from memory — where each
one belonged. The 2026-08-29 retro's six findings were filed by hand into three
different repos using three different idioms, and the one that landed as a
backlog item came out with EMPTY provenance and the whole finding crammed into
its title (assistant/.build-loop/backlog/items/ASSI-ROUTING-m17d596x87nz0dg0ct8zq.md:16).

This module makes that step deterministic and checkable:

  extract  — pull findings out of the retro's finding-bearing sections.
  resolve  — map each finding to the repo it is ABOUT, then to that repo's
             filing idiom (backlog > KNOWN-ISSUES.md > LESSONS-LEARNED.md >
             build-loop's own KNOWN-ISSUES.md).
  plan     — dry-run: what would be filed, where, and which of the five
             segments could not be derived from the text.
  apply    — execute the plan.
  lint     — a retro that NAMES an issue but files nothing FAILS.

Dry-run is the default. `apply` never invents a segment: a finding whose
segments cannot be derived is reported as `needs_input` so the calling agent
supplies them, rather than filing a hollow item like the one above.

CLI:
  python3 -m retrospective.file_findings plan  --retro <path> [--json]
  python3 -m retrospective.file_findings apply --retro <path> [--json]
  python3 -m retrospective.file_findings lint  --retro <path> [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

# A heading names findings when it carries one of these stems. Deliberately
# narrow: "patterns", "what shipped", and "what went well" describe the run,
# they do not name work someone must now do. Including them would flood every
# target repo with observations and train readers to ignore the filing.
_FINDING_HEADING_STEMS = (
    "failure", "issue", "problem", "defect", "gap", "bug", "risk",
    "regression", "recommendation", "could be done better", "should be enforced",
    "known issue", "debt", "follow-up", "followup", "action item",
)

# Headings that look finding-ish by stem but are explicitly NOT findings.
_FINDING_HEADING_EXCLUDE = ("went well", "what shipped", "no issues")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)

# A finding is a top-level list item: `1. text`, `- text`, or `* text`.
# Continuation lines (indented, or plain wrapped prose) belong to the item above.
_LIST_ITEM_RE = re.compile(r"^(?:(\d{1,3})[.)]|[-*+])\s+(.*)$")

# Filed-findings section the lint requires. Matched case-insensitively so a
# hand-written retro that says "## Filed Findings" still passes.
FILED_SECTION_TITLE = "Filed findings"
_FILED_HEADING_RE = re.compile(r"^#{1,6}\s+filed\s+findings\s*$", re.M | re.I)

# Segment markers found in real retro prose. Ordered longest-first per group so
# "Fix encoded:" wins over "Fix:".
_IMPACT_MARKERS = ("cost:", "impact:", "blast radius:")
_RECOMMENDATION_MARKERS = (
    "fix encoded:", "recommendation:", "recommended:", "fix:", "leverage:", "next:",
)
_WHY_MARKERS = ("root cause:", "why:", "because ", "cause:")

_BOLD_LEAD_RE = re.compile(r"^\*\*(.+?)\.?\*\*")


@dataclass
class Finding:
    """One issue/recommendation named by a retrospective."""
    index: int
    section: str
    title: str
    text: str
    what_happened: str = ""
    impact: str = ""
    recommendation: str = ""
    why: str = ""
    observed: str = ""

    def missing_segments(self) -> list[str]:
        """Which of the five segments could NOT be derived from the text.

        `When` is derived from the retro (date in filename/heading), so it is
        checked by the caller, not here.
        """
        return [
            name for name, value in (
                ("what_happened", self.what_happened),
                ("impact", self.impact),
                ("recommendation", self.recommendation),
                ("why", self.why),
            ) if not value.strip()
        ]


def _is_finding_heading(title: str) -> bool:
    low = title.lower()
    if any(bad in low for bad in _FINDING_HEADING_EXCLUDE):
        return False
    return any(stem in low for stem in _FINDING_HEADING_STEMS)


def _iter_sections(text: str) -> Iterable[tuple[str, str]]:
    """Yield (heading_title, body) for every markdown heading in order."""
    matches = list(_HEADING_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        yield m.group(2).strip(), text[m.end():end]


def _split_list_items(body: str) -> list[str]:
    """Split a section body into top-level list items, joining continuations.

    A section of pure prose (no list markers) yields [] — deliberately. The
    2026-08-29 retro's "Standing debts left open" is one comma-separated
    sentence; treating it as a finding would file one unactionable blob rather
    than the eight distinct debts it mentions. Prose stays unfiled and the lint
    surfaces it instead of guessing.
    """
    items: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            if current:
                current.append("")
            continue
        # A table row or blockquote is never a finding item.
        if line.lstrip().startswith(("|", ">")):
            continue
        m = _LIST_ITEM_RE.match(line.strip()) if not line.startswith(("  ", "\t")) else None
        if m:
            if current:
                items.append("\n".join(current).strip())
            current = [m.group(2)]
        elif current:
            current.append(line.strip())
    if current:
        items.append("\n".join(current).strip())
    return [i for i in items if i.strip()]


def _find_marker(text: str, markers: tuple[str, ...]) -> tuple[int, str] | None:
    """Return (position, marker) of the earliest marker present, else None."""
    hits = [(text.lower().find(m), m) for m in markers]
    hits = [(pos, m) for pos, m in hits if pos >= 0]
    return min(hits) if hits else None


def _segment_after(text: str, markers: tuple[str, ...]) -> tuple[str, int]:
    """Extract the clause introduced by the first matching marker.

    Returns (clause, start_index) — start_index is where the marker begins, so
    the caller can trim it off the preceding segment. ("", -1) when absent.
    """
    hit = _find_marker(text, markers)
    if hit is None:
        return "", -1
    pos, marker = hit
    rest = text[pos + len(marker):].strip()
    # Stop at the next marker of ANY group so segments do not swallow each other.
    stops = [
        rest.lower().find(m)
        for m in (_IMPACT_MARKERS + _RECOMMENDATION_MARKERS + _WHY_MARKERS)
        if rest.lower().find(m) > 0
    ]
    if stops:
        rest = rest[:min(stops)].strip()
    return rest.strip(" .;"), pos


def _derive_segments(finding: Finding) -> None:
    """Fill the five segments from the finding's prose, in place.

    Marker-driven and conservative: an undeducible segment stays EMPTY and the
    plan reports it as needing input. Guessing here would reproduce the exact
    defect this module exists to fix — a filed item that looks complete and
    says nothing.
    """
    text = finding.text
    impact, impact_at = _segment_after(text, _IMPACT_MARKERS)
    rec, rec_at = _segment_after(text, _RECOMMENDATION_MARKERS)
    why, why_at = _segment_after(text, _WHY_MARKERS)

    cuts = [p for p in (impact_at, rec_at, why_at) if p >= 0]
    head = text[: min(cuts)] if cuts else text
    # Drop the bold lead-in (it became the title) from the narrative segment.
    head = _BOLD_LEAD_RE.sub("", head).strip(" .*—-")

    finding.what_happened = head.strip()
    finding.impact = impact
    finding.recommendation = rec
    finding.why = why


_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def retro_date(retro_path: Path, text: str) -> str:
    """Best-effort observation date: filename date, else first date in the text."""
    m = _DATE_RE.search(retro_path.name)
    if m:
        return m.group(1)
    m = _DATE_RE.search(text)
    return m.group(1) if m else ""


def extract_findings(text: str, retro_path: Path | None = None) -> list[Finding]:
    """Return every finding named by the retrospective, in document order."""
    observed = retro_date(retro_path, text) if retro_path else ""
    findings: list[Finding] = []
    for title, body in _iter_sections(text):
        if not _is_finding_heading(title):
            continue
        for item in _split_list_items(body):
            lead = _BOLD_LEAD_RE.match(item)
            first_line = item.splitlines()[0] if item else ""
            raw_heading = lead.group(1) if lead else first_line[:120]
            heading = raw_heading.strip().strip(" .*:—-").strip()
            f = Finding(
                index=len(findings) + 1,
                section=title,
                title=heading,
                text=item,
                observed=observed,
            )
            _derive_segments(f)
            findings.append(f)
    return findings


# --------------------------------------------------------------------------
# Target resolution
# --------------------------------------------------------------------------

DEFAULT_REPO_ROOTS = (Path.home() / "dev" / "git-folder",)

# Tokens too generic to identify a repo even when a directory has that name.
_STOPWORD_TOKENS = {
    "app", "apps", "src", "test", "tests", "docs", "tmp", "web", "api", "ui",
    "code", "main", "core", "lib", "data", "memory", "plugins", "scripts",
    # Agent/model/vendor identities. These name WHO did the work, not WHAT
    # broke, and they appear incidentally in almost every retrospective. On the
    # 2026-08-29 fixture, "which Codex had to propose" pulled a build-loop
    # process lesson into agent-rally-point purely because a `codex` plugin
    # directory exists there. A visible fallback beats a confident wrong repo.
    "codex", "claude", "gemini", "opus", "sonnet", "haiku", "fable",
    "terra", "luna", "sol", "gpt", "llm", "agent", "agents",
}


@dataclass
class RepoIndex:
    """token -> ordered candidate repos, built from real directories.

    A token maps to a LIST, not one repo, because a plugin's own repo and the
    monorepo that ships it are both legitimate homes for a finding. The owning
    repo comes first; the monorepo is the outward fallback used when the owning
    repo keeps no issue log of its own.
    """
    tokens: dict[str, list[Path]] = field(default_factory=dict)

    def match(self, text: str) -> tuple[list[Path], str]:
        """Longest matching token wins — `mockup-gallery` beats `gallery`."""
        low = text.lower()
        best: tuple[int, str, list[Path]] | None = None
        for token, repos in self.tokens.items():
            if not re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", low):
                continue
            if best is None or len(token) > best[0]:
                best = (len(token), token, repos)
        if best is None:
            return [], ""
        return best[2], best[1]


def build_repo_index(roots: Iterable[Path] = DEFAULT_REPO_ROOTS) -> RepoIndex:
    """Index repo names and the plugins they own.

    Plugin names matter because a finding names the SURFACE it hit
    ("mockup-gallery"), not the repo that ships it (RossLabs-AI-Toolkit). Built
    by walking the filesystem so a new repo or plugin is indexed the day it
    lands — no table to update.
    """
    index = RepoIndex()

    def _add(token: str, repo: Path) -> None:
        token = token.strip().lower()
        if len(token) < 3 or token in _STOPWORD_TOKENS:
            return
        candidates = index.tokens.setdefault(token, [])
        if repo not in candidates:
            candidates.append(repo)

    repos = [
        repo
        for root in roots if root.is_dir()
        for repo in sorted(root.iterdir())
        if repo.is_dir() and (repo / ".git").exists()
    ]

    # TWO PASSES, and the order is load-bearing. A repo's OWN name must be
    # registered before any plugin name, because monorepos vendor copies of
    # other repos: RossLabs-AI-Toolkit/plugins/build-loop/ would otherwise claim
    # the token `build-loop` (it sorts before the real repo, and first-writer
    # wins), sending every build-loop finding to the toolkit's LESSONS-LEARNED.md.
    # Observed on the 2026-08-29 fixture before this split.
    for repo in repos:
        _add(repo.name, repo)
    for repo in repos:
        plugins_dir = repo / "plugins"
        if plugins_dir.is_dir():
            # Index EVERY plugins/ child by directory name, not only those with a
            # manifest. Several are symlinks to their own repos and carry the
            # manifest on the far side; requiring one here missed them and the
            # monorepo never became an outward candidate.
            for child in sorted(plugins_dir.iterdir()):
                if child.is_dir():
                    _add(child.name, repo)
        for base in (plugins_dir, repo):
            if not base.is_dir():
                continue
            for manifest in sorted(base.glob("*/.claude-plugin/plugin.json")):
                try:
                    name = json.loads(manifest.read_text(encoding="utf-8")).get("name")
                except (OSError, ValueError):
                    name = None
                if isinstance(name, str):
                    _add(name, repo)
    return index


@dataclass
class Target:
    repo: str
    repo_name: str
    mechanism: str          # backlog | known-issues | lessons-learned
    path: str
    fallback: bool = False
    matched_token: str = ""


def resolve_mechanism(repo: Path, build_loop_root: Path) -> Target:
    """Pick the repo's filing idiom by DETECTING what it already uses.

    Ladder: `.build-loop/backlog/` > KNOWN-ISSUES.md > LESSONS-LEARNED.md >
    build-loop's own KNOWN-ISSUES.md. Detection (not configuration) is what
    makes this work in a repo nobody has onboarded.
    """
    if (repo / ".build-loop" / "backlog").is_dir():
        return Target(str(repo), repo.name, "backlog",
                      str(repo / ".build-loop" / "backlog"))
    if (repo / "KNOWN-ISSUES.md").is_file():
        return Target(str(repo), repo.name, "known-issues", str(repo / "KNOWN-ISSUES.md"))
    if (repo / "LESSONS-LEARNED.md").is_file():
        return Target(str(repo), repo.name, "lessons-learned",
                      str(repo / "LESSONS-LEARNED.md"))
    return Target(str(build_loop_root), build_loop_root.name, "known-issues",
                  str(build_loop_root / "KNOWN-ISSUES.md"), fallback=True)


def resolve_target(finding: Finding, index: RepoIndex, build_loop_root: Path,
                   default_repo: Path | None = None) -> Target:
    """Map one finding to the place it should be filed.

    Walks the candidate repos OUTWARD — the owning repo first, then any
    monorepo that ships it — and takes the first that keeps an issue log of its
    own. Only when none does do we drop to build-loop's KNOWN-ISSUES.md.
    Without the outward walk, a finding about `mockup-gallery` (whose own repo
    has no issue log; RossLabs-AI-Toolkit symlinks it in and keeps
    LESSONS-LEARNED.md) would skip its real home and land in build-loop.
    """
    candidates, token = index.match(finding.text)
    if not candidates:
        if default_repo is not None:
            target = resolve_mechanism(default_repo, build_loop_root)
            target.matched_token = "default"
            return target
        # Last rung, taken literally: a finding naming no recognizable surface
        # parks in build-loop's KNOWN-ISSUES.md, NOT its backlog. An unattributed
        # finding is a triage question, and KNOWN-ISSUES.md is where a human
        # reads them; filing it as a structured backlog item would assert an
        # ownership we do not have.
        return Target(str(build_loop_root), build_loop_root.name, "known-issues",
                      str(build_loop_root / "KNOWN-ISSUES.md"), fallback=True)

    for repo in candidates:
        target = resolve_mechanism(repo, build_loop_root)
        if not target.fallback:
            target.matched_token = token
            return target
    target = resolve_mechanism(candidates[0], build_loop_root)
    target.matched_token = token
    return target


# --------------------------------------------------------------------------
# Plan / apply
# --------------------------------------------------------------------------

def _area_for(finding: Finding) -> str:
    """Derive a backlog `area` (the theme axis) from the finding's title."""
    words = re.findall(r"[a-z]{4,}", finding.title.lower())
    skip = {"with", "that", "this", "from", "into", "when", "were", "have", "been", "before"}
    for w in words:
        if w not in skip:
            return w
    return "general"


def backlog_command(finding: Finding, target: Target, retro_ref: str,
                    backlog_py: Path) -> list[str]:
    """Build the exact `backlog.py new` invocation for this finding."""
    return [
        sys.executable, str(backlog_py), "new",
        "--repo", target.repo,
        "--area", _area_for(finding),
        "--type", "fix",
        "--title", finding.title[:180],
        "--provenance-source", "retrospective",
        "--provenance-ref", retro_ref,
        "--observed", finding.observed,
        "--impact", finding.impact,
        "--what-happened", finding.what_happened,
        "--recommendation", finding.recommendation,
        "--why", finding.why,
        "--json",
    ]


def markdown_entry(finding: Finding, retro_ref: str) -> str:
    """Render the five segments as a markdown section for a KNOWN-ISSUES /
    LESSONS-LEARNED file — same five questions, same order as a backlog item."""
    return (
        f"\n## {finding.observed or 'undated'} — {finding.title}\n\n"
        f"_Source: retrospective `{retro_ref}`_\n\n"
        f"**What happened.** {finding.what_happened}\n\n"
        f"**When.** {finding.observed or 'unrecorded'}\n\n"
        f"**Impact.** {finding.impact}\n\n"
        f"**Recommendation.** {finding.recommendation}\n\n"
        f"**Why.** {finding.why}\n"
    )


def _atomic_append(dest: Path, text: str) -> None:
    """Write via a temp file + os.replace.

    These writes land in OTHER repos' issue logs. A plain `write_text` that dies
    mid-call leaves someone else's KNOWN-ISSUES.md truncated, and this tool runs
    unattended at run-close. Same guarantee `backlog.py:_atomic_write_text`
    already gives its own store.
    """
    tmp = dest.parent / f".{dest.name}.{os.getpid()}.tmp"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, dest)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def find_existing(finding: Finding, target: Target, retro_ref: str) -> str | None:
    """Where was THIS finding from THIS retro already filed? None if nowhere.

    `apply` must be safe to run twice. A retrospective gets regenerated after a
    crash, re-narrated by the LLM step, or swept a second time at SessionEnd —
    without this check each replay appended another copy of every finding,
    inflating the theme index and the cross-repo roll-up with phantom
    recurrence. Measured before the fix: two runs produced 2 identical
    LESSONS-LEARNED entries and 2 identical backlog items.

    Returns the PATH rather than a bool so a regenerated retrospective — whose
    `## Filed findings` appendix was wiped by `write_active` rebuilding the file
    from its section keys — can rebuild that receipt from what is already on
    disk. Without the path, a replay left the lint failing with no machine
    route back to exit 0.

    The identity is (retro ref, finding title) — a plain substring test, so it
    works across both filing shapes without importing the backlog reader.
    """
    title = finding.title.strip()
    if not title:
        return None

    def _hit(path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            # ValueError covers UnicodeDecodeError: a target file in a repo we
            # do not own may hold invalid UTF-8, and that must not abort a run
            # that has already filed other findings.
            return False
        return retro_ref in text and title in text

    if target.mechanism == "backlog":
        items = Path(target.path) / "items"
        if not items.is_dir():
            return None
        for item in sorted(items.glob("*.md")):
            if _hit(item):
                return str(item)
        return None

    dest = Path(target.path)
    return str(dest) if dest.is_file() and _hit(dest) else None


def already_filed(finding: Finding, target: Target, retro_ref: str) -> bool:
    """Boolean form of `find_existing`, kept for readability at call sites."""
    return find_existing(finding, target, retro_ref) is not None


def plan(retro_path: Path, index: RepoIndex | None = None,
         build_loop_root: Path | None = None,
         default_repo: Path | None = None,
         repo_roots: Iterable[Path] = DEFAULT_REPO_ROOTS) -> dict[str, Any]:
    """Dry-run: what would be filed and where. Never writes."""
    build_loop_root = build_loop_root or Path(__file__).resolve().parents[2]
    text = retro_path.read_text(encoding="utf-8")
    idx = index if index is not None else build_repo_index(repo_roots)
    findings = extract_findings(text, retro_path)
    retro_ref = str(retro_path)

    entries: list[dict[str, Any]] = []
    for f in findings:
        target = resolve_target(f, idx, build_loop_root, default_repo)
        missing = f.missing_segments()
        if not f.observed:
            missing.append("observed")
        entries.append({
            "finding": asdict(f),
            "target": asdict(target),
            "needs_input": missing,
            "fileable": not missing,
        })

    return {
        "command": "plan",
        "retro": retro_ref,
        "observed": retro_date(retro_path, text),
        "finding_count": len(findings),
        "already_filed": bool(_FILED_HEADING_RE.search(text)),
        "targets": sorted({e["target"]["path"] for e in entries}),
        "entries": entries,
    }


def apply(retro_path: Path, plan_result: dict[str, Any] | None = None,
          backlog_py: Path | None = None,
          index: RepoIndex | None = None,
          build_loop_root: Path | None = None,
          default_repo: Path | None = None,
          repo_roots: Iterable[Path] = DEFAULT_REPO_ROOTS,
          record: bool = True) -> dict[str, Any]:
    """Execute a plan, then append the `## Filed findings` receipt to the retro.

    `plan_result` accepts a plan whose `needs_input` entries have been FILLED IN
    by the caller (the CLI's `--plan` flag reads one from a file or stdin).
    Without that route, a retro whose findings all need input — which is every
    finding of the real 2026-08-29 fixture — could be filed only from Python,
    and a Codex or Gemini host following AGENTS.md had no way to complete the
    flow at all.

    `record=False` suppresses the receipt append, for callers that assemble the
    section themselves.
    """
    build_loop_root = build_loop_root or Path(__file__).resolve().parents[2]
    backlog_py = backlog_py or (build_loop_root / "scripts" / "backlog.py")
    result = plan_result or plan(
        retro_path, index=index, build_loop_root=build_loop_root,
        default_repo=default_repo, repo_roots=repo_roots,
    )
    retro_ref = result["retro"]

    filed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for entry in result["entries"]:
        f = Finding(**entry["finding"])
        target = Target(**entry["target"])
        # Re-derive rather than trusting the plan's cached list, so a caller
        # that filled the segments in does not also have to clear the flag.
        missing = f.missing_segments() + ([] if f.observed else ["observed"])
        if missing:
            skipped.append({"title": f.title, "reason": "needs_input",
                            "missing": missing})
            continue
        existing = find_existing(f, target, retro_ref)
        if existing:
            # Carry the PATH so a regenerated retro can rebuild its receipt.
            filed.append({"title": f.title, "mechanism": target.mechanism,
                          "id": Path(existing).stem
                          if target.mechanism == "backlog" else None,
                          "path": existing, "preexisting": True})
            skipped.append({"title": f.title, "reason": "already_filed",
                            "path": existing})
            continue
        if target.mechanism == "backlog":
            cmd = backlog_command(f, target, retro_ref, backlog_py)
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                skipped.append({"title": f.title, "reason": "backlog_error",
                                "detail": (proc.stdout or proc.stderr)[:300]})
                continue
            try:
                created = json.loads(proc.stdout)
            except ValueError:
                created = {}
            filed.append({"title": f.title, "mechanism": "backlog",
                          "id": created.get("id"), "path": created.get("path")})
        else:
            dest = Path(target.path)
            try:
                existing = dest.read_text(encoding="utf-8") if dest.is_file() else ""
                _atomic_append(dest, existing.rstrip("\n") + "\n"
                               + markdown_entry(f, retro_ref))
            except OSError as exc:
                skipped.append({"title": f.title, "reason": "write_error",
                                "detail": str(exc)})
                continue
            filed.append({"title": f.title, "mechanism": target.mechanism,
                          "id": None, "path": str(dest)})

    # Write the receipt HERE, not in a docstring telling an agent to hand-write
    # it. `render_filed_section` previously had no caller but its own test, so
    # the one checkable artifact the lint requires depended on an LLM
    # remembering to produce it.
    recorded = False
    if record and filed:
        try:
            current = retro_path.read_text(encoding="utf-8")
            m = _FILED_HEADING_RE.search(current)
            if m:
                # Replace a stale receipt rather than stacking a second one.
                nxt = re.search(r"^#{1,6}\s+\S", current[m.end():], re.M)
                end = m.end() + (nxt.start() if nxt else len(current) - m.end())
                current = current[: m.start()] + current[end:]
            _atomic_append(retro_path,
                           current.rstrip("\n") + "\n" + render_filed_section(filed))
            recorded = True
        except (OSError, ValueError):
            recorded = False

    newly = [f for f in filed if not f.get("preexisting")]
    return {"command": "apply", "retro": retro_ref, "filed": filed,
            "skipped": skipped, "filed_count": len(newly),
            "accounted_count": len(filed), "receipt_written": recorded}


def render_filed_section(filed: list[dict[str, Any]]) -> str:
    """The retro's closing section — the checkable artifact.

    "Disposition claims need a checkable artifact" is the standing norm: a retro
    that says it filed its findings must name each id/path so a reader can go
    look. This section is what `lint` requires.
    """
    lines = [f"\n## {FILED_SECTION_TITLE}\n"]
    if not filed:
        lines.append("_No findings named._\n")
        return "\n".join(lines)
    lines.append("| finding | filed as | location |")
    lines.append("|---------|----------|----------|")
    for item in filed:
        ident = item.get("id") or item.get("mechanism", "")
        lines.append(f"| {item['title']} | {ident} | `{item['path']}` |")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Lint
# --------------------------------------------------------------------------

_LOCATION_RE = re.compile(r"[A-Z0-9]+-[A-Z0-9]+-[0-9a-z]{6,}|[^\s`|]+\.md")


def filed_locations(body: str, retro_name: str) -> list[str]:
    """Distinct filed locations named in the `## Filed findings` body.

    Excludes any reference that resolves to the retrospective UNDER LINT. A
    sentence like "See 2026-08-29-quote.md for details." otherwise satisfied the
    check by pointing at the very document that was supposed to have filed
    something — the section citing itself as its own evidence.
    """
    out: list[str] = []
    for hit in _LOCATION_RE.findall(body):
        if Path(hit).name == retro_name:
            continue
        if hit not in out:
            out.append(hit)
    return out


def lint(retro_path: Path) -> dict[str, Any]:
    """A retro that NAMES an issue but files fewer than all of them FAILS.

    Three checks, because each of the first two is independently gameable:
    the section must EXIST; it must name locations that are not the retro
    itself; and it must account for EVERY finding, not just one. Counting "at
    least one location" let a retro naming six findings and filing one pass
    clean — the delinquency this lint exists to catch, wearing a passing shape.
    """
    text = retro_path.read_text(encoding="utf-8")
    findings = extract_findings(text, retro_path)
    m = _FILED_HEADING_RE.search(text)
    section_present = m is not None
    locations: list[str] = []
    rows = 0
    if m:
        after = text[m.end():]
        nxt = re.search(r"^#{1,6}\s+\S", after, re.M)
        body = after[: nxt.start()] if nxt else after
        locations = filed_locations(body, retro_path.name)
        # Count table rows that actually NAME a location. Two findings filed to
        # the same LESSONS-LEARNED.md are 2 rows but 1 distinct location, so
        # rows are the accounting unit — but only rows carrying a location,
        # since a header or a prose row proves nothing. Excluding merely the
        # header by its text let a 2-finding retro pass on 1 filed row.
        rows = len([
            ln for ln in body.splitlines()
            if ln.strip().startswith("|") and filed_locations(ln, retro_path.name)
        ])
    accounted = max(rows, len(locations))

    # DISTINCT failures with distinct messages, because each needs a different
    # fix: no section means nothing was filed; no location means the filing ran
    # and recorded nothing; a shortfall means it recorded only some.
    violations: list[str] = []
    if findings:
        if not section_present:
            violations.append(
                f"retro names {len(findings)} finding(s) but has no "
                f"`## {FILED_SECTION_TITLE}` section"
            )
        elif not locations:
            violations.append(
                f"`## {FILED_SECTION_TITLE}` section is present but names no filed "
                f"location for {len(findings)} finding(s)"
            )
        elif accounted < len(findings):
            violations.append(
                f"`## {FILED_SECTION_TITLE}` accounts for {accounted} of "
                f"{len(findings)} finding(s)"
            )

    return {
        "command": "lint",
        "retro": str(retro_path),
        "finding_count": len(findings),
        "filed_section_present": section_present,
        "filed_locations": len(locations),
        "filed_accounted": accounted,
        "violations": violations,
        "ok": not violations,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="retrospective.file_findings",
        description="File a retrospective's findings to their relevant location.",
    )
    ap.add_argument("mode", choices=("plan", "apply", "lint"))
    ap.add_argument("--retro", required=True, help="Path to the retrospective markdown.")
    ap.add_argument("--repo-root", action="append", default=[], dest="repo_root",
                    help="Directory holding repos to index (repeatable). "
                         "Default: ~/dev/git-folder.")
    ap.add_argument("--default-repo", default="",
                    help="Repo to file findings that name no recognizable surface.")
    ap.add_argument("--plan", default="", dest="plan_file",
                    help="apply only: read a plan JSON (from `plan`) whose "
                         "needs_input segments you have filled in. `-` reads "
                         "stdin. Without this, apply recomputes the plan and "
                         "can only file findings whose segments parse cleanly.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    retro = Path(args.retro).expanduser().resolve()
    if not retro.is_file():
        print(json.dumps({"error": f"retro not found: {retro}"}))
        return 2

    roots = [Path(r).expanduser() for r in args.repo_root] or list(DEFAULT_REPO_ROOTS)
    default_repo = Path(args.default_repo).expanduser().resolve() if args.default_repo else None

    if args.mode == "lint":
        result = lint(retro)
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if args.mode == "plan":
        result = plan(retro, repo_roots=roots, default_repo=default_repo)
        print(json.dumps(result, indent=2))
        return 0

    filled: dict[str, Any] | None = None
    if args.plan_file:
        try:
            raw = sys.stdin.read() if args.plan_file == "-" \
                else Path(args.plan_file).expanduser().read_text(encoding="utf-8")
            filled = json.loads(raw)
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": f"could not read --plan: {exc}"}))
            return 2
        if not isinstance(filled, dict) or "entries" not in filled:
            print(json.dumps({"error": "--plan must be a plan JSON with an 'entries' list"}))
            return 2
    result = apply(retro, plan_result=filled, repo_roots=roots,
                   default_repo=default_repo)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
