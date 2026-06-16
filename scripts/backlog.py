# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""backlog.py — host-agnostic, multi-repo BACKLOG SYSTEM for build-loop.

A backlog item is plain Markdown + YAML frontmatter (host-neutral: Claude,
Codex, or any other coding agent reads it). The store is filesystem-first —
``grep`` + frontmatter, NO vector / graph / DB (validated by the Letta
filesystem-memory benchmark and the "don't over-engineer" principle).

Two layers:
  * ITEMS are canonical truth        — ``.build-loop/backlog/items/<ID>.md``
  * INDEX is a derived, regenerable view — ``.build-loop/backlog/INDEX.md``
``sync`` regenerates INDEX deterministically from the items, consolidates
done/dropped items into ``archive/`` (never deletes), flags items past their
TTL (``review_by``), and mirrors a per-item copy into the per-user long-term
memory (``build-loop-memory/projects/<slug>/backlog/``) so the cross-repo view
stays current. The per-repo backlog travels with the repo (committed,
team-shareable); the personal-memory mirror aggregates across all the user's
repos.

Pure Python stdlib. No third-party imports (asserted by test_backlog.py). A
tiny hand-rolled YAML reader/writer handles the flat-plus-list-plus-one-nested
frontmatter this schema uses — we do NOT pull in PyYAML.

Subcommands::

    backlog.py new   --repo <path> --area <a> --type <t> --title "..."
                     [--priority P2] [--status open] [--gated none]
                     [--entities a,b] [--provenance-source ...] [--provenance-ref ...]
                     [--evidence p1,p2] [--owner ...] [--review-days 30] [--today YYYY-MM-DD]
    backlog.py sync  --repo <path> [--today YYYY-MM-DD] [--no-mirror]
    backlog.py list  --repo <path> [--status ...] [--area ...] [--priority ...]

All commands print a JSON summary on stdout (``list`` prints a text table
unless ``--json``).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# Schema constants
# ----------------------------------------------------------------------------

# Item schema version. Bump when the frontmatter contract changes in a way a
# reader must know about. The reader is deliberately TOLERANT (forward/back
# compatible): it DEFAULTS missing fields and IGNORES unknown fields, so an item
# written by a newer OR older build-loop still reads cleanly. This is the
# download/upgrade-safety contract — a downloaded item never crashes a reader
# just because the two sides are a version apart.
SCHEMA_VERSION = 1

STATUS_VALUES = ("open", "in-progress", "blocked", "deferred", "done", "dropped")
PRIORITY_VALUES = ("P0", "P1", "P2", "P3")
TYPE_VALUES = ("feature", "fix", "debt", "infra", "decision", "cleanup", "research")
GATED_VALUES = ("none", "prod-deploy", "db-migration", "infra", "product-decision")

# Items in these statuses are consolidated to archive/ by `sync`.
ARCHIVE_STATUSES = ("done", "dropped")

# Frontmatter field order — deterministic INDEX + item rendering depend on a
# stable key order.
FIELD_ORDER = (
    "id", "schema_version", "title", "status", "priority", "type", "area",
    "entities", "gated", "provenance", "evidence", "supersedes", "superseded_by",
    "created", "updated", "review_by", "owner",
)

# Default values for every schema field. The tolerant reader fills these in for
# any field a (possibly older) item omits, so downstream code can rely on the
# keys existing. Mutable defaults are produced fresh per call via item_defaults().
def item_defaults() -> dict[str, Any]:
    """Return a fresh dict of default values for every known schema field.

    Used by ``read_item`` to fill in fields an older/foreign item may omit.
    Unknown fields present on the item are preserved as-is (forward compat);
    missing known fields are defaulted here (backward compat).
    """
    return {
        "id": None,
        "schema_version": SCHEMA_VERSION,
        "title": "",
        "status": "open",
        "priority": "P2",
        "type": "debt",
        "area": "general",
        "entities": [],
        "gated": "none",
        "provenance": {},
        "evidence": [],
        "supersedes": None,
        "superseded_by": None,
        "created": None,
        "updated": None,
        "review_by": None,
        "owner": "unassigned",
    }

DEFAULT_REVIEW_DAYS = 30

# Item-ID shape: <PREFIX>-<AREA>-<NNN>. Used to scope the memory-mirror prune so
# it only ever deletes files this system wrote, never a hand-dropped note.
_ITEM_ID_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+-\d{3,}$")

# Body section scaffold for a fresh item.
_BODY_TEMPLATE = """## Context
{context}

## Acceptance
- <verifiable condition 1>

## Notes
{notes}
"""


# ----------------------------------------------------------------------------
# Date helper — harness-safe (accepts an injected --today; never assumes the
# clock is callable in a sandbox).
# ----------------------------------------------------------------------------

def resolve_today(today_arg: str | None) -> str:
    """Resolve the working date as YYYY-MM-DD.

    Precedence: explicit --today arg > BACKLOG_TODAY env var > system date.
    Validates the format so a malformed value fails loudly instead of
    poisoning frontmatter.
    """
    raw = today_arg or os.environ.get("BACKLOG_TODAY")
    if raw:
        raw = raw.strip()
        try:
            _dt.date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"--today/BACKLOG_TODAY must be YYYY-MM-DD, got {raw!r}") from exc
        return raw
    return _dt.date.today().isoformat()


def add_days(date_str: str, days: int) -> str:
    """Return date_str + days as YYYY-MM-DD."""
    base = _dt.date.fromisoformat(date_str)
    return (base + _dt.timedelta(days=days)).isoformat()


# ----------------------------------------------------------------------------
# Repo / slug helpers
# ----------------------------------------------------------------------------

def project_slug(repo: Path) -> str:
    """Derive a stable project slug from the repo path.

    Precedence: ``BACKLOG_SLUG`` env var (set by ``--slug``) > the repo
    directory's basename, lowercased with non-alphanumerics collapsed to single
    hyphens. The override exists because the working-dir basename is the wrong
    identity inside a git worktree or CI checkout (e.g. a worktree at
    ``/tmp/atomize-backlog`` still belongs to project ``atomize-ai``). The slug
    drives the ID prefix, the INDEX header, and the personal-memory mirror path,
    so pinning it keeps those stable across checkouts. No git calls required.
    """
    override = os.environ.get("BACKLOG_SLUG")
    raw = override if override else repo.resolve().name
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug or "repo"


def area_slug(area: str) -> str:
    """Uppercased, hyphen-collapsed area token used in the item ID."""
    token = re.sub(r"[^a-z0-9]+", "-", (area or "").lower()).strip("-")
    return (token or "general").upper()


def proj_id_prefix(repo: Path) -> str:
    """ID prefix from the slug: alnum uppercased (e.g. atomize-ai -> ATOM... )."""
    slug = project_slug(repo)
    # Use the leading alphanumeric run, uppercased, capped at 6 chars for
    # readable IDs (ATOMIZE-AI -> ATOM). Falls back to the full cleaned slug.
    cleaned = re.sub(r"[^A-Za-z0-9]", "", slug).upper()
    return (cleaned[:4] or "REPO")


# ----------------------------------------------------------------------------
# Minimal YAML frontmatter reader/writer (stdlib only)
# ----------------------------------------------------------------------------
#
# The schema is intentionally shallow: scalars, one-level inline lists
# (`[a, b]`), and a single one-level nested mapping (`provenance: {source, ref}`).
# We parse exactly that — not arbitrary YAML — which keeps us off PyYAML.

def _parse_scalar(raw: str) -> Any:
    """Parse a YAML scalar: null, bool, quoted string, or bare string."""
    s = raw.strip()
    if s == "" or s in ("null", "~"):
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        inner = s[1:-1]
        # Reverse the escaping applied by _dump_scalar on write. Double-quoted
        # values escape `\` then `"`; un-escape in the opposite order so a value
        # round-trips byte-for-byte and a backslash does not accumulate across
        # parse->render cycles.
        if s[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return s


def _split_top_level(inner: str) -> list[str]:
    """Split on commas that are NOT inside a quoted span.

    Tracks single/double quote state so a quoted element containing a comma
    stays one field. Backslash-escaped quote chars inside a double-quoted span
    do not toggle the quote state.
    """
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(inner):
        ch = inner[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(inner):
                buf.append(ch)
                buf.append(inner[i + 1])
                i += 2
                continue
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _parse_inline_list(raw: str) -> list[Any]:
    """Parse `[a, b, c]` into a list of scalars. `[]` -> [].

    Splits on top-level commas only, so a quoted element containing a comma
    (e.g. `"a,b"`) round-trips as one element.
    """
    inner = raw.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    inner = inner.strip()
    if not inner:
        return []
    return [_parse_scalar(part) for part in _split_top_level(inner) if part.strip() != ""]


def _parse_inline_map(raw: str) -> dict[str, Any]:
    """Parse `{ source: x, ref: y }` into a dict of scalars. `{}` -> {}."""
    inner = raw.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    inner = inner.strip()
    if not inner:
        return {}
    out: dict[str, Any] = {}
    for part in inner.split(","):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        out[k.strip()] = _parse_scalar(v)
    return out


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown doc into (frontmatter_dict, body_str).

    Supports flat scalars, inline lists, inline maps, and a block-style nested
    map (``provenance:`` followed by indented ``  source: ...`` lines). Returns
    ({}, full_text) when no frontmatter fence is present. Never raises on a
    well-formed-enough file; unknown lines are skipped.
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    # First line is the opening '---'. Find the closing fence.
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    fm_lines = lines[1:end]
    body = "\n".join(lines[end + 1:])
    if body and not body.endswith("\n"):
        body += "\n"

    data: dict[str, Any] = {}
    i = 0
    while i < len(fm_lines):
        line = fm_lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        # Indented line with no parent handled inside block-map branch below.
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, rest = m.group(1), m.group(2)
        rest_stripped = rest.strip()
        if rest_stripped.startswith("[") :
            data[key] = _parse_inline_list(rest_stripped)
        elif rest_stripped.startswith("{"):
            data[key] = _parse_inline_map(rest_stripped)
        elif rest_stripped == "":
            # Could be a block-style nested map: peek at indented children.
            children: dict[str, Any] = {}
            j = i + 1
            while j < len(fm_lines) and re.match(r"^\s+\S", fm_lines[j]):
                cm = re.match(r"^\s+([A-Za-z_][\w-]*):\s*(.*)$", fm_lines[j])
                if cm:
                    children[cm.group(1)] = _parse_scalar(cm.group(2))
                j += 1
            if children:
                data[key] = children
                i = j
                continue
            data[key] = None
        else:
            data[key] = _parse_scalar(rest_stripped)
        i += 1
    return data, body


def read_item(text: str) -> tuple[dict[str, Any], str]:
    """Tolerant item read: parse frontmatter, then DEFAULT missing known fields
    and PRESERVE unknown fields.

    This is the download/upgrade-safety contract in one place. An item written
    by an older build-loop (missing newer fields like ``schema_version``) reads
    back with those fields defaulted; an item written by a NEWER build-loop
    (carrying fields this version doesn't know) reads back with those extra
    fields intact and untouched. Either way the read never raises on a
    well-formed file. Returns ``(item_dict, body)``.
    """
    fm, body = parse_frontmatter(text)
    merged = item_defaults()
    merged.update(fm)  # explicit values (incl. unknown future fields) win
    return merged, body


def _dump_scalar(value: Any) -> str:
    """Render a scalar for frontmatter output (deterministic)."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    s = str(value)
    # Quote only when the value could be misread (leading special char, colon+space,
    # a delimiter that would corrupt inline-list/map parsing, an embedded quote or
    # backslash, or a YAML-significant bare word). Keep plain when safe for
    # readable diffs.
    if s == "":
        return '""'
    needs_quote = (
        s[0] in "[]{}#&*!|>'\"%@`,?:-" or
        ": " in s or
        any(c in s for c in (",", "[", "]", "{", "}", '"', "\\")) or
        s.strip() != s or
        s in ("null", "true", "false", "~")
    )
    if needs_quote:
        # Escape backslash first, then the quote char, so _parse_scalar can
        # reverse it in the opposite order without accumulating backslashes.
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _dump_inline_list(value: list[Any]) -> str:
    if not value:
        return "[]"
    return "[" + ", ".join(_dump_scalar(v) for v in value) + "]"


def render_frontmatter(data: dict[str, Any]) -> str:
    """Render the item dict to a deterministic YAML frontmatter block.

    Keys are emitted in FIELD_ORDER; any extra keys follow in sorted order.
    `provenance` renders as a block map (readable). Lists render inline.
    """
    out: list[str] = ["---"]
    ordered_keys = [k for k in FIELD_ORDER if k in data]
    extra_keys = sorted(k for k in data if k not in FIELD_ORDER)
    for key in ordered_keys + extra_keys:
        value = data[key]
        if key == "provenance" and isinstance(value, dict):
            out.append("provenance:")
            for sub in ("source", "ref"):
                if sub in value:
                    out.append(f"  {sub}: {_dump_scalar(value[sub])}")
            for sub in sorted(k for k in value if k not in ("source", "ref")):
                out.append(f"  {sub}: {_dump_scalar(value[sub])}")
        elif isinstance(value, list):
            out.append(f"{key}: {_dump_inline_list(value)}")
        elif isinstance(value, dict):
            out.append(f"{key}: {{{', '.join(f'{k}: {_dump_scalar(v)}' for k, v in value.items())}}}")
        else:
            out.append(f"{key}: {_dump_scalar(value)}")
    out.append("---")
    return "\n".join(out)


# ----------------------------------------------------------------------------
# Item filesystem operations
# ----------------------------------------------------------------------------

def backlog_root(repo: Path) -> Path:
    return repo / ".build-loop" / "backlog"


def items_dir(repo: Path) -> Path:
    return backlog_root(repo) / "items"


def archive_dir(repo: Path) -> Path:
    return backlog_root(repo) / "archive"


def ensure_dirs(repo: Path) -> None:
    items_dir(repo).mkdir(parents=True, exist_ok=True)
    archive_dir(repo).mkdir(parents=True, exist_ok=True)


def _item_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.md"))


def load_items(repo: Path, include_archive: bool = False) -> list[dict[str, Any]]:
    """Load all item frontmatter dicts (+ _path, _body) from items/ (and archive/)."""
    items: list[dict[str, Any]] = []
    dirs = [items_dir(repo)]
    if include_archive:
        dirs.append(archive_dir(repo))
    for d in dirs:
        for path in _item_files(d):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fm, body = parse_frontmatter(text)
            fm["_path"] = str(path)
            fm["_body"] = body
            fm["_archived"] = (d == archive_dir(repo))
            items.append(fm)
    return items


def next_counter(repo: Path, area: str) -> int:
    """Highest existing NNN for this PROJ-AREA across items/ AND archive/, +1."""
    prefix = f"{proj_id_prefix(repo)}-{area_slug(area)}-"
    highest = 0
    for d in (items_dir(repo), archive_dir(repo)):
        for path in _item_files(d):
            try:
                fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            iid = str(fm.get("id", ""))
            if iid.startswith(prefix):
                tail = iid[len(prefix):]
                if tail.isdigit():
                    highest = max(highest, int(tail))
    return highest + 1


def make_item_id(repo: Path, area: str, counter: int) -> str:
    return f"{proj_id_prefix(repo)}-{area_slug(area)}-{counter:03d}"


# Bounded retry cap for the atomic-create race loop. 50 is far above any
# realistic concurrent-`new` fan-out (a handful of agents), yet still terminates
# instead of spinning forever if something pathological keeps colliding.
_CREATE_MAX_ATTEMPTS = 50


def atomic_create_item(repo: Path, area: str, render: "Any") -> tuple[str, Path]:
    """Allocate the next sequential ID and create its file ATOMICALLY.

    The naive read-max-then-write path is a TOCTOU race: N concurrent ``new``
    processes all read the same highest-NNN, all compute the same next ID, and
    all write the same file — N-1 silently clobber each other (reproduced: 6
    concurrent creates → only 2 survivors). This breaks the core multi-agent
    use case (Claude + Codex / parallel fan-out adding items at once).

    Fix: the filesystem is the lock. We ``os.open`` the candidate path with
    ``O_CREAT | O_EXCL`` so creation fails loudly (``FileExistsError``) if any
    peer already took that ID. On collision we recompute max-NNN+1 and retry,
    bounded to ``_CREATE_MAX_ATTEMPTS``. IDs stay sequential and readable.

    ``render`` is a callable ``(item_id: str) -> str`` returning the full file
    text — deferred so the ID (and any ID-derived body) is only materialised
    once we know which ID we actually won.

    Returns ``(item_id, path)``. Raises ``RuntimeError`` if every attempt within
    the cap lost the race (pathological contention).
    """
    items = items_dir(repo)
    items.mkdir(parents=True, exist_ok=True)
    last_exc: OSError | None = None
    for _ in range(_CREATE_MAX_ATTEMPTS):
        counter = next_counter(repo, area)
        item_id = make_item_id(repo, area, counter)
        path = items / f"{item_id}.md"
        try:
            # O_EXCL makes this the atomic test-and-set: exactly one concurrent
            # caller can create a given path; the rest raise FileExistsError and
            # loop to recompute a fresh (higher) counter.
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            last_exc = exc
            continue
        try:
            os.write(fd, render(item_id).encode("utf-8"))
        finally:
            os.close(fd)
        return item_id, path
    raise RuntimeError(
        f"atomic_create_item exhausted {_CREATE_MAX_ATTEMPTS} attempts for "
        f"area {area!r} under contention"
    ) from last_exc


# ----------------------------------------------------------------------------
# `new`
# ----------------------------------------------------------------------------

def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def cmd_new(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(args.repo)
    ensure_dirs(repo)
    today = resolve_today(args.today)
    review_days = args.review_days if args.review_days is not None else DEFAULT_REVIEW_DAYS

    if args.status not in STATUS_VALUES:
        raise ValueError(f"--status must be one of {STATUS_VALUES}, got {args.status!r}")
    if args.priority not in PRIORITY_VALUES:
        raise ValueError(f"--priority must be one of {PRIORITY_VALUES}, got {args.priority!r}")
    if args.type not in TYPE_VALUES:
        raise ValueError(f"--type must be one of {TYPE_VALUES}, got {args.type!r}")
    if args.gated not in GATED_VALUES:
        raise ValueError(f"--gated must be one of {GATED_VALUES}, got {args.gated!r}")

    provenance: dict[str, Any] = {}
    if args.provenance_source or args.provenance_ref:
        provenance = {
            "source": args.provenance_source or None,
            "ref": args.provenance_ref or None,
        }

    body = _BODY_TEMPLATE.format(
        context=(args.context or "<why this matters / what's the situation>"),
        notes=(args.notes or "<additional detail>"),
    )

    def _render(item_id: str) -> str:
        data: dict[str, Any] = {
            "id": item_id,
            "schema_version": SCHEMA_VERSION,
            "title": args.title,
            "status": args.status,
            "priority": args.priority,
            "type": args.type,
            "area": args.area,
            "entities": _csv(args.entities),
            "gated": args.gated,
            "provenance": provenance,
            "evidence": _csv(args.evidence),
            "supersedes": None,
            "superseded_by": None,
            "created": today,
            "updated": today,
            "review_by": add_days(today, review_days),
            "owner": args.owner or "unassigned",
        }
        doc = render_frontmatter(data) + "\n\n" + body
        if not doc.endswith("\n"):
            doc += "\n"
        return doc

    # Atomic, race-safe allocation: the next sequential ID is claimed via an
    # O_EXCL create so N concurrent `new` calls in the same area never clobber
    # each other (the read-max-then-write TOCTOU defect). The ID is only known
    # once we win the create, so the body is rendered lazily inside the helper.
    item_id, path = atomic_create_item(repo, args.area, _render)
    return {
        "command": "new",
        "id": item_id,
        "path": str(path),
        "slug": project_slug(repo),
    }


# ----------------------------------------------------------------------------
# INDEX rendering (deterministic)
# ----------------------------------------------------------------------------

_STATUS_RANK = {s: i for i, s in enumerate(STATUS_VALUES)}
_PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITY_VALUES)}


def _sort_key(item: dict[str, Any]) -> tuple:
    return (
        _STATUS_RANK.get(str(item.get("status")), 99),
        str(item.get("area", "")),
        _PRIORITY_RANK.get(str(item.get("priority")), 99),
        str(item.get("id", "")),
    )


def _esc_cell(value: Any) -> str:
    s = "" if value is None else str(value)
    return s.replace("|", "\\|").replace("\n", " ").strip()


def render_index(repo: Path, items: list[dict[str, Any]], today: str) -> str:
    """Render INDEX.md deterministically from open (non-archived) items.

    Grouped status -> area -> priority. Stale (past review_by) and gated items
    are flagged in dedicated columns so any agent sees them at a glance.
    Identical item sets always produce a byte-identical INDEX (no timestamps in
    the body — the date column is per-item `updated`, not "now").
    """
    slug = project_slug(repo)
    active = [it for it in items if not it.get("_archived")]
    active_sorted = sorted(active, key=_sort_key)

    open_p01 = sum(
        1 for it in active_sorted
        if it.get("status") in ("open", "in-progress", "blocked")
        and it.get("priority") in ("P0", "P1")
    )
    stale = [it for it in active_sorted if _is_stale(it, today)]
    gated = [it for it in active_sorted if str(it.get("gated", "none")) != "none"]

    lines: list[str] = []
    lines.append(f"# Backlog — {slug}")
    lines.append("")
    lines.append(
        "Generated by `scripts/backlog.py sync`. **Do not hand-edit** — this "
        "file is a derived view; the canonical truth is the items in `items/`. "
        "Run `backlog.py sync` to regenerate."
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Active items: {len(active_sorted)}")
    lines.append(f"- Open P0/P1: {open_p01}")
    lines.append(f"- Past review_by (stale): {len(stale)}")
    lines.append(f"- Gated: {len(gated)}")
    lines.append("")

    if stale:
        lines.append("## ⚠ Stale (past review_by)")
        lines.append("")
        lines.append("| id | title | review_by |")
        lines.append("|----|-------|-----------|")
        for it in stale:
            lines.append(
                f"| {_esc_cell(it.get('id'))} | {_esc_cell(it.get('title'))} "
                f"| {_esc_cell(it.get('review_by'))} |"
            )
        lines.append("")

    # Main table, grouped by status then area.
    lines.append("## Items")
    lines.append("")
    if not active_sorted:
        lines.append("_No active items._")
        lines.append("")
    else:
        last_status = None
        last_area = None
        for it in active_sorted:
            status = str(it.get("status", ""))
            area = str(it.get("area", ""))
            if status != last_status:
                lines.append(f"### {status}")
                lines.append("")
                last_status = status
                last_area = None
            if area != last_area:
                lines.append(f"#### {area or '(no area)'}")
                lines.append("")
                lines.append("| id | priority | type | title | gated | review_by |")
                lines.append("|----|----------|------|-------|-------|-----------|")
                last_area = area
            lines.append(
                f"| {_esc_cell(it.get('id'))} | {_esc_cell(it.get('priority'))} "
                f"| {_esc_cell(it.get('type'))} | {_esc_cell(it.get('title'))} "
                f"| {_esc_cell(it.get('gated'))} | {_esc_cell(it.get('review_by'))} |"
            )
        lines.append("")

    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    return text


def _is_stale(item: dict[str, Any], today: str) -> bool:
    rb = item.get("review_by")
    if not rb or item.get("status") in ARCHIVE_STATUSES:
        return False
    try:
        return _dt.date.fromisoformat(str(rb)) < _dt.date.fromisoformat(today)
    except ValueError:
        return False


# ----------------------------------------------------------------------------
# Consolidation + mirror
# ----------------------------------------------------------------------------

def consolidate(repo: Path, today: str) -> dict[str, Any]:
    """Move done/dropped items to archive/; report stale + unlinked queue files.

    Never deletes. Returns {archived: [ids], stale: [ids], unlinked: [paths]}.
    """
    ensure_dirs(repo)
    archived: list[str] = []
    for path in _item_files(items_dir(repo)):
        try:
            fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if str(fm.get("status")) in ARCHIVE_STATUSES:
            dest = archive_dir(repo) / path.name
            # Never clobber: if a same-name archive exists, suffix it.
            if dest.exists():
                stem = dest.stem
                dest = archive_dir(repo) / f"{stem}-{fm.get('updated', today)}.md"
            path.replace(dest)
            archived.append(str(fm.get("id", path.stem)))

    remaining = load_items(repo, include_archive=False)
    stale = [str(it.get("id")) for it in remaining if _is_stale(it, today)]
    unlinked = _unlinked_queue_files(repo, remaining)
    return {"archived": archived, "stale": stale, "unlinked": unlinked}


def _unlinked_queue_files(repo: Path, items: list[dict[str, Any]]) -> list[str]:
    """Files under .build-loop/{followup,issues,proposals} not referenced by any
    item's evidence or provenance.ref. Advisory — surfaces scattered work that
    hasn't been triaged into the backlog yet."""
    referenced: set[str] = set()
    for it in items:
        for ev in (it.get("evidence") or []):
            referenced.add(Path(str(ev)).name)
        prov = it.get("provenance") or {}
        if isinstance(prov, dict) and prov.get("ref"):
            referenced.add(Path(str(prov["ref"])).name)
    unlinked: list[str] = []
    for qname in ("followup", "issues", "proposals"):
        qdir = repo / ".build-loop" / qname
        if not qdir.is_dir():
            continue
        for path in sorted(qdir.glob("*.md")):
            if path.name not in referenced:
                unlinked.append(str(path.relative_to(repo)))
    return unlinked


def memory_root() -> Path:
    """Resolve the per-user build-loop-memory root.

    Env override (BUILD_LOOP_MEMORY_DIR) wins for tests/sandboxes; otherwise the
    canonical sibling path.
    """
    override = os.environ.get("BUILD_LOOP_MEMORY_DIR")
    if override:
        return Path(override)
    return Path.home() / "dev" / "git-folder" / "build-loop-memory"


def mirror_to_memory(repo: Path, today: str) -> dict[str, Any]:
    """Mirror active item files into the per-user memory backlog lane.

    One-way: per-repo backlog -> personal memory. Writes per-item copies under
    ``build-loop-memory/projects/<slug>/backlog/`` plus a regenerated
    ``INDEX.md`` there. Best-effort: if the memory root is absent/unwritable,
    returns {written: 0, skipped: <reason>} without failing sync.
    """
    slug = project_slug(repo)
    dest_dir = memory_root() / "projects" / slug / "backlog"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"written": 0, "skipped": f"memory_dir_unwritable: {exc}", "dir": str(dest_dir)}

    items = load_items(repo, include_archive=False)
    written = 0
    written_names: set[str] = set()
    for it in items:
        src = Path(it["_path"])
        try:
            (dest_dir / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            written += 1
            written_names.add(src.name)
        except OSError:
            continue
    # Prune mirror files whose source item no longer exists (kept in sync,
    # one-way). Only prune files that LOOK like item mirrors — an ID-shaped stem
    # `<PREFIX>-<AREA>-<NNN>`. A hand-dropped note (USER-NOTES.md, etc.) in this
    # dir is never touched: the mirror prune is the system's only delete path and
    # must not over-reach onto files it did not write.
    for path in sorted(dest_dir.glob("*.md")):
        if path.name == "INDEX.md":
            continue
        if not _ITEM_ID_RE.match(path.stem):
            continue
        if path.name not in written_names:
            try:
                path.unlink()
            except OSError:
                pass
    # Mirror INDEX too.
    try:
        (dest_dir / "INDEX.md").write_text(render_index(repo, items, today), encoding="utf-8")
    except OSError:
        pass
    return {"written": written, "dir": str(dest_dir)}


# ----------------------------------------------------------------------------
# `sync`
# ----------------------------------------------------------------------------

def write_index(repo: Path, today: str) -> dict[str, Any]:
    items = load_items(repo, include_archive=False)
    index_text = render_index(repo, items, today)
    index_path = backlog_root(repo) / "INDEX.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(index_text, encoding="utf-8")
    return {"path": str(index_path), "active_count": len(items)}


def cmd_sync(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(args.repo)
    ensure_dirs(repo)
    today = resolve_today(args.today)

    # 1. Consolidate first (done/dropped -> archive) so INDEX reflects the move.
    consolidation = consolidate(repo, today)
    # 2. Regenerate INDEX deterministically from the remaining items.
    index = write_index(repo, today)
    # 3. Mirror to personal memory (unless suppressed).
    mirror: dict[str, Any] = {"written": 0, "skipped": "disabled"}
    if not args.no_mirror:
        mirror = mirror_to_memory(repo, today)

    return {
        "command": "sync",
        "slug": project_slug(repo),
        "index": index,
        "consolidation": consolidation,
        "mirror": mirror,
        "today": today,
    }


# ----------------------------------------------------------------------------
# `list`
# ----------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(args.repo)
    items = load_items(repo, include_archive=bool(args.include_archive))

    def keep(it: dict[str, Any]) -> bool:
        if args.status and str(it.get("status")) != args.status:
            return False
        if args.area and str(it.get("area")) != args.area:
            return False
        if args.priority and str(it.get("priority")) != args.priority:
            return False
        return True

    filtered = sorted([it for it in items if keep(it)], key=_sort_key)
    rows = [
        {
            "id": it.get("id"),
            "status": it.get("status"),
            "priority": it.get("priority"),
            "type": it.get("type"),
            "area": it.get("area"),
            "gated": it.get("gated"),
            "title": it.get("title"),
        }
        for it in filtered
    ]
    return {"command": "list", "count": len(rows), "items": rows}


def _print_list_text(result: dict[str, Any]) -> None:
    rows = result["items"]
    if not rows:
        print("(no matching backlog items)")
        return
    for r in rows:
        gated = f" [gated:{r['gated']}]" if r.get("gated") and r["gated"] != "none" else ""
        print(f"{r['id']:<20} {r['priority']:<3} {str(r['status']):<12} "
              f"{str(r['area']):<14} {r['title']}{gated}")
    print(f"\n{result['count']} item(s)")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backlog.py",
        description="Host-agnostic, multi-repo build-loop backlog system (pure stdlib).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    pn = sub.add_parser("new", help="Create a new backlog item.")
    pn.add_argument("--repo", required=True)
    pn.add_argument("--slug", default="",
                    help="Override project slug (use inside a worktree/CI checkout "
                         "where the dir basename isn't the repo name).")
    pn.add_argument("--area", required=True)
    pn.add_argument("--type", required=True, choices=TYPE_VALUES)
    pn.add_argument("--title", required=True)
    pn.add_argument("--priority", default="P2", choices=PRIORITY_VALUES)
    pn.add_argument("--status", default="open", choices=STATUS_VALUES)
    pn.add_argument("--gated", default="none", choices=GATED_VALUES)
    pn.add_argument("--entities", default="", help="comma-separated")
    pn.add_argument("--evidence", default="", help="comma-separated paths/refs")
    pn.add_argument("--provenance-source", default="", dest="provenance_source")
    pn.add_argument("--provenance-ref", default="", dest="provenance_ref")
    pn.add_argument("--owner", default="")
    pn.add_argument("--context", default="")
    pn.add_argument("--notes", default="")
    pn.add_argument("--review-days", type=int, default=None, dest="review_days")
    pn.add_argument("--today", default=None)
    pn.add_argument("--json", action="store_true")

    ps = sub.add_parser("sync", help="Regenerate INDEX, consolidate, mirror to memory.")
    ps.add_argument("--repo", required=True)
    ps.add_argument("--slug", default="",
                    help="Override project slug (worktree/CI checkout).")
    ps.add_argument("--today", default=None)
    ps.add_argument("--no-mirror", action="store_true",
                    help="Skip the personal-memory mirror (per-repo only).")
    ps.add_argument("--json", action="store_true")

    pl = sub.add_parser("list", help="Filtered text/JSON view of items.")
    pl.add_argument("--repo", required=True)
    pl.add_argument("--slug", default="",
                    help="Override project slug (worktree/CI checkout).")
    pl.add_argument("--status", default="")
    pl.add_argument("--area", default="")
    pl.add_argument("--priority", default="")
    pl.add_argument("--include-archive", action="store_true", dest="include_archive")
    pl.add_argument("--json", action="store_true")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # A --slug override pins the project identity (worktree/CI checkout) for the
    # rest of the process; project_slug() reads BACKLOG_SLUG.
    if getattr(args, "slug", ""):
        os.environ["BACKLOG_SLUG"] = args.slug
    try:
        if args.command == "new":
            result = cmd_new(args)
            print(json.dumps(result, indent=2))
        elif args.command == "sync":
            result = cmd_sync(args)
            print(json.dumps(result, indent=2))
        elif args.command == "list":
            result = cmd_list(args)
            if getattr(args, "json", False):
                print(json.dumps(result, indent=2))
            else:
                _print_list_text(result)
        else:  # pragma: no cover - argparse enforces
            parser.error(f"unknown command {args.command!r}")
            return 2
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
