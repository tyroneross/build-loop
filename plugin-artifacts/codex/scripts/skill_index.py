#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
skill_index.py — generate a host-neutral routing index of every skill in a plugin.

Every coding agent that touches this repo needs the same answer to "which skill
handles this request?", but each host reads a different entrypoint: Claude Code
reads CLAUDE.md, Codex reads AGENTS.md, Cursor reads .cursor/rules/. Restating
the skill list in each of those files creates three copies that drift. This
script makes ONE generated table the single source of truth; the host files
point at it instead of repeating it.

The index is DERIVED, never hand-maintained. `SKILL.md` frontmatter is the only
input, so a newly authored skill lands in the index the moment the file exists.
`--check` is the drift guard: it regenerates in memory and exits 1 when the
on-disk file differs, the same generate-and-assert-in-sync contract that
`scripts/build_codex_plugin_artifact.py` uses for the Codex bundle.

Exposure is not decided here. `scripts/exposure_policy.py` owns the rule — the
same module `scripts/surface_policy.py` imports — so the index cannot disagree
with the gate about what a file exposes. This script only collapses that rule's
four classes onto the three columns the table shows (see `_exposure`).

CLI usage:
    python3 scripts/skill_index.py --workdir . --apply       # write the index
    python3 scripts/skill_index.py --workdir . --check       # exit 1 on drift
    python3 scripts/skill_index.py --workdir . --json        # inspect rows
    python3 scripts/skill_index.py --workdir <other-plugin> --apply

    --workdir   plugin root to scan (default: cwd); works on any plugin dir
    --output    index path relative to workdir (default: docs/SKILL-INDEX.md)
    --check     regenerate in memory, fail if the on-disk index differs
    --apply     write the index
    --json      emit JSON instead of plain text
    --plain     emit plain text (default)

Exit codes:
    0  success / index in sync
    1  drift detected, missing skills directory, or unreadable index
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

if __package__ in (None, ""):  # direct `python3 scripts/skill_index.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import exposure_policy  # noqa: E402
from exposure_policy import (  # noqa: E402
    EXCLUDED_PATH_SEGMENTS,
    JUSTIFICATION_FIELD,
    USER_INVOCABLE_FIELD,
    is_excluded_path,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT = Path("docs") / "SKILL-INDEX.md"
SKILLS_DIRNAME = "skills"

#: NO LONGER TRUNCATED. This used to cap the table cell at 160 chars on the
#: assumption that "the full text always remains one click away in the linked
#: SKILL.md". That assumption is true for READING and false for ROUTING: an
#: agent picks a skill FROM the row — it does not open 50 files to disambiguate
#: first. This repo's house style puts the disambiguator LAST ("... NOT for X,
#: use Y"), so a head-truncation deleted precisely the sentence that decides
#: between two similar skills. Measured 2026-08-18: 9 source descriptions
#: carried a `NOT for` clause and 0 survived into the rendered table.
#: A markdown table cell has no real width limit; only this constant did.
#: Kept as the threshold for the adequacy test's "is this row long enough to
#: have lost something" heuristic, not as a render-path cut.
DESCRIPTION_MAX = 160

#: The table's OWN column vocabulary — three values, not the policy's four. The
#: index is a routing aid, so it only needs "can a user load this, and was that
#: on purpose"; the two undeclared policy classes answer that identically and
#: collapse into one column. `_exposure` owns the mapping; the DETERMINATION
#: stays in `exposure_policy.classify`.
HIDDEN = "hidden"
PUBLIC = "public"
PUBLIC_UNDECLARED = "public-undeclared"
EXPOSURE_CLASSES = (PUBLIC, PUBLIC_UNDECLARED, HIDDEN)

#: policy class -> table column.
_POLICY_TO_COLUMN = {
    exposure_policy.HIDDEN: HIDDEN,
    exposure_policy.PUBLIC_JUSTIFIED: PUBLIC,
    exposure_policy.PUBLIC_UNJUSTIFIED: PUBLIC_UNDECLARED,
    exposure_policy.DEFAULT_PUBLIC: PUBLIC_UNDECLARED,
}

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
SCALAR_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$")

GENERATED_BANNER = "GENERATED FILE — DO NOT EDIT BY HAND."


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillRow:
    """One routing row: what the skill is, when to use it, how it is reached."""

    skill_id: str
    name: str
    path: str          # POSIX path relative to the plugin root
    description: str   # full, whitespace-collapsed frontmatter description
    exposure: str      # "public" | "hidden"
    invocation: str    # host-neutral reach instruction
    warning: str | None = None


# ---------------------------------------------------------------------------
# Frontmatter parsing (stdlib only — no PyYAML dependency)
# ---------------------------------------------------------------------------

def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        return inner.replace('\\"', '"') if value[0] == '"' else inner
    return value


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """Return top-level scalar frontmatter fields, or None when absent.

    Supports plain scalars, quoted scalars, and `|` / `>` block scalars — the
    three shapes SKILL.md frontmatter actually uses. Nested mappings and lists
    are skipped rather than raising: an index row is worth more than a crash.
    """
    match = FRONTMATTER_RE.match(text)
    if match is None:
        return None

    fields: dict[str, str] = {}
    lines = match.group(1).split("\n")
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        idx += 1
        if not line.strip() or line.startswith("#") or line[:1].isspace():
            continue
        key_match = SCALAR_KEY_RE.match(line)
        if key_match is None:
            continue
        key, raw = key_match.group(1), key_match.group(2).strip()
        if raw.startswith("|") or raw.startswith(">"):
            block: list[str] = []
            while idx < len(lines):
                nxt = lines[idx]
                if nxt.strip() and not nxt[:1].isspace():
                    break
                block.append(nxt.strip())
                idx += 1
            fields[key] = " ".join(part for part in block if part).strip()
        else:
            fields[key] = _strip_quotes(raw)
    return fields


def collapse(value: str) -> str:
    """Collapse all whitespace runs to single spaces."""
    return " ".join(value.split())


#: Phrasings that signal a description tells an agent WHEN to reach for the
#: skill, rather than only what it is. Deliberately broad — a false "has
#: triggers" is cheaper than appending a redundant when_to_use block.
TRIGGER_MARKERS = (
    "use when", "use this", "invoke", "trigger", "fires", "activates",
    "run before", "run after", "call when", "when the user", "when a",
    "when an", "asks to", "asks for", "reach for",
)


def _has_trigger_language(description: str) -> bool:
    low = description.lower()
    return any(m in low for m in TRIGGER_MARKERS)


def truncate(value: str, limit: int = DESCRIPTION_MAX) -> str:
    """Deterministically shorten *value* to *limit* chars on a word boundary."""
    value = collapse(value)
    if len(value) <= limit:
        return value
    head = value[: limit - 1]
    cut = head.rfind(" ")
    if cut > limit // 2:
        head = head[:cut]
    return head.rstrip(" ,;:.—-") + "…"


def escape_cell(value: str) -> str:
    """Make *value* safe inside a markdown table cell."""
    return collapse(value).replace("\\", "\\\\").replace("|", "\\|")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def plugin_name(workdir: Path) -> str:
    """Resolve the plugin name from its manifest, falling back to the dir name."""
    for manifest in (
        workdir / ".claude-plugin" / "plugin.json",
        workdir / ".codex-plugin" / "plugin.json",
    ):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:  # missing file, bad json — fall through
            continue
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return workdir.resolve().name


#: Kept as a module-level name because callers and tests import it from here;
#: the walk list and the matching are `exposure_policy`'s, shared with
#: `surface_policy.py` so both scripts see the same set of files.
_is_excluded = is_excluded_path


def iter_skill_files(workdir: Path) -> list[Path]:
    """Every non-worktree `skills/**/SKILL.md` under *workdir*, sorted."""
    skills_dir = workdir / SKILLS_DIRNAME
    found: list[Path] = []
    for path in skills_dir.rglob("SKILL.md"):
        if not path.is_file():
            continue
        if _is_excluded(path.relative_to(workdir).parts):
            continue
        found.append(path)
    return sorted(found, key=lambda p: p.relative_to(workdir).as_posix())


def _exposure(fields: dict[str, str]) -> str:
    """Render the shared exposure class as one of this table's three columns.

    The determination — including the fail-open ``userInvocable ?? true`` default
    that makes a MISSING field PUBLIC — belongs to ``exposure_policy.classify``.
    An absent field must arrive there as ``None``, never as ``""``: absent and
    empty are different cases and only the former is the harness default.

    The table collapses the policy's two undeclared classes (no field at all /
    a field that states no reason) into one ``public-undeclared`` column, because
    an agent routing a request cares that the skill is reachable-but-unintended,
    not which of the two ways it got there.
    """
    return _POLICY_TO_COLUMN[
        exposure_policy.classify(
            fields.get(USER_INVOCABLE_FIELD),
            fields.get(JUSTIFICATION_FIELD),
        )
    ]


def build_row(workdir: Path, path: Path, plugin: str) -> SkillRow:
    rel = path.relative_to(workdir).as_posix()
    text = path.read_text(encoding="utf-8", errors="replace")
    fields = parse_frontmatter(text)
    warning: str | None = None

    if fields is None:
        fields = {}
        warning = "no YAML frontmatter block"

    name = fields.get("name", "").strip()
    if not name:
        name = path.parent.name
        warning = warning or "missing `name:` — fell back to the directory name"

    description = collapse(fields.get("description", ""))
    if not description:
        description = "(no description in frontmatter — read the file)"
        warning = warning or "missing `description:` — cannot route on this skill"
    elif not _has_trigger_language(description):
        # A description that says what a skill IS but never WHEN to use it
        # cannot be routed on. Some skills put their triggers in a separate
        # `when_to_use:` block, which this generator never read — so that
        # content was invisible to every agent choosing from this table.
        extra = collapse(fields.get("when_to_use", ""))
        if extra:
            description = f"{description} When to use: {extra}"
        else:
            warning = warning or (
                "description states what the skill IS but not WHEN to use it, "
                "and no `when_to_use:` to fall back on — agents cannot route on this"
            )

    skill_id = name if ":" in name else f"{plugin}:{name}"
    exposure = _exposure(fields)
    invocation = (
        f"internal — routed to by `{plugin}`"
        if exposure == HIDDEN
        else f"load `{skill_id}`"
    )
    return SkillRow(
        skill_id=skill_id,
        name=name,
        path=rel,
        description=description,
        exposure=exposure,
        invocation=invocation,
        warning=warning,
    )


class SkillIndexError(Exception):
    """Raised when the index cannot be generated or verified."""


def discover(workdir: Path) -> list[SkillRow]:
    """All skill rows for *workdir*, sorted by skill id."""
    skills_dir = workdir / SKILLS_DIRNAME
    if not skills_dir.is_dir():
        raise SkillIndexError(f"no skills directory: {skills_dir}")
    plugin = plugin_name(workdir)
    rows = [build_row(workdir, path, plugin) for path in iter_skill_files(workdir)]
    return sorted(rows, key=lambda row: (row.skill_id, row.path))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render(rows: list[SkillRow], plugin: str, output_rel: Path) -> str:
    """Render the index markdown. Deterministic — no timestamps, no host syntax."""
    link_base = os.path.relpath(".", output_rel.parent.as_posix() or ".")
    tally = {cls: sum(1 for row in rows if row.exposure == cls) for cls in EXPOSURE_CLASSES}

    lines: list[str] = [
        "<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr "
        "<46267523+tyroneross@users.noreply.github.com> | "
        "SPDX-License-Identifier: Apache-2.0 -->",
        f"<!-- {GENERATED_BANNER} -->",
        f"<!-- Source of truth: {SKILLS_DIRNAME}/**/SKILL.md frontmatter. -->",
        "<!-- Regenerate: python3 scripts/skill_index.py --workdir . --apply -->",
        "<!-- Verify in sync: python3 scripts/skill_index.py --workdir . --check -->",
        "",
        f"# Skill Index — {plugin}",
        "",
        "Routing table for any coding agent — Claude Code, Codex, Cursor, or",
        "anything else that reads plain markdown. Use it to pick the skill that",
        "owns a request, then read that skill's `SKILL.md` for the procedure.",
        "",
        "This file is generated from the skills' own frontmatter. Editing it by",
        "hand is pointless: the next `--apply` overwrites the change, and",
        "`--check` fails the build until it matches. To change a row, edit the",
        "skill's `SKILL.md`.",
        "",
        "Column meanings:",
        "",
        "- **Skill** — the canonical skill id, linked to its source file.",
        "- **When to use** — the skill's own `description`, in full, plus its"
        " `when_to_use:` block when the description states what the skill IS"
        " but not WHEN to reach for it. Not truncated: the disambiguator in"
        " this repo's house style comes last, and cutting it is what makes two"
        " similar skills indistinguishable to a router.",
        "- **Invocation** — how an agent reaches it. `internal` skills are not"
        " loaded directly by a user; the plugin entrypoint routes to them.",
        "- **Exposure** — `hidden` is `user-invocable: false`. `public` is"
        " `user-invocable: true` plus a non-empty `public-justification:` in the"
        " same frontmatter. `public-undeclared` is exposed without a stated"
        " reason: the harness resolves visibility as `userInvocable ?? true`, so"
        " a skill with no `user-invocable` field is public by default, not"
        " hidden.",
        "",
        f"**{len(rows)} skills** · {tally[PUBLIC]} public · "
        f"{tally[PUBLIC_UNDECLARED]} public-undeclared · {tally[HIDDEN]} hidden",
        "",
        "| Skill | When to use | Invocation | Exposure |",
        "| --- | --- | --- | --- |",
    ]

    for row in rows:
        target = f"{link_base}/{row.path}" if link_base != "." else row.path
        lines.append(
            f"| [`{escape_cell(row.skill_id)}`]({target}) "
            f"| {escape_cell(row.description)} "
            f"| {escape_cell(row.invocation)} "
            f"| {row.exposure} |"
        )

    warned = [row for row in rows if row.warning]
    if warned:
        lines += ["", "## Frontmatter warnings", ""]
        lines += [
            "These skills are listed but cannot be routed reliably. Fix the"
            " frontmatter, then regenerate.",
            "",
        ]
        for row in warned:
            lines.append(f"- `{row.path}` — {row.warning}")

    lines.append("")
    return "\n".join(lines)


def generate(workdir: Path, output_rel: Path) -> str:
    rows = discover(workdir)
    return render(rows, plugin_name(workdir), output_rel)


# ---------------------------------------------------------------------------
# Apply / check
# ---------------------------------------------------------------------------

def apply_index(workdir: Path, output_rel: Path) -> tuple[str, bool]:
    """Write the index. Returns (content, changed)."""
    content = generate(workdir, output_rel)
    target = workdir / output_rel
    previous = target.read_text(encoding="utf-8") if target.is_file() else None
    if previous == content:
        return content, False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return content, True


def check_index(workdir: Path, output_rel: Path) -> None:
    """Raise SkillIndexError when the on-disk index differs from a fresh render."""
    expected = generate(workdir, output_rel)
    target = workdir / output_rel
    if not target.is_file():
        raise SkillIndexError(
            f"skill index missing: {output_rel.as_posix()} — run with --apply"
        )
    actual = target.read_text(encoding="utf-8")
    if actual != expected:
        raise SkillIndexError(
            f"skill index is stale: {output_rel.as_posix()} — run with --apply"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def describe(workdir: Path, output_rel: Path) -> dict:
    rows = discover(workdir)
    return {
        "workdir": str(workdir.resolve()),
        "plugin": plugin_name(workdir),
        "index_path": output_rel.as_posix(),
        "count": len(rows),
        "public": sum(1 for row in rows if row.exposure == PUBLIC),
        "public_undeclared": sum(
            1 for row in rows if row.exposure == PUBLIC_UNDECLARED
        ),
        "hidden": sum(1 for row in rows if row.exposure == HIDDEN),
        "warnings": [
            {"path": row.path, "warning": row.warning} for row in rows if row.warning
        ],
        "skills": [asdict(row) for row in rows],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0].strip())
    parser.add_argument("--workdir", default=".", help="Plugin root to scan.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Index path, relative to --workdir.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Fail on index drift.")
    mode.add_argument("--apply", action="store_true", help="Write the index.")
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="Emit JSON.")
    fmt.add_argument("--plain", action="store_true", help="Emit plain text (default).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workdir = Path(args.workdir).expanduser().resolve()
    output_rel = Path(args.output)
    if output_rel.is_absolute():
        try:
            output_rel = output_rel.relative_to(workdir)
        except ValueError:
            print("error: --output must live inside --workdir", file=sys.stderr)
            return 1

    try:
        if args.check:
            check_index(workdir, output_rel)
            payload = describe(workdir, output_rel) | {"in_sync": True}
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                print(
                    f"skill index up to date: {output_rel.as_posix()} "
                    f"({payload['count']} skills)"
                )
            return 0

        if args.apply:
            _, changed = apply_index(workdir, output_rel)
            payload = describe(workdir, output_rel) | {"changed": changed}
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                verb = "written" if changed else "unchanged"
                print(
                    f"skill index {verb}: {output_rel.as_posix()} "
                    f"({payload['count']} skills)"
                )
            return 0

        payload = describe(workdir, output_rel)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"{payload['plugin']}: {payload['count']} skills")
            for row in payload["skills"]:
                print(f"{row['skill_id']}\t{row['exposure']}\t{row['path']}")
        return 0
    except SkillIndexError as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "in_sync": False}, indent=2))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
