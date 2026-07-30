#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
stamp_skill_frontmatter.py — deterministic `user-invocable` stamper for SKILL.md files.

WHY
    The Claude Code harness computes `userInvocable ?? true`, so a SKILL.md that
    carries NO `user-invocable:` field is PUBLIC. The default is fail-open and
    skills are born exposed. A prompt instruction is not a control; this script is
    the harness half — every authoring and promotion path calls it, so the field
    exists because the pipeline put it there, not because someone remembered.

CONTRACT (one file, one verdict — the script judges a FILE, never a policy scope)
    frontmatter has no `user-invocable:`
        --apply  -> insert `user-invocable: false` as the LAST frontmatter line
                    (status `stamped`, exit 0)
        --check  -> status `would_stamp`, exit 1
    `user-invocable: false`
        -> status `compliant`, byte-identical no-op, exit 0
    `user-invocable: true` WITH a `public-justification:` field
        -> status `approved_exception`, untouched, exit 0
    `user-invocable: true` WITHOUT `public-justification:`
        -> status `violation`, untouched, exit 1 in BOTH modes.
           `--apply` deliberately does NOT flip it to false: someone decided to
           expose this skill, and silently reversing a deliberate decision is a
           worse failure than stopping. A human resolves it, either by adding
           `public-justification:` or by setting the field to false.
    any other value (`yes`, `1`, `False`, empty, ...)
        -> status `violation` (unrecognized value), untouched, exit 1.
           Matching is CASE-SENSITIVE on purpose. `test_agent_surface_policy.py`
           compares the literal string `false`, so a YAML-valid `False` would pass
           the harness and fail the repo gate. Surfacing it beats accepting it,
           and rewriting it would edit a field someone deliberately typed.
           An EMPTY value (`user-invocable:` = YAML null) also lands here: the
           harness reads null as PUBLIC, and inserting a second field would create
           a duplicate key, so this is reported rather than stamped.
    missing / malformed frontmatter, undecodable bytes, BOM before the opening
    `---`
        -> status `malformed` + a reason, NEVER a write, exit 1. Fail soft: the
           file is left exactly as found and the run continues to the next path.

BYTE PRESERVATION
    Files are read with `read_bytes()` and decoded manually — `read_text()` would
    silently translate CRLF to LF and rewrite every line of a CRLF file. Lines are
    split only after `\\n` (not `str.splitlines()`, which also breaks on \\v, \\f,
    \\x1c and U+2028). Only ONE element is inserted into the line list, immediately
    before the closing `---`; every other byte — field order, comments, blank
    lines, the entire body including any `---` sequences inside it, and the
    presence or absence of a trailing newline — is passed through untouched. The
    inserted line copies the EOL style of the frontmatter it joins. Writes are
    atomic (temp file in the same directory + `os.replace`), so a partial file is
    never observable. Running twice produces identical bytes.

COMPATIBILITY
    Frontmatter detection and field lookup mirror `scripts/test_agent_surface_policy.py`
    (FRONTMATTER_RE / USER_INVOCABLE_RE) — the same files are read by both, so a
    file this script reports as `compliant` is a file that test reads as `'false'`.

CLI usage
    python3 scripts/stamp_skill_frontmatter.py --check PATH [PATH ...]
    python3 scripts/stamp_skill_frontmatter.py --apply --workdir . --json

    --check     report only, never write (default)
    --apply     insert the missing field; still refuses to resolve a violation
    --workdir   walk DIR for **/SKILL.md instead of (or in addition to) PATHs.
                Skips `.git`, `node_modules`, `.venv`, `__pycache__` and any
                `worktrees` directory — a git worktree is another agent's
                checkout, not this tree's business.
    --json      emit a JSON envelope
    --plain     emit one human-readable line per file (default)

SCOPE
    This script judges a FILE, not a policy scope. It carries no allowlist and
    knows nothing about which skills are legitimately public, so a caller that
    walks a whole repo will see every `user-invocable: true` file — including
    deliberate host-entrypoint wrappers such as `codex-skills/build-loop/`. Give
    those a `public-justification:`, or pass an explicitly scoped path list.

Exit codes
    0  every file is compliant, an approved exception, or was stamped
    1  at least one violation, malformed file, or (in --check) pending stamp
    2  usage error (no paths given)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIELD_NAME = "user-invocable"
DEFAULT_VALUE = "false"
JUSTIFICATION_FIELD = "public-justification"

#: A frontmatter delimiter line: ``---`` plus optional trailing blanks, then EOL.
DELIMITER_RE = re.compile(r"---[ \t]*\r?\n\Z")
#: Kept identical in shape to ``test_agent_surface_policy.USER_INVOCABLE_RE``.
USER_INVOCABLE_RE = re.compile(rf"^{FIELD_NAME}:\s*(.*?)\s*$", re.MULTILINE)
JUSTIFICATION_RE = re.compile(rf"^{JUSTIFICATION_FIELD}:\s*(.*?)\s*$", re.MULTILINE)

# Directories never walked in --workdir mode. `worktrees` is excluded because a
# git worktree is a SEPARATE checkout (`.build-loop/worktrees/`, `.claude/worktrees/`,
# `build-loop.worktrees/`) — a walk that crossed into one would report, and in
# --apply mode WRITE INTO, another agent's isolated tree.
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "worktrees"}

STATUS_COMPLIANT = "compliant"
STATUS_STAMPED = "stamped"
STATUS_WOULD_STAMP = "would_stamp"
STATUS_APPROVED_EXCEPTION = "approved_exception"
STATUS_VIOLATION = "violation"
STATUS_MALFORMED = "malformed"

#: Statuses that make the overall run fail.
FAILING = {STATUS_WOULD_STAMP, STATUS_VIOLATION, STATUS_MALFORMED}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StampResult:
    """Verdict for a single SKILL.md."""

    path: str
    status: str
    reason: str
    changed: bool = False
    value: str | None = None
    justified: bool = False

    @property
    def ok(self) -> bool:
        return self.status not in FAILING

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "status": self.status,
            "reason": self.reason,
            "changed": self.changed,
            "value": self.value,
            "justified": self.justified,
            "ok": self.ok,
        }


# ---------------------------------------------------------------------------
# Parsing helpers (byte-exact)
# ---------------------------------------------------------------------------

def split_lines_keepends(text: str) -> list[str]:
    """Split *text* after every ``\\n``, keeping the terminator.

    ``str.splitlines()`` also splits on \\v, \\f, \\x1c-\\x1e, U+2028 and U+2029,
    which would silently rewrite a body containing those bytes. This split is
    exact: ``"".join(split_lines_keepends(t)) == t`` for every string.
    """
    if not text:
        return []
    return re.split(r"(?<=\n)", text)


def _is_delimiter(line: str) -> bool:
    return DELIMITER_RE.fullmatch(line) is not None


def _eol_of(line: str) -> str:
    return "\r\n" if line.endswith("\r\n") else "\n"


def find_frontmatter(text: str) -> tuple[list[str], int] | None:
    """Return ``(lines, closing_index)`` or ``None`` when there is no frontmatter.

    ``closing_index`` is the index of the closing ``---`` line, so the frontmatter
    fields are ``lines[1:closing_index]`` and the body is ``lines[closing_index + 1:]``.
    An unterminated block, or a closing ``---`` without a trailing newline, counts
    as no frontmatter — matching what the surface-policy test's FRONTMATTER_RE sees.
    """
    lines = split_lines_keepends(text)
    if not lines or not _is_delimiter(lines[0]):
        return None
    for index in range(1, len(lines)):
        if _is_delimiter(lines[index]):
            return lines, index
    return None


def _classify_value(raw: str | None) -> str | None:
    """Normalize a frontmatter scalar to ``'false'`` / ``'true'`` / ``None``.

    Unquoting mirrors ``test_agent_surface_policy.read_user_invocable``. The
    comparison is case-SENSITIVE for the same reason: that test asserts the
    literal string ``false``, so ``False`` is not interchangeable here.
    """
    if raw is None:
        return None
    cleaned = raw.strip().strip('"').strip("'").strip()
    if cleaned in {"false", "true"}:
        return cleaned
    return None


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def evaluate(text: str) -> tuple[str, str, str | None, bool, str | None]:
    """Judge *text*, returning ``(status, reason, value, justified, new_text)``.

    ``new_text`` is non-``None`` only when a stamp is warranted; it is the exact
    content to write. This function is pure — it performs no I/O.
    """
    parsed = find_frontmatter(text)
    if parsed is None:
        if text.startswith("\ufeff"):
            return (STATUS_MALFORMED, "byte-order mark precedes the opening `---`",
                    None, False, None)
        return (STATUS_MALFORMED, "no `---` frontmatter block at the top of the file",
                None, False, None)

    lines, closing = parsed
    block = "".join(lines[1:closing])
    match = USER_INVOCABLE_RE.search(block)
    justified = JUSTIFICATION_RE.search(block) is not None

    if match is None:
        eol = _eol_of(lines[closing - 1] if closing > 1 else lines[0])
        stamped = lines[:closing] + [f"{FIELD_NAME}: {DEFAULT_VALUE}{eol}"] + lines[closing:]
        return (STATUS_STAMPED, f"inserted `{FIELD_NAME}: {DEFAULT_VALUE}`",
                None, justified, "".join(stamped))

    raw = match.group(1)
    value = _classify_value(raw)

    if value == "false":
        return (STATUS_COMPLIANT, "already hidden", "false", justified, None)

    if value == "true":
        if justified:
            return (STATUS_APPROVED_EXCEPTION,
                    f"public by explicit `{JUSTIFICATION_FIELD}:`", "true", True, None)
        return (STATUS_VIOLATION,
                f"`{FIELD_NAME}: true` without a `{JUSTIFICATION_FIELD}:` field — "
                "add the justification or set the field to false",
                "true", False, None)

    return (STATUS_VIOLATION,
            f"unrecognized `{FIELD_NAME}` value {raw.strip()!r}; expected the literal "
            f"lowercase `false`, or `true` plus a `{JUSTIFICATION_FIELD}:` field",
            raw.strip() or None, justified, None)


def _atomic_write(path: Path, data: bytes) -> None:
    """Replace *path* with *data* atomically — a partial file is never visible.

    The original file mode is carried over; ``mkstemp`` creates 0600, which would
    otherwise silently tighten permissions on every stamped SKILL.md.
    """
    mode = path.stat().st_mode
    handle, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.chmod(tmp, mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def stamp_file(path: Path, apply: bool = False) -> StampResult:
    """Evaluate (and in ``apply`` mode fix) a single SKILL.md. Never raises."""
    label = str(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return StampResult(label, STATUS_MALFORMED, f"unreadable: {exc.strerror or exc}")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return StampResult(label, STATUS_MALFORMED, f"not valid UTF-8: {exc.reason}")

    status, reason, value, justified, new_text = evaluate(text)

    if status != STATUS_STAMPED:
        return StampResult(label, status, reason, False, value, justified)

    if not apply:
        return StampResult(label, STATUS_WOULD_STAMP,
                           f"missing `{FIELD_NAME}:` — `--apply` would insert "
                           f"`{FIELD_NAME}: {DEFAULT_VALUE}`",
                           False, None, justified)

    assert new_text is not None  # guaranteed by evaluate() for STATUS_STAMPED
    try:
        _atomic_write(path, new_text.encode("utf-8"))
    except OSError as exc:
        return StampResult(label, STATUS_MALFORMED, f"write failed: {exc.strerror or exc}",
                           False, None, justified)
    return StampResult(label, STATUS_STAMPED, reason, True, DEFAULT_VALUE, justified)


def discover(workdir: Path) -> list[Path]:
    """Return every ``SKILL.md`` under *workdir*, sorted, skipping SKIP_DIRS."""
    found: list[Path] = []
    for root, dirs, files in os.walk(workdir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        if "SKILL.md" in files:
            found.append(Path(root) / "SKILL.md")
    return sorted(found)


def stamp_paths(paths: list[Path], apply: bool = False) -> list[StampResult]:
    """Evaluate every path in order. Public entrypoint for authoring/promotion paths."""
    return [stamp_file(path, apply=apply) for path in paths]


def summarize(results: list[StampResult], mode: str) -> dict:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return {
        "ok": all(result.ok for result in results),
        "mode": mode,
        "checked": len(results),
        "counts": counts,
        "results": [result.to_dict() for result in results],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=("Ensure every SKILL.md declares `user-invocable`. The Claude Code "
                     "harness treats a missing field as PUBLIC, so the field must be "
                     "written by the pipeline, not by memory."),
    )
    p.add_argument("paths", nargs="*", type=Path, metavar="PATH",
                   help="One or more SKILL.md paths.")
    p.add_argument("--workdir", type=Path, default=None, metavar="DIR",
                   help="Walk DIR for **/SKILL.md (combined with any PATHs given).")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="Report only, never write (default).")
    mode.add_argument("--apply", action="store_true",
                      help="Insert the missing field; violations are still refused.")
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="Emit a JSON envelope.")
    fmt.add_argument("--plain", action="store_true",
                     help="Emit one line per file (default).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    paths: list[Path] = list(args.paths)
    if args.workdir is not None:
        paths.extend(discover(args.workdir))
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)

    if not unique:
        _build_parser().error("no SKILL.md paths given (pass PATH... or --workdir DIR)")

    mode = "apply" if args.apply else "check"
    results = stamp_paths(unique, apply=args.apply)
    envelope = summarize(results, mode)

    if args.json:
        print(json.dumps(envelope, indent=2))
    else:
        for result in results:
            print(f"{result.status:<18} {result.path}  — {result.reason}")
        counts = ", ".join(f"{k}={v}" for k, v in sorted(envelope["counts"].items()))
        print(f"\n{mode}: {envelope['checked']} file(s); {counts or 'nothing to report'}")

    return 0 if envelope["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
