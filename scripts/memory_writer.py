#!/usr/bin/env python3
"""Canonical writer for build-loop memory files with provenance frontmatter.

Companion to session_registry.py + memory_index.py. Memory writes go
through this script so every entry under ~/.build-loop/memory/ carries
the provenance fields that make cross-repo trust gradients possible.

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

DEFAULT_MEMORY_DIR = Path.home() / ".build-loop" / "memory"

VALID_HOSTS = frozenset({"claude_code", "codex", "gemini", "other"})
VALID_TYPES = frozenset({
    "tool", "deployment", "library-choice", "user-preference", "pattern",
    "feedback", "reference", "design", "convention", "gotcha", "decision",
    "contract",
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

    path = memory_dir / file_rel
    workdir_abs = str(Path(workdir).resolve())

    existing_fm: dict[str, Any] = {}
    if path.exists():
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
        "cross_repo_validated": existing_fm.get("cross_repo_validated", False),
        "applied_in_repos": list(existing_fm.get("applied_in_repos", []) or []),
        "created_at": existing_fm.get("created_at", now),
        "last_updated_at": now,
    }
    if extra_frontmatter:
        # Forbid overwriting provenance fields via extra.
        for k in REQUIRED_PROVENANCE_FIELDS | {"name", "description", "type"}:
            extra_frontmatter.pop(k, None)
        fm.update(extra_frontmatter)

    content = _emit_frontmatter(fm) + "\n" + body.lstrip("\n")
    _atomic_write_text(path, content)

    # Append to memory index for sibling discovery.
    action = "update" if existing_fm else "write"
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
    try:
        fm = write(
            Path(args.memory_dir),
            file_rel=args.file,
            body=body,
            name=args.name,
            description=args.description,
            type_=args.type,
            run_id=args.run_id,
            workdir=args.workdir,
            host=args.host,
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
            Path(args.memory_dir),
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
        Path(args.memory_dir),
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
        default=str(DEFAULT_MEMORY_DIR),
        help="Override default ~/.build-loop/memory/ (testing).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="Create or update a memory file")
    w.add_argument("--file", required=True, help="Relative path inside memory-dir")
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
