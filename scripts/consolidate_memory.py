#!/usr/bin/env python3
"""Memory consolidation pass.

Reads `.semantic/_candidates.jsonl` (one JSON object per line), embeds
each candidate, runs cosine search against existing
`agent_memory.<schema>.semantic_facts`, and applies the dedup ladder
from design ref §12 + decision 0006:

  cosine >= 0.90  AND same (subject, predicate)              -> IGNORE
  0.85 <= cosine < 0.90 AND same (subject, predicate)        -> MERGE/UPDATE
  cosine < 0.85                                              -> INSERT
  same (subject, predicate) BUT both confidence='explicit'
       AND different object                                  -> CONFLICT

CONFLICT writes a row to `fact_conflicts` and does NOT auto-resolve.

After processing, every candidate is appended to
`.semantic/_candidates_history.jsonl` with the action taken and an ISO
timestamp. The original `_candidates.jsonl` is truncated.

Best-effort embedding: a single embedding failure does not abort the
run; the offending candidate is recorded with action=ERROR.

Candidate schema (input):
  {
    "subject": str (required),
    "predicate": str (required),
    "object": str (required),
    "confidence": "explicit" | "confirmed" | "inferred" | "assumed" (optional, default "inferred"),
    "source_episode_id": str | null (optional),
    "tags": list[str] (optional),
    "metadata": dict (optional)
  }

Exit codes:
  0 success
  1 validation error (bad candidate row, schema mismatch)
  2 filesystem / DB error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DEFAULT_SCHEMA = "build_loop_memory"

# Confidence-to-float mapping mirrors write_decision.py
_CONF_FLOAT = {"assumed": 0.25, "inferred": 0.5, "confirmed": 0.75, "explicit": 1.0}

# Dedup thresholds (from decision 0006 + design ref §12)
COSINE_IGNORE = 0.90
COSINE_MERGE = 0.85


def _log(msg: str) -> None:
    print(f"[consolidate] {msg}", file=sys.stderr)


def _safe_schema(schema: str) -> str:
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        raise ValueError(f"unsafe schema name: {schema!r}")
    return schema


def _load_candidates(cand_path: Path) -> list[dict]:
    if not cand_path.exists():
        return []
    out: list[dict] = []
    for i, line in enumerate(cand_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"line {i}: invalid JSON: {e}")
        for required in ("subject", "predicate", "object"):
            if not obj.get(required):
                raise ValueError(f"line {i}: missing required field {required!r}")
        out.append(obj)
    return out


def _archive(history_path: Path, candidate: dict, action: str, details: dict) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "candidate": candidate,
        "action": action,
        "details": details,
    }
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _candidate_text(c: dict) -> str:
    """Produce a stable text rendering for embedding.

    Matches the format used when seed facts are embedded in tests, so
    that an exact-duplicate candidate scores cosine ~1.0 against the
    existing row.
    """
    return f"{c['subject']} {c['predicate']}: {c['object']}"


def _process_candidate(
    c: dict,
    schema: str,
    dry_run: bool,
) -> tuple[str, dict]:
    """Classify and apply dedup logic. Returns (action, details)."""
    from db import execute, query, vector_literal  # type: ignore  # noqa: PLC0415
    from embed_backend import embed  # type: ignore  # noqa: PLC0415

    schema = _safe_schema(schema)
    text = _candidate_text(c)

    try:
        embedding = embed(text)
    except Exception as e:  # noqa: BLE001
        return "ERROR", {"error": f"embed failed: {e}"}

    # Top-1 cosine + (subject, predicate) match
    sql = (
        "SELECT id::text AS id, subject, predicate, object, confidence, status, "
        "       (1 - (embedding <=> %s::vector)) AS cosine_sim "
        f"FROM {schema}.semantic_facts "
        "WHERE status = 'active' AND embedding IS NOT NULL "
        "ORDER BY embedding <=> %s::vector ASC "
        "LIMIT 1"
    )
    emb_lit = vector_literal(embedding)
    rows = query(sql, (emb_lit, emb_lit))

    candidate_conf = c.get("confidence") or "inferred"
    candidate_conf_f = _CONF_FLOAT.get(candidate_conf, 0.5)

    if not rows:
        # Empty table -> always INSERT
        if not dry_run:
            _do_insert(c, embedding, schema)
        return "INSERT", {"reason": "no existing facts", "cosine": None}

    top = rows[0]
    cosine = float(top["cosine_sim"])
    same_sp = (top["subject"] == c["subject"]) and (top["predicate"] == c["predicate"])

    # Branch on cosine + (subject, predicate)
    if same_sp and cosine >= COSINE_IGNORE:
        return "IGNORE", {"reason": "near-duplicate", "cosine": cosine, "matched_id": top["id"]}

    if same_sp and cosine >= COSINE_MERGE:
        # MERGE: same (subject, predicate), close cosine, possibly different object phrasing.
        # Resolve via confidence ladder. Equal confidence + different object is a CONFLICT.
        existing_conf = float(top["confidence"] or 0.5)
        if abs(candidate_conf_f - existing_conf) < 1e-6 and top["object"] != c["object"]:
            # CONFLICT: same (subject, predicate, confidence) but different object
            if not dry_run:
                _record_conflict(top["id"], c, embedding, schema)
            return "CONFLICT", {"reason": "equal-confidence different-object", "cosine": cosine, "matched_id": top["id"]}
        if candidate_conf_f > existing_conf:
            if not dry_run:
                _do_update(top["id"], c, embedding, schema)
            return "UPDATE", {"reason": "higher confidence merges over lower", "cosine": cosine, "matched_id": top["id"]}
        # candidate_conf_f <= existing_conf: nothing to do
        return "MERGE_SKIP", {"reason": "candidate confidence not higher than existing", "cosine": cosine, "matched_id": top["id"]}

    # Same (subject, predicate) but cosine < MERGE -> they're different facts on same topic.
    # Treat as INSERT (parallel facts; let humans clean up if needed).
    if not dry_run:
        _do_insert(c, embedding, schema)
    return "INSERT", {"reason": "novel or low-similarity", "cosine": cosine, "matched_id": top["id"]}


def _do_insert(c: dict, embedding: list[float], schema: str) -> None:
    from db import execute, vector_literal  # type: ignore  # noqa: PLC0415

    schema = _safe_schema(schema)
    metadata = {
        "source": "consolidate_memory",
        "tags": c.get("tags") or [],
        "source_episode_id": c.get("source_episode_id"),
        "candidate_metadata": c.get("metadata") or {},
    }
    execute(
        f"INSERT INTO {schema}.semantic_facts "
        "(subject, predicate, object, confidence, status, embedding, metadata) "
        "VALUES (%s, %s, %s, %s, 'active', %s::vector, %s::jsonb)",
        (
            c["subject"],
            c["predicate"],
            c["object"],
            _CONF_FLOAT.get(c.get("confidence") or "inferred", 0.5),
            vector_literal(embedding),
            json.dumps(metadata, ensure_ascii=False),
        ),
    )


def _do_update(target_id: str, c: dict, embedding: list[float], schema: str) -> None:
    """UPDATE replaces object/confidence on the existing row."""
    from db import execute, vector_literal  # type: ignore  # noqa: PLC0415

    schema = _safe_schema(schema)
    metadata = {
        "source": "consolidate_memory",
        "tags": c.get("tags") or [],
        "source_episode_id": c.get("source_episode_id"),
        "merged_from_candidate": True,
        "candidate_metadata": c.get("metadata") or {},
    }
    execute(
        f"UPDATE {schema}.semantic_facts "
        "SET object = %s, confidence = %s, embedding = %s::vector, "
        "    metadata = %s::jsonb, valid_from = now() "
        "WHERE id = %s::uuid",
        (
            c["object"],
            _CONF_FLOAT.get(c.get("confidence") or "inferred", 0.5),
            vector_literal(embedding),
            json.dumps(metadata, ensure_ascii=False),
            target_id,
        ),
    )


def _record_conflict(existing_id: str, c: dict, embedding: list[float], schema: str) -> None:
    """Insert the candidate as a 'proposed' fact, then log a fact_conflicts row."""
    from db import execute, query_one, vector_literal  # type: ignore  # noqa: PLC0415

    schema = _safe_schema(schema)
    metadata = {
        "source": "consolidate_memory",
        "tags": c.get("tags") or [],
        "source_episode_id": c.get("source_episode_id"),
        "candidate_metadata": c.get("metadata") or {},
    }
    # Insert proposed fact and capture the new ID
    new_row = query_one(
        f"INSERT INTO {schema}.semantic_facts "
        "(subject, predicate, object, confidence, status, embedding, metadata) "
        "VALUES (%s, %s, %s, %s, 'proposed', %s::vector, %s::jsonb) "
        "RETURNING id::text AS id",
        (
            c["subject"],
            c["predicate"],
            c["object"],
            _CONF_FLOAT.get(c.get("confidence") or "inferred", 0.5),
            vector_literal(embedding),
            json.dumps(metadata, ensure_ascii=False),
        ),
    )
    new_id = (new_row or {}).get("id")
    execute(
        f"INSERT INTO {schema}.fact_conflicts "
        "(fact_id_a, fact_id_b, conflict_type, resolved, metadata) "
        "VALUES (%s::uuid, %s::uuid, %s, FALSE, %s::jsonb)",
        (
            existing_id,
            new_id,
            "equal-confidence-different-object",
            json.dumps({"detected_by": "consolidate_memory"}),
        ),
    )


# ---------- main ----------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Consolidate semantic candidates into agent_memory.semantic_facts")
    p.add_argument("--workdir", default=".")
    p.add_argument("--schema", default=DEFAULT_SCHEMA)
    p.add_argument("--dry-run", action="store_true", help="Classify without writing to DB or archiving")
    args = p.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    cand_path = workdir / ".semantic" / "_candidates.jsonl"
    history_path = workdir / ".semantic" / "_candidates_history.jsonl"

    try:
        candidates = _load_candidates(cand_path)
    except ValueError as e:
        _log(f"validation error: {e}")
        return 1

    if not candidates:
        _log(f"no candidates at {cand_path}; nothing to do")
        return 0

    try:
        _safe_schema(args.schema)
    except ValueError as e:
        _log(f"validation error: {e}")
        return 1

    counts: dict[str, int] = {}
    for c in candidates:
        try:
            action, details = _process_candidate(c, args.schema, args.dry_run)
        except Exception as e:  # noqa: BLE001
            _log(f"db error processing candidate {c.get('subject')}/{c.get('predicate')}: {e}")
            return 2
        counts[action] = counts.get(action, 0) + 1
        _log(f"{action}: ({c.get('subject')}, {c.get('predicate')}, {c.get('object')[:40]!r}) {details}")
        if not args.dry_run:
            _archive(history_path, c, action, details)

    if not args.dry_run:
        # Truncate the candidates file
        cand_path.write_text("", encoding="utf-8")
        try:
            cand_path.unlink()
        except (FileNotFoundError, OSError):
            pass

    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    _log(f"done — {summary}{' (dry-run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
