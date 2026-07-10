#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Phase 1 backend health-check surface.

Probes each of build-loop's four memory backends and returns a one-line
summary plus a JSON envelope suitable for `state.json.architecture.backendHealth`.

Backends:
  1. runs[]       — `state.json.runs[]` (filesystem; always probable)
  2. decisions    — `build-loop-memory/projects/<project>/decisions/*.md`
  3. semantic     — Postgres `agent_memory.<schema>.semantic_facts`
  4. debugger     — native `.build-loop/issues/*.md` incident notes

Budget:
  - 5s per backend
  - 30s total wall-clock cap

Exit codes:
  0  health probe completed (any backend may be down — graceful degradation)
  1  unrecoverable error (CLI parse / IO error before probing)

CLI:
    python3 scripts/backend_health.py [--workdir .] [--json] [--quiet]

`recall()` semantics: this script is intentionally read-only. It never
spawns long-lived servers, never writes to backends, and never raises on
backend-down conditions. The JSON envelope is the contract; the one-line
summary is for human terminal output.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]

# Shared DB-URL resolver. `_db_url` is stdlib-only (os, pathlib) so this
# import does not pull psycopg at module top.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from _db_url import NO_URL_REASON, resolve_db_url  # noqa: E402

PER_BACKEND_TIMEOUT_S = 5
TOTAL_BUDGET_S = 30

# Test injection points — set via setter functions below to mock backends
# / DB calls without requiring `unittest.mock` patching.
_DEBUGGER_RUNNER_OVERRIDE: Optional[Callable[..., Tuple[bool, str]]] = None
_SEMANTIC_RUNNER_OVERRIDE: Optional[Callable[..., Tuple[bool, str]]] = None


def set_debugger_runner(fn: Optional[Callable[..., Tuple[bool, str]]]) -> None:
    """Inject a callable used by `probe_debugger` instead of the npx CLI.

    The callable receives no arguments and must return `(ok: bool, reason_or_msg: str)`.
    """
    global _DEBUGGER_RUNNER_OVERRIDE
    _DEBUGGER_RUNNER_OVERRIDE = fn


def set_semantic_runner(fn: Optional[Callable[..., Tuple[bool, str]]]) -> None:
    """Inject a callable used by `probe_semantic` instead of psycopg.

    The callable receives no arguments and must return `(ok: bool, reason_or_msg: str)`.
    """
    global _SEMANTIC_RUNNER_OVERRIDE
    _SEMANTIC_RUNNER_OVERRIDE = fn


# ---------------------------------------------------------------------------
# Backend 1: runs[]
# ---------------------------------------------------------------------------

def probe_runs(workdir: Path) -> Dict[str, Any]:
    """Probe `state.json.runs[]`. Always cheap — single file read."""
    state_path = workdir / ".build-loop" / "state.json"
    started = time.monotonic()
    if not state_path.is_file():
        return {
            "ok": False,
            "reason": "state_json_missing",
            "count": 0,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {
            "ok": False,
            "reason": f"state_json_unreadable: {type(e).__name__}",
            "count": 0,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    runs = state.get("runs") or []
    return {
        "ok": True,
        "count": len(runs),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


# ---------------------------------------------------------------------------
# Backend 2: episodic decisions/
# ---------------------------------------------------------------------------

def _resolve_project_slug(workdir: Path) -> Optional[str]:
    """Resolve the memory project slug for `workdir`. `None` on any failure.

    Shared by `_resolve_canonical_decisions_dir` and the rename-detection
    heuristic in `detect_possible_rename` — both need the same slug.
    """
    try:
        scripts_dir = Path(__file__).resolve().parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415
        return resolve_project(workdir) or None
    except Exception:  # noqa: BLE001 — best-effort resolution
        return None


def _resolve_canonical_decisions_dir(workdir: Path) -> Optional[Path]:
    """Resolve the canonical (global) decisions directory for this project.

    Mirrors `memory_facade._resolve_decision_dirs` — uses `_paths` and
    `project_resolver` to land on
    `~/dev/git-folder/build-loop-memory/projects/<project>/decisions/`. Returns
    `None` on any resolution failure (graceful degradation contract).
    """
    try:
        # Both modules live under scripts/, importable when this file is
        # imported by tests (which prepend scripts/ to sys.path) or by the
        # CLI (where scripts/ is the parent dir).
        scripts_dir = Path(__file__).resolve().parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from _paths import project_decisions_dir  # type: ignore  # noqa: PLC0415
        proj = _resolve_project_slug(workdir)
        if not proj:
            return None
        return project_decisions_dir(proj)
    except Exception:  # noqa: BLE001 — best-effort resolution
        return None


def _projects_root_dir() -> Optional[Path]:
    """Return `<memory_store_root()>/projects`. `None` on any failure."""
    try:
        scripts_dir = Path(__file__).resolve().parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from _paths import memory_store_root  # type: ignore  # noqa: PLC0415
        return memory_store_root() / "projects"
    except Exception:  # noqa: BLE001 — best-effort resolution
        return None


def _dir_has_content(project_dir: Path) -> int:
    """Cheap count of `*.md` files under a project dir's `decisions/` +
    `lessons/` subdirs. `0` on any I/O error or if neither subdir exists."""
    total = 0
    for sub in ("decisions", "lessons"):
        d = project_dir / sub
        if not d.is_dir():
            continue
        try:
            total += sum(1 for _ in d.glob("*.md"))
        except OSError:
            continue
    return total


def detect_possible_rename(
    current_slug: Optional[str], projects_root: Optional[Path],
) -> Optional[Dict[str, Any]]:
    """Advisory rename-detection (actuates the `dir_missing` signal).

    `backend_health` already computes `dir_missing` when the canonical
    decisions store for the CURRENT slug doesn't exist, but nothing routed
    that into a surfaced warning (2026-07-09 control-plane RCA, P0-4:
    a `RossLabs-AI-Assistant` rename orphaned 7 lessons under the old
    `ai-assistant` slug and this exact condition fired silently).

    Heuristic: list sibling `<projects_root>/<slug>/` dirs (excluding the
    current slug and `_unscoped`) that have content (decisions or lessons
    markdown files), rank them by string similarity to `current_slug`
    (`difflib.SequenceMatcher.ratio`), and report the best match as the
    likely pre-rename slug when the similarity clears a low bar (renamed
    repos typically keep a recognizable substring, e.g. `ai-assistant` ->
    `rosslabs-ai-assistant`). Below that bar, or with several similarly-
    scored candidates, all candidates are still returned so the operator
    can eyeball them — this is a hint, not an assertion.

    Returns `None` (never raises) when: `current_slug` or `projects_root`
    is unavailable, the projects root doesn't exist, or no sibling has any
    content. Non-blocking by design — callers surface this as a WARNING,
    never a failure.
    """
    if not current_slug or projects_root is None or not projects_root.is_dir():
        return None
    candidates: List[Tuple[float, str, int]] = []
    try:
        children = sorted(projects_root.iterdir())
    except OSError:
        return None
    for child in children:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        slug = child.name
        if slug == current_slug or slug == "_unscoped":
            continue
        count = _dir_has_content(child)
        if count <= 0:
            continue
        ratio = difflib.SequenceMatcher(None, current_slug, slug).ratio()
        candidates.append((ratio, slug, count))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (-t[0], t[1]))
    best_ratio, best_slug, best_count = candidates[0]
    likely_old_slug = best_slug if best_ratio >= 0.3 else None
    return {
        "current_slug": current_slug,
        "likely_old_slug": likely_old_slug,
        "likely_old_slug_file_count": best_count if likely_old_slug else None,
        "candidates": [
            {"slug": s, "file_count": c} for _, s, c in candidates[:5]
        ],
    }


def _probe_one_decisions_dir(path: Optional[Path]) -> Dict[str, Any]:
    """Probe a single decisions directory; return a `{ok, count, path, [reason]}` dict."""
    if path is None:
        return {"ok": False, "count": 0, "path": None, "reason": "unresolved"}
    if not path.is_dir():
        return {"ok": False, "count": 0, "path": str(path), "reason": "dir_missing"}
    try:
        count = sum(1 for _ in path.glob("*.md"))
    except OSError as e:
        return {
            "ok": False,
            "count": 0,
            "path": str(path),
            "reason": f"dir_unreadable: {type(e).__name__}",
        }
    return {"ok": True, "count": count, "path": str(path)}


def probe_decisions(workdir: Path) -> Dict[str, Any]:
    """Probe both decision stores and return a structured envelope.

    Canonical store after the memory-store cutover:

      1. **Canonical**: `~/dev/git-folder/build-loop-memory/
         projects/<project>/decisions/*.md`
      2. **Legacy diagnostic**: `<workdir>/.episodic/decisions/*.md`

    Envelope shape (Priority 20):

        {
          "ok": <True if EITHER store has files>,
          "count": <canonical_count when canonical present, else legacy_count>,
          "duration_ms": <int>,
          "legacy":    {"ok": bool, "count": int, "path": str|None, ...},
          "canonical": {"ok": bool, "count": int, "path": str|None, ...},
          "reason":    "<aggregate reason when both DOWN>"  # only on DOWN
        }

    Backward-compat: top-level `ok` / `count` / `duration_ms` keys retain
    the pre-Priority-20 contract so any consumer reading those fields keeps
    working. New callers that need to distinguish the two stores read the
    `legacy` / `canonical` sub-keys.
    """
    started = time.monotonic()
    legacy_path = workdir / ".episodic" / "decisions"
    canonical_path = _resolve_canonical_decisions_dir(workdir)

    legacy = _probe_one_decisions_dir(legacy_path)
    canonical = _probe_one_decisions_dir(canonical_path)

    # Actuate the dir_missing signal: when the canonical store for the
    # CURRENT slug is absent, check whether a sibling projects/<slug>/ with
    # content exists — the signature of an un-pinned repo rename (P0-4 RCA).
    # Advisory only; never affects `ok`/`count`/`reason` below.
    if canonical.get("reason") == "dir_missing":
        try:
            current_slug = _resolve_project_slug(workdir)
            rename_warning = detect_possible_rename(current_slug, _projects_root_dir())
        except Exception:  # noqa: BLE001 — advisory surfacing must never break the probe
            rename_warning = None
        if rename_warning is not None:
            canonical["renameWarning"] = rename_warning

    duration_ms = int((time.monotonic() - started) * 1000)
    # Top-level ok/count retain the pre-Priority-20 contract: ok when EITHER
    # store has decision files; count prefers the canonical store when present
    # and falls back to the legacy store otherwise (so a legacy-only machine
    # still reports its real count). Consumers needing both read the
    # `legacy`/`canonical` sub-keys.
    any_ok = canonical["ok"] or legacy["ok"]
    total_count = canonical["count"] if canonical["ok"] else legacy["count"]

    envelope: Dict[str, Any] = {
        "ok": any_ok,
        "count": total_count,
        "duration_ms": duration_ms,
        "legacy": legacy,
        "canonical": canonical,
    }
    if not any_ok:
        # Aggregate reason — useful for the one-liner.
        envelope["reason"] = "canonical decision store missing"
    return envelope


# ---------------------------------------------------------------------------
# Backend 3: Postgres semantic_facts
# ---------------------------------------------------------------------------

def probe_semantic(workdir: Path) -> Dict[str, Any]:
    """Probe Postgres `agent_memory.<schema>.semantic_facts` reachability.

    Mirrors the contract from `memory_facade.read_semantic`: the shared
    resolver (`_db_url.resolve_db_url`) drives the connection. We just
    attempt a `SELECT 1 FROM <schema>.semantic_facts LIMIT 1`
    so a wedged-but-up Postgres still classifies as `ok`.
    """
    started = time.monotonic()
    if _SEMANTIC_RUNNER_OVERRIDE is not None:
        ok, msg = _SEMANTIC_RUNNER_OVERRIDE()
        result: Dict[str, Any] = {
            "ok": ok,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        if ok:
            # Caller may pass a count via msg in the form "count=N"
            m = re.match(r"count=(\d+)", msg or "")
            result["count"] = int(m.group(1)) if m else None
        else:
            result["reason"] = msg or "postgres_unavailable"
        return result

    db_url = resolve_db_url()
    if not db_url:
        return {
            "ok": False,
            "reason": f"postgres_unavailable: {NO_URL_REASON}",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    try:
        import psycopg  # type: ignore  # noqa: PLC0415
    except ImportError:
        return {
            "ok": False,
            "reason": "postgres_unavailable: psycopg not installed",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    schema = os.environ.get("AGENT_MEMORY_SCHEMA", "personal_memory")
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        return {
            "ok": False,
            "reason": f"postgres_unavailable: unsafe schema {schema!r}",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    try:
        with psycopg.connect(db_url, connect_timeout=PER_BACKEND_TIMEOUT_S) as conn:  # type: ignore
            with conn.cursor() as cur:
                cur.execute(f'SELECT count(*) FROM {schema}.semantic_facts')  # nosec: schema is a validated identifier (^[a-z][a-z0-9_]*$); values bound as params
                row = cur.fetchone()
                count = int(row[0]) if row else 0
        return {
            "ok": True,
            "count": count,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as e:  # noqa: BLE001 — graceful-degradation contract
        return {
            "ok": False,
            "reason": f"postgres_unavailable: {type(e).__name__}: {e}",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }


# ---------------------------------------------------------------------------
# Backend 4: build-loop native debugging incidents
# ---------------------------------------------------------------------------

def probe_debugger(workdir: Path) -> Dict[str, Any]:  # noqa: ARG001 — kept for API symmetry
    """Probe native `.build-loop/issues` debugging memory."""
    started = time.monotonic()
    if _DEBUGGER_RUNNER_OVERRIDE is not None:
        ok, msg = _DEBUGGER_RUNNER_OVERRIDE()
        result: Dict[str, Any] = {
            "ok": ok,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        if not ok:
            result["reason"] = msg or "debugger_unavailable"
        return result

    issues_dir = workdir / ".build-loop" / "issues"
    if not issues_dir.is_dir():
        return {
            "ok": False,
            "reason": "debugger_unavailable: local issue dir absent",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    count = sum(1 for _ in issues_dir.rglob("*.md"))
    return {
        "ok": True,
        "count": count,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def probe_embedder(workdir: Path) -> Dict[str, Any]:  # noqa: ARG001 — kept for API symmetry
    """Probe the embedder backend by issuing a single canary embed call.

    Catches the silent-fallback class of failure where MLX is broken and
    Ollama silently takes over (or vice versa). Verifies vector dimension
    matches the schema (1024). Records active backend + latency.

    Soft-fail: never raises. Returns ``{ok: False, reason: ...}`` if the
    embedder can't return a vector at all.
    """
    started = time.monotonic()
    out: Dict[str, Any] = {"ok": False, "duration_ms": 0}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        # Local import — keeps backend_health.py importable on hosts
        # without embed_backend's transitive deps installed.
        from embed_backend import EMBED_DIM, active_backend, embed  # type: ignore
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"import_failed:{type(exc).__name__}"
        out["duration_ms"] = int((time.monotonic() - started) * 1000)
        return out
    try:
        vec = embed("backend_health canary")
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"embed_failed:{type(exc).__name__}"
        out["duration_ms"] = int((time.monotonic() - started) * 1000)
        return out
    out["duration_ms"] = int((time.monotonic() - started) * 1000)
    if not isinstance(vec, list) or len(vec) != EMBED_DIM:
        out["reason"] = f"dim_mismatch:expected_{EMBED_DIM}_got_{len(vec) if isinstance(vec, list) else type(vec).__name__}"
        return out
    try:
        out["backend"] = active_backend()
    except Exception:  # noqa: BLE001
        out["backend"] = "unknown"
    out["dim"] = len(vec)
    out["ok"] = True
    return out


def probe_fts(workdir: Path) -> Dict[str, Any]:  # noqa: ARG001 — kept for API symmetry
    """Probe Postgres for full-text-search readiness.

    Surfaces three things hybrid retrieval depends on:
      - pg_trgm extension present (used by today's recall.py)
      - tsvector / GIN index present on semantic_facts.object (Phase A target)
      - HNSW index present on semantic_facts.embedding

    Each missing piece is reported individually so Phase A can detect
    work needed without re-running the migration script. Soft-fail.
    """
    started = time.monotonic()
    out: Dict[str, Any] = {"ok": False, "duration_ms": 0}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from db import query  # type: ignore
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"import_failed:{type(exc).__name__}"
        out["duration_ms"] = int((time.monotonic() - started) * 1000)
        return out
    schema = os.environ.get("AGENT_MEMORY_SCHEMA", "personal_memory")
    try:
        ext_rows = query("SELECT extname FROM pg_extension WHERE extname IN ('pg_trgm', 'vector')")
        extensions = {r["extname"] for r in ext_rows} if ext_rows else set()
        idx_rows = query(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = %s AND tablename = 'semantic_facts'",
            (schema,),
        ) or []
    except Exception as exc:  # noqa: BLE001
        # Mirror probe_semantic's failure-classifier so the one-liner stays consistent.
        msg = str(exc).lower()
        if "could not connect" in msg or "connection refused" in msg or "no such host" in msg:
            out["reason"] = "postgres_unavailable"
        else:
            out["reason"] = f"query_failed:{type(exc).__name__}"
        out["duration_ms"] = int((time.monotonic() - started) * 1000)
        return out
    idx_defs = " ".join((r["indexdef"] or "") for r in idx_rows).lower()
    out["pg_trgm"] = "pg_trgm" in extensions
    out["pgvector"] = "vector" in extensions
    out["hnsw_on_embedding"] = "hnsw" in idx_defs and "embedding" in idx_defs
    out["gin_on_object"] = "gin" in idx_defs and ("to_tsvector" in idx_defs or "tsvector" in idx_defs)
    out["duration_ms"] = int((time.monotonic() - started) * 1000)
    # Phase A acceptance: pg_trgm + pgvector + HNSW are required today;
    # gin_on_object is the new piece Phase A will add. Mark ok based on
    # today's set so the canary doesn't false-alarm pre-Phase-A.
    out["ok"] = out["pg_trgm"] and out["pgvector"] and out["hnsw_on_embedding"]
    if not out["ok"]:
        missing = [k for k in ("pg_trgm", "pgvector", "hnsw_on_embedding") if not out[k]]
        out["reason"] = "missing:" + ",".join(missing)
    return out


def _format_one_liner(envelope: Dict[str, Any]) -> str:
    """Human-readable single line. Matches verification rule 5 contract."""
    parts: List[str] = []
    runs = envelope.get("runs", {})
    parts.append(f"runs: {'OK' if runs.get('ok') else 'DOWN'} {runs.get('count', 0)} entries"
                 if runs.get("ok") else f"runs: DOWN {runs.get('reason', 'unknown')}")
    decisions = envelope.get("decisions", {})
    if decisions.get("ok"):
        legacy = decisions.get("legacy") or {}
        canonical = decisions.get("canonical") or {}
        if "legacy" in decisions and "canonical" in decisions:
            legacy_n = legacy.get("count", 0) if legacy.get("ok") else 0
            canonical_n = canonical.get("count", 0) if canonical.get("ok") else 0
            parts.append(
                f"decisions: OK {canonical_n} canonical + {legacy_n} legacy-diagnostic"
            )
        else:
            parts.append(f"decisions: OK {decisions.get('count', 0)} entries")
    else:
        parts.append(f"decisions: DOWN {decisions.get('reason', 'unknown')}")
    rename_warning = (decisions.get("canonical") or {}).get("renameWarning")
    if rename_warning:
        current = rename_warning.get("current_slug", "?")
        likely = rename_warning.get("likely_old_slug")
        if likely:
            n = rename_warning.get("likely_old_slug_file_count", "?")
            parts.append(
                f"WARNING: canonical memory dir missing for '{current}' but "
                f"projects/{likely}/ has {n} file(s) — possible un-pinned repo "
                f"rename. FIX: register '{likely}' as an alias so old references "
                f"walk to the current id — run `python3 scripts/migrate_project_identity.py "
                f"--apply` (adds the alias in build-loop-memory/config/projects.yaml, "
                f"folders stay put), or hand-add '{likely}' to the matching project's "
                f"aliases: []. The single-value memoryProjectSlug pin in "
                f".build-loop/config.json still works as the fallback."
            )
        else:
            slugs = ", ".join(c["slug"] for c in rename_warning.get("candidates", [])[:3])
            parts.append(
                f"WARNING: canonical memory dir missing for '{current}' — sibling "
                f"project dir(s) with content exist ({slugs}). Check whether this repo "
                f"was renamed; if so register the old slug as an ALIAS of the current "
                f"id (build-loop-memory/config/projects.yaml, or run "
                f"`python3 scripts/migrate_project_identity.py --apply`). The "
                f"memoryProjectSlug pin remains the single-value fallback."
            )
    semantic = envelope.get("semantic", {})
    if semantic.get("ok"):
        parts.append(f"semantic: OK")
    else:
        # Trim noisy reason strings to the leading classifier token.
        r = (semantic.get("reason") or "unknown").split(":")[0]
        parts.append(f"semantic: DOWN {r}")
    debugger = envelope.get("debugger", {})
    if debugger.get("ok"):
        parts.append(f"debugger: OK")
    else:
        r = (debugger.get("reason") or "unknown").split(":")[0]
        parts.append(f"debugger: DOWN {r}")
    embedder = envelope.get("embedder")
    if embedder is not None:
        if embedder.get("ok"):
            parts.append(f"embedder: OK {embedder.get('backend', '?')}/{embedder.get('dim', '?')}d {embedder.get('duration_ms', 0)}ms")
        else:
            r = (embedder.get("reason") or "unknown").split(":")[0]
            parts.append(f"embedder: DOWN {r}")
    fts = envelope.get("fts")
    if fts is not None:
        if fts.get("ok"):
            gin = " +gin" if fts.get("gin_on_object") else ""
            parts.append(f"fts: OK pg_trgm+hnsw{gin}")
        else:
            r = (fts.get("reason") or "unknown").split(":")[0]
            parts.append(f"fts: DOWN {r}")
    return " | ".join(parts)


def run_health_check(workdir: Path, *, include_retrieval: bool = False) -> Dict[str, Any]:
    """Run all backend probes; respect the 30s total budget.

    ``include_retrieval`` adds embedder + FTS probes. Off by default to
    keep the existing Phase 1 latency budget intact; SessionStart / Phase F
    callers pass True to surface silent retrieval-stack breakage.
    """
    started_all = time.monotonic()
    envelope: Dict[str, Any] = {}

    envelope["runs"] = probe_runs(workdir)
    envelope["decisions"] = probe_decisions(workdir)

    elapsed = time.monotonic() - started_all
    if elapsed >= TOTAL_BUDGET_S:
        # Filesystem probes overshot — extreme edge case. Mark remaining as skipped.
        envelope["semantic"] = {"ok": False, "reason": "budget_exhausted", "duration_ms": 0}
        envelope["debugger"] = {"ok": False, "reason": "budget_exhausted", "duration_ms": 0}
        if include_retrieval:
            envelope["embedder"] = {"ok": False, "reason": "budget_exhausted", "duration_ms": 0}
            envelope["fts"] = {"ok": False, "reason": "budget_exhausted", "duration_ms": 0}
    else:
        envelope["semantic"] = probe_semantic(workdir)
        elapsed = time.monotonic() - started_all
        if elapsed >= TOTAL_BUDGET_S:
            envelope["debugger"] = {"ok": False, "reason": "budget_exhausted", "duration_ms": 0}
        else:
            envelope["debugger"] = probe_debugger(workdir)
        if include_retrieval:
            elapsed = time.monotonic() - started_all
            if elapsed >= TOTAL_BUDGET_S:
                envelope["embedder"] = {"ok": False, "reason": "budget_exhausted", "duration_ms": 0}
                envelope["fts"] = {"ok": False, "reason": "budget_exhausted", "duration_ms": 0}
            else:
                envelope["embedder"] = probe_embedder(workdir)
                elapsed = time.monotonic() - started_all
                if elapsed >= TOTAL_BUDGET_S:
                    envelope["fts"] = {"ok": False, "reason": "budget_exhausted", "duration_ms": 0}
                else:
                    envelope["fts"] = probe_fts(workdir)

    envelope["generated_at"] = datetime.now(timezone.utc).isoformat()
    envelope["total_duration_ms"] = int((time.monotonic() - started_all) * 1000)
    envelope["summary"] = _format_one_liner(envelope)
    return envelope


def write_into_state(workdir: Path, envelope: Dict[str, Any]) -> None:
    """Append envelope to `state.json.architecture.backendHealth`. Best-effort."""
    state_path = workdir / ".build-loop" / "state.json"
    if not state_path.parent.exists():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return
    arch = state.get("architecture") or {}
    arch["backendHealth"] = envelope
    state["architecture"] = arch
    try:
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument("--json", action="store_true", help="Emit full JSON envelope")
    parser.add_argument("--quiet", action="store_true", help="Suppress one-liner; only write to state.json")
    parser.add_argument("--no-cache", action="store_true",
                        help="Do not write to state.json.architecture.backendHealth")
    parser.add_argument("--include-retrieval", action="store_true",
                        help="Add embedder + FTS probes (catches silent retrieval-stack breakage)")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    envelope = run_health_check(workdir, include_retrieval=args.include_retrieval)

    if not args.no_cache:
        write_into_state(workdir, envelope)

    if args.json:
        json.dump(envelope, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif not args.quiet:
        print(envelope["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
