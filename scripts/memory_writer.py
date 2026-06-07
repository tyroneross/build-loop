#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Canonical writer for build-loop-memory files with provenance frontmatter.

Companion to memory_index.py. Memory writes go through this script so
every entry under the canonical build-loop-memory lanes carries the provenance fields
that make cross-repo trust gradients possible. Concurrent-presence
detection is a separate concern owned by Rally Point presence
(scripts/rally_point/presence.py), not this writer.

Provenance schema (YAML frontmatter added to every memory file):
  ---
  name: <slug>
  description: <one-line summary>
  type: tool | deployment | library-choice | user-preference | pattern | feedback | reference | design | convention | gotcha | decision | contract
  source_repo: "<git remote url or null>"
  source_workdir: "<abs path>"
  source_run_id: "run_<UTC>_<hash>"
  source_host: "claude_code | codex | gemini | other"
  cross_repo_validated: false   # flips true when a different repo applies it
  applied_in_repos: []          # appended-to when a different repo applies it
  created_at: "ISO8601 UTC"
  last_updated_at: "ISO8601 UTC"
  ---

Subcommands:
  write          — create or update a memory file. Adds/refreshes provenance
                   frontmatter, preserves applied_in_repos history, then
                   appends a row to memory_index.
  mark-applied   — flag that the CURRENT repo successfully applied a memory
                   that originated elsewhere. Appends to applied_in_repos[]
                   and flips cross_repo_validated=true once a second repo
                   confirms the lesson holds.
  migrate        — idempotent one-time backfill: add provenance frontmatter
                   to existing memory files that don't have it. Safe to
                   re-run; never overwrites a file that already has all the
                   required fields.

Concurrency: atomic writes (tmpfile + os.replace). The memory file IS the
lock — no separate lock file. The INDEX append uses memory_index.py's
existing fcntl-based locking.

Stdlib only. Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import memory_index as mi  # noqa: E402
import memory_update_ledger as mul  # noqa: E402

from _paths import (  # type: ignore  # noqa: E402
    project_lessons_dir,
    top_level_lessons_dir,
)


def default_memory_dir() -> Path:
    """Default durable memory write lane: top-level canonical lessons."""
    return top_level_lessons_dir()


DEFAULT_MEMORY_DIR = default_memory_dir()

# Project sublanes that live as siblings under ``projects/<slug>/``.
# When a caller passes ``--file <lane>/x.md`` or ``--file projects/<slug>/<lane>/x.md``
# under ``--scope project``, the writer re-resolves memory_dir to the sublane
# rather than nesting beneath ``lessons/``. Match the lane helpers in _paths.py.
PROJECT_SUBLANES = frozenset({
    "lessons", "issues", "decisions", "debugging", "design", "product",
    "architecture", "raw",
})

# Top-level lanes (siblings under ``build-loop-memory/``). Same idea: a
# ``--scope top-level --file <lane>/x.md`` should land in the lane, not nested
# under ``lessons/``.
TOP_LEVEL_LANES = frozenset({
    "lessons", "debugging", "design", "product", "architecture",
})

VALID_HOSTS = frozenset({"claude_code", "codex", "gemini", "other"})
VALID_TYPES = frozenset({
    "tool", "deployment", "library-choice", "user-preference", "pattern",
    "feedback", "reference", "design", "convention", "gotcha", "decision",
    "contract", "lesson", "run-summary", "debug-incident", "debug-fix",
    "procedure", "architecture", "api-contract", "design-guidance",
    "product-idea", "product-backlog", "product-opportunity",
    "product-use-case", "product-ruled-out", "source-summary", "agent",
    "plugin", "skill",
})

REQUIRED_PROVENANCE_FIELDS = frozenset({
    "source_workdir", "source_run_id", "source_host",
    "cross_repo_validated", "applied_in_repos",
    "created_at", "last_updated_at",
})


def iso_utc(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Frontmatter parser (lightweight YAML subset — stdlib only)
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Empty frontmatter dict if absent.

    Parses a simple YAML subset:
      - key: value
      - key: ["item1", "item2"]   (JSON-style list)
      - key:                       (multi-line not supported — single line only)
    Quoted strings: "..." OR '...' (quotes stripped). Booleans: true/false.
    Null: ~ or null. Numbers: int/float.
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    block = text[4:end]
    body = text[end + 5 :]
    fm: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        # Match `key: value` — value may be empty.
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        fm[key] = _coerce_scalar(val)
    return fm, body


def _coerce_scalar(val: str) -> Any:
    if val == "" or val == "~" or val.lower() == "null":
        return None
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    if val.startswith("[") and val.endswith("]"):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    if (val.startswith('"') and val.endswith('"')) or (
        val.startswith("'") and val.endswith("'")
    ):
        return val[1:-1]
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def _emit_frontmatter(fm: dict) -> str:
    """Serialize back to the YAML subset we parse. Preserves key order via
    insertion order (Python 3.7+ dicts are ordered)."""
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {_emit_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _emit_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        # Standard JSON: item separator = ',', key separator = ':'.
        # Use compact form to keep frontmatter scannable.
        return json.dumps(v, separators=(",", ":"))
    if isinstance(v, dict):
        return json.dumps(v, separators=(",", ":"))
    s = str(v)
    if any(c in s for c in ":#[]{},&*!|>'\"%@`") or s != s.strip():
        return json.dumps(s)
    return s


# ---------------------------------------------------------------------------
# Atomic IO
# ---------------------------------------------------------------------------


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _detect_git_remote(workdir: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(workdir), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _detect_git_head(workdir: Path) -> str | None:
    """Return the full sha of HEAD in workdir, or None if unavailable."""
    try:
        r = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None



# ---------------------------------------------------------------------------
# Path normalization + canonical filename derivation (P2 writer guard)
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to ``-``, strip ends."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "untitled"


def canonical_filename(*, type_: str, name: str, date: str | None = None) -> str:
    """Return ``<YYYY-MM-DD>-<type>-<slug>.md`` (P2 contract).

    Centralised here so callers that omit ``--file`` get a stable, sortable
    filename without picking the lane themselves. The leading date keeps
    chronologically related entries clustered in directory listings; the type
    keeps mixed-lane folders (e.g. ``lessons/``) grouped by kind.
    """
    date_str = date or iso_utc().split("T", 1)[0]
    return f"{date_str}-{_slugify(type_)}-{_slugify(name)}.md"


def _normalize_file_rel(
    file_rel: str,
    *,
    scope: str,
    project: str | None,
    memory_dir: Path,
) -> tuple[str, Path]:
    """Strip lane prefixes and re-resolve memory_dir when the path implies a sublane.

    Single source of truth for the "lane is implicit" guard. Without this,
    ``--file projects/<slug>/issues/x.md --scope project --project <slug>``
    landed at ``<root>/projects/<slug>/lessons/projects/<slug>/issues/x.md``
    because ``memory_dir`` was already ``projects/<slug>/lessons`` and the
    caller's path was treated as lane-relative under it.

    Rules (idempotent — running twice yields the same result):
      * scope=project, project=<p>: strip a leading ``projects/<p>/`` segment;
        if the remainder starts with a known PROJECT_SUBLANE (``issues/``,
        ``decisions/``, ``architecture/`` ...), re-point ``memory_dir`` to
        ``project_root(p) / <sublane>`` and drop that segment from the path.
      * scope=top-level: strip a leading ``<lane>/`` segment when the lane is
        in TOP_LEVEL_LANES (callers writing into ``debugging/`` should land in
        ``<root>/debugging/`` even if memory_dir defaulted to ``lessons/``).
      * Absolute paths are rejected (security: never resolve outside the lane).
      * ``..`` is rejected for the same reason.

    Returns ``(normalized_file_rel, normalized_memory_dir)``.
    """
    if not file_rel:
        raise ValueError("file_rel is empty")
    p = Path(file_rel)
    if p.is_absolute():
        raise ValueError(f"--file must be lane-relative, not absolute: {file_rel!r}")
    if ".." in p.parts:
        raise ValueError(f"--file must not contain '..': {file_rel!r}")

    parts = list(p.parts)
    new_memory_dir = memory_dir

    # Strip lane/project prefixes in a loop so a doubly-prefixed path
    # (``issues/projects/<p>/issues/x.md``) reduces to its base filename in
    # one normalisation. The strip rules are idempotent at the leaf, so the
    # loop terminates as soon as nothing was stripped this pass.
    # Each pass tries: (1) projects/<p>/ -> strip, (2) <sublane>/ -> strip
    # and re-point memory_dir.
    if scope == "project" and project:
        from _paths import project_root  # type: ignore  # noqa: PLC0415
        while True:
            before = list(parts)
            if len(parts) >= 2 and parts[0] == "projects" and parts[1] == project:
                parts = parts[2:]
            if parts and parts[0] in PROJECT_SUBLANES:
                sublane = parts[0]
                new_memory_dir = project_root(project) / sublane
                parts = parts[1:]
            if parts == before:
                break
        if not parts:
            raise ValueError(
                f"--file {file_rel!r} resolved to empty filename after lane strip"
            )
    elif scope == "top-level":
        from _paths import memory_store_root, project_root  # type: ignore  # noqa: PLC0415
        while True:
            before = list(parts)
            # Strip recognized top-level lane prefixes.
            if parts and parts[0] in TOP_LEVEL_LANES:
                lane = parts[0]
                new_memory_dir = memory_store_root() / lane
                parts = parts[1:]
            # Also strip a leading ``projects/<slug>/`` segment (and its sublane)
            # when the caller passed a fully-qualified project path without scope=.
            # This makes the guard unconditional: a no-scope in-process call with
            # file_rel="projects/<p>/<sublane>/x.md" lands once, not double-nested.
            elif len(parts) >= 2 and parts[0] == "projects":
                detected_slug = parts[1]
                parts = parts[2:]
                if parts and parts[0] in PROJECT_SUBLANES:
                    sublane = parts[0]
                    new_memory_dir = project_root(detected_slug) / sublane
                    parts = parts[1:]
                else:
                    new_memory_dir = project_root(detected_slug) / "lessons"
            if parts == before:
                break
        if not parts:
            raise ValueError(
                f"--file {file_rel!r} resolved to empty filename after lane strip"
            )

    return str(Path(*parts)), new_memory_dir


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def write(
    memory_dir: Path,
    file_rel: str,
    body: str,
    *,
    name: str,
    description: str,
    type_: str,
    run_id: str,
    workdir: str,
    host: str,
    extra_frontmatter: dict | None = None,
    scope: str | None = None,
    project: str | None = None,
) -> dict:
    """Create or update a memory file with provenance frontmatter.

    On update, preserves `created_at`, `applied_in_repos`, and any non-
    provenance frontmatter keys. Refreshes `last_updated_at`.

    Returns the final frontmatter as a dict.
    """
    if host not in VALID_HOSTS:
        raise ValueError(f"host must be one of {sorted(VALID_HOSTS)}; got {host!r}")
    if type_ not in VALID_TYPES:
        raise ValueError(f"type must be one of {sorted(VALID_TYPES)}; got {type_!r}")

    # P2 guard: unconditionally normalise the path so a lane-prefixed
    # --file argument never double-nests under memory_dir regardless of
    # whether the caller passed scope=.  When scope is None we default to
    # "top-level" strip semantics (strips recognized TOP_LEVEL_LANES prefixes
    # only; project-lane stripping requires an explicit scope + project pair).
    _eff_scope = scope if scope is not None else "top-level"
    file_rel, memory_dir = _normalize_file_rel(
        file_rel, scope=_eff_scope, project=project, memory_dir=memory_dir,
    )

    path = memory_dir / file_rel
    workdir_abs = str(Path(workdir).resolve())

    existed_before = path.exists()
    existing_fm: dict[str, Any] = {}
    if existed_before:
        existing_fm, _ = _split_frontmatter(path.read_text(encoding="utf-8"))

    # Merge: existing values win for created_at + applied_in_repos +
    # cross_repo_validated (state owned by repos that applied the memory,
    # NOT by the writer). Everything else is refreshed.
    now = iso_utc()
    fm: dict[str, Any] = {
        "name": name,
        "description": description,
        "type": type_,
        "source_repo": _detect_git_remote(Path(workdir_abs)),
        "source_workdir": workdir_abs,
        "source_run_id": run_id,
        "source_host": host,
        "as_of_commit": _detect_git_head(Path(workdir_abs)),  # repo HEAD at write time
        "cross_repo_validated": existing_fm.get("cross_repo_validated", False),
        "applied_in_repos": list(existing_fm.get("applied_in_repos", []) or []),
        "created_at": existing_fm.get("created_at", now),
        "last_updated_at": now,
    }
    if extra_frontmatter:
        # Copy the caller's dict before mutating — otherwise our .pop() drains the
        # caller's dict as a side effect, which surprised a code reviewer 2026-05-11.
        extra = dict(extra_frontmatter)
        for k in REQUIRED_PROVENANCE_FIELDS | {"name", "description", "type"}:
            extra.pop(k, None)
        fm.update(extra)

    content = _emit_frontmatter(fm) + "\n" + body.lstrip("\n")
    _atomic_write_text(path, content)

    # Append to memory index for sibling discovery.
    # Use path.exists() rather than existing_fm truthiness — a file that existed
    # but had unparseable/empty frontmatter would otherwise log as a fresh "write"
    # even though it's being overwritten. The on-disk state is the ground truth;
    # this branch checks file presence BEFORE the atomic write above so the
    # post-write state doesn't confuse the comparison.
    # NOTE: by this point _atomic_write_text has already replaced the file, so we
    # must use the pre-write existence signal — captured as `existed_before` below.
    action = "update" if existed_before else "write"
    try:
        mi.append_row(
            memory_dir,
            run_id=run_id,
            action=action,
            file_rel=file_rel,
            source_repo=fm["source_repo"],
            source_workdir=workdir_abs,
            source_host=host,
        )
    except Exception as exc:  # never block the write on index failure
        print(f"WARN: memory_index append failed: {exc}", file=sys.stderr)

    # Global store audit/freshness ledger. Fire-and-forget: the memory file is
    # canonical, and ledger repair is possible from on-disk files.
    try:
        memory_root = mul.infer_memory_root_for_path(path, fallback=memory_dir)
        mul.append_update(
            memory_root=memory_root,
            action=action,
            path=path,
            writer="memory_writer.py",
            run_id=run_id,
            source_repo=fm.get("source_repo"),
            source_workdir=workdir_abs,
            source_commit=fm.get("as_of_commit"),
            source_host=host,
            memory_id=name,
            summary=description,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: memory_update_ledger append failed: {exc}", file=sys.stderr)

    # M5 + Step 8: emit memory-write telemetry to TELEMETRY.jsonl (separate file
    # from INDEX.jsonl; preserves M5 discovery schema untouched). Fire-and-forget.
    try:
        try:
            from scripts import memory_telemetry as _mt  # type: ignore  # noqa: PLC0415
        except ImportError:
            import memory_telemetry as _mt  # type: ignore  # noqa: PLC0415
        _why = (extra_frontmatter or {}).get("why_durable") or description or "(unspecified)"
        _mt.emit_write(
            phase=str((extra_frontmatter or {}).get("phase", "unknown")),
            writer=host,
            memory_id=str(memory_dir / file_rel),
            why_durable=_why,
            action=action,
            telemetry_path=memory_dir / "TELEMETRY.jsonl",
        )
    except Exception as exc:  # noqa: BLE001 — fire-and-forget per protocol
        print(f"WARN: memory_telemetry emit_write failed: {exc}", file=sys.stderr)

    return fm


def mark_applied(
    memory_dir: Path,
    file_rel: str,
    applying_repo: str,
    applying_workdir: str,
    applying_run_id: str,
) -> dict:
    """Record that `applying_repo` successfully used the memory at `file_rel`.

    Appends to `applied_in_repos[]` (dedup) and flips `cross_repo_validated`
    to True once at least one repo OTHER than `source_repo` has applied it.

    Returns the updated frontmatter.

    Raises FileNotFoundError if the memory file doesn't exist.
    """
    path = memory_dir / file_rel
    if not path.exists():
        raise FileNotFoundError(f"memory file not found: {path}")
    text = path.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)
    applying_workdir_abs = str(Path(applying_workdir).resolve())

    applied = list(fm.get("applied_in_repos", []) or [])
    # Each entry is `{repo, workdir, run_id, applied_at}`. Dedup by (repo, workdir).
    seen = {(e.get("repo"), e.get("workdir")) for e in applied if isinstance(e, dict)}
    key = (applying_repo, applying_workdir_abs)
    if key not in seen:
        applied.append({
            "repo": applying_repo,
            "workdir": applying_workdir_abs,
            "run_id": applying_run_id,
            "applied_at": iso_utc(),
        })
    fm["applied_in_repos"] = applied
    # Cross-repo validation: True if any applying repo differs from source.
    source_repo = fm.get("source_repo")
    source_workdir = fm.get("source_workdir")
    cross_validated = any(
        (e.get("repo") and e.get("repo") != source_repo)
        or (e.get("workdir") and e.get("workdir") != source_workdir)
        for e in applied
        if isinstance(e, dict)
    )
    fm["cross_repo_validated"] = cross_validated
    fm["last_updated_at"] = iso_utc()

    _atomic_write_text(path, _emit_frontmatter(fm) + "\n" + body.lstrip("\n"))
    try:
        memory_root = mul.infer_memory_root_for_path(path, fallback=memory_dir)
        mul.append_update(
            memory_root=memory_root,
            action="mark-applied",
            path=path,
            writer="memory_writer.py",
            run_id=applying_run_id,
            source_repo=applying_repo,
            source_workdir=applying_workdir_abs,
            memory_id=str(fm.get("name") or Path(file_rel).stem),
            summary=f"Marked {file_rel} as applied",
            metadata={"cross_repo_validated": cross_validated},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: memory_update_ledger append failed: {exc}", file=sys.stderr)
    return fm


def migrate(
    memory_dir: Path,
    *,
    run_id: str,
    workdir: str,
    host: str,
    dry_run: bool = False,
) -> dict:
    """Idempotent backfill: add provenance frontmatter to every memory file
    in `memory_dir` that doesn't already have all the required fields.

    Files that already have all REQUIRED_PROVENANCE_FIELDS are skipped.
    `name`, `description`, `type` are taken from existing frontmatter if
    present; otherwise inferred from filename (`name` from filename stem,
    `description` empty, `type` from filename prefix or 'pattern').

    Returns a summary dict: {migrated: [paths], skipped: [paths], errors: [...]}.
    """
    if not memory_dir.exists():
        return {"migrated": [], "skipped": [], "errors": [], "scanned": 0}
    workdir_abs = str(Path(workdir).resolve())
    now = iso_utc()
    summary: dict[str, list] = {"migrated": [], "skipped": [], "errors": []}
    scanned = 0

    for path in memory_dir.glob("*.md"):
        scanned += 1
        if path.name == "MEMORY.md":
            summary["skipped"].append(str(path))  # index file
            continue
        if path.name == "INDEX.jsonl":
            summary["skipped"].append(str(path))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            summary["errors"].append({"file": str(path), "error": str(exc)})
            continue
        fm, body = _split_frontmatter(text)
        if REQUIRED_PROVENANCE_FIELDS.issubset(fm.keys()):
            summary["skipped"].append(str(path))
            continue

        # Backfill — infer missing scalars defensively.
        stem = path.stem
        inferred_name = fm.get("name") or stem
        inferred_desc = fm.get("description") or ""
        inferred_type = fm.get("type")
        if not inferred_type:
            # Filename prefix heuristic.
            prefix = stem.split("_", 1)[0]
            inferred_type = prefix if prefix in VALID_TYPES else "pattern"

        new_fm: dict[str, Any] = {
            "name": inferred_name,
            "description": inferred_desc,
            "type": inferred_type,
            "source_repo": _detect_git_remote(Path(workdir_abs)),
            "source_workdir": workdir_abs,
            "source_run_id": run_id,
            "source_host": host,
            "cross_repo_validated": fm.get("cross_repo_validated", False),
            "applied_in_repos": list(fm.get("applied_in_repos", []) or []),
            "created_at": fm.get("created_at", now),
            "last_updated_at": now,
            "migration_note": "backfilled by memory_writer.py migrate",
        }
        # Preserve any non-provenance, non-required keys from the original.
        preserved_keys = set(fm.keys()) - set(new_fm.keys())
        for k in preserved_keys:
            new_fm[k] = fm[k]

        content = _emit_frontmatter(new_fm) + "\n" + body.lstrip("\n")
        if not dry_run:
            try:
                _atomic_write_text(path, content)
                try:
                    memory_root = mul.infer_memory_root_for_path(path, fallback=memory_dir)
                    mul.append_update(
                        memory_root=memory_root,
                        action="migrate",
                        path=path,
                        writer="memory_writer.py",
                        run_id=run_id,
                        source_repo=new_fm.get("source_repo"),
                        source_workdir=workdir_abs,
                        source_host=host,
                        memory_id=str(new_fm.get("name") or path.stem),
                        summary="Backfilled memory provenance frontmatter",
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"WARN: memory_update_ledger append failed: {exc}", file=sys.stderr)
            except OSError as exc:
                summary["errors"].append({"file": str(path), "error": str(exc)})
                continue
        summary["migrated"].append(str(path))

    summary["scanned"] = scanned
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_write(args: argparse.Namespace) -> int:
    body = args.body
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    if body is None:
        # Fall back to stdin
        body = sys.stdin.read()
    # P2: --file is optional; auto-derive ``<date>-<type>-<slug>.md`` when missing.
    file_rel = args.file or canonical_filename(type_=args.type, name=args.name)
    try:
        fm = write(
            _cli_memory_dir(args),
            file_rel=file_rel,
            body=body,
            name=args.name,
            description=args.description,
            type_=args.type,
            run_id=args.run_id,
            workdir=args.workdir,
            host=args.host,
            scope=args.scope,
            project=_cli_resolved_project(args),
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        json.dump(fm, sys.stdout, sort_keys=True, default=str)
        sys.stdout.write("\n")
    return 0


def _cli_mark_applied(args: argparse.Namespace) -> int:
    try:
        fm = mark_applied(
            _cli_memory_dir(args),
            file_rel=args.file,
            applying_repo=args.applying_repo,
            applying_workdir=args.applying_workdir,
            applying_run_id=args.applying_run_id,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        json.dump(fm, sys.stdout, sort_keys=True, default=str)
        sys.stdout.write("\n")
    return 0


def _cli_migrate(args: argparse.Namespace) -> int:
    summary = migrate(
        _cli_memory_dir(args),
        run_id=args.run_id,
        workdir=args.workdir,
        host=args.host,
        dry_run=args.dry_run,
    )
    if args.json:
        json.dump(summary, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(f"scanned: {summary['scanned']}")
        print(f"migrated: {len(summary['migrated'])}")
        print(f"skipped: {len(summary['skipped'])}")
        print(f"errors: {len(summary['errors'])}")
        if args.dry_run:
            print("(dry-run; no files modified)")
    return 1 if summary["errors"] else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--memory-dir",
        default=None,
        help="Override default canonical memory dir (testing/advanced).",
    )
    p.add_argument(
        "--scope",
        choices=("top-level", "project"),
        default="top-level",
        help="Default write lane when --memory-dir is omitted.",
    )
    p.add_argument(
        "--project",
        default=None,
        help="Project tag for --scope project. Defaults to resolver on --workdir.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="Create or update a memory file")
    w.add_argument("--file", default=None, help="Relative path inside memory-dir (optional; auto-derived from --type+--name when omitted)")
    w.add_argument("--name", required=True)
    w.add_argument("--description", required=True)
    w.add_argument("--type", required=True, choices=sorted(VALID_TYPES))
    w.add_argument("--run-id", required=True)
    w.add_argument("--workdir", required=True)
    w.add_argument("--host", required=True, choices=sorted(VALID_HOSTS))
    bodygrp = w.add_mutually_exclusive_group()
    bodygrp.add_argument("--body", default=None, help="Inline body")
    bodygrp.add_argument("--body-file", default=None, help="Read body from this file")
    w.add_argument("--json", action="store_true")

    a = sub.add_parser("mark-applied", help="Record cross-repo application")
    a.add_argument("--file", required=True)
    a.add_argument("--applying-repo", required=True, help="Git remote of the applying repo, or 'null'")
    a.add_argument("--applying-workdir", required=True)
    a.add_argument("--applying-run-id", required=True)
    a.add_argument("--json", action="store_true")

    m = sub.add_parser("migrate", help="Backfill provenance frontmatter")
    m.add_argument("--run-id", required=True)
    m.add_argument("--workdir", required=True)
    m.add_argument("--host", required=True, choices=sorted(VALID_HOSTS))
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--json", action="store_true")

    return p.parse_args(argv)


def _cli_resolved_project(args: argparse.Namespace) -> str | None:
    """Resolve the project tag the same way _cli_memory_dir does.

    Kept in lockstep so the scope/project passed to write() (for path
    normalization) matches the lane _cli_memory_dir resolves. When
    --scope is top-level, returns None — the writer guard ignores it.
    """
    if args.scope != "project":
        return None
    workdir = (
        getattr(args, "workdir", None)
        or getattr(args, "applying_workdir", None)
        or "."
    )
    if args.project:
        return args.project
    from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415
    return resolve_project(Path(workdir))


def _cli_memory_dir(args: argparse.Namespace) -> Path:
    if args.memory_dir:
        return Path(args.memory_dir)
    if args.scope == "project":
        from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415

        workdir = getattr(args, "workdir", None) or getattr(args, "applying_workdir", None) or "."
        project = args.project or resolve_project(Path(workdir))
        return project_lessons_dir(project)
    return default_memory_dir()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dispatch = {
        "write": _cli_write,
        "mark-applied": _cli_mark_applied,
        "migrate": _cli_migrate,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
