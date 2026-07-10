#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Ingest markdown memory files into the SQLite lessons index.

Walks the memory lanes for a project (or top-level) and upserts facts
into the SQLite DB, skipping files whose sha256 is unchanged (incremental).

Embeddings are computed lazily: if the embed backend is available AND the
fact's sha256 is new/changed, a vector is stored in `embeddings`. If the
backend is unavailable (MLX not installed, Ollama down, etc.) ingest
continues silently in FTS-only mode — graceful degradation is mandatory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = HERE.parent
# Insert scripts/ for _paths, embed_backend deps.
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Import open_db via importlib to avoid collision with write_decision/schema.py
# which is also named 'schema' and appears on sys.path in the full test suite.
import importlib.util as _iutil  # noqa: E402

def _load_schema_module():
    spec = _iutil.spec_from_file_location(
        "lessons_index._schema", HERE / "schema.py"
    )
    mod = _iutil.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_schema_mod = _load_schema_module()
open_db = _schema_mod.open_db


# ---------------------------------------------------------------------------
# Frontmatter parser — reuse the same tiny YAML subset as memory_writer.py.
# Imported from write_decision/frontmatter if available, otherwise inline.
# ---------------------------------------------------------------------------

def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Empty dict if no frontmatter.

    Identical subset as memory_writer._split_frontmatter: handles key: value,
    key: ["list"], booleans, null, int, float, quoted strings.
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    block = text[4:end]
    body = text[end + 5:]
    fm: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        fm[m.group(1)] = _coerce(m.group(2).strip())
    return fm, body


def _coerce(val: str) -> Any:
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


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pack_floats(vec: list[float]) -> bytes:
    """Pack a list of float32 into a BLOB."""
    return struct.pack(f"{len(vec)}f", *vec)


# ---------------------------------------------------------------------------
# Embed backend — lazy import, fail-safe
# ---------------------------------------------------------------------------

def _try_embed(text: str) -> tuple[list[float] | None, str | None]:
    """Return (vector, model_name) or (None, None) if backend unavailable.

    Checks EMBED_BACKEND_UNAVAILABLE env var first so tests can force the
    no-embedding path without complex mocking.
    """
    if os.environ.get("EMBED_BACKEND_UNAVAILABLE"):
        return None, None
    try:
        import embed_backend as _eb  # type: ignore  # noqa: PLC0415
        vec = _eb.embed(text)
        model = _eb.active_model()
        return vec, model
    except Exception:  # noqa: BLE001
        return None, None


# ---------------------------------------------------------------------------
# Lane discovery
# ---------------------------------------------------------------------------

def _lane_dirs(project: str | None) -> list[tuple[str, Path]]:
    """Return [(lane_name, directory)] pairs to walk for the given project.

    Lane names correspond to the memory lane taxonomy:
      lessons, decisions, product, architecture (per project or top-level).
    """
    from _paths import (  # type: ignore  # noqa: PLC0415
        project_decisions_dir,
        project_lessons_dir,
        project_product_dir,
        project_research_dir,
        project_root,
        top_level_lessons_dir,
        memory_store_root,
    )
    if project:
        return [
            ("lessons", project_lessons_dir(project)),
            ("decisions", project_decisions_dir(project)),
            ("product", project_product_dir(project)),
            ("research", project_research_dir(project)),
            ("references", project_root(project) / "references"),
        ]
    else:
        return [
            ("lessons", top_level_lessons_dir()),
            ("research", memory_store_root() / "research"),
            ("references", memory_store_root() / "references"),
        ]


# ---------------------------------------------------------------------------
# Core ingest
# ---------------------------------------------------------------------------

def ingest(
    project: str | None = None,
    db_path: str | Path | None = None,
) -> dict:
    """Walk memory lanes and upsert markdown → SQLite.

    Returns a summary dict: {upserted, skipped, errors, total_scanned}.

    Rules:
    - Skip files whose sha256 is unchanged (incremental).
    - Skip non-.md files and INDEX.md / MEMORY.md / TELEMETRY.jsonl.
    - Embeddings: computed lazily; silently skipped if backend unavailable.
    - Idempotent: safe to call multiple times.
    """
    from _paths import memory_store_root  # type: ignore  # noqa: PLC0415

    if db_path is None:
        db_path = memory_store_root() / "indexes" / "lessons_index.db"
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = open_db(db_path)
    summary = {"upserted": 0, "skipped": 0, "errors": [], "total_scanned": 0}

    try:
        lanes = _lane_dirs(project)
        for lane_name, lane_dir in lanes:
            if not lane_dir.exists():
                continue
            for md_path in sorted(lane_dir.rglob("*.md")):
                relative_parts = md_path.relative_to(lane_dir).parts[:-1]
                if md_path.name in {"INDEX.md", "MEMORY.md", "README.md"} or any(
                    part in {"raw-originals", "archive", "indexes", "raw"}
                    for part in relative_parts
                ):
                    continue
                summary["total_scanned"] += 1
                try:
                    _ingest_file(conn, md_path, lane_name,
                                 project or "_unscoped", summary)
                except Exception as exc:  # noqa: BLE001
                    summary["errors"].append(
                        {"file": str(md_path), "error": str(exc)}
                    )
    finally:
        # Update last_ingest_ts.
        now_ts = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('last_ingest_ts', ?)",
            (now_ts,),
        )
        conn.commit()
        conn.close()

    return summary


def _ingest_file(
    conn,
    path: Path,
    lane: str,
    project: str,
    summary: dict,
) -> None:
    """Upsert one markdown file into the DB."""
    text = path.read_text(encoding="utf-8")
    sha = _sha256(text)
    source_path = str(path)
    mtime = path.stat().st_mtime

    # Check if sha256 is unchanged — skip if so.
    row = conn.execute(
        "SELECT id, sha256 FROM facts WHERE source_path = ?", (source_path,)
    ).fetchone()

    if row is not None and row["sha256"] == sha:
        summary["skipped"] += 1
        return

    # Parse frontmatter + body.
    fm, body = _split_frontmatter(text)
    name = str(fm.get("name") or path.stem)
    description = str(fm.get("description") or "")
    fm_json = json.dumps(fm, ensure_ascii=False, default=str)

    # Upsert into facts.
    if row is None:
        # New row.
        cur = conn.execute(
            """INSERT INTO facts
               (lane, project, name, description, body, frontmatter_json,
                source_path, mtime, sha256)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (lane, project, name, description, body.strip(),
             fm_json, source_path, mtime, sha),
        )
        fact_id = cur.lastrowid
        # FTS triggers handle INSERT automatically.
    else:
        fact_id = row["id"]
        conn.execute(
            """UPDATE facts SET
               lane=?, project=?, name=?, description=?, body=?,
               frontmatter_json=?, mtime=?, sha256=?
               WHERE id=?""",
            (lane, project, name, description, body.strip(),
             fm_json, mtime, sha, fact_id),
        )
        # FTS triggers handle UPDATE automatically.

    conn.commit()
    summary["upserted"] += 1

    # Embeddings: lazy, skip on any failure.
    vec, model = _try_embed(f"{name}\n{description}\n{body}")
    if vec is not None and model is not None:
        packed = _pack_floats(vec)
        dim = len(vec)
        conn.execute(
            """INSERT INTO embeddings(fact_id, model, dim, vec)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(fact_id) DO UPDATE SET
               model=excluded.model, dim=excluded.dim, vec=excluded.vec""",
            (fact_id, model, dim, packed),
        )
        conn.commit()
