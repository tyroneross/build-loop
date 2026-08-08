#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""premise_revalidation.py — re-validate queue-item premises at drain time.

Mirrors the design shipped in RossLabs Operations Center (commit 3fd0a23):
a nullable ``validated:`` frontmatter field, freshness falling back to
``created:``, a 7-day default window, a refusal code
(``stale_needs_revalidation``) wired into the same gate that already refuses
a card with no target repo, plus a ``validate --note`` stamp-and-receipt
command and a ``stale`` listing. Build-loop's queues are Markdown files, not
a DB, so the plumbing differs but the semantics match — do not reinvent them.

Beyond the OC design, this module ALSO mechanically re-checks the item's
cited anchors (file paths, commit SHAs) — this is what catches the "my own
disproof was itself stale" failure mode: an operator sweep reported a file
as deleted when it had actually been RELOCATED, and a repo as 0-ahead when
it was 6. A missing cited path is never concluded ``premise_broken`` on its
own — a same-basename match elsewhere in the repo routes it to
``needs_human_recheck`` (with the candidate named) instead, so a relocation
is never mistaken for a resolution.

Covers all three Phase-5 drain surfaces: ``.build-loop/issues/``,
``.build-loop/backlog/`` (top-level items AND the ``items/`` subdir), and
``.build-loop/followup/``.

Frontmatter parsing reuses ``backlog.py``'s ``parse_frontmatter`` /
``render_frontmatter`` rather than writing a second YAML-ish reader. Loaded
via ``importlib.util.spec_from_file_location`` (NOT a plain ``import
backlog``) because ``scripts/backlog/`` is ALSO a real package in this repo
(capture-time product-impact triage) — a plain ``sys.path.insert`` +
``import backlog`` resolves to that package (packages shadow same-named
modules in one FileFinder directory), silently hiding
``backlog.py``'s frontmatter functions. Loading ``backlog.py`` by explicit
file path sidesteps the name collision entirely without touching either
pre-existing file.
Deliberately NOT using ``backlog.py``'s ``read_item`` (which defaults every
field in backlog.py's OWN item schema — id/priority/type/area/gated/...):
issues/ and followup/ items have a different, thinner frontmatter shape, and
defaulting backlog-schema fields onto them would inject schema noise on the
next ``validate`` rewrite. ``parse_frontmatter``/``render_frontmatter`` are
schema-agnostic — they round-trip exactly the keys present.

Pure stdlib. Subprocess is used only for two OPTIONAL git calls
(``git ls-files`` for the basename/relocation index, ``git cat-file -e`` for
SHA reachability); both degrade gracefully (skip, never crash) when the
target directory isn't a git repo or git isn't on PATH.

Subcommands::

    premise_revalidation.py sweep    [--repo P] [--window-days N]
                                      [--queue issues|backlog|followup|all]
                                      [--json]
    premise_revalidation.py gate     --item <path> [--repo P]
                                      [--window-days N]
    premise_revalidation.py validate --item <path> --note "<evidence>"
    premise_revalidation.py stale    [--repo P] [--window-days N]
                                      [--queue issues|backlog|followup|all]

``sweep``/``stale`` always exit 0 (a sweep/listing reports; it does not
gate). ``gate`` exits 1 on ``stale_needs_revalidation``, ``premise_broken``,
or ``needs_human_recheck`` — only ``fresh`` is exit 0 — so the drain refuses
to schedule an item whose premise wasn't re-confirmed. ``validate`` exits 1
if ``--note`` is missing/empty (a bare timestamp would assert freshness
without evidence — the exact failure mode this module exists to close) and
does not touch the file in that case.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent


def _load_backlog_module():
    """Load scripts/backlog.py's parse_frontmatter/render_frontmatter by file
    path — NOT `sys.path.insert` + `import backlog`, because scripts/backlog/
    is ALSO a real package in this repo and would shadow the module (see
    module docstring)."""
    backlog_path = _SCRIPTS / "backlog.py"
    spec = importlib.util.spec_from_file_location("_premise_revalidation_backlog", backlog_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load backlog.py from {backlog_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backlog = _load_backlog_module()  # parse_frontmatter, render_frontmatter

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

DEFAULT_WINDOW_DAYS = 7
_ENV_WINDOW_DAYS = "BL_PREMISE_TTL_DAYS"

# Statuses excluded from revalidation entirely — done/dropped work is history,
# not a live premise to re-check.
_EXCLUDED_STATUSES = ("done", "closed")

# Each queue maps to one or more subpaths under .build-loop/ to glob for
# `*.md` items. "backlog" covers BOTH the flat top-level dir (legacy/simple
# items, templates/backlog-item.md shape) and the items/ subdir (backlog.py's
# own schema) — the brief calls out both explicitly.
QUEUE_DIRS: dict[str, list[tuple[str, ...]]] = {
    "issues": [("issues",)],
    "backlog": [("backlog",), ("backlog", "items")],
    "followup": [("followup",)],
}
ALL_QUEUES = tuple(QUEUE_DIRS)

# Managed/derived files that live alongside real items in these dirs and must
# never be treated as an item to classify.
_SKIP_NAMES = {"INDEX.md", "README.md", "BACKLOG.md"}

VERDICTS = (
    "fresh",
    "stale_needs_revalidation",
    "premise_broken",
    "needs_human_recheck",
)

# ----------------------------------------------------------------------------
# Date helpers
# ----------------------------------------------------------------------------


def _today_iso(today: str | None = None) -> str:
    """Resolve "today" as YYYY-MM-DD. Explicit arg wins; else the system clock.

    No env/CLI override is exposed for this — the public functions accept
    ``today`` directly for deterministic unit tests, which is simpler than a
    second injection path and keeps the CLI surface to what the brief asked
    for.
    """
    if today:
        date.fromisoformat(today)  # validate; raises loudly on garbage
        return today
    return date.today().isoformat()


def resolve_window_days(arg: int | None) -> int:
    """--window-days > BL_PREMISE_TTL_DAYS env > DEFAULT_WINDOW_DAYS."""
    if arg is not None:
        return int(arg)
    env = os.environ.get(_ENV_WINDOW_DAYS)
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return DEFAULT_WINDOW_DAYS


def _freshness_date(fm: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (source, date_str). ``validated`` wins; falls back to ``created``.

    An item filed a minute ago is fresh by construction (the ``created``
    fallback); one filed weeks ago and never re-checked is not.
    """
    validated = fm.get("validated")
    if validated:
        return "validated", str(validated)
    created = fm.get("created")
    if created:
        return "created", str(created)
    return None, None


# ----------------------------------------------------------------------------
# Anchor extraction (file paths + commit SHAs cited in the item body)
# ----------------------------------------------------------------------------

_BACKTICK_RE = re.compile(r"`([^`\s]+)`")
_BARE_PATH_RE = re.compile(r"\b((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,6})\b")
_PATH_EXT_HINT_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")

# A cited SHA must appear near a commit-ish word — bare 7-40 char hex runs are
# far too common (line numbers, IDs) to treat as commit citations on their own.
_SHA_CONTEXT_RE = re.compile(
    r"(?:commit|sha|rev(?:ision)?)\b[^\n]{0,24}?`?\b([0-9a-fA-F]{7,40})\b",
    re.IGNORECASE,
)


def _looks_like_path_token(tok: str) -> bool:
    """Conservative path-shaped check — a false positive is worse than a miss."""
    tok = tok.strip()
    if not tok or " " in tok or "\t" in tok:
        return False
    if "://" in tok or tok.startswith("http"):
        return False
    if tok.startswith("-"):
        return False
    if "/" not in tok:
        return False
    if _PATH_EXT_HINT_RE.search(tok):
        return True
    return tok.count("/") >= 1 and not tok.endswith("/")


def extract_paths(body: str) -> list[str]:
    """Extract plausible repo-relative file paths cited in an item body.

    Backtick-quoted tokens (`` `scripts/gone.py` ``) and bare
    extension-bearing paths. Trailing punctuation from prose (``.``, ``,``,
    ``)``, ``:``) is stripped so a citation at the end of a sentence still
    matches.
    """
    found: set[str] = set()
    for m in _BACKTICK_RE.finditer(body):
        tok = m.group(1).rstrip(".,;:)")
        if _looks_like_path_token(tok):
            found.add(tok)
    for m in _BARE_PATH_RE.finditer(body):
        tok = m.group(1).rstrip(".,;:)")
        if _looks_like_path_token(tok):
            found.add(tok)
    return sorted(found)


def extract_shas(body: str) -> list[str]:
    """Extract commit SHAs cited in a commit-ish context (7-40 hex chars)."""
    found: set[str] = set()
    for m in _SHA_CONTEXT_RE.finditer(body):
        found.add(m.group(1).lower())
    return sorted(found)


# ----------------------------------------------------------------------------
# Repo introspection (git-first, filesystem-walk fallback)
# ----------------------------------------------------------------------------

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}


def _repo_tracked_files(repo: Path) -> list[str]:
    """Repo-relative POSIX paths of every file git knows about (tracked +
    untracked-but-not-ignored), or a filesystem walk when git is unavailable.

    ``--others --exclude-standard`` alongside ``--cached`` so a freshly moved
    file that hasn't been ``git add``-ed yet still counts as "exists in the
    repo" for the relocation check — the exact case a careless `git status`
    only, or a stage-first workflow, would miss.
    """
    if (repo / ".git").is_dir():
        try:
            r = subprocess.run(
                ["git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard"],
                capture_output=True,
                text=True,
                check=True,
            )
            return [ln for ln in r.stdout.splitlines() if ln]
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            pass
    out: list[str] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            full = Path(root) / name
            try:
                out.append(full.relative_to(repo).as_posix())
            except ValueError:
                continue
    return out


def build_basename_index(repo: Path) -> dict[str, list[str]]:
    """basename -> [repo-relative paths] for the entire repo (relocation lookup)."""
    idx: dict[str, list[str]] = {}
    for rel in _repo_tracked_files(repo):
        idx.setdefault(Path(rel).name, []).append(rel)
    return idx


def _relocation_candidates(
    repo: Path, rel_path: str, basename_index: dict[str, list[str]] | None
) -> list[str]:
    basename = Path(rel_path).name
    if basename_index is None:
        basename_index = build_basename_index(repo)
    return [p for p in basename_index.get(basename, []) if p != rel_path]


def _reachable_sha(repo: Path, sha: str) -> bool | None:
    """True/False if determinable; None if not a git repo or git is absent
    (SKIP, never fail the item just because we couldn't check)."""
    if not (repo / ".git").is_dir():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    return r.returncode == 0


# ----------------------------------------------------------------------------
# Core classification
# ----------------------------------------------------------------------------


def classify_item(
    fm: dict[str, Any],
    body: str,
    repo: Path,
    window_days: int,
    today: str,
    basename_index: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Classify one item into one of VERDICTS. Pure function — no filesystem
    writes, no git repo mutation. ``repo`` is read-only'd for path existence
    and the two optional git lookups.
    """
    repo = Path(repo)
    status = str(fm.get("status") or "").strip().lower()
    excluded = status in _EXCLUDED_STATUSES

    if excluded:
        return {
            "verdict": "fresh",
            "reason_code": "excluded_done",
            "excluded": True,
            "freshness_source": None,
            "freshness_date": None,
            "age_days": None,
            "window_days": window_days,
            "anchors": {
                "paths_checked": [],
                "shas_checked": [],
                "broken_paths": [],
                "relocated_paths": [],
                "broken_shas": [],
            },
        }

    freshness_source, freshness_value = _freshness_date(fm)
    age_days: int | None = None
    stale = False
    if freshness_value:
        try:
            age_days = (date.fromisoformat(today) - date.fromisoformat(freshness_value)).days
            # Inclusive at the boundary: an item exactly `window_days` old is
            # still fresh (the window is a closed interval); only strictly
            # PAST the window is stale.
            stale = age_days > window_days
        except ValueError:
            pass  # malformed date — degrade gracefully, don't crash the sweep

    paths = extract_paths(body)
    broken_paths: list[dict[str, Any]] = []
    relocated_paths: list[dict[str, Any]] = []
    for rel in paths:
        if (repo / rel).exists():
            continue
        candidates = _relocation_candidates(repo, rel, basename_index)
        if candidates:
            relocated_paths.append({"path": rel, "candidates": candidates})
        else:
            broken_paths.append({"path": rel})

    shas = extract_shas(body)
    broken_shas: list[str] = []
    for sha in shas:
        reachable = _reachable_sha(repo, sha)
        if reachable is False:
            broken_shas.append(sha)
        # reachable is True -> fine; None -> can't determine, skip (never
        # penalize an item just because we lack a git repo to check against).

    if broken_paths or broken_shas:
        verdict = "premise_broken"
    elif relocated_paths:
        verdict = "needs_human_recheck"
    elif stale:
        verdict = "stale_needs_revalidation"
    else:
        verdict = "fresh"

    return {
        "verdict": verdict,
        "reason_code": verdict,
        "excluded": False,
        "freshness_source": freshness_source,
        "freshness_date": freshness_value,
        "age_days": age_days,
        "window_days": window_days,
        "anchors": {
            "paths_checked": paths,
            "shas_checked": shas,
            "broken_paths": broken_paths,
            "relocated_paths": relocated_paths,
            "broken_shas": broken_shas,
        },
    }


# ----------------------------------------------------------------------------
# Queue discovery
# ----------------------------------------------------------------------------


def _normalize_queues(queues: list[str] | str | None) -> list[str]:
    if queues is None or queues == "all" or queues == ["all"]:
        return list(ALL_QUEUES)
    if isinstance(queues, str):
        queues = [queues]
    return [q for q in queues if q in QUEUE_DIRS]


def iter_queue_files(repo: Path, queues: list[str] | str | None = None) -> list[tuple[str, Path]]:
    """[(queue_name, path), ...] for every real item file across the requested
    queues, de-duplicated (a path is never double-listed even if two queue
    subpaths happened to resolve to the same directory)."""
    repo = Path(repo)
    out: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for q in _normalize_queues(queues):
        for parts in QUEUE_DIRS[q]:
            d = repo / ".build-loop"
            for part in parts:
                d = d / part
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.md")):
                if f.name in _SKIP_NAMES:
                    continue
                key = f.resolve()
                if key in seen:
                    continue
                seen.add(key)
                out.append((q, f))
    return out


# ----------------------------------------------------------------------------
# `sweep`
# ----------------------------------------------------------------------------


def sweep(
    repo: str | Path,
    window_days: int = DEFAULT_WINDOW_DAYS,
    queues: list[str] | str | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """Verdict per item across the requested queues + counts. Never raises on
    a well-formed-enough tree; a per-file read error is skipped, not fatal."""
    repo = Path(repo)
    today = _today_iso(today)
    basename_index = build_basename_index(repo)
    counts = {v: 0 for v in VERDICTS}
    items: list[dict[str, Any]] = []
    for queue_name, path in iter_queue_files(repo, queues):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = backlog.parse_frontmatter(text)
        result = classify_item(fm, body, repo, window_days, today, basename_index)
        try:
            rel = str(path.relative_to(repo))
        except ValueError:
            rel = str(path)
        entry = {
            "path": rel,
            "queue": queue_name,
            "id": fm.get("id") or path.stem,
            "title": fm.get("title"),
            **result,
        }
        items.append(entry)
        if not result["excluded"]:
            counts[result["verdict"]] += 1
    return {
        "command": "sweep",
        "repo": str(repo),
        "today": today,
        "window_days": window_days,
        "queues": _normalize_queues(queues),
        "counts": counts,
        "items": items,
    }


def _render_sweep_text(result: dict[str, Any]) -> str:
    lines = [
        f"Premise sweep — {result['repo']} (window {result['window_days']}d, today {result['today']})",
        "",
    ]
    for v in VERDICTS:
        lines.append(f"  {v}: {result['counts'][v]}")
    lines.append("")
    for it in result["items"]:
        if it["excluded"]:
            continue
        lines.append(f"  [{it['verdict']}] {it['queue']}/{it['path']}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# `gate` — the drain-time refusal check
# ----------------------------------------------------------------------------


def _infer_repo(item_path: Path) -> Path:
    """Walk up from the item looking for a containing .build-loop/; falls
    back to cwd when the item lives outside any recognizable repo."""
    item_path = item_path.resolve()
    for parent in item_path.parents:
        if (parent / ".build-loop").is_dir():
            return parent
    return Path.cwd()


def gate(
    item_path: str | Path,
    repo: str | Path | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: str | None = None,
) -> dict[str, Any]:
    """The drain-time gate for ONE item. Exit 1 (refuse) on anything but
    ``fresh``. Always returns — never raises on a well-formed file."""
    item_path = Path(item_path)
    repo_path = Path(repo) if repo else _infer_repo(item_path)
    today = _today_iso(today)
    text = item_path.read_text(encoding="utf-8")
    fm, body = backlog.parse_frontmatter(text)
    result = classify_item(fm, body, repo_path, window_days, today)
    verdict = result["verdict"]
    exit_code = 0 if verdict == "fresh" else 1
    out = {
        "command": "gate",
        "item": str(item_path),
        "repo": str(repo_path),
        "exit_code": exit_code,
    }
    out.update(result)
    return out


# ----------------------------------------------------------------------------
# `validate` — stamp freshness AND write an evidence receipt
# ----------------------------------------------------------------------------

_RECEIPT_HEADING = "## Premise validated"


def validate(item_path: str | Path, note: str | None, today: str | None = None) -> dict[str, Any]:
    """Stamp ``validated: <date>`` AND append a receipt section carrying the
    date + note. ``note`` is REQUIRED and non-empty — a bare timestamp would
    assert freshness without evidence, which is the exact failure mode this
    module exists to close. Returns ``{"ok": False, ...}`` without touching
    the file when note is missing/empty."""
    note = (note or "").strip()
    if not note:
        return {
            "ok": False,
            "error": "validate requires a non-empty --note (evidence) — a bare timestamp asserts freshness without evidence",
            "item": str(item_path),
        }
    item_path = Path(item_path)
    today = _today_iso(today)
    text = item_path.read_text(encoding="utf-8")
    fm, body = backlog.parse_frontmatter(text)
    fm["validated"] = today

    body_stripped = body.rstrip("\n")
    receipt = f"{_RECEIPT_HEADING}\n\n{today} — {note}\n"
    new_body = (body_stripped + "\n\n" + receipt) if body_stripped else receipt

    doc = backlog.render_frontmatter(fm) + "\n\n" + new_body
    if not doc.endswith("\n"):
        doc += "\n"

    tmp = item_path.parent / f".{item_path.name}.{os.getpid()}.tmp"
    try:
        tmp.write_text(doc, encoding="utf-8")
        os.replace(tmp, item_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    return {"ok": True, "item": str(item_path), "validated": today, "note": note}


# ----------------------------------------------------------------------------
# `stale` — open items past the window (time-only; not the full anchor recheck)
# ----------------------------------------------------------------------------


def stale(
    repo: str | Path,
    window_days: int = DEFAULT_WINDOW_DAYS,
    queues: list[str] | str | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """List OPEN items past the freshness window. done/closed items never
    appear here (excluded upstream in classify_item)."""
    result = sweep(repo, window_days=window_days, queues=queues, today=today)
    items = [
        it for it in result["items"]
        if it["verdict"] == "stale_needs_revalidation" and not it["excluded"]
    ]
    return {
        "command": "stale",
        "repo": result["repo"],
        "today": result["today"],
        "window_days": window_days,
        "items": items,
        "count": len(items),
    }


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="premise_revalidation.py")
    sub = p.add_subparsers(dest="command", required=True)

    sp_sweep = sub.add_parser("sweep", help="Verdict per item + counts")
    sp_sweep.add_argument("--repo", default=".")
    sp_sweep.add_argument("--window-days", type=int, default=None)
    sp_sweep.add_argument("--queue", default="all", choices=[*ALL_QUEUES, "all"])
    sp_sweep.add_argument("--json", action="store_true")

    sp_gate = sub.add_parser("gate", help="Drain-time gate for one item")
    sp_gate.add_argument("--item", required=True)
    sp_gate.add_argument("--repo", default=None)
    sp_gate.add_argument("--window-days", type=int, default=None)
    # `gate`/`validate`/`stale` always emit JSON. `--json` is accepted as a no-op
    # so every documented invocation in phase-5-iterate.md and
    # agents/build-orchestrator.md parses, and so the flag is uniform across the
    # sibling scripts an orchestrator calls in the same breath. A documented
    # command that argparse rejects is a shipped defect, not a cosmetic one.
    sp_gate.add_argument("--json", action="store_true", help="no-op; output is always JSON")

    sp_validate = sub.add_parser("validate", help="Stamp validated: + write receipt")
    sp_validate.add_argument("--item", required=True)
    sp_validate.add_argument("--note", default=None)
    sp_validate.add_argument("--repo", default=None, help="accepted for call-site symmetry; unused")
    sp_validate.add_argument("--json", action="store_true", help="no-op; output is always JSON")

    sp_stale = sub.add_parser("stale", help="List open items past the window")
    sp_stale.add_argument("--repo", default=".")
    sp_stale.add_argument("--window-days", type=int, default=None)
    sp_stale.add_argument("--queue", default="all", choices=[*ALL_QUEUES, "all"])
    sp_stale.add_argument("--json", action="store_true", help="no-op; output is always JSON")

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "sweep":
        window_days = resolve_window_days(args.window_days)
        result = sweep(args.repo, window_days=window_days, queues=args.queue)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(_render_sweep_text(result))
        return 0

    if args.command == "gate":
        window_days = resolve_window_days(args.window_days)
        result = gate(args.item, repo=args.repo, window_days=window_days)
        print(json.dumps(result, indent=2))
        return result["exit_code"]

    if args.command == "validate":
        result = validate(args.item, args.note)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.command == "stale":
        window_days = resolve_window_days(args.window_days)
        result = stale(args.repo, window_days=window_days, queues=args.queue)
        print(json.dumps(result, indent=2))
        return 0

    return 1  # unreachable — argparse enforces `command` is one of the above


if __name__ == "__main__":
    sys.exit(main())
