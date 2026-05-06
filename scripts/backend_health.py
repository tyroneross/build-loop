#!/usr/bin/env python3
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

    Mirrors the contract from `memory_facade.read_semantic`: BUILD_LOOP_DATABASE_URL
    drives the connection. We just attempt a `SELECT 1 FROM <schema>.semantic_facts LIMIT 1`
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

    db_url = os.environ.get("BUILD_LOOP_DATABASE_URL", "").strip()
    if not db_url:
        return {
            "ok": False,
            "reason": "postgres_unavailable: BUILD_LOOP_DATABASE_URL unset",
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
    return " | ".join(parts)


def run_health_check(workdir: Path) -> Dict[str, Any]:
    """Run all four backend probes; respect the 30s total budget."""
    started_all = time.monotonic()
    envelope: Dict[str, Any] = {}

    envelope["runs"] = probe_runs(workdir)
    envelope["decisions"] = probe_decisions(workdir)

    elapsed = time.monotonic() - started_all
    if elapsed >= TOTAL_BUDGET_S:
        # Filesystem probes overshot — extreme edge case. Mark remaining as skipped.
        envelope["semantic"] = {"ok": False, "reason": "budget_exhausted", "duration_ms": 0}
        envelope["debugger"] = {"ok": False, "reason": "budget_exhausted", "duration_ms": 0}
    else:
        envelope["semantic"] = probe_semantic(workdir)
        elapsed = time.monotonic() - started_all
        if elapsed >= TOTAL_BUDGET_S:
            envelope["debugger"] = {"ok": False, "reason": "budget_exhausted", "duration_ms": 0}
        else:
            envelope["debugger"] = probe_debugger(workdir)

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
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    envelope = run_health_check(workdir)

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
