#!/usr/bin/env python3
"""swift-platform-parity — detect Swift cross-platform parity issues and write
NavGator lessons.

Plan source: ~/.claude/plans/review-my-coding-work-cosmic-hopcroft.md §B3
Approach: regex/heuristic. Tree-sitter is NOT available locally; per plan's
risk-mitigation note we start with a small 3-rule set and tune over time.
TAG:INFERRED — no published spec; heuristic rules grounded in FloDoro's
recent commit history (Build 73-75 modernization fixed exactly these patterns).

Rules implemented:
  R1 hardcoded Color literal not from theme tokens.
       Flags: bare Color("..."), Color(red:green:blue:), Color.red/.green/.blue
       Allowlist: Color(.systemBlue) etc., Color.clear, Color.primary,
       Color.secondary, Color.accentColor (these are platform-correct).
  R2 .font(.system(...)) without a relativeTo: Dynamic Type anchor.
       Flags: .font(.system(size: N)), .font(.system(size: N, weight: .X))
       Allowlist: relativeTo: .body / .headline / etc. present.
  R3 hardcoded spacing constants not from a defined Spacing enum/token.
       Flags: .padding(N), .padding(.X, N), .padding(.X, N) where N is a
       numeric literal AND no Spacing.* nearby.
       Allowlist: .padding(Spacing.foo), .padding(.X, Spacing.foo).

Usage:
  swift-platform-parity.py <project-root> [--write-lessons]
  swift-platform-parity.py --self-test

Exit codes: 0 = ran (regardless of findings), 2 = bad arg / missing project.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = "1.0.0"
LESSONS_FILENAME = "lessons.json"
LESSONS_DIRNAME = ".navgator/lessons"

# ---------------------- Allowlists ----------------------

# System colors that ARE platform-correct (do not flag).
SYSTEM_COLOR_ALLOWLIST = {
    "primary", "secondary", "accentColor", "clear",
    "white", "black",  # rare but legal as semantic constants in some contexts
}
# Color(.systemX) and Color(uiColor: .systemX) are platform-correct.
SYSTEM_COLOR_INIT_RE = re.compile(
    r"Color\s*\(\s*(?:\.system\w+|uiColor\s*:\s*\.system\w+|nsColor\s*:\s*\.system\w+)"
)

# ---------------------- Rule patterns ----------------------

# R1a: Color("string-literal")  → asset-catalog name; allow when paired with theme accessor
RE_COLOR_STRING = re.compile(r'\bColor\s*\(\s*"([^"]+)"\s*\)')
# R1b: Color(red:green:blue:) — hardcoded RGB
RE_COLOR_RGB = re.compile(r"\bColor\s*\(\s*red\s*:")
# R1c: Color.red / .green / .blue / .yellow / .orange / .purple / .pink / .gray
#      Flags Color.x where x is a literal hue (not the allowlist).
RE_COLOR_DOT = re.compile(r"\bColor\.([A-Za-z][A-Za-z0-9]*)\b")
HUE_NAMES = {"red", "green", "blue", "yellow", "orange", "purple", "pink",
             "gray", "grey", "brown", "cyan", "indigo", "mint", "teal"}

# R2: .font(.system(size: N ...)) — flag when no relativeTo: in the call
RE_FONT_SYSTEM = re.compile(
    r"\.font\(\s*\.system\(\s*size\s*:\s*[0-9.]+([^)]*)\)\s*\)"
)

# R3: .padding(N) or .padding(.edge, N) where N is numeric and no Spacing token nearby.
RE_PADDING_NUM = re.compile(
    r"\.padding\(\s*(?:\.[a-zA-Z]+\s*,\s*)?([0-9]+(?:\.[0-9]+)?)\s*\)"
)
# also catches .padding(.horizontal, 16) etc.

# ---------------------- Helpers ----------------------

def iter_swift_files(root: Path) -> Iterable[Path]:
    """Yield .swift files under root, skipping build/derived/test dirs."""
    skip_parts = {"build", ".build", "DerivedData", ".derived-data", "Pods",
                  ".swiftpm", "node_modules", ".git"}
    for p in root.rglob("*.swift"):
        if any(part in skip_parts for part in p.parts):
            continue
        yield p


def detect_issues(path: Path) -> list[dict]:
    """Return a list of {rule, line, snippet, file} dicts for one file."""
    findings: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return findings
    lines = text.splitlines()

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue

        # R1a: Color("string"). Skip if paired with an asset-catalog wrapper
        # like Color("brand", bundle:) is fine; just emit a soft flag.
        for m in RE_COLOR_STRING.finditer(line):
            name = m.group(1)
            findings.append({
                "rule": "R1-color-literal",
                "line": i,
                "snippet": stripped[:160],
                "match": f'Color("{name}")',
                "hint": "Prefer a theme token like Theme.Color.<semantic> over an inline asset name",
            })

        # R1b: Color(red:green:blue:)
        if RE_COLOR_RGB.search(line):
            findings.append({
                "rule": "R1-color-rgb",
                "line": i,
                "snippet": stripped[:160],
                "match": "Color(red: ...)",
                "hint": "Hardcoded RGB defeats Dark Mode + theming. Move to a Theme.Color.* token.",
            })

        # R1c: Color.<hue>
        for m in RE_COLOR_DOT.finditer(line):
            sym = m.group(1)
            if sym in SYSTEM_COLOR_ALLOWLIST:
                continue
            if sym not in HUE_NAMES:
                continue
            # double-check it's not Color.systemX surrounding context
            if SYSTEM_COLOR_INIT_RE.search(line):
                continue
            findings.append({
                "rule": "R1-color-hue",
                "line": i,
                "snippet": stripped[:160],
                "match": f"Color.{sym}",
                "hint": "Direct hue (Color.red etc.) skips theming. Use a semantic Theme.Color token.",
            })

        # R2: .font(.system(size:)) — must include relativeTo:
        for m in RE_FONT_SYSTEM.finditer(line):
            tail = m.group(1)  # remaining args after "size: N"
            if "relativeTo" in tail:
                continue
            findings.append({
                "rule": "R2-font-no-dynamic-type",
                "line": i,
                "snippet": stripped[:160],
                "match": m.group(0),
                "hint": "Add `relativeTo: .body` (or other text style) so Dynamic Type scales the font.",
            })

        # R3: .padding(numeric) — flag only when no Spacing.* on the same line
        if "Spacing." in line:
            continue
        for m in RE_PADDING_NUM.finditer(line):
            findings.append({
                "rule": "R3-spacing-literal",
                "line": i,
                "snippet": stripped[:160],
                "match": m.group(0),
                "hint": "Hardcoded spacing breaks tokenization. Use a Spacing.* enum value.",
            })

    return findings


def aggregate_by_rule(all_findings: list[dict]) -> dict[str, list[dict]]:
    """Group findings by rule id. Used to write one lesson per rule."""
    by_rule: dict[str, list[dict]] = {}
    for f in all_findings:
        by_rule.setdefault(f["rule"], []).append(f)
    return by_rule


def write_lessons(project_root: Path, by_rule: dict[str, list[dict]]) -> Path:
    """Append (or merge) one lesson per rule into .navgator/lessons/lessons.json.
    Schema follows agent-studio's lessons.json (verified 2026-05-01)."""
    lessons_dir = project_root / LESSONS_DIRNAME
    lessons_dir.mkdir(parents=True, exist_ok=True)
    lessons_path = lessons_dir / LESSONS_FILENAME

    if lessons_path.exists():
        with lessons_path.open() as f:
            doc = json.load(f)
    else:
        doc = {"schema_version": SCHEMA_VERSION, "lessons": []}

    today = time.strftime("%Y-%m-%d")
    existing_ids = {l.get("id") for l in doc.get("lessons", [])}

    rule_meta = {
        "R1-color-literal": ("pp-color-literal",
            "Hardcoded asset-name Color() literals not flowing through theme tokens",
            ['Color\\("[^"]+"\\)']),
        "R1-color-rgb": ("pp-color-rgb",
            "Hardcoded Color(red:green:blue:) literals defeat Dark Mode + theming",
            ["Color\\(\\s*red\\s*:"]),
        "R1-color-hue": ("pp-color-hue",
            "Direct Color.<hue> usage (Color.red/.blue) skips theming",
            ["Color\\.(red|green|blue|yellow|orange|purple|pink|gray|cyan|indigo|mint|teal)"]),
        "R2-font-no-dynamic-type": ("pp-font-no-dyn",
            ".font(.system(size:)) without relativeTo: anchor — fails Dynamic Type",
            ["\\.font\\(\\.system\\(size:"]),
        "R3-spacing-literal": ("pp-spacing-literal",
            "Hardcoded numeric .padding(N) constants instead of Spacing tokens",
            ["\\.padding\\([0-9.,\\s]+\\)"]),
    }

    written = 0
    for rule, items in by_rule.items():
        lid, pattern, signature = rule_meta.get(rule, (f"pp-{rule}", rule, [rule]))
        files = sorted({Path(it["file"]).relative_to(project_root).as_posix()
                        for it in items if "file" in it})[:20]
        sample = items[0]
        lesson = {
            "id": lid,
            "category": "platform-parity",
            "pattern": pattern,
            "signature": signature,
            "severity": "important",
            "context": {
                "first_seen": today,
                "last_seen": today,
                "occurrences": len(items),
                "files_affected": files,
                "resolution": (
                    "Centralize via theme tokens. For colors: define a Theme.Color.* "
                    "enum and reference everywhere. For fonts: always pair "
                    ".system(size:) with `relativeTo:` for Dynamic Type. For spacing: "
                    "define a Spacing enum and use Spacing.<token>. Sweep findings "
                    "with grep against the rule signature."
                ),
            },
            "example": {
                "bad": sample.get("snippet", ""),
                "good": (
                    "Use the project's theme: e.g., `.foregroundStyle(Theme.Color.accent)` "
                    "instead of `.foregroundColor(Color.red)`; "
                    "`.font(.system(size: 16, weight: .semibold, relativeTo: .body))` "
                    "instead of `.font(.system(size: 16))`; `.padding(Spacing.medium)` "
                    "instead of `.padding(16)`."
                ),
                "why": sample.get("hint", ""),
            },
            "validation": {
                "last_validated": today,
                "source": "swift-platform-parity.py",
                "status": "current",
            },
        }

        if lid in existing_ids:
            for i, existing in enumerate(doc["lessons"]):
                if existing.get("id") == lid:
                    # update last_seen / occurrences / files but preserve first_seen
                    existing.setdefault("context", {})
                    existing["context"]["last_seen"] = today
                    existing["context"]["occurrences"] = len(items)
                    existing["context"]["files_affected"] = files
                    existing.setdefault("validation", {})["last_validated"] = today
                    doc["lessons"][i] = existing
                    break
        else:
            doc["lessons"].append(lesson)
            written += 1

    with lessons_path.open("w") as f:
        json.dump(doc, f, indent=2)
    return lessons_path


# ---------------------- Self-test ----------------------

SELF_TEST_SAMPLES = [
    # (label, swift snippet, expected rule(s) — empty list = no flag)
    ("R1-string", '.foregroundColor(Color("brand-primary"))', ["R1-color-literal"]),
    ("R1-rgb",    'let c = Color(red: 0.1, green: 0.2, blue: 0.3)', ["R1-color-rgb"]),
    ("R1-hue",    '.foregroundColor(Color.red)', ["R1-color-hue"]),
    ("R1-allow",  '.foregroundColor(Color(.systemBlue))', []),
    ("R1-allow2", '.foregroundColor(Color.primary)', []),
    ("R2-bad",    '.font(.system(size: 16, weight: .bold))', ["R2-font-no-dynamic-type"]),
    ("R2-good",   '.font(.system(size: 16, weight: .bold, relativeTo: .body))', []),
    ("R3-bad",    '.padding(.horizontal, 16)', ["R3-spacing-literal"]),
    ("R3-good",   '.padding(.horizontal, Spacing.medium)', []),
    ("R3-bare",   '.padding(8)', ["R3-spacing-literal"]),
    ("comment",   '// .padding(16) is fine in a comment', []),
]

def run_self_test() -> int:
    fails = 0
    tmp = Path("/tmp/swift_pp_self_test.swift")
    for label, snippet, expected in SELF_TEST_SAMPLES:
        tmp.write_text(snippet + "\n")
        got = detect_issues(tmp)
        got_rules = sorted({f["rule"] for f in got})
        expected_sorted = sorted(expected)
        ok = got_rules == expected_sorted
        status = "PASS" if ok else "FAIL"
        if not ok: fails += 1
        print(f"  [{status}] {label}: expected={expected_sorted} got={got_rules}")
        if not ok:
            for f in got:
                print(f"      → {f['rule']} matched {f.get('match')}")
    tmp.unlink(missing_ok=True)
    print(f"\n{len(SELF_TEST_SAMPLES) - fails}/{len(SELF_TEST_SAMPLES)} self-test cases pass")
    return 0 if fails == 0 else 1


# ---------------------- CLI ----------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("project", nargs="?", help="Project root (must contain Swift files)")
    ap.add_argument("--write-lessons", action="store_true",
                    help="Write findings to <project>/.navgator/lessons/lessons.json")
    ap.add_argument("--self-test", action="store_true",
                    help="Run regex self-test and exit")
    ap.add_argument("--max-print", type=int, default=20,
                    help="Max findings printed inline (default 20)")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    if not args.project:
        ap.error("project root required (or pass --self-test)")

    project_root = Path(args.project).resolve()
    if not project_root.is_dir():
        print(f"ERROR: not a directory: {project_root}", file=sys.stderr)
        return 2

    all_findings: list[dict] = []
    file_count = 0
    for swift in iter_swift_files(project_root):
        file_count += 1
        for f in detect_issues(swift):
            f["file"] = str(swift)
            all_findings.append(f)

    by_rule = aggregate_by_rule(all_findings)

    print(f"\nswift-platform-parity scan: {project_root}")
    print(f"  scanned {file_count} .swift files")
    print(f"  total findings: {len(all_findings)}")
    for rule, items in sorted(by_rule.items()):
        print(f"  {rule}: {len(items)}")

    if all_findings:
        print(f"\nFirst {min(len(all_findings), args.max_print)} findings:")
        for f in all_findings[:args.max_print]:
            rel = Path(f['file']).relative_to(project_root) if Path(f['file']).is_absolute() else f['file']
            print(f"  {rel}:{f['line']} [{f['rule']}] {f['match']}")

    if args.write_lessons:
        if not by_rule:
            print("\nNo findings — not writing lessons.")
        else:
            path = write_lessons(project_root, by_rule)
            print(f"\nWrote/updated lessons → {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
