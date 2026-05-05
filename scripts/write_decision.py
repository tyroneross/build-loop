#!/usr/bin/env python3
"""Atomic decision writer for repo-local episodic memory.

Mirrors the atomicity contract of `write_run_entry.py`:
  - fcntl.flock(LOCK_EX) on a sidecar `.lock` file
  - tempfile + os.replace for any updated file
  - exit codes 0/1/2 (success / validation / filesystem)

The "memory triad" write-once pattern (design §9.A) — every successful
invocation produces THREE artifacts atomically as a unit:
  1. `.episodic/decisions/NNNN-YYYY-MM-DD-slug.md`     (canonical MADR)
  2. updated `.episodic/decisions/INDEX.md`             (browseable summary)
  3. one line appended to `.episodic/events.jsonl`     (timeline event)

Topic-identity supersession (design §10):
  Same `primary_tag + entity` triggers an overwrite check:
    - higher confidence auto-supersedes lower (prior moves to _history/)
    - equal confidence requires `--supersedes <id>` flag
    - lower confidence is rejected (exit 1)

Postgres dual-write (Phase 2, opt-in via --db / --no-db, default --db):
  After file writes succeed, embed the decision body via the
  `embed_backend` abstraction (MLX `mxbai-embed-large-v1` by default,
  Ollama `mxbai-embed-large` fallback, both 1024-dim), then INSERT a
  row into `agent_memory.<schema>.semantic_facts` over a persistent
  psycopg connection (`scripts/db.py`). DB errors LOG and continue —
  the file is canonical; DB is regenerable via
  `sync_db_from_files.py`.

Contract:
  stdout      -> decision_id (zero-padded 4-digit) on success, nothing else
  stderr      -> human-readable log lines
  exit 0      -> success
  exit 1      -> validation error (bad args, vocab violation, supersession refused)
  exit 2      -> filesystem error (permission denied, disk full, lock timeout)

Phase 1 uses stdlib only. Phase 2 DB path uses `psycopg[binary]` (added
2026-05-04 to enable batched Stop-hook writes; ~5-10ms/query vs
~50-100ms via psql subprocess).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

LOCK_TIMEOUT_S = 15
CONFIDENCE_ORDER = {"assumed": 0, "inferred": 1, "confirmed": 2, "explicit": 3}
VALID_CONFIDENCES = set(CONFIDENCE_ORDER)
VALID_STATUSES = {"proposed", "accepted", "superseded", "rejected"}
VALID_SOURCES = {
    "manual",
    "auto-explicit",
    "auto-confirmed",
    "auto-inferred",
    "auto-assumed",
    "migration",
    "orchestrator",
}
VALID_TYPES = {"decision", "issue", "research"}
# v2 metadata enums (added 2026-05-04, design §15).
# `tool` and `task_category` are closed; `model`, `author`, `project` are free-form.
VALID_TOOLS = {
    "claude-code",
    "codex",
    "cursor",
    "aider",
    "goose",
    "manual",
    "migration",
    "unknown",
}
VALID_TASK_CATEGORIES = {
    "feature",
    "bugfix",
    "refactor",
    "research",
    "docs",
    "migration",
    "experiment",
    "config",
    "unknown",
}

# v3 metadata enums (added 2026-05-04, design §16).
# `confidence_source` decouples *who asserted* from *how it was captured*
# (the pre-existing `source` field still records the capture mechanism).
VALID_CONFIDENCE_SOURCES = {
    "user_statement",
    "ai_inference",
    "tool_extraction",
    "external_import",
    "unknown",
}
# `domain` is a stricter MECE axis than `primary_tag` (which stays as a
# legacy alias). Keep this enum closed; expand only with a TAXONOMY bump.
VALID_DOMAINS = {
    "ui",
    "api",
    "data",
    "search",
    "auth",
    "build",
    "infra",
    "tooling",
    "docs",
    "test",
    "meta",
    "unknown",
}
# `goal` captures *why* the work was done — orthogonal to domain.
VALID_GOALS = {
    "user-value",
    "reliability",
    "performance",
    "security",
    "dev-velocity",
    "maintainability",
    "compliance",
    "learning",
    "unknown",
}

# Default embedding model version. Re-embed is required when the active
# backend's model changes; the version stamp is what makes that detectable.
DEFAULT_EMBEDDING_MODEL_VERSION = "mxbai-embed-large-v1"

VALID_EVENT_KINDS = {
    "run_completed",
    "run_failed",
    "decision_proposed",
    "decision_accepted",
    "decision_superseded",
    "decision_revoked",
    "issue_opened",
    "issue_closed",
    "library_added",
    "library_bumped",
    "library_removed",
    "architecture_component_added",
    "architecture_component_removed",
    "manual_intervention",
    "escalation",
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------- atomic primitives (mirrors write_run_entry.py) ----------


class LockedFile:
    """Exclusive fcntl.flock on a sidecar lockfile. Auto-released on close."""

    def __init__(self, target: Path, timeout_s: float = LOCK_TIMEOUT_S) -> None:
        self.lock_path = target.with_suffix(target.suffix + ".lock")
        self.timeout_s = timeout_s
        self._fd: int | None = None

    def __enter__(self) -> "LockedFile":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise TimeoutError(
                        f"Could not acquire lock on {self.lock_path} within {self.timeout_s}s"
                    )
                time.sleep(0.05)

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".tmp.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


# ---------- frontmatter helpers ----------


_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_frontmatter(text: str) -> dict | None:
    """Tiny YAML-subset parser. Handles only what this writer emits.

    Supported value shapes:
      key: scalar
      key: 'quoted scalar'
      key: null
      key: [item, 'item with spaces', null]
    """
    m = _FM_RE.match(text)
    if not m:
        return None
    body = m.group(1)
    out: dict[str, Any] = {}
    for line in body.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        out[key] = _parse_yaml_value(val)
    return out


def _parse_yaml_value(val: str) -> Any:
    if val == "" or val == "null":
        return None
    if val.startswith("'") and val.endswith("'"):
        return val[1:-1]
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]
    # Bare integer (positive or negative). Floats are intentionally NOT coerced
    # — none of the v1/v2/v3 fields are floats and matching ".5" eagerly would
    # collide with date-like or version-like strings.
    if re.match(r"^-?\d+$", val):
        try:
            return int(val)
        except ValueError:
            pass
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        items: list[Any] = []
        # naive split on commas; values may be 'quoted, with comma'
        depth = 0
        cur = ""
        in_quote = False
        for ch in inner:
            if ch == "'" and not in_quote:
                in_quote = True
                cur += ch
            elif ch == "'" and in_quote:
                in_quote = False
                cur += ch
            elif ch == "," and not in_quote and depth == 0:
                items.append(_parse_yaml_value(cur.strip()))
                cur = ""
            else:
                cur += ch
        if cur.strip():
            items.append(_parse_yaml_value(cur.strip()))
        return items
    return val


def emit_frontmatter(fm: dict[str, Any]) -> str:
    """Emit the small YAML subset above. Order-preserving (python ≥3.7)."""
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {_yaml_emit_value(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _yaml_emit_value(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[" + ", ".join(_yaml_emit_scalar_for_list(x) for x in v) + "]"
    return _yaml_emit_scalar(v)


def _yaml_emit_scalar(v: Any) -> str:
    s = str(v)
    # Quote when leading char is YAML-special, or value would otherwise look like null/number/bool/list,
    # or contains chars that confuse the tiny parser.
    needs_quote = (
        s == ""
        or s.lower() in {"null", "true", "false", "yes", "no"}
        or s[0].isdigit()
        or any(c in s for c in [":", "#", "[", "]", "{", "}", "'", '"'])
    )
    if needs_quote:
        return "'" + s.replace("'", "''") + "'"
    return s


def _yaml_emit_scalar_for_list(v: Any) -> str:
    if v is None:
        return "null"
    return _yaml_emit_scalar(v)


# ---------- TAXONOMY loader (small, just what write_decision needs) ----------


def load_taxonomy(workdir: Path) -> dict[str, set[str]]:
    """Return {tags, primary_tags, confidences, sources, statuses}.

    Reads `.semantic/TAXONOMY.md` if present. Falls back to conservative
    defaults so the writer still works in a fresh tree (the test fixture
    seeds its own TAXONOMY).
    """
    tax_path = workdir / ".semantic" / "TAXONOMY.md"
    defaults = {
        "tags": {
            "architecture",
            "data",
            "ui",
            "infra",
            "tooling",
            "process",
            "security",
            "performance",
            "testing",
        },
        "primary_tags": {
            "architecture",
            "data",
            "ui",
            "infra",
            "tooling",
            "process",
            "security",
            "performance",
            "testing",
        },
        "sources": set(VALID_SOURCES),
        "statuses": set(VALID_STATUSES),
    }
    if not tax_path.exists():
        return defaults
    text = tax_path.read_text(encoding="utf-8")
    # Parse the bullet items in §1 (Decision tags).
    tags: set[str] = set()
    in_tags = False
    for line in text.splitlines():
        if line.startswith("## 1.") or line.lower().startswith("## 1. decision tags"):
            in_tags = True
            continue
        if in_tags and line.startswith("## "):
            break
        if in_tags:
            m = re.match(r"^- `([a-z][a-z0-9-]*)`", line)
            if m:
                tags.add(m.group(1))
    if tags:
        defaults["tags"] = tags
        defaults["primary_tags"] = set(tags)
    # Parse §6 (Source attribution) for sources.
    sources: set[str] = set()
    in_src = False
    for line in text.splitlines():
        if line.startswith("## 6.") or "Source attribution" in line:
            in_src = True
            continue
        if in_src and line.startswith("## "):
            break
        if in_src:
            m = re.match(r"^- `([a-z][a-z0-9-]*)`", line)
            if m:
                sources.add(m.group(1))
    if sources:
        defaults["sources"] = sources
    return defaults


# ---------- Decision discovery / topic identity ----------


def slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:80] or "decision"


def list_decisions(decisions_dir: Path) -> list[Path]:
    if not decisions_dir.exists():
        return []
    return sorted(decisions_dir.glob("[0-9][0-9][0-9][0-9]-*.md"))


def next_id(decisions_dir: Path, history_dir: Path) -> str:
    used: set[int] = set()
    for d in (decisions_dir, history_dir):
        if d.exists():
            for f in d.iterdir():
                m = re.match(r"^(\d{4})-", f.name)
                if m:
                    used.add(int(m.group(1)))
    nxt = (max(used) + 1) if used else 1
    return f"{nxt:04d}"


def find_same_topic(decisions_dir: Path, primary_tag: str, entity: str) -> tuple[Path, dict] | None:
    for f in list_decisions(decisions_dir):
        text = f.read_text(encoding="utf-8")
        fm = parse_frontmatter(text) or {}
        if fm.get("primary_tag") == primary_tag and fm.get("entity") == entity:
            return f, fm
    return None


# ---------- INDEX regenerate ----------


def regenerate_index(decisions_dir: Path, confidence_floor: str = "confirmed") -> None:
    """Write `decisions/INDEX.md` with rollup of frontmatter.

    Filters by `confidence >= floor` per design §10 (default confirmed).
    """
    floor = CONFIDENCE_ORDER[confidence_floor]
    rows: list[tuple[str, dict, Path]] = []
    for f in list_decisions(decisions_dir):
        fm = parse_frontmatter(f.read_text(encoding="utf-8")) or {}
        conf = fm.get("confidence", "assumed")
        if CONFIDENCE_ORDER.get(conf, 0) < floor:
            continue
        rows.append((str(fm.get("id", "")), fm, f))
    rows.sort(key=lambda r: r[0])

    lines = [
        "# Decisions — INDEX",
        "",
        f"_Auto-generated by `scripts/regenerate_knowledge_index.py`. Default filter: confidence ≥ {confidence_floor}._",
        "",
        "| id | date | title | primary_tag | entity | confidence | status |",
        "|---|---|---|---|---|---|---|",
    ]
    for did, fm, f in rows:
        lines.append(
            f"| {did} | {fm.get('date','')} | [{fm.get('title','')}]({f.name}) | "
            f"{fm.get('primary_tag','')} | {fm.get('entity','')} | "
            f"{fm.get('confidence','')} | {fm.get('status','')} |"
        )
    if not rows:
        lines.append("| _(no entries at or above confidence floor)_ |  |  |  |  |  |  |")
    out = "\n".join(lines) + "\n"
    atomic_write_bytes(decisions_dir / "INDEX.md", out.encode("utf-8"))


# ---------- events.jsonl append ----------


def append_event(events_path: Path, event: dict) -> None:
    if event.get("kind") not in VALID_EVENT_KINDS:
        raise ValueError(f"event kind must be in vocabulary; got {event.get('kind')!r}")
    with LockedFile(events_path):
        existing = events_path.read_bytes() if events_path.exists() else b""
        line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        atomic_write_bytes(events_path, existing + line)


# ---------- MADR rendering ----------


def render_madr(fm: dict[str, Any], body: dict[str, str]) -> str:
    fm_text = emit_frontmatter(fm)
    parts = [fm_text, f"# {fm.get('title','')}", ""]
    if body.get("context"):
        parts.append("## Context\n")
        parts.append(body["context"])
        parts.append("")
    if body.get("decision"):
        parts.append("## Decision\n")
        parts.append(body["decision"])
        parts.append("")
    if body.get("alternatives"):
        parts.append("## Alternatives considered\n")
        parts.append(body["alternatives"])
        parts.append("")
    if body.get("consequences"):
        parts.append("## Consequences\n")
        parts.append(body["consequences"])
        parts.append("")
    if body.get("notes"):
        parts.append("## Notes\n")
        parts.append(body["notes"])
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


# ---------- DB dual-write (Phase 2) ----------


def db_dualwrite(
    decision_id: str,
    fm: dict[str, Any],
    body_text: str,
    workdir: Path,
    schema: str,
    embed_model: str,  # retained for back-compat; embed_backend reads its own env
) -> None:
    """Embed body and INSERT into agent_memory.<schema>.semantic_facts.

    Best-effort: any failure is logged and swallowed. The file is canonical.
    Uses the persistent psycopg connection from `db.py`. Embedding goes
    through `embed_backend.embed` (MLX default, Ollama fallback, 1024-dim).
    """
    try:
        from embed_backend import embed as _embed  # type: ignore  # noqa: PLC0415

        try:
            embedding = _embed(body_text)
        except Exception as e:  # noqa: BLE001
            log(f"db dual-write: embed unavailable ({e}); skipping db row for decision {decision_id}")
            return
        # Local import keeps Phase 1 (`--no-db` test runs) from requiring psycopg.
        from db import execute, vector_literal  # type: ignore  # noqa: PLC0415

        subject = f"decision:{decision_id}"
        predicate = fm.get("primary_tag") or "decision"
        obj_summary = fm.get("title") or ""
        metadata = {
            "decision_id": decision_id,
            "entity": fm.get("entity"),
            "tags": fm.get("tags"),
            "status": fm.get("status"),
            "confidence": fm.get("confidence"),
            "source": fm.get("source"),
            "date": fm.get("date"),
            # v2 fields mirrored into JSONB so older readers still see them.
            "project": fm.get("project"),
            "tool": fm.get("tool"),
            "model": fm.get("model"),
            "task_category": fm.get("task_category"),
            "author": fm.get("author"),
            "files_touched": fm.get("files_touched") or [],
            "closing_commit": fm.get("closing_commit"),
            # v3 mirrored into JSONB (design §16) so older readers still see them.
            "confidence_source": fm.get("confidence_source"),
            "confirmation_count": fm.get("confirmation_count"),
            "valid_until": fm.get("valid_until"),
            "causal_parent_id": fm.get("causal_parent_id"),
            "embedding_model_version": fm.get("embedding_model_version"),
            "domain": fm.get("domain"),
            "goal": fm.get("goal"),
        }
        # Schema is operator-controlled (CLI flag), not user input. Validate shape
        # to keep the f-string interpolation safe; psycopg cannot bind table names.
        if not re.match(r"^[a-z][a-z0-9_]*$", schema):
            raise ValueError(f"unsafe schema name: {schema!r}")
        # v2: write typed columns when present (graceful degrade if migration
        # hasn't run yet — caught by the broad except below).
        sql = (
            f"INSERT INTO {schema}.semantic_facts "
            "(subject, predicate, object, confidence, status, embedding, metadata, "
            " project, tool, model, task_category, author, files_touched, closing_commit, "
            " confidence_source, confirmation_count, valid_until, causal_parent_id, "
            " embedding_model_version, domain, goal) "
            "VALUES (%s, %s, %s, %s, 'active', %s::vector, %s::jsonb, "
            " %s, %s, %s, %s, %s, %s, %s, "
            " %s, %s, %s, %s, %s, %s, %s);"
        )
        files = fm.get("files_touched") or []
        # confirmation_count may arrive from frontmatter as int already; coerce
        # defensively because YAML scalars sometimes parse as strings.
        cc_val = fm.get("confirmation_count")
        try:
            cc_db = int(cc_val) if cc_val is not None else 0
        except (TypeError, ValueError):
            cc_db = 0
        execute(
            sql,
            (
                subject,
                predicate,
                obj_summary,
                _confidence_to_float(fm.get("confidence")),
                vector_literal(embedding),
                json.dumps(metadata, ensure_ascii=False),
                fm.get("project"),
                fm.get("tool"),
                fm.get("model"),
                fm.get("task_category"),
                fm.get("author"),
                list(files) if isinstance(files, list) else [],
                fm.get("closing_commit"),
                fm.get("confidence_source"),
                cc_db,
                fm.get("valid_until"),
                fm.get("causal_parent_id"),
                fm.get("embedding_model_version"),
                fm.get("domain"),
                fm.get("goal"),
            ),
        )
        log(f"db dual-write: inserted semantic_facts row for decision {decision_id}")
    except Exception as e:  # noqa: BLE001
        log(f"db dual-write: error (file write succeeded; DB regenerable via sync_db_from_files.py): {e}")


def _confidence_to_float(c: str | None) -> float:
    return {"assumed": 0.25, "inferred": 0.5, "confirmed": 0.75, "explicit": 1.0}.get(c or "", 0.5)


def ollama_embed(text: str, model: str) -> list[float] | None:
    """Deprecation shim. Delegates to `embed_backend.embed`.

    Kept for back-compat with code that previously imported this name
    from write_decision (e.g. recall.py, sync_db_from_files.py,
    scan_transcript_for_decisions.py). Returns None on any error so
    legacy callers' "if embedding is None: skip" branches still work.

    The `model` argument is ignored — embed_backend reads $EMBED_BACKEND
    and $EMBED_MODEL from the environment. This is intentional: the
    abstraction owns model selection now, not the caller.
    """
    try:
        from embed_backend import embed as _embed  # type: ignore  # noqa: PLC0415

        return _embed(text)
    except Exception as e:  # noqa: BLE001
        log(f"embed_backend (legacy ollama_embed shim): {e}")
        return None


# NOTE: `psql_run` was removed when production scripts migrated to psycopg
# (`scripts/db.py`) on 2026-05-04. Callers should `from db import execute,
# execute_script, query, query_one`. The legacy name is preserved here as
# a deprecation shim for any out-of-tree code that might still import it.


def psql_run(sql: str, workdir: Path) -> None:  # pragma: no cover - shim
    """Deprecated. Use `db.execute_script(sql)` for multi-statement SQL or
    `db.execute(sql, params)` for parameterized single statements.
    """
    from db import execute_script  # type: ignore  # noqa: PLC0415

    execute_script(sql)


# ---------- main pipeline ----------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Atomic decision writer for repo-local episodic memory.")
    p.add_argument("--workdir", default=".", help="Project root containing .episodic/, .semantic/")
    p.add_argument("--title", required=True)
    p.add_argument("--decision", required=True, help="One-sentence decision body")
    p.add_argument("--context", default="")
    p.add_argument("--alternatives", default="")
    p.add_argument("--consequences", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--tags", required=True, help="Comma-separated tag list")
    p.add_argument("--primary-tag", required=True)
    p.add_argument("--entity", required=True)
    p.add_argument("--confidence", required=True, choices=sorted(VALID_CONFIDENCES))
    p.add_argument("--status", default="accepted", choices=sorted(VALID_STATUSES))
    p.add_argument("--source", default="manual")
    p.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to today (UTC)")
    p.add_argument("--related-runs", default="", help="Comma-separated run_ids")
    p.add_argument("--related-decisions", default="", help="Comma-separated decision IDs")
    p.add_argument("--supersedes", default=None, help="Decision ID this replaces (overrides confidence ladder)")
    p.add_argument("--bookmark-snapshot-id", default=None)
    p.add_argument("--captured-turn-excerpt", default=None)

    # v2 metadata (design §15). All optional at the CLI; defaults applied
    # in `_apply_v2_defaults` so legacy callers continue to work.
    p.add_argument("--project", default=None, help="Repo-scoped project name. Default: derived from --entity prefix or $CLAUDE_PROJECT_DIR basename.")
    p.add_argument("--tool", default=None, help=f"Authoring tool. One of: {sorted(VALID_TOOLS)}. Default: 'claude-code' (manual MADRs use 'manual').")
    p.add_argument("--model", default=None, help="Free-form model ID. Default: 'claude-opus-4-7'.")
    p.add_argument("--task-category", default=None, help=f"Task category. One of: {sorted(VALID_TASK_CATEGORIES)}. Default: 'unknown'.")
    p.add_argument("--author", default=None, help="Free-form author identifier. Default: $USER env var, else 'unknown'.")
    p.add_argument("--files-touched", default=None, help="Comma-separated repo-relative paths. Default: empty list (or git diff if --infer-files-touched).")
    p.add_argument("--infer-files-touched", action="store_true", help="Populate files_touched from `git diff --name-only HEAD~1 HEAD` when no --files-touched is given.")
    p.add_argument("--closing-commit", default=None, help="Git SHA that closed this decision. Default: null.")
    p.add_argument("--last-validated", default=None, help="ISO date for last_validated. Default: null.")
    p.add_argument("--last-accessed", default=None, help="ISO date for last_accessed. Default: null.")

    # v3 metadata (design §16). All optional at the CLI; defaults applied
    # in `apply_v3_defaults` so legacy callers continue to work.
    p.add_argument(
        "--confidence-source",
        default=None,
        help=(
            "Who asserted the fact (decoupled from `source`, which is *how* it was captured). "
            f"One of: {sorted(VALID_CONFIDENCE_SOURCES)}. Default: derived from --source "
            "(manual→user_statement, auto-*→ai_inference, migration→external_import)."
        ),
    )
    p.add_argument(
        "--confirmation-count",
        default=None,
        help="Times this memory was successfully acted upon. Integer >= 0. Default: 0.",
    )
    p.add_argument(
        "--valid-until",
        default=None,
        help="Explicit expiration as ISO date (YYYY-MM-DD or full ISO-8601). Default: null.",
    )
    p.add_argument(
        "--causal-parent-id",
        default=None,
        help="Decision id this one was caused by. Enables decision-chain queries. Default: null.",
    )
    p.add_argument(
        "--embedding-model-version",
        default=None,
        help=(
            f"Model that produced the embedding stored alongside this fact. Default: "
            f"$EMBED_MODEL env var if set, else {DEFAULT_EMBEDDING_MODEL_VERSION!r}. "
            "Triggers re-embed when the model id changes."
        ),
    )
    p.add_argument(
        "--domain",
        default=None,
        help=f"Subject domain (stricter MECE than primary_tag). One of: {sorted(VALID_DOMAINS)}. Default: 'unknown'.",
    )
    p.add_argument(
        "--goal",
        default=None,
        help=f"Why the work was done. One of: {sorted(VALID_GOALS)}. Default: 'unknown'.",
    )

    # DB dual-write (Phase 2)
    p.add_argument("--db", dest="db", action="store_true", default=True, help="Enable Postgres dual-write (default)")
    p.add_argument("--no-db", dest="db", action="store_false", help="Skip DB dual-write")
    p.add_argument("--schema", default="build_loop_memory", help="Postgres schema for this project")
    p.add_argument(
        "--embed-model",
        default="mxbai-embed-large",
        help=(
            "Legacy flag kept for back-compat. Embedding model is now selected by "
            "$EMBED_BACKEND ('mlx' default, 'ollama' fallback) and $EMBED_MODEL via "
            "scripts/embed_backend.py. Both backends produce 1024-dim vectors."
        ),
    )
    return p.parse_args(argv)


def split_csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


# ---------- v2 metadata defaults (design §15) ----------


def _derive_project(entity: str | None, workdir: Path) -> str:
    """Default project: prefix of entity before ':' if present, else
    $CLAUDE_PROJECT_DIR basename, else workdir basename, else 'unknown'.
    """
    if entity and ":" in entity:
        prefix = entity.split(":", 1)[0].strip()
        if prefix:
            return prefix
    cpd = os.environ.get("CLAUDE_PROJECT_DIR")
    if cpd:
        name = Path(cpd).name
        if name:
            return name
    name = workdir.name
    return name or "unknown"


def _git_diff_files(workdir: Path) -> list[str]:
    """Return repo-relative paths from `git diff --name-only HEAD~1 HEAD`.
    Returns [] on any error (no commits yet, not a repo, missing git, etc.)."""
    import subprocess  # noqa: PLC0415
    try:
        cp = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if cp.returncode != 0:
        return []
    return [line.strip() for line in cp.stdout.splitlines() if line.strip()]


def apply_v2_defaults(
    *,
    project: str | None,
    tool: str | None,
    model: str | None,
    task_category: str | None,
    author: str | None,
    files_touched: list[str] | None,
    closing_commit: str | None,
    last_validated: str | None,
    last_accessed: str | None,
    source: str,
    entity: str,
    workdir: Path,
    infer_files: bool = False,
) -> dict[str, Any]:
    """Apply schema-v2 defaults. Returns a dict of the 9 v2 fields.

    Defaults follow design §15 / TAXONOMY.md §9. The `source` value
    influences the `tool` default ('manual' source → 'manual' tool;
    'migration' source → 'migration'; everything else → 'claude-code').
    """
    if tool is None:
        if source == "manual":
            tool = "manual"
        elif source == "migration":
            tool = "migration"
        else:
            tool = "claude-code"
    if model is None:
        model = "claude-opus-4-7"
    if task_category is None:
        task_category = "unknown"
    if author is None:
        author = os.environ.get("USER") or "unknown"
    if project is None:
        project = _derive_project(entity, workdir)
    if files_touched is None:
        files_touched = _git_diff_files(workdir) if infer_files else []
    return {
        "project": project,
        "tool": tool,
        "model": model,
        "task_category": task_category,
        "author": author,
        "last_validated": last_validated,
        "last_accessed": last_accessed,
        "files_touched": files_touched,
        "closing_commit": closing_commit,
    }


# ---------- v3 metadata defaults & validator (design §16) ----------


def _confidence_source_default_for_source(source: str) -> str:
    """Map the existing `source` field to a sensible `confidence_source`
    default. The two fields are orthogonal but correlate at write time."""
    if source == "manual":
        return "user_statement"
    if source == "migration":
        return "external_import"
    if isinstance(source, str) and source.startswith("auto-"):
        return "ai_inference"
    if source == "orchestrator":
        return "ai_inference"
    return "unknown"


def _default_embedding_model_version() -> str:
    """Read $EMBED_MODEL env var (set by embed_backend's deployment config)
    or fall back to the canonical default. The env var convention matches
    `embed_backend._select_backend()`, so v3 entries written during a
    process where MLX/Ollama selected a non-default model will record that
    model id verbatim."""
    return os.environ.get("EMBED_MODEL") or DEFAULT_EMBEDDING_MODEL_VERSION


def apply_v3_defaults(
    *,
    confidence_source: str | None,
    confirmation_count: str | int | None,
    valid_until: str | None,
    causal_parent_id: str | None,
    embedding_model_version: str | None,
    domain: str | None,
    goal: str | None,
    source: str,
) -> dict[str, Any]:
    """Apply schema-v3 defaults. Returns a dict of the 7 v3 fields.

    Defaults follow design §16. CLI args arrive as strings; this helper
    coerces `confirmation_count` to int and leaves the rest as-is.
    """
    if confidence_source is None:
        confidence_source = _confidence_source_default_for_source(source)
    if confirmation_count is None:
        cc_int: int = 0
    else:
        try:
            cc_int = int(confirmation_count)
        except (TypeError, ValueError) as e:
            raise ValueError(f"confirmation_count must be int, got {confirmation_count!r}") from e
    if embedding_model_version is None:
        embedding_model_version = _default_embedding_model_version()
    if domain is None:
        domain = "unknown"
    if goal is None:
        goal = "unknown"
    return {
        "confidence_source": confidence_source,
        "confirmation_count": cc_int,
        "valid_until": valid_until,
        "causal_parent_id": causal_parent_id,
        "embedding_model_version": embedding_model_version,
        "domain": domain,
        "goal": goal,
    }


def validate_v3(v3: dict[str, Any]) -> None:
    """Raise ValueError on any v3 field violation."""
    cs = v3.get("confidence_source")
    if cs not in VALID_CONFIDENCE_SOURCES:
        raise ValueError(
            f"confidence_source {cs!r} not in {sorted(VALID_CONFIDENCE_SOURCES)}"
        )
    cc = v3.get("confirmation_count")
    if not isinstance(cc, int) or isinstance(cc, bool):
        raise ValueError(f"confirmation_count must be int, got {type(cc).__name__}")
    if cc < 0:
        raise ValueError(f"confirmation_count must be >= 0, got {cc}")
    vu = v3.get("valid_until")
    if vu not in (None, "null", ""):
        if not isinstance(vu, str) or not _valid_iso_date(vu):
            raise ValueError(f"valid_until must be ISO date or null, got {vu!r}")
    emv = v3.get("embedding_model_version")
    if not isinstance(emv, str) or not emv.strip():
        raise ValueError(f"embedding_model_version must be a non-empty string, got {emv!r}")
    d = v3.get("domain")
    if d not in VALID_DOMAINS:
        raise ValueError(f"domain {d!r} not in {sorted(VALID_DOMAINS)}")
    g = v3.get("goal")
    if g not in VALID_GOALS:
        raise ValueError(f"goal {g!r} not in {sorted(VALID_GOALS)}")
    cp = v3.get("causal_parent_id")
    if cp is not None and (not isinstance(cp, str) or not cp.strip()):
        raise ValueError(f"causal_parent_id must be a non-empty string or null, got {cp!r}")


_ISO_DATE_VALIDATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:?\d{2})?)?$"
)


def _valid_iso_date(s: str) -> bool:
    return bool(_ISO_DATE_VALIDATE_RE.match(s))


def validate_v2(v2: dict[str, Any]) -> None:
    """Raise ValueError on any v2 field violation."""
    if v2["tool"] not in VALID_TOOLS:
        raise ValueError(f"tool {v2['tool']!r} not in {sorted(VALID_TOOLS)}")
    if v2["task_category"] not in VALID_TASK_CATEGORIES:
        raise ValueError(
            f"task_category {v2['task_category']!r} not in {sorted(VALID_TASK_CATEGORIES)}"
        )
    if not isinstance(v2["files_touched"], list):
        raise ValueError("files_touched must be a list")
    for p in v2["files_touched"]:
        if not isinstance(p, str):
            raise ValueError(f"files_touched item must be string, got {type(p).__name__}")
    for f in ("project", "model", "author"):
        if not v2[f] or not isinstance(v2[f], str):
            raise ValueError(f"{f} must be a non-empty string")


def validate_tags(tags: list[str], primary_tag: str, taxonomy: dict[str, set[str]]) -> None:
    if primary_tag not in taxonomy["primary_tags"]:
        raise ValueError(
            f"primary_tag {primary_tag!r} not in taxonomy. Allowed: {sorted(taxonomy['primary_tags'])}"
        )
    for t in tags:
        if t.startswith("proposed:"):
            continue
        if t not in taxonomy["tags"]:
            raise ValueError(
                f"tag {t!r} not in taxonomy and not prefixed `proposed:`. Allowed: {sorted(taxonomy['tags'])}"
            )


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return 1 if e.code else 0

    workdir = Path(args.workdir).resolve()
    decisions_dir = workdir / ".episodic" / "decisions"
    history_dir = decisions_dir / "_history"
    events_path = workdir / ".episodic" / "events.jsonl"

    decisions_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    events_path.parent.mkdir(parents=True, exist_ok=True)

    # Load taxonomy
    try:
        tax = load_taxonomy(workdir)
    except Exception as e:  # noqa: BLE001
        log(f"validation error: failed to load TAXONOMY: {e}")
        return 1

    tags = split_csv(args.tags)
    if args.primary_tag not in tags:
        tags = [args.primary_tag] + tags

    try:
        validate_tags(tags, args.primary_tag, tax)
    except ValueError as e:
        log(f"validation error: {e}")
        return 1

    if args.source not in tax["sources"]:
        log(f"validation error: source {args.source!r} not in taxonomy. Allowed: {sorted(tax['sources'])}")
        return 1

    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        log(f"validation error: --date must be YYYY-MM-DD, got {date!r}")
        return 1

    # Acquire writer lock for the whole flow (id alloc + supersession + writes are atomic)
    writer_lock_target = decisions_dir / ".writer"
    writer_lock_target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LockedFile(writer_lock_target):
            return _do_write(args, workdir, decisions_dir, history_dir, events_path, tags, date)
    except TimeoutError as e:
        log(f"filesystem error: {e}")
        return 2


def _do_write(
    args: argparse.Namespace,
    workdir: Path,
    decisions_dir: Path,
    history_dir: Path,
    events_path: Path,
    tags: list[str],
    date: str,
) -> int:
    # 1) Resolve supersession
    same = find_same_topic(decisions_dir, args.primary_tag, args.entity)
    explicit_supersede = args.supersedes
    auto_supersede_id: str | None = None

    if same is not None:
        prior_path, prior_fm = same
        prior_id = prior_fm.get("id", "")
        prior_conf = prior_fm.get("confidence", "assumed")
        new_conf = args.confidence
        prior_rank = CONFIDENCE_ORDER.get(prior_conf, 0)
        new_rank = CONFIDENCE_ORDER.get(new_conf, 0)

        if explicit_supersede is not None:
            # User asserted; bypass ladder.
            if explicit_supersede != prior_id:
                log(
                    f"validation error: --supersedes={explicit_supersede} but same-topic prior is {prior_id}; "
                    f"either match or remove the prior first"
                )
                return 1
            auto_supersede_id = prior_id
        else:
            if new_rank > prior_rank:
                # Higher-confidence: auto-supersede
                auto_supersede_id = prior_id
            elif new_rank == prior_rank:
                log(
                    f"validation error: same-topic decision {prior_id} ({prior_conf}) already exists at equal "
                    f"confidence; pass --supersedes {prior_id} to replace it explicitly"
                )
                return 1
            else:
                log(
                    f"validation error: same-topic decision {prior_id} has higher confidence ({prior_conf}); "
                    f"lower-confidence ({new_conf}) cannot displace it"
                )
                return 1

    # 2) Allocate ID
    new_id = next_id(decisions_dir, history_dir)
    slug = slugify(args.title)
    new_filename = f"{new_id}-{date}-{slug}.md"
    new_path = decisions_dir / new_filename

    # 3) Build frontmatter
    # Apply v2 defaults (design §15). Validate before writing.
    files_touched_arg: list[str] | None = None
    if getattr(args, "files_touched", None):
        files_touched_arg = split_csv(args.files_touched)
    try:
        v2 = apply_v2_defaults(
            project=getattr(args, "project", None),
            tool=getattr(args, "tool", None),
            model=getattr(args, "model", None),
            task_category=getattr(args, "task_category", None),
            author=getattr(args, "author", None),
            files_touched=files_touched_arg,
            closing_commit=getattr(args, "closing_commit", None),
            last_validated=getattr(args, "last_validated", None),
            last_accessed=getattr(args, "last_accessed", None),
            source=args.source,
            entity=args.entity,
            workdir=workdir,
            infer_files=getattr(args, "infer_files_touched", False),
        )
        validate_v2(v2)
        # v3 (design §16). Applied AFTER v2 so v3 defaults can read v2.source.
        v3 = apply_v3_defaults(
            confidence_source=getattr(args, "confidence_source", None),
            confirmation_count=getattr(args, "confirmation_count", None),
            valid_until=getattr(args, "valid_until", None),
            causal_parent_id=getattr(args, "causal_parent_id", None),
            embedding_model_version=getattr(args, "embedding_model_version", None),
            domain=getattr(args, "domain", None),
            goal=getattr(args, "goal", None),
            source=args.source,
        )
        validate_v3(v3)
    except ValueError as e:
        log(f"validation error: {e}")
        return 1

    fm: dict[str, Any] = {
        "id": new_id,
        "slug": slug,
        "title": args.title,
        "type": "decision",
        "status": args.status,
        "confidence": args.confidence,
        "date": date,
        "tags": tags,
        "primary_tag": args.primary_tag,
        "entity": args.entity,
        "project": v2["project"],
        "tool": v2["tool"],
        "model": v2["model"],
        "task_category": v2["task_category"],
        "author": v2["author"],
        "source": args.source,
        "related_runs": split_csv(args.related_runs),
        "related_decisions": split_csv(args.related_decisions),
        "supersedes": auto_supersede_id,
        "superseded_by": None,
        "bookmark_snapshot_id": args.bookmark_snapshot_id,
        "captured_turn_excerpt": args.captured_turn_excerpt,
        "last_validated": v2["last_validated"],
        "last_accessed": v2["last_accessed"],
        "files_touched": v2["files_touched"],
        "closing_commit": v2["closing_commit"],
        # v3 (design §16) — appended after v2 to keep canonical order stable.
        "confidence_source": v3["confidence_source"],
        "confirmation_count": v3["confirmation_count"],
        "valid_until": v3["valid_until"],
        "causal_parent_id": v3["causal_parent_id"],
        "embedding_model_version": v3["embedding_model_version"],
        "domain": v3["domain"],
        "goal": v3["goal"],
    }
    body = {
        "context": args.context,
        "decision": args.decision,
        "alternatives": args.alternatives,
        "consequences": args.consequences,
        "notes": args.notes,
    }
    body_text = render_madr(fm, body)

    try:
        # 4) Write the new MADR atomically
        atomic_write_bytes(new_path, body_text.encode("utf-8"))

        # 5) If supersession, archive the prior + update its frontmatter
        if auto_supersede_id is not None:
            assert same is not None
            prior_path, prior_fm = same
            history_path = _archive_to_history(prior_path, prior_fm, history_dir, new_id)
            log(f"archived prior decision {auto_supersede_id} → {history_path}")

        # 6) Regenerate INDEX
        regenerate_index(decisions_dir)

        # 7) Append event(s) to events.jsonl
        if auto_supersede_id is not None:
            append_event(
                events_path,
                {
                    "ts": _iso_utc(),
                    "kind": "decision_superseded",
                    "decision_id": auto_supersede_id,
                    "superseded_by": new_id,
                    "primary_tag": args.primary_tag,
                    "entity": args.entity,
                    "project": v2["project"],
                    "tool": v2["tool"],
                    "model": v2["model"],
                    "task_category": v2["task_category"],
                    "author": v2["author"],
                    "source": args.source,
                    # v3 mirror (design §16)
                    "confidence_source": v3["confidence_source"],
                    "confirmation_count": v3["confirmation_count"],
                    "valid_until": v3["valid_until"],
                    "causal_parent_id": v3["causal_parent_id"],
                    "embedding_model_version": v3["embedding_model_version"],
                    "domain": v3["domain"],
                    "goal": v3["goal"],
                    "dedup_key": f"decision:{auto_supersede_id}:superseded_by:{new_id}",
                },
            )
        accept_kind = "decision_accepted" if args.status == "accepted" else "decision_proposed"
        append_event(
            events_path,
            {
                "ts": _iso_utc(),
                "kind": accept_kind,
                "decision_id": new_id,
                "title": args.title,
                "primary_tag": args.primary_tag,
                "entity": args.entity,
                "project": v2["project"],
                "tool": v2["tool"],
                "model": v2["model"],
                "task_category": v2["task_category"],
                "author": v2["author"],
                "confidence": args.confidence,
                "source": args.source,
                "supersedes": auto_supersede_id,
                # v3 mirror (design §16)
                "confidence_source": v3["confidence_source"],
                "confirmation_count": v3["confirmation_count"],
                "valid_until": v3["valid_until"],
                "causal_parent_id": v3["causal_parent_id"],
                "embedding_model_version": v3["embedding_model_version"],
                "domain": v3["domain"],
                "goal": v3["goal"],
                "dedup_key": f"decision:{new_id}:{accept_kind}",
            },
        )
    except (OSError, TimeoutError) as e:
        log(f"filesystem error: {e}")
        return 2

    # 8) DB dual-write (best-effort)
    if args.db:
        db_dualwrite(new_id, fm, body_text, workdir, args.schema, args.embed_model)

    print(new_id)
    log(f"wrote decision {new_id} to {new_path}")
    return 0


def _iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _archive_to_history(prior_path: Path, prior_fm: dict, history_dir: Path, new_id: str) -> Path:
    """Move prior_path to history_dir/<id>-vN.md, updating frontmatter.

    `_history/` filenames follow `<id>-v<N>.md` where N starts at 1 and
    increments based on existing versions for the same id.
    """
    pid = prior_fm.get("id", "0000")
    # Determine version
    versions = []
    for f in history_dir.glob(f"{pid}-v*.md"):
        m = re.match(rf"^{pid}-v(\d+)\.md$", f.name)
        if m:
            versions.append(int(m.group(1)))
    next_version = (max(versions) + 1) if versions else 1
    dest = history_dir / f"{pid}-v{next_version}.md"

    # Update frontmatter on the prior body.
    text = prior_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text) or {}
    fm["status"] = "superseded"
    fm["superseded_by"] = new_id
    # Strip frontmatter from text and reattach updated version.
    body = _FM_RE.sub("", text, count=1)
    new_text = emit_frontmatter(fm) + body
    atomic_write_bytes(dest, new_text.encode("utf-8"))
    # Remove the prior from decisions/ root.
    try:
        prior_path.unlink()
    except FileNotFoundError:
        pass
    return dest


if __name__ == "__main__":
    sys.exit(main())
