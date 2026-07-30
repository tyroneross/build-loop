#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
surface_policy.py — derived view of what a plugin directory EXPOSES to the user.

The surface is COMPUTED from the files on every invocation. Nothing is stored.
There is deliberately no `surface-policy.json`: the harness reads only SKILL.md
frontmatter, so any stored copy of the exposure list is a second source of truth
that drifts away from the files it claims to describe. Ask the files, always.

THE FAIL-OPEN DEFAULT (the reason this script exists)
-----------------------------------------------------
The harness resolves a skill's visibility as ``userInvocable ?? true``. A
SKILL.md with NO ``user-invocable`` field is therefore **PUBLIC**, not hidden.
Skills are born exposed. A plugin author who simply never writes the field has
shipped a public skill without ever deciding to. This class is reported as
"PUBLIC BY HARNESS DEFAULT" and is the headline of every report.

Skill classification (four classes, mutually exclusive):
    hidden              user-invocable: false                  — not user-facing
    public_justified    user-invocable: true  + public-justification:
    public_unjustified  user-invocable: true  , no justification — undeclared why
    default_public      no user-invocable field at all          — PUBLIC BY DEFAULT

The rule itself lives in `scripts/exposure_policy.py` and is imported, not
restated — `skill_index.py` classifies the same frontmatter and must not be able
to disagree with this script about what a file exposes.

Commands are unconditionally public; being reachable by the user is what a
command IS. They are listed for completeness, never flagged.

CLI usage:
    python3 scripts/surface_policy.py report --workdir DIR [--json | --plain]
    python3 scripts/surface_policy.py check  --workdir DIR [--json | --plain]

    report  print the derived surface; always exit 0
    check   exit 1 if any skill is public without a public-justification, or
            lacks the user-invocable field entirely; exit 0 when clean

Works on ANY plugin directory via --workdir — build-loop, RossLabs-AI-Assistant,
or a plugin that does not exist yet. No per-repo code, no repo-specific paths.
A directory with no skills/ and no commands/ reports an empty surface and passes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):  # direct `python3 scripts/surface_policy.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from exposure_policy import (  # noqa: E402
    DEFAULT_PUBLIC,
    EXPOSURE_CLASSES,
    HARNESS_DEFAULT_NOTE,
    HIDDEN,
    JUSTIFICATION_FIELD,
    PUBLIC_JUSTIFIED,
    PUBLIC_UNJUSTIFIED,
    UNDECLARED_CLASSES,
    USER_INVOCABLE_FIELD,
    classify,
    is_excluded_path,
    is_public,
    normalize_flag,
    unquote as _unquote,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILLS_DIRNAME = "skills"
COMMANDS_DIRNAME = "commands"

#: Class names, class order, and the exposure rule all come from
#: `exposure_policy` — re-exported here because the tests and callers of this
#: module import them from it.
SKILL_CLASSES = EXPOSURE_CLASSES

# The two classes `check` rejects.
VIOLATION_CLASSES = UNDECLARED_CLASSES

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
USER_INVOCABLE_RE = re.compile(rf"^{USER_INVOCABLE_FIELD}:\s*(.+?)\s*$", re.MULTILINE)
JUSTIFICATION_RE = re.compile(rf"^{JUSTIFICATION_FIELD}:\s*(.+?)\s*$", re.MULTILINE)

_HARNESS_DEFAULT_NOTE = HARNESS_DEFAULT_NOTE


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> str | None:
    """Return the YAML frontmatter block, or None when the file has none.

    A file whose frontmatter is malformed (missing, unterminated) yields None.
    Callers must treat that as "no field present" — which, under the harness
    default, means PUBLIC. Fail-safe means erring toward reporting exposure.
    """
    match = FRONTMATTER_RE.match(text)
    return match.group(1) if match else None


def classify_skill(path: Path, root: Path) -> dict:
    """Classify one SKILL.md into exactly one of the four surface classes."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""

    frontmatter = parse_frontmatter(text)
    malformed = frontmatter is None
    block = frontmatter or ""

    name_match = NAME_RE.search(block)
    if name_match:
        raw_name = _unquote(name_match.group(1))
        name = raw_name.split(":", 1)[1] if ":" in raw_name else raw_name
    else:
        name = path.parent.name

    inv_match = USER_INVOCABLE_RE.search(block)
    just_match = JUSTIFICATION_RE.search(block)
    justification = _unquote(just_match.group(1)) if just_match else None
    declared = normalize_flag(inv_match.group(1) if inv_match else None)

    # One rule, defined once, in exposure_policy.classify.
    klass = classify(declared, justification)

    return {
        "name": name,
        "path": str(path.relative_to(root)),
        "class": klass,
        "user_invocable": declared,
        "public_justification": justification,
        "malformed_frontmatter": malformed,
        "public": is_public(klass),
    }


def discover_skills(workdir: Path) -> list[dict]:
    """Every ``<workdir>/skills/**/SKILL.md``, sorted by path.

    Scoped to the plugin's own ``skills/`` tree on purpose. Recursing from the
    workdir root would sweep in build worktrees and runtime-authored drafts
    (e.g. ``.build-loop/worktrees/*/skills/``) that the harness never loads.
    ``exposure_policy.is_excluded_path`` catches the same trees NESTED under
    ``skills/`` — same list `skill_index.py` walks with, so the two scripts
    cannot disagree about which files are part of the surface.
    """
    skills_dir = workdir / SKILLS_DIRNAME
    if not skills_dir.is_dir():
        return []
    return [
        classify_skill(path, workdir)
        for path in sorted(skills_dir.rglob("SKILL.md"))
        if path.is_file() and not is_excluded_path(path.relative_to(workdir).parts)
    ]


def discover_commands(workdir: Path) -> list[dict]:
    """Every ``<workdir>/commands/**/*.md``. Commands are always public."""
    commands_dir = workdir / COMMANDS_DIRNAME
    if not commands_dir.is_dir():
        return []
    found = []
    for path in sorted(commands_dir.rglob("*.md")):
        if not path.is_file():
            continue
        rel = path.relative_to(commands_dir).with_suffix("")
        found.append({
            "name": ":".join(rel.parts),
            "path": str(path.relative_to(workdir)),
        })
    return found


# ---------------------------------------------------------------------------
# Derived report
# ---------------------------------------------------------------------------

def build_report(workdir: Path) -> dict:
    """Compute the full surface for *workdir*. Pure function of the files."""
    skills = discover_skills(workdir)
    commands = discover_commands(workdir)

    by_class = {klass: [s for s in skills if s["class"] == klass] for klass in SKILL_CLASSES}
    violations = [s for s in skills if s["class"] in VIOLATION_CLASSES]

    return {
        "workdir": str(workdir),
        "plugin": workdir.name,
        "commands": commands,
        "skills": skills,
        "counts": {
            "commands": len(commands),
            "skills": len(skills),
            **{klass: len(by_class[klass]) for klass in SKILL_CLASSES},
        },
        "by_class": by_class,
        "violations": violations,
        "ok": not violations,
        "harness_default_note": _HARNESS_DEFAULT_NOTE,
    }


# ---------------------------------------------------------------------------
# Plain rendering
# ---------------------------------------------------------------------------

def _render_default_public_block(report: dict) -> list[str]:
    """The headline block. An absent field is PUBLIC — say so in the output."""
    items = report["by_class"][DEFAULT_PUBLIC]
    if not items:
        return [
            "  PUBLIC BY HARNESS DEFAULT   0   (every skill declares user-invocable)",
        ]
    lines = [
        f"  PUBLIC BY HARNESS DEFAULT   {len(items)}   <-- READ THIS",
        "",
        "  !! These skills carry NO `user-invocable` field.",
        "  !! The harness resolves visibility as `userInvocable ?? true`, so an",
        "  !! ABSENT field means PUBLIC. They are exposed to the user right now,",
        "  !! even though nothing in the file declares them public.",
        "  !! Fix: add `user-invocable: false`, or `user-invocable: true` plus a",
        "  !! `public-justification:` saying why the user should reach it directly.",
        "",
    ]
    width = max(len(s["name"]) for s in items)
    lines += [f"       {s['name']:<{width}}  {s['path']}" for s in items]
    return lines


def render_plain(report: dict) -> str:
    counts = report["counts"]
    lines = [
        f"Surface report — {report['plugin']}",
        f"  {report['workdir']}",
        "  (derived from the files on this run; nothing is stored)",
        "",
    ]

    lines.append(f"COMMANDS ({counts['commands']}) — always public; that is what a command is")
    if report["commands"]:
        lines += [f"    /{c['name']}   {c['path']}" for c in report["commands"]]
    else:
        lines.append("    (none)")
    lines.append("")

    lines.append(f"SKILLS ({counts['skills']})")
    if not report["skills"]:
        lines += ["    (none)", ""]
    else:
        lines += _render_default_public_block(report)
        lines.append("")

        pub_unjust = report["by_class"][PUBLIC_UNJUSTIFIED]
        lines.append(f"  public, NO justification    {len(pub_unjust)}")
        for s in pub_unjust:
            lines.append(f"       {s['name']}   {s['path']}")

        pub_just = report["by_class"][PUBLIC_JUSTIFIED]
        lines.append(f"  public, justified           {len(pub_just)}")
        for s in pub_just:
            lines.append(f"       {s['name']}   {s['path']}")
            lines.append(f"         why: {s['public_justification']}")

        hidden = report["by_class"][HIDDEN]
        lines.append(f"  hidden                      {len(hidden)}")
        lines.append("")

    if report["ok"]:
        lines.append("OK — every skill has an explicit `user-invocable`, and every public one is justified.")
    else:
        lines.append(
            f"VIOLATIONS: {len(report['violations'])} skill(s) are public without a stated reason. "
            "`check` exits 1."
        )

    return "\n".join(lines)


def render_check_plain(report: dict) -> str:
    if report["ok"]:
        return (
            f"OK — {report['counts']['skills']} skill(s) in {report['plugin']}: "
            "all declare `user-invocable`, all public ones carry a `public-justification`."
        )
    lines = [f"FAIL — {len(report['violations'])} skill(s) are publicly exposed without a stated reason:"]
    for s in report["violations"]:
        if s["class"] == DEFAULT_PUBLIC:
            reason = f"PUBLIC BY HARNESS DEFAULT ({_HARNESS_DEFAULT_NOTE})"
        else:
            reason = "user-invocable: true with no `public-justification:`"
        lines.append(f"    {s['name']}   {s['path']}")
        lines.append(f"      {reason}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Show (report) or enforce (check) what a plugin directory exposes to the "
            "user. The surface is derived from SKILL.md frontmatter every run — never stored."
        )
    )
    p.add_argument("subcommand", choices=("report", "check"))
    p.add_argument("--workdir", type=Path, default=Path("."), metavar="DIR",
                   help="Plugin directory to inspect (any plugin, not just build-loop).")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="Emit JSON instead of the human-readable report.")
    p.add_argument("--plain", dest="as_plain", action="store_true",
                   help="Force plain text output (the default).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    workdir = args.workdir.resolve()
    report = build_report(workdir)

    if args.as_json and not args.as_plain:
        if args.subcommand == "check":
            print(json.dumps(
                {"ok": report["ok"], "workdir": report["workdir"],
                 "counts": report["counts"], "violations": report["violations"]},
                indent=2,
            ))
        else:
            print(json.dumps(report, indent=2))
    else:
        print(render_plain(report) if args.subcommand == "report" else render_check_plain(report))

    return 0 if (args.subcommand == "report" or report["ok"]) else 1


if __name__ == "__main__":
    sys.exit(main())
