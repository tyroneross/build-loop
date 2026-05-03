#!/usr/bin/env python3
"""Static-scan UX-impacting issues and write queue entries for build-loop Review Sub-step D.

Covers the deterministic portion of four dimensions: interactability, performance,
data accuracy beyond current scope, usability. Agent-driven analysis (performance
profiling, LLM judge for usability subtlety) is invoked separately by the
orchestrator and merged into the queue.

For each finding, writes `.build-loop/ux-queue/<id>.md` from the
`templates/ux-fix-plan.md` template. Architecture-impact detection is conservative
by default (false) — the orchestrator may flip it after deeper review.

Output: JSON summary to stdout. Exit 0 always (advisory). Exit 2 on usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SKIP_DIRS = {
    # Tooling / build output
    "node_modules", "dist", "build", ".next", ".turbo", ".cache", ".vercel",
    # Test / coverage reports (HTML pages with generated buttons that look handlerless)
    "coverage", "playwright-report", "visual-reports", "visual-viewer-test",
    "test-results", "tests-results", "html-report",
    # Plugin/tool runtime data
    ".build-loop", ".ibr", ".navgator", ".bookmark", ".git", "_draft",
    # Internal tool / mockup directories (not production code)
    ".claude", "_archive", "archive",
    # Common documentation mockup folders
    "docs/mockups", "docs/_mockups",
}
# Glob-style suffix excludes for files (relative path endings to skip)
SKIP_SUFFIXES = (
    ".test.tsx", ".test.jsx", ".spec.tsx", ".spec.jsx",
    ".stories.tsx", ".stories.jsx",
)
# Path substrings to skip (catches nested mockup/sample dirs that aren't always at the root)
SKIP_PATH_CONTAINS = ("/mockups/", "/_mockups/", "/sample-data/", "/__fixtures__/", "/__snapshots__/")
WEB_EXT = {".tsx", ".jsx", ".vue", ".svelte"}  # static .html is usually docs/mockups, not production UI
SWIFT_EXT = {".swift"}
ALL_UI_EXT = WEB_EXT | SWIFT_EXT

CHECKS: dict[str, list[tuple[str, str, str]]] = {
    "interactability": [
        # (label, regex, hint)
        # NOTE: button-no-handler-web is handled by find_dead_buttons() below — the line-bound
        # regex `<button\b[^>]*?(?<!on[A-Z])>` only checked the trailing chars before `>`, so a tag
        # like `<button onClick={x} className="y">` passed the lookbehind (`"y"` ≠ `on[A-Z]`) and
        # got falsely flagged. Switched to per-file full-tag scanning.
        # NOTE: link-no-target-web is handled by find_dead_anchors() below — JSX <a> tags
        # commonly span newlines (`<a\n  href="..."\n>`) which line-bound regex flags as false positives.
        ("icon-button-no-label-web", r"<button[^>]*aria-hidden|<IconButton(?![^>]*aria-label)", "Icon-only button missing aria-label"),
        ("empty-action-swift", r"Button\([^)]*action:\s*\{\s*\}", "SwiftUI Button with empty action closure"),
    ],
    "performance": [
        ("n-plus-one-web", r"\b(forEach|map)\s*\([^)]*\)\s*=>\s*[^\n]*\bawait\s+fetch", "Possible N+1 fetch inside forEach/map"),
        ("blocking-import-lodash", r"^import\s+_\s+from\s+['\"]lodash['\"]", "Full-lodash import — prefer per-method imports for tree-shaking"),
        ("unbounded-effect", r"useEffect\([^,]+,\s*\[\s*\]\s*\).*fetch", "useEffect with empty deps fetching — verify no race or repeat"),
    ],
    "data-accuracy": [
        ("hardcoded-stat-web", r">\s*\$?\d+(\.\d+)?\s*(%|percent|million|billion)\s*<", "Hardcoded numeric stat in JSX — verify source"),
        ("as-of-date-web", r">\s*[Aa]s of\s+\w+\s+\d{4}\s*<", "Hardcoded 'as of <date>' in JSX — likely stale"),
        ("hardcoded-year", r">\s*(20\d{2})\s*<", "Hardcoded year in JSX — verify intent"),
    ],
    "usability": [
        # Most usability checks live in audit-design-rules.mjs; only the cross-cutting greps here.
        ("status-pill-web", r"className=\"[^\"]*\b(bg-(red|green|yellow|amber)-\d{3})[^\"]*rounded-full", "Status badge using background color — prefer text color only (Calm Precision Signal/Noise rule)"),
        ("missing-empty-state", r"\.map\([^)]+\)\s*\}", "List render without visible empty/error branch — verify state coverage"),
    ],
}

ARCHITECTURE_KEYWORDS = (
    "new flow",
    "new screen",
    "navigation graph",
    "router",
    "schema migration",
    "auth provider",
)


def iter_ui_files(workdir: Path) -> list[Path]:
    out: list[Path] = []
    for p in workdir.rglob("*"):
        if not p.is_file() or p.suffix not in ALL_UI_EXT:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        rel = str(p.relative_to(workdir))
        if any(rel.endswith(s) for s in SKIP_SUFFIXES):
            continue
        if any(s in "/" + rel for s in SKIP_PATH_CONTAINS):
            continue
        # Skip generated HTML reports (pattern: contains /coverage/ or generated headers)
        out.append(p)
    return out


def grep_file(path: Path, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return hits
    for i, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            hits.append((i, line.strip()[:200]))
    return hits


# Multi-line tag rule: find every <a ...> opening tag (may span lines), then check
# whether the tag content has href= or onClick=. If neither, it's a dead link.
# Using DOTALL because JSX wraps attributes onto multiple lines for readability.
_A_TAG_RE = re.compile(r"<a\b([^<>]*?)>", re.DOTALL)
_HAS_HREF_OR_HANDLER = re.compile(r"\b(href|onClick|onPress|onTap|to)\s*=")


def find_dead_anchors(path: Path) -> list[tuple[int, str]]:
    """Per-file scan for `<a>` tags missing href/onClick across multi-line attribute lists."""
    hits: list[tuple[int, str]] = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return hits
    for m in _A_TAG_RE.finditer(text):
        attrs = m.group(1) or ""
        if _HAS_HREF_OR_HANDLER.search(attrs):
            continue
        # Compute the line number of the tag opening
        line_no = text.count("\n", 0, m.start()) + 1
        snippet = m.group(0).replace("\n", " ").strip()[:200]
        hits.append((line_no, snippet))
    return hits


# Multi-line button rule: find every <button ...> opening tag (may span lines), then check
# whether the tag content has any onX={...} handler OR `disabled` (intentionally non-interactive).
# Replaces the prior line-bound `<button\b[^>]*?(?<!on[A-Z])>` regex which only inspected the
# chars immediately before `>` — it flagged `<button onClick={x} className="y">` as dead because
# `"y"` precedes `>`, not `on[A-Z]`. Per atomize-ai integration test 2026-05-03: 5/9 reported
# findings were this exact false-positive shape.
_BUTTON_TAG_RE = re.compile(r"<button\b([^<>]*?)>", re.DOTALL)
_HAS_HANDLER_OR_DISABLED = re.compile(r"\b(on[A-Z][a-zA-Z]+|disabled|aria-disabled|formAction|type\s*=\s*['\"]submit['\"])")


def find_dead_buttons(path: Path) -> list[tuple[int, str]]:
    """Per-file scan for `<button>` tags missing any handler / disabled state."""
    hits: list[tuple[int, str]] = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return hits
    for m in _BUTTON_TAG_RE.finditer(text):
        attrs = m.group(1) or ""
        if _HAS_HANDLER_OR_DISABLED.search(attrs):
            continue
        line_no = text.count("\n", 0, m.start()) + 1
        snippet = m.group(0).replace("\n", " ").strip()[:200]
        hits.append((line_no, snippet))
    return hits


def severity_for(dimension: str, label: str, count: int) -> str:
    if dimension == "interactability" and "no-handler" in label:
        return "blocker"
    if dimension == "data-accuracy" and ("hardcoded-stat" in label or "as-of" in label):
        return "blocker"
    if dimension == "performance" and "n-plus-one" in label:
        return "major"
    if count >= 5:
        return "major"
    return "minor"


def classify_architecture_impact(hint: str, files: list[str]) -> bool:
    blob = (hint + " " + " ".join(files)).lower()
    return any(k in blob for k in ARCHITECTURE_KEYWORDS)


def make_id(dimension: str, label: str, files: list[str]) -> str:
    # Stable ID across re-runs: hash dimension+label only. Same finding-class
    # always lands on the same .md filename regardless of how many evidence
    # files appear or in what order. Prevents the "queue accretes duplicates
    # on each --clear-less run" bug.
    h = hashlib.sha256("|".join([dimension, label]).encode()).hexdigest()[:8]
    return f"{dimension}-{label}-{h}"


def write_entry(
    queue_dir: Path,
    template: str,
    *,
    entry_id: str,
    dimension: str,
    severity: str,
    label: str,
    hint: str,
    findings: list[tuple[str, int, str]],
    architecture_impact: bool,
) -> Path:
    files_touched = sorted({f for (f, _, _) in findings})
    evidence_lines = "\n".join(f"- `{f}:{ln}` — `{snippet}`" for (f, ln, snippet) in findings[:25])
    body = template.format(
        id=entry_id,
        dimension=dimension,
        severity=severity,
        label=label,
        hint=hint,
        evidence=evidence_lines or "(no inline evidence — see scanner output)",
        files_touched_yaml="\n".join(f"  - {f}" for f in files_touched),
        architecture_impact="true" if architecture_impact else "false",
        proposed_fix=f"Address all occurrences of `{label}` in the listed files. Re-run `ux_triage.py` to confirm zero remaining.",
        rollback="git checkout -- " + " ".join(files_touched[:5]) + (" ..." if len(files_touched) > 5 else ""),
    )
    path = queue_dir / f"{entry_id}.md"
    path.write_text(body)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Static UX scan + queue writer for build-loop Review-D.")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--template", help="Path to ux-fix-plan.md template (default: alongside this script)")
    ap.add_argument("--clear", action="store_true", help="Clear queue dir before writing (use between builds)")
    args = ap.parse_args()

    workdir = Path(args.workdir).resolve()
    if not workdir.is_dir():
        print(json.dumps({"error": "workdir not a directory", "path": str(workdir)}))
        return 2

    template_path = (
        Path(args.template).resolve()
        if args.template
        else Path(__file__).resolve().parent.parent / "skills" / "build-loop" / "templates" / "ux-fix-plan.md"
    )
    if not template_path.is_file():
        print(json.dumps({"error": "template not found", "path": str(template_path)}))
        return 2
    template = template_path.read_text()

    queue_dir = workdir / ".build-loop" / "ux-queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    if args.clear:
        for old in queue_dir.glob("*.md"):
            old.unlink()

    started = time.time()
    files = iter_ui_files(workdir)
    summary: dict[str, Any] = {
        "scanned_files": len(files),
        "queue_dir": str(queue_dir),
        "entries": [],
        "by_dimension": {dim: 0 for dim in CHECKS},
    }

    # Multi-line scans: dead <a> and <button> tags whose attributes span newlines, AND
    # tags whose handlers don't sit at the trailing position. Both are full-tag scans
    # because the prior line-bound regexes had >90% false-positive rates against real JSX.
    multi_line_findings: list[tuple[str, str, str, list[tuple[str, int, str]]]] = []  # (label, hint, severity, findings)

    dead_anchor_findings: list[tuple[str, int, str]] = []
    dead_button_findings: list[tuple[str, int, str]] = []
    for path in files:
        if path.suffix not in WEB_EXT:
            continue
        for line_no, snippet in find_dead_anchors(path):
            dead_anchor_findings.append((str(path.relative_to(workdir)), line_no, snippet))
        for line_no, snippet in find_dead_buttons(path):
            dead_button_findings.append((str(path.relative_to(workdir)), line_no, snippet))
    if dead_anchor_findings:
        multi_line_findings.append((
            "link-no-target-web",
            "Anchor without href, onClick, or routing target — likely dead. Multi-line tag scan.",
            "major", dead_anchor_findings,
        ))
    if dead_button_findings:
        multi_line_findings.append((
            "button-no-handler-web",
            "Button without any onX handler, disabled, or formAction. Multi-line tag scan; recognizes disabled/aria-disabled/type=submit as valid handler-equivalents.",
            "blocker", dead_button_findings,
        ))
    for label, hint, severity, fs in multi_line_findings:
        arch = classify_architecture_impact(hint, [f for (f, _, _) in fs])
        entry_id = make_id("interactability", label, [f for (f, _, _) in fs])
        write_entry(
            queue_dir, template,
            entry_id=entry_id, dimension="interactability", severity=severity,
            label=label, hint=hint, findings=fs, architecture_impact=arch,
        )
        summary["entries"].append({
            "id": entry_id, "dimension": "interactability", "severity": severity,
            "label": label, "count": len(fs),
            "architecture_impact": arch,
            "path": str((queue_dir / f"{entry_id}.md").relative_to(workdir)),
        })
        summary["by_dimension"]["interactability"] = summary["by_dimension"].get("interactability", 0) + 1

    for dimension, checks in CHECKS.items():
        for label, regex_str, hint in checks:
            try:
                pattern = re.compile(regex_str)
            except re.error:
                continue
            findings: list[tuple[str, int, str]] = []
            for path in files:
                # Swift checks only against .swift; web checks only against web ext
                if "-swift" in label and path.suffix not in SWIFT_EXT:
                    continue
                if "-web" in label and path.suffix not in WEB_EXT:
                    continue
                for line_no, snippet in grep_file(path, pattern):
                    rel = str(path.relative_to(workdir))
                    findings.append((rel, line_no, snippet))
            if not findings:
                continue
            severity = severity_for(dimension, label, len(findings))
            if severity == "minor":
                # Minor entries report only — don't enter queue
                continue
            arch = classify_architecture_impact(hint, [f for (f, _, _) in findings])
            entry_id = make_id(dimension, label, [f for (f, _, _) in findings])
            path = write_entry(
                queue_dir,
                template,
                entry_id=entry_id,
                dimension=dimension,
                severity=severity,
                label=label,
                hint=hint,
                findings=findings,
                architecture_impact=arch,
            )
            summary["entries"].append(
                {
                    "id": entry_id,
                    "dimension": dimension,
                    "severity": severity,
                    "label": label,
                    "count": len(findings),
                    "architecture_impact": arch,
                    "path": str(path.relative_to(workdir)),
                }
            )
            summary["by_dimension"][dimension] += 1

    summary["duration_s"] = round(time.time() - started, 2)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
