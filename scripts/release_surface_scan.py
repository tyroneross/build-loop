#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""release_surface_scan.py — advisory scan for developer/owner surfaces that ship in Release.

Why this exists (incident 2026-09-25): an iOS app shipped a hidden owner/admin
panel, revealed by tapping the version row five times, whose gate used
client-side trust-on-first-use ownership. On every fresh install no owner
anchor existed, so any stranger could claim ownership with their own Face ID.
The independent auditor and the security reviewer both passed it: they checked
that an EXISTING anchor could not be taken over and never asked what a stranger
with a fresh install could reach. The deterministic half of that question is
"does this surface compile into the Release binary at all?", which is greppable.

What it does: walks Swift sources, tracks `#if / #elseif / #else / #endif`
nesting, and reports every dev-surface marker (admin/owner surfaces, ownership
claims, trust-on-first-use, debug/developer menus, multi-tap and shake reveals,
launch-argument seams) that is NOT inside a debug-only compilation block.

Advisory only: exit 0 whenever the scan completes, regardless of findings. The
consumer (security-reviewer, Phase 4 Review-A) decides severity by applying
the stranger test. Exit 2 only on a usage error (missing path).

A line may be allowlisted with a trailing or preceding comment
`// release-surface: allow <reason>` — it is then reported under `allowed`,
never silently dropped.

CLI::

    python3 scripts/release_surface_scan.py --path <repo> [--files a.swift b.swift]
        [--debug-flag INTERNAL_BUILD] [--json]

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Markers — single source of truth. `audit_before_commit.py` imports
# GATED_SURFACE_PATTERNS so the commit packet and this scan agree.

# (category, label, compiled regex, signal strength)
MARKERS: tuple[tuple[str, str, re.Pattern, str], ...] = (
    ("ownership_claim", "client-side ownership claim",
     re.compile(r"\bclaim(?:Ownership|Owner|Admin)\b"), "high"),
    ("ownership_claim", "owner anchor (trust-on-first-use shape)",
     re.compile(r"\b\w*owner\w*anchor\w*\b|\b\w*anchor\w*owner\w*\b", re.IGNORECASE), "high"),
    ("ownership_claim", "trust-on-first-use",
     re.compile(r"\btrust[\s_-]?on[\s_-]?first[\s_-]?use\b|\bTOFU\b", re.IGNORECASE), "high"),
    ("gesture_reveal", "multi-tap reveal",
     re.compile(r"onTapGesture\(\s*count:\s*(?:[3-9]|\d{2,})\b"), "high"),
    ("gesture_reveal", "multi-tap reveal",
     re.compile(r"numberOfTapsRequired\s*=\s*(?:[3-9]|\d{2,})\b"), "high"),
    ("gesture_reveal", "tap-counter reveal",
     re.compile(r"\b\w*taps?(?:Count|Counter)\w*\s*(?:>=|==|>)\s*\w+", re.IGNORECASE), "high"),
    ("gesture_reveal", "tap-count unlock threshold",
     re.compile(r"\b\w*(?:Unlock|Reveal|Secret|Hidden)Taps?\w*\b"), "high"),
    ("gesture_reveal", "shake reveal",
     re.compile(r"\bmotionShake\b|\bdeviceDidShake\w*\b|\bonShake\b"), "high"),
    ("owner_admin_surface", "admin/owner surface",
     re.compile(r"\b\w*(?:Admin|Owner)(?:Gate|Panel|View|Menu|Mode|Screen|Console|Access|Tools?|Dashboard|Settings)\b"),
     "medium"),
    ("owner_admin_surface", "admin/owner/developer flag",
     re.compile(r"\bis(?:Admin|Owner|Developer|Internal)(?:User|Mode)?\b"), "medium"),
    ("debug_surface", "debug surface",
     re.compile(r"\b\w*Debug(?:View|Panel|Menu|Screen|Console|Tools?|Overlay|Settings)\b"), "medium"),
    ("debug_surface", "developer surface",
     re.compile(r"\b(?:Dev|Developer|Internal)(?:Menu|Mode|Panel|Settings|Tools?|Screen|Options)\b"), "medium"),
    ("launch_arg_seam", "launch-argument / environment seam",
     re.compile(r"\bProcessInfo\.processInfo\.(?:arguments|environment)\b|\bCommandLine\.arguments\b"), "low"),
)

# Subset used by the commit-audit packet: surfaces that decide who may reach
# privileged behaviour. Launch-arg seams are excluded (too common to be a gate).
GATED_SURFACE_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (label, rx) for cat, label, rx, _ in MARKERS if cat != "launch_arg_seam"
)

ALLOW_RE = re.compile(r"//\s*release-surface:\s*allow\b(.*)$", re.IGNORECASE)
DIRECTIVE_RE = re.compile(r"^\s*#(if|elseif|else|endif)\b(.*)$")

EXCLUDED_DIRS = frozenset({
    ".git", ".build", "build", "DerivedData", "Pods", "Carthage", "node_modules",
    "SourcePackages", "checkouts", ".swiftpm", "Preview Content", ".build-loop",
    "worktrees",
})
TEST_DIR_RE = re.compile(r"(?:^|/)[^/]*(?:Tests?|UITests?|TestSupport|Specs?)(?:/|$)")
TEST_FILE_RE = re.compile(r"(?:Tests?|Spec)\.swift$")

DEFAULT_DEBUG_FLAGS = ("DEBUG",)


# ---------------------------------------------------------------------------
# Compilation-condition evaluation


def _is_debug_only(cond: str, debug_flags: tuple[str, ...]) -> bool:
    """True when every disjunct of `cond` requires a positive debug flag.

    `DEBUG`, `DEBUG && os(iOS)`, `(DEBUG)` → True. `DEBUG || INTERNAL` → False
    unless INTERNAL is also a declared debug flag. `!DEBUG` → False.
    """
    cond = cond.split("//", 1)[0].strip()
    if not cond:
        return False
    for disjunct in cond.split("||"):
        terms = [t.strip().strip("()").strip() for t in disjunct.split("&&")]
        if not any(t in debug_flags for t in terms):
            return False
    return True


def _is_release_only(cond: str, debug_flags: tuple[str, ...]) -> bool:
    """True for a bare negated debug flag (`!DEBUG`), whose #else is debug-only."""
    cond = cond.split("//", 1)[0].strip().strip("()").strip()
    return cond.startswith("!") and cond[1:].strip().strip("()").strip() in debug_flags


def scan_text(text: str, rel_path: str, debug_flags: tuple[str, ...] = DEFAULT_DEBUG_FLAGS) -> dict:
    """Scan one Swift file's text. Pure: no filesystem access."""
    findings: list[dict] = []
    allowed: list[dict] = []
    guarded = 0
    # Each frame: {"conds": [prior branch conds], "debug_only": bool}
    stack: list[dict] = []
    prev_line = ""
    in_block_comment = False

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw
        stripped = line.strip()

        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
            prev_line = raw
            continue
        if stripped.startswith("/*") and "*/" not in stripped:
            in_block_comment = True
            prev_line = raw
            continue

        m = DIRECTIVE_RE.match(line)
        if m:
            kind, cond = m.group(1), m.group(2).strip()
            if kind == "if":
                stack.append({"conds": [cond], "debug_only": _is_debug_only(cond, debug_flags)})
            elif kind == "elseif" and stack:
                stack[-1]["conds"].append(cond)
                stack[-1]["debug_only"] = _is_debug_only(cond, debug_flags)
            elif kind == "else" and stack:
                conds = stack[-1]["conds"]
                stack[-1]["debug_only"] = len(conds) == 1 and _is_release_only(conds[0], debug_flags)
            elif kind == "endif" and stack:
                stack.pop()
            prev_line = raw
            continue

        if stripped.startswith("//") or stripped.startswith("*"):
            prev_line = raw
            continue

        code = line.split("//", 1)[0] if "//" in line and "://" not in line else line
        hits = []
        for category, label, rx, strength in MARKERS:
            mm = rx.search(code)
            if mm:
                hits.append((category, label, strength, mm.group(0)))
        if not hits:
            prev_line = raw
            continue

        # One record per line: strongest signal wins, others listed.
        order = {"high": 0, "medium": 1, "low": 2}
        hits.sort(key=lambda h: order[h[2]])
        category, label, strength, match = hits[0]
        record = {
            "file": rel_path,
            "line": lineno,
            "category": category,
            "signal": label,
            "strength": strength,
            "match": match,
            "also": sorted({h[0] for h in hits[1:]} - {category}),
            "snippet": stripped[:160],
        }
        if any(frame["debug_only"] for frame in stack):
            guarded += 1
        else:
            allow = ALLOW_RE.search(raw) or ALLOW_RE.search(prev_line)
            if allow:
                record["allow_reason"] = allow.group(1).strip()
                allowed.append(record)
            else:
                findings.append(record)
        prev_line = raw

    return {"findings": findings, "allowed": allowed, "guarded": guarded}


# ---------------------------------------------------------------------------
# Repo walking


def is_apple_project(root: Path) -> bool:
    if (root / "Package.swift").exists() or (root / "project.yml").exists():
        return True
    try:
        for entry in root.iterdir():
            if entry.suffix in (".xcodeproj", ".xcworkspace"):
                return True
    except OSError:
        return False
    return any(True for _ in _iter_swift(root, limit=1))


def is_excluded_path(rel: str) -> bool:
    return bool(TEST_DIR_RE.search(rel) or TEST_FILE_RE.search(rel))


def _iter_swift(root: Path, limit: int | None = None):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.endswith(".xcassets")]
        for name in filenames:
            if not name.endswith(".swift"):
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if is_excluded_path(rel):
                continue
            yield path, rel
            count += 1
            if limit is not None and count >= limit:
                return


def scan_repo(root: Path, files: list[str] | None = None,
              debug_flags: tuple[str, ...] = DEFAULT_DEBUG_FLAGS) -> dict:
    root = root.resolve()
    applicable = is_apple_project(root)
    result: dict = {
        "tool": "release_surface_scan",
        "advisory": True,
        "root": str(root),
        "applicable": applicable,
        "debug_flags": list(debug_flags),
        "files_scanned": 0,
        "findings": [],
        "allowed": [],
        "guarded_count": 0,
    }
    if not applicable:
        result["verdict"] = "not_applicable"
        return result

    if files:
        targets = []
        for f in files:
            p = (root / f) if not Path(f).is_absolute() else Path(f)
            if p.suffix == ".swift" and p.exists():
                rel = p.resolve().relative_to(root).as_posix() if p.resolve().is_relative_to(root) else str(p)
                if not is_excluded_path(rel):
                    targets.append((p, rel))
    else:
        targets = list(_iter_swift(root))

    for path, rel in targets:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        r = scan_text(text, rel, debug_flags)
        result["files_scanned"] += 1
        result["findings"].extend(r["findings"])
        result["allowed"].extend(r["allowed"])
        result["guarded_count"] += r["guarded"]

    counts: dict[str, int] = {}
    for f in result["findings"]:
        counts[f["category"]] = counts.get(f["category"], 0) + 1
    result["counts"] = counts
    result["high_signal_count"] = sum(1 for f in result["findings"] if f["strength"] == "high")
    result["verdict"] = "warn" if result["findings"] else "clean"
    return result


def _render_text(result: dict) -> str:
    if result["verdict"] == "not_applicable":
        return "release_surface_scan: not an Apple/Swift project — skipped."
    lines = [
        f"release_surface_scan: {result['verdict'].upper()} (advisory) — "
        f"{len(result['findings'])} dev-surface marker(s) compile into Release, "
        f"{result['high_signal_count']} high-signal; {result['guarded_count']} guarded by a debug-only #if; "
        f"{len(result['allowed'])} allowlisted; {result['files_scanned']} file(s) scanned.",
    ]
    for f in result["findings"]:
        lines.append(f"  [{f['strength']}] {f['file']}:{f['line']} {f['signal']} — {f['snippet']}")
    for f in result["allowed"]:
        lines.append(f"  [allowed] {f['file']}:{f['line']} {f['signal']} — {f.get('allow_reason') or '(no reason)'}")
    if result["findings"]:
        lines.append(
            "Apply the stranger test to each: what can a person with a fresh install and their own "
            "account reach? Default fix: wrap in #if DEBUG; if it must ship, gate by an identity pinned "
            "at build time or verified server-side."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--path", default=".", help="repo root to scan")
    ap.add_argument("--files", nargs="*", help="restrict to these files (e.g. the diff's changed files)")
    ap.add_argument("--debug-flag", action="append", default=[],
                    help="additional compilation condition that is absent from Release (repeatable)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.path).expanduser()
    if not root.is_dir():
        print(f"release_surface_scan: path not found: {root}", file=sys.stderr)
        return 2
    flags = tuple(DEFAULT_DEBUG_FLAGS) + tuple(args.debug_flag)
    result = scan_repo(root, args.files, flags)
    print(json.dumps(result, indent=2) if args.json else _render_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
