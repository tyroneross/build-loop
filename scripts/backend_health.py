#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Phase 1 backend health-check surface.

Probes each of build-loop's four memory backends and returns a one-line
summary plus a JSON envelope suitable for `state.json.architecture.backendHealth`.

Backends:
  1. runs[]       — `state.json.runs[]` (filesystem; always probable)
  2. decisions    — `.episodic/decisions/*.md` (filesystem)
  3. semantic     — Postgres `agent_memory.<schema>.semantic_facts`
  4. debugger     — `@tyroneross/claude-code-debugger` MCP / npx CLI

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
import json
import os
import re
import subprocess
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

# Test injection points — set via setter functions below to mock subprocess
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

def _resolve_canonical_decisions_dir(workdir: Path) -> Optional[Path]:
    """Resolve the canonical (global) decisions directory for this project.

    Mirrors `memory_facade._resolve_decision_dirs` — uses `_paths` and
    `project_resolver` to land on
    `~/dev/git-folder/build-loop-memory/decisions/<project>/`. Returns
    `None` on any resolution failure (graceful degradation contract).
    """
    try:
        # Both modules live under scripts/, importable when this file is
        # imported by tests (which prepend scripts/ to sys.path) or by the
        # CLI (where scripts/ is the parent dir).
        scripts_dir = Path(__file__).resolve().parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from _paths import decisions_dir_for_project  # type: ignore  # noqa: PLC0415
        from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415
        proj = resolve_project(workdir)
        if not proj:
            return None
        return decisions_dir_for_project(proj)
    except Exception:  # noqa: BLE001 — best-effort resolution
        return None


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

    Two stores after the v0.10.0 cutover (mirrors `memory_facade`'s read
    path):

      1. **Legacy (per-repo)**: `<workdir>/.episodic/decisions/*.md`
      2. **Canonical (global)**: `~/dev/git-folder/build-loop-memory/
         decisions/<project>/*.md` (resolved via `_paths` +
         `project_resolver`).

    Envelope shape (Priority 20):

        {
          "ok": <True if either store has files>,
          "count": <legacy_count + canonical_count>,
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

    duration_ms = int((time.monotonic() - started) * 1000)
    any_ok = legacy["ok"] or canonical["ok"]
    total_count = (legacy["count"] if legacy["ok"] else 0) + (
        canonical["count"] if canonical["ok"] else 0
    )

    envelope: Dict[str, Any] = {
        "ok": any_ok,
        "count": total_count,
        "duration_ms": duration_ms,
        "legacy": legacy,
        "canonical": canonical,
    }
    if not any_ok:
        # Aggregate reason — useful for the one-liner.
        envelope["reason"] = "no decision stores"
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
                cur.execute(f'SELECT count(*) FROM {schema}.semantic_facts')
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
# Backend 4: claude-code-debugger MCP
# ---------------------------------------------------------------------------

def probe_debugger(workdir: Path) -> Dict[str, Any]:  # noqa: ARG001 — kept for API symmetry
    """Probe `@tyroneross/claude-code-debugger` MCP reachability.

    We attempt a `status` invocation through the npx-installed CLI. If the
    package isn't installed or `npx` isn't on PATH, classify as `mcp_unreachable`.
    Mirror of `memory_facade.read_debugger`'s probe pattern.
    """
    started = time.monotonic()
    if _DEBUGGER_RUNNER_OVERRIDE is not None:
        ok, msg = _DEBUGGER_RUNNER_OVERRIDE()
        result: Dict[str, Any] = {
            "ok": ok,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        if not ok:
            result["reason"] = msg or "mcp_unreachable"
        return result

    try:
        proc = subprocess.run(
            ["npx", "--no-install", "@tyroneross/claude-code-debugger", "status"],
            capture_output=True,
            text=True,
            timeout=PER_BACKEND_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return {
            "ok": False,
            "reason": f"mcp_unreachable: {type(e).__name__}",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    if proc.returncode != 0:
        return {
            "ok": False,
            "reason": f"mcp_unreachable: rc={proc.returncode}",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    return {
        "ok": True,
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
        extensions = {r[0] for r in ext_rows} if ext_rows else set()
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
    idx_defs = " ".join((d or "") for _name, d in idx_rows).lower()
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
        # Priority 20 — show legacy + canonical split when both shapes are present.
        legacy = decisions.get("legacy") or {}
        canonical = decisions.get("canonical") or {}
        if "legacy" in decisions and "canonical" in decisions:
            legacy_n = legacy.get("count", 0) if legacy.get("ok") else 0
            canonical_n = canonical.get("count", 0) if canonical.get("ok") else 0
            parts.append(
                f"decisions: OK {legacy_n} legacy + {canonical_n} canonical"
            )
        else:
            parts.append(f"decisions: OK {decisions.get('count', 0)} entries")
    else:
        parts.append(f"decisions: DOWN {decisions.get('reason', 'unknown')}")
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
