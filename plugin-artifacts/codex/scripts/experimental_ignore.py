# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""experimental_ignore.py — make git state ENCODE the promotion decision.

build-loop AUTHORS skills and agents at runtime (Phase 6 Learn →
``self-improvement-architect`` → ``.build-loop/skills/experimental/<name>/SKILL.md``).
Those drafts are machine-written and unreviewed, so they must never be
committable. Promotion (``experimental/`` → ``active/``) is the human decision
point, and after promotion the artifact must become TRACKED — so ``git status``
shows the promotion rather than merely describing it.

This script writes the managed ``.gitignore`` block that produces that
behaviour, reusing the mechanism ``scripts/backlog.py`` already established for
consumer repos: rooted rules, a marker comment, an idempotent rewrite, and pure
text inspection with NO git invocation (host-agnostic; works on a non-repo tmp
dir).

The rules and WHY the ordering works
------------------------------------
git will not re-include a file if a PARENT DIRECTORY of that file is excluded —
it never descends into an excluded directory, so the negation is never even
evaluated. A blanket ``.build-loop/`` therefore defeats
``!/.build-loop/skills/active/**`` outright. The block re-opens each parent
level before re-closing the children:

  ROOT (emitted only when needed, see below)
    !/.build-loop/              re-open the root so git descends
    /.build-loop/*              re-close everything directly under it

  TIER (always emitted)
    !/.build-loop/skills/       re-open skills/ so git descends
    /.build-loop/skills/*       re-close its children (experimental/ included)
    /.build-loop/skills/experimental/   explicit: the artifact class we ignore
    !/.build-loop/skills/active/        re-open the promoted dir
    !/.build-loop/skills/active/**      re-include its contents
    (same five for agents/)

The ROOT pair is emitted ONLY when the file blanket-ignores ``.build-loop`` and
has not already re-opened it with ``!/.build-loop/``. When ``backlog.py adopt``
has already written its block, ``!/.build-loop/`` and ``/.build-loop/*`` are
present upstream and this block appends AFTER them — emitting ``/.build-loop/*``
again would land after ``!/.build-loop/backlog/`` and silently re-exclude the
backlog. Conditioning the ROOT pair is what keeps the two blocks composable.

Idempotency + drift: the managed block is located positionally by its marker,
lifted out, recomputed, and re-appended at the end of the file. Running twice
adds nothing. If another tool later appends a blanket ``.build-loop`` rule after
this block, the rebuilt text differs from the current text, ``--check`` reports
drift, and ``--apply`` moves the block back to the end.

Usage::

    experimental_ignore.py --check  [--workdir PATH] [--json|--plain]
    experimental_ignore.py --apply  [--workdir PATH] [--json|--plain]

Exit codes::

    0  compliant (--check), or write succeeded (--apply), or a soft error
    1  --check found drift (rules missing / out of order)

Soft errors (missing workdir, unreadable or undecodable ``.gitignore``) return
``ok: false`` with ``action: "error"`` and exit 0, and NEVER write — the script
is safe to wire into a hook without it becoming a new way to block work.

Pure Python stdlib. No third-party imports.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

MARKER = (
    "# build-loop experimental artifacts (added by `experimental_ignore.py` — "
    "promotion to active/ makes them tracked)"
)

# Re-open the runtime root so git descends into it, then re-close its children.
# Emitted only when the file blanket-ignores `.build-loop` and has not already
# re-opened it (see module docstring).
ROOT_RULES = (
    "!/.build-loop/",
    "/.build-loop/*",
)

# Per-tier rules. Always emitted. Rooted (leading `/`) so a checked-out sibling
# with its own nested `.build-loop/` is not affected — same reasoning as
# backlog.py's rooted-rule migration.
TIER_RULES = (
    "!/.build-loop/skills/",
    "/.build-loop/skills/*",
    "/.build-loop/skills/experimental/",
    "!/.build-loop/skills/active/",
    "!/.build-loop/skills/active/**",
    "!/.build-loop/agents/",
    "/.build-loop/agents/*",
    "/.build-loop/agents/experimental/",
    "!/.build-loop/agents/active/",
    "!/.build-loop/agents/active/**",
)

ALL_RULES = frozenset(ROOT_RULES) | frozenset(TIER_RULES)

# Patterns that swallow the whole runtime tree at directory level. Any of these
# stops git descending, so the tier negations need the ROOT pair to survive.
BLANKET_EXCLUDES = frozenset(
    {
        ".build-loop",
        ".build-loop/",
        ".build-loop/*",
        ".build-loop/**",
        "/.build-loop",
        "/.build-loop/",
        "/.build-loop/*",
        "/.build-loop/**",
    }
)

REOPEN_ROOT = frozenset({"!/.build-loop/", "!/.build-loop", "!.build-loop/", "!.build-loop"})


def strip_managed_block(lines: list[str]) -> list[str]:
    """Lift out every managed block, located POSITIONALLY by its marker.

    A block is the marker line plus the run of following lines that are managed
    rules. Deliberately positional: filtering by rule text alone would also
    delete backlog.py's ``!/.build-loop/`` and ``/.build-loop/*``, which this
    block depends on and does not own.
    """
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() != MARKER:
            out.append(lines[i])
            i += 1
            continue
        i += 1  # drop the marker
        while i < len(lines) and lines[i].strip() in ALL_RULES:
            i += 1  # drop the block body
    return out


def desired_rules(base_lines: list[str]) -> tuple[tuple[str, ...], bool]:
    """Compute the rules this file needs, given its content minus our block.

    Returns ``(rules, needs_root)``. The ROOT pair is required only when a
    blanket exclusion is present AND nothing has already re-opened the root.
    """
    stripped = {ln.strip() for ln in base_lines}
    blanket = bool(stripped & BLANKET_EXCLUDES)
    already_reopened = bool(stripped & REOPEN_ROOT)
    needs_root = blanket and not already_reopened
    rules = (ROOT_RULES + TIER_RULES) if needs_root else TIER_RULES
    return rules, needs_root


def render(base_lines: list[str], rules: tuple[str, ...]) -> str:
    """Rebuild the file with the managed block appended at the end."""
    block = "\n".join([MARKER, *rules]) + "\n"
    head = "\n".join(base_lines).rstrip()
    if not head:
        return block
    return head + "\n\n" + block


def evaluate(workdir: Path, apply: bool) -> tuple[dict[str, Any], int]:
    """Inspect (and optionally fix) ``<workdir>/.gitignore``. Never raises."""
    report: dict[str, Any] = {
        "ok": True,
        "workdir": str(workdir),
        "mode": "apply" if apply else "check",
    }
    if not workdir.is_dir():
        report.update(ok=False, action="error", reason="workdir_not_a_directory")
        return report, 0

    gi = workdir / ".gitignore"
    report["gitignore"] = str(gi)
    existed = gi.is_file()
    if gi.exists() and not existed:
        report.update(ok=False, action="error", reason="gitignore_not_a_regular_file")
        return report, 0

    lines: list[str] = []
    current = ""
    if existed:
        try:
            current = gi.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report.update(
                ok=False,
                action="error",
                reason="gitignore_unreadable",
                detail=type(exc).__name__,
            )
            return report, 0
        lines = current.splitlines()

    base_lines = strip_managed_block(lines)
    rules, needs_root = desired_rules(base_lines)
    rebuilt = render(base_lines, rules)

    present = {ln.strip() for ln in lines}
    report.update(
        needs_root=needs_root,
        rules=list(rules),
        added=[r for r in rules if r not in present],
        gitignore_existed=existed,
    )

    if existed and rebuilt == current:
        report["action"] = "already_compliant"
        report["applied"] = False
        return report, 0

    if not apply:
        report["action"] = "would_create" if not existed else "would_apply"
        report["applied"] = False
        return report, 1

    try:
        gi.write_text(rebuilt, encoding="utf-8")
    except OSError as exc:
        report.update(
            ok=False, action="error", reason="gitignore_unwritable", detail=type(exc).__name__
        )
        return report, 0

    report["action"] = "created" if not existed else "applied"
    report["applied"] = True
    return report, 0


def _plain(report: dict[str, Any]) -> str:
    head = f"{report.get('action', 'unknown')}  {report.get('gitignore', report['workdir'])}"
    if not report.get("ok", True):
        return f"{head}\n  reason: {report.get('reason', 'unknown')}"
    out = [head, f"  needs_root: {report.get('needs_root')}"]
    added = report.get("added") or []
    out.append(f"  added ({len(added)}):")
    out.extend(f"    {r}" for r in added)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="experimental_ignore.py",
        description=(
            "Ensure .build-loop/{skills,agents}/experimental/ are gitignored and "
            "the matching active/ dirs are tracked."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Report drift; exit 1 if not compliant.")
    mode.add_argument("--apply", action="store_true", help="Write the managed block.")
    parser.add_argument("--workdir", default=None, help="Repo root (default: $PWD).")
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="JSON output (default).")
    fmt.add_argument("--plain", action="store_true", help="Human-readable output.")
    args = parser.parse_args(argv)

    try:
        workdir = Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        workdir = workdir.resolve()
    except OSError:
        print(json.dumps({"ok": False, "action": "error", "reason": "workdir_unresolvable"}))
        return 0

    report, code = evaluate(workdir, apply=bool(args.apply))
    print(_plain(report) if args.plain else json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
