#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Build a Phase 1 context packet from all memory surfaces.

This is the automatic memory bootstrap layer for Build Loop Assess. It keeps
the canonical build-loop memory facade as the source of durable project/global
truth, then adds the surfaces that the facade deliberately does not own:

  - repo-local `.build-loop/` state, feedback, goal, intent, and plan files
  - Codex memory registry at `~/.codex/memories/MEMORY.md`
  - rollout summaries linked from relevant Codex registry blocks
  - best-effort Rally / coordination state when coordination context exists

The contract is graceful degradation: missing or unavailable surfaces are
reported in `reasons[]`; they never fail Phase 1 by themselves.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from memory_facade import recall as recall_memory  # type: ignore  # noqa: E402
from project_resolver import resolve_project  # type: ignore  # noqa: E402
from _paths import (  # type: ignore  # noqa: E402
    memory_indexes_dir,
    memory_store_root,
    project_decisions_dir,
    project_lessons_dir,
    top_level_lessons_dir,
)


DEFAULT_CODEX_MEMORY_ROOT = Path("~/.codex/memories")
DEFAULT_LIMIT = 6
DEFAULT_MAX_EXCERPT_CHARS = 1600
CONSTITUTION_TEMPLATE = HERE.parent / "templates" / "memory" / "constitution.md.template"
QUEUE_NAMES = ("issues", "backlog", "ux-queue", "followup", "proposals", "pending-lessons")
SESSION_PREFS_VALID = ("ask", "always", "never")
REPO_LOCAL_FILES = (
    ".build-loop/feedback.md",
    ".build-loop/state.json",
    ".build-loop/intent.md",
    ".build-loop/goal.md",
    ".build-loop/plan.md",
)
ROLLOUT_REF_RE = re.compile(r"(rollout_summaries/[^\s)]+)")
THREAD_ID_RE = re.compile(r"thread_id=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
WORD_RE = re.compile(r"[a-z0-9][a-z0-9._/-]{2,}", re.IGNORECASE)


@dataclass(frozen=True)
class RegistryBlock:
    title: str
    start_line: int
    end_line: int
    text: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def expand_path(raw: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(raw)))).resolve()


def read_text(path: Path, max_chars: int | None = None) -> tuple[str | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"missing: {path}"
    except OSError as exc:
        return None, f"read_error: {path}: {exc}"
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars], f"truncated: {path}: first {max_chars} chars"
    return text, None


def ensure_root_constitution(memory_root: Path) -> list[str]:
    """Seed root constitution.md from the shipped template if missing.

    This is intentionally narrow: it never overwrites an existing file and it
    does not create project-specific constitutions. The root constitution is
    the binding default that Phase 1 and advisory judges expect to exist.
    """
    target = memory_root / "constitution.md"
    if target.exists():
        return []
    if not CONSTITUTION_TEMPLATE.exists():
        return [f"constitution_template_missing: {CONSTITUTION_TEMPLATE}"]
    try:
        body = CONSTITUTION_TEMPLATE.read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.write(fd, body)
        finally:
            os.close(fd)
    except FileExistsError:
        return []
    except OSError as exc:
        return [f"constitution_seed_error: {target}: {exc}"]
    return [f"constitution_seeded: {target}"]


def token_set(query: str, workdir: Path, project: str) -> list[str]:
    raw = " ".join(
        [
            query or "",
            project or "",
            workdir.name,
            str(workdir),
        ]
    )
    seen: set[str] = set()
    out: list[str] = []
    for match in WORD_RE.findall(raw.lower()):
        term = match.strip("/._-")
        if len(term) < 3 or term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


def score_text(text: str, terms: Iterable[str]) -> int:
    lower = text.lower()
    score = 0
    for term in terms:
        if not term:
            continue
        count = lower.count(term.lower())
        if count:
            score += count
            if "/" in term or "-" in term or "_" in term:
                score += 3
    return score


def excerpt(text: str, terms: Iterable[str], max_chars: int = DEFAULT_MAX_EXCERPT_CHARS) -> str:
    if len(text) <= max_chars:
        return text.strip()
    lower = text.lower()
    positions = [
        pos
        for term in terms
        if term
        for pos in [lower.find(term.lower())]
        if pos >= 0
    ]
    if positions:
        mid = min(positions)
        start = max(0, mid - max_chars // 3)
    else:
        start = 0
    end = min(len(text), start + max_chars)
    chunk = text[start:end].strip()
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{chunk}{suffix}"


def short_path(path: Path, workdir: Path | None = None) -> str:
    try:
        if workdir is not None:
            return str(path.relative_to(workdir))
    except ValueError:
        pass
    home = Path.home()
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def load_state_summary(state_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    text, reason = read_text(state_path)
    if text is None:
        return None, reason
    try:
        state = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"json_error: {state_path}: {exc}"

    architecture = state.get("architecture") if isinstance(state.get("architecture"), dict) else {}
    summary = {
        "execution": state.get("execution"),
        "phase": state.get("phase"),
        "triggers": state.get("triggers"),
        "approachLenses": state.get("approachLenses"),
        "synthesisDensity": state.get("synthesisDensity"),
        "backendHealth": architecture.get("backendHealth") if isinstance(architecture, dict) else None,
        "runs_tail": (state.get("runs") or [])[-3:] if isinstance(state.get("runs"), list) else [],
    }
    present = {k: v for k, v in summary.items() if v not in (None, [], {})}
    return present, None


def _frontmatter_title(path: Path) -> str:
    """Return the title/name from a file's frontmatter, or the stem as fallback.

    Reads only the first 40 lines to keep queue surfacing cheap.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[:40]
    except OSError:
        return path.stem
    in_fm = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            if not in_fm:
                in_fm = True
                continue
            else:
                break  # end of frontmatter
        if in_fm:
            m = re.match(r"^(title|name)\s*:\s*(.+)$", stripped, re.IGNORECASE)
            if m:
                return m.group(2).strip().strip('"').strip("'")
    return path.stem


def queue_context(workdir: Path) -> dict[str, Any]:
    """Count .md files in each .build-loop/<queue>/ dir and extract top-3 titles.

    Missing dirs → count 0, empty top. Never raises.
    """
    bl = workdir / ".build-loop"
    result: dict[str, Any] = {}
    for qname in QUEUE_NAMES:
        qdir = bl / qname
        if not qdir.is_dir():
            result[qname] = {"count": 0, "top": []}
            continue
        try:
            items = sorted(qdir.glob("*.md"))
        except OSError:
            result[qname] = {"count": 0, "top": []}
            continue
        top = [
            {"title": _frontmatter_title(p), "file": p.name}
            for p in items[:3]
        ]
        result[qname] = {"count": len(items), "top": top}
    return result


def lessons_progressive_context(
    query: str,
    project: str,
    workdir: Path,
    limit: int = 5,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run incremental ingest then FTS query from lessons_index.

    Returns (results, reasons). Degrades to ([], reasons) on any failure.
    Always safe: never raises; never hard-fails bootstrap.
    """
    reasons: list[str] = []
    try:
        import lessons_index as _li  # type: ignore  # noqa: PLC0415
    except ImportError as exc:
        reasons.append(f"lessons_index_import_failed: {exc}")
        return [], reasons

    try:
        # query() scopes to (project OR '_unscoped'), so BOTH lanes must be
        # ingested: top-level cross-project lessons (stored as _unscoped) AND
        # the project's own lane. A single project-or-None ingest covers only
        # one, leaving the other half of the query scope unpopulated.
        _li.ingest(project=None)        # top-level lanes -> stored as _unscoped
        _li.ingest(project=project)     # projects/<project>/ lanes (incl. literal _unscoped)
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"lessons_index_ingest_error: {exc}")

    try:
        # Pass the literal project so query scopes to (project OR _unscoped);
        # project=None would broaden to ALL projects (not current-work-scoped).
        raw = _li.query(goal_text=query or workdir.name, project=project, limit=limit)
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"lessons_index_query_error: {exc}")
        return [], reasons

    if not raw:
        reasons.append("lessons_index_empty_or_no_match")
        return [], reasons

    results = [
        {
            "name": r.get("name", ""),
            "description": r.get("description", ""),
            "snippet": r.get("snippet", ""),
            "score": r.get("score", 0.0),
            "source_path": r.get("source_path", ""),
        }
        for r in raw
    ]
    return results, reasons


def read_session_prefs(workdir: Path) -> dict[str, Any]:
    """Read session_prefs from state.json and config.json (config overrides).

    Returns a dict with keys: continue_from_queues, set_at, source.
    Default (absent) = {continue_from_queues: "ask", source: "default"}.
    Config override (.build-loop/config.json sessionPrefs.continueFromQueues) →
    source "config". State.json session_prefs → source as stored.
    """
    default: dict[str, Any] = {
        "continue_from_queues": "ask",
        "set_at": None,
        "source": "default",
    }

    # 1. Load state.json session_prefs.
    state_path = workdir / ".build-loop" / "state.json"
    state_prefs: dict[str, Any] | None = None
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            sp = state.get("session_prefs")
            if isinstance(sp, dict) and sp.get("continue_from_queues") in SESSION_PREFS_VALID:
                state_prefs = {
                    "continue_from_queues": sp["continue_from_queues"],
                    "set_at": sp.get("set_at"),
                    "source": sp.get("source", "asked"),
                }
        except (json.JSONDecodeError, OSError):
            pass

    # 2. Load config.json override.
    config_path = workdir / ".build-loop" / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            val = (cfg.get("sessionPrefs") or {}).get("continueFromQueues")
            if val in SESSION_PREFS_VALID:
                return {
                    "continue_from_queues": val,
                    "set_at": None,
                    "source": "config",
                }
        except (json.JSONDecodeError, OSError):
            pass

    return state_prefs if state_prefs is not None else default


def write_session_prefs(
    workdir: Path,
    continue_from_queues: str,
    source: str = "asked",
) -> None:
    """Persist session_prefs into state.json.

    Reads state.json, merges session_prefs, writes back atomically.
    Creates .build-loop/state.json (with skeleton) if absent.
    Never raises — errors are silently swallowed so bootstrap stays live.
    """
    if continue_from_queues not in SESSION_PREFS_VALID:
        return
    bl = workdir / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    state_path = bl / "state.json"

    try:
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        else:
            state = {"runs": [], "schema_version": "1.0.0"}

        state["session_prefs"] = {
            "continue_from_queues": continue_from_queues,
            "set_at": utc_now(),
            "source": source,
        }
        tmp = state_path.with_name(f".{state_path.name}.tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, state_path)
    except (OSError, json.JSONDecodeError):
        pass


def should_continue_into_queues(workdir: Path) -> bool:
    """Return True when the end-of-run backlog/issues drain should run.

    SHIPPED DEFAULT (2026-06-04): an *unset* preference behaves as ``"always"``
    so every build-loop run auto-drains its backlog at end-of-thread without
    operator intervention. Existing explicit preferences are still respected:

    - ``source == "default"`` (unset, fresh repo)   → True  (auto-drain)
    - ``continue_from_queues == "always"``           → True
    - ``continue_from_queues == "never"``            → False (per-repo opt-out)
    - ``continue_from_queues == "ask"`` (explicit)   → False (legacy opt-in path)

    The per-repo opt-out remains ``continue_from_queues: "never"`` in
    ``.build-loop/config.json`` or via ``write_session_prefs(workdir, "never")``.
    """
    prefs = read_session_prefs(workdir)
    if prefs["continue_from_queues"] == "always":
        return True
    if prefs["continue_from_queues"] == "never":
        return False
    # "ask" — distinguish unset (default flip → True) from explicit ask (legacy False).
    return prefs.get("source") == "default"


def pending_queue_items(workdir: Path) -> dict[str, Any]:
    """Return per-queue counts for issues and backlog only.

    Used by the end-of-run continuation check to decide whether there is
    actually anything to drain before entering the extra iterate cycle.
    Returns {"issues": int, "backlog": int} — never raises.
    """
    bl = workdir / ".build-loop"
    result: dict[str, Any] = {}
    for qname in ("issues", "backlog"):
        qdir = bl / qname
        if not qdir.is_dir():
            result[qname] = 0
            continue
        try:
            result[qname] = len(list(qdir.glob("*.md")))
        except OSError:
            result[qname] = 0
    return result


def repo_local_context(workdir: Path, terms: list[str], max_chars: int) -> dict[str, Any]:
    reasons: list[str] = []
    files: list[dict[str, Any]] = []

    for rel in REPO_LOCAL_FILES:
        path = workdir / rel
        if rel.endswith("state.json"):
            summary, reason = load_state_summary(path)
            if reason:
                reasons.append(reason)
                files.append({"path": rel, "exists": False, "reason": reason})
            else:
                files.append({"path": rel, "exists": True, "summary": summary})
            continue

        text, reason = read_text(path)
        if text is None:
            reasons.append(reason or f"missing: {path}")
            files.append({"path": rel, "exists": False, "reason": reason})
            continue
        files.append(
            {
                "path": rel,
                "exists": True,
                "score": score_text(text, terms),
                "excerpt": excerpt(text, terms, max_chars=max_chars),
            }
        )

    coord_dir = workdir / ".build-loop" / "coordination"
    coordination_files: list[dict[str, Any]] = []
    if coord_dir.is_dir():
        # Most-RECENT 8 coordination files, not alphabetical-first. Alphabetical
        # order loaded stale files and starved the freshest signal (audit 2026-05-31).
        def _mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0
        for path in sorted(coord_dir.glob("*.md"), key=_mtime, reverse=True)[:8]:
            text, reason = read_text(path)
            if text is None:
                reasons.append(reason or f"read_error: {path}")
                continue
            coordination_files.append(
                {
                    "path": short_path(path, workdir),
                    "score": score_text(text, terms),
                    "excerpt": excerpt(text, terms, max_chars=min(max_chars, 1000)),
                }
            )

    return {
        "files": files,
        "coordination_files": coordination_files,
        "reasons": reasons,
    }


def canonical_memory_context(
    workdir: Path,
    query: str,
    project: str,
    terms: list[str],
    limit: int,
    include_postgres: bool,
    include_debugger: bool,
    max_chars: int,
) -> dict[str, Any]:
    kinds = ["runs", "decisions", "lessons"]
    if include_postgres:
        kinds.append("semantic")
    if include_debugger:
        kinds.append("debugger")

    results_by_kind: dict[str, list[dict[str, Any]]] = {}
    merged: list[dict[str, Any]] = []
    reasons: list[str] = []
    telemetry_ids: list[str] = []
    telemetry_warnings: list[str] = []
    root = memory_store_root()
    reasons.extend(ensure_root_constitution(root))

    try:
        for kind in kinds:
            stderr_buf = io.StringIO()
            with contextlib.redirect_stderr(stderr_buf):
                envelope = recall_memory(
                    query=query,
                    kind=kind,
                    project=project if project != "_unscoped" else None,
                    limit=limit,
                    workdir=workdir,
                    skip_postgres=not include_postgres,
                )
            for line in stderr_buf.getvalue().splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("WARN: memory_telemetry"):
                    telemetry_warnings.append(stripped)
                else:
                    reasons.append(f"memory_facade_stderr: {stripped}")
            kind_results = envelope.get("results_by_kind", {}).get(kind, [])
            results_by_kind[kind] = kind_results
            merged.extend(kind_results)
            reasons.extend(envelope.get("reasons", []))
            if envelope.get("telemetry_correlation_id"):
                telemetry_ids.append(envelope["telemetry_correlation_id"])
    except Exception as exc:  # noqa: BLE001 - bootstrap must not block Assess
        return {
            "ok": False,
            "results_by_kind": {},
            "merged": [],
            "reasons": [f"canonical_memory_error: {exc}"],
        }
    merged.sort(key=lambda row: row.get("_recency_ts") or 0, reverse=True)
    if not include_postgres:
        reasons.append("skipped_postgres: context_bootstrap default file-backed pass")
    if not include_debugger:
        reasons.append("skipped_debugger: context_bootstrap default file-backed pass")
    canonical_files, file_reasons = canonical_memory_files(
        memory_root=root,
        project=project,
        terms=terms,
        max_chars=max_chars,
    )
    reasons.extend(file_reasons)
    return {
        "ok": True,
        "files": canonical_files,
        "results_by_kind": results_by_kind,
        "merged": merged[: limit * len(kinds)],
        "reasons": reasons,
        "telemetry_correlation_ids": telemetry_ids,
        "telemetry_warnings": telemetry_warnings,
    }


def canonical_memory_files(
    memory_root: Path,
    project: str,
    terms: list[str],
    max_chars: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    candidates: list[tuple[Path, str, bool]] = [
        (memory_root / "MEMORY.md", "file", True),
        (memory_root / "constitution.md", "file", True),
        (memory_indexes_dir() / "INDEX.jsonl", "file", True),
        (top_level_lessons_dir(), "dir", True),
    ]
    if project and project != "_unscoped":
        candidates.extend(
            [
                (memory_root / "projects" / project / "MEMORY.md", "file", True),
                (memory_root / "projects" / project / "constitution.md", "file", True),
                (project_decisions_dir(project), "dir", True),
                (project_lessons_dir(project), "dir", True),
            ]
        )

    files: list[dict[str, Any]] = []
    reasons: list[str] = []
    for path, kind, optional in candidates:
        if kind == "dir":
            if not path.is_dir():
                files.append(
                    {
                        "path": short_path(path),
                        "exists": False,
                        "kind": kind,
                        "optional": optional,
                        "reason": f"missing: {path}",
                    }
                )
                if not optional:
                    reasons.append(f"missing: {path}")
                continue
            try:
                entries = sorted(p.name for p in path.glob("*.md"))
            except OSError as exc:
                reasons.append(f"read_error: {path}: {exc}")
                files.append(
                    {
                        "path": short_path(path),
                        "exists": False,
                        "kind": kind,
                        "optional": optional,
                        "reason": f"read_error: {path}: {exc}",
                    }
                )
                continue
            files.append(
                {
                    "path": short_path(path),
                    "exists": True,
                    "kind": kind,
                    "count": len(entries),
                    "entries_sample": entries[:8],
                    "score": score_text("\n".join(entries), terms),
                    "excerpt": "\n".join(entries[:8]),
                }
            )
            continue

        text, reason = read_text(path)
        if text is None:
            files.append(
                {
                    "path": short_path(path),
                    "exists": False,
                    "kind": kind,
                    "optional": optional,
                    "reason": reason,
                }
            )
            if not optional:
                reasons.append(reason or f"missing: {path}")
            continue
        files.append(
            {
                "path": short_path(path),
                "exists": True,
                "kind": kind,
                "score": score_text(text, terms),
                "excerpt": excerpt(text, terms, max_chars=max_chars),
            }
        )
    if not any(item.get("exists") for item in files):
        reasons.append("canonical_memory_no_files_present")
    return files, reasons


def split_registry_blocks(text: str) -> list[RegistryBlock]:
    lines = text.splitlines()
    starts: list[int] = []
    for idx, line in enumerate(lines, start=1):
        if line.startswith("# Task Group:"):
            starts.append(idx)
    if not starts:
        return [RegistryBlock(title="MEMORY.md", start_line=1, end_line=len(lines), text=text)]

    blocks: list[RegistryBlock] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] - 1 if i + 1 < len(starts) else len(lines)
        block_lines = lines[start - 1:end]
        title = block_lines[0].lstrip("# ").strip() if block_lines else "Task Group"
        blocks.append(
            RegistryBlock(
                title=title,
                start_line=start,
                end_line=end,
                text="\n".join(block_lines),
            )
        )
    return blocks


def codex_memory_context(
    memory_root: Path,
    workdir: Path,
    project: str,
    terms: list[str],
    limit: int,
    rollout_limit: int,
    max_chars: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    registry_path = memory_root / "MEMORY.md"
    text, reason = read_text(registry_path)
    if text is None:
        return {
            "memory_root": str(memory_root),
            "registry_path": short_path(registry_path),
            "registry_hits": [],
            "rollout_hits": [],
            "reasons": [reason or f"missing: {registry_path}"],
        }

    scored: list[tuple[int, RegistryBlock]] = []
    workdir_text = str(workdir).lower()
    project_text = project.lower()
    for block in split_registry_blocks(text):
        score = score_text(block.text, terms)
        block_lower = block.text.lower()
        if workdir_text and workdir_text in block_lower:
            score += 50
        if project_text and project_text != "_unscoped" and project_text in block.title.lower():
            score += 20
        if score > 0:
            scored.append((score, block))
    scored.sort(key=lambda item: (item[0], -item[1].start_line), reverse=True)

    registry_hits: list[dict[str, Any]] = []
    rollout_refs: list[str] = []
    for score, block in scored[:limit]:
        refs = list(dict.fromkeys(ROLLOUT_REF_RE.findall(block.text)))
        rollout_refs.extend(refs)
        registry_hits.append(
            {
                "title": block.title,
                "path": short_path(registry_path),
                "line_start": block.start_line,
                "line_end": block.end_line,
                "score": score,
                "rollout_refs": refs[:5],
                "thread_ids": list(dict.fromkeys(THREAD_ID_RE.findall(block.text)))[:5],
                "excerpt": excerpt(block.text, terms, max_chars=max_chars),
            }
        )

    rollout_hits: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for ref in rollout_refs:
        if ref in seen_refs:
            continue
        seen_refs.add(ref)
        path = memory_root / ref
        summary_text, summary_reason = read_text(path)
        if summary_text is None:
            reasons.append(summary_reason or f"missing: {path}")
            continue
        score = score_text(summary_text, terms)
        rollout_hits.append(
            {
                "path": ref,
                "score": score,
                "thread_ids": list(dict.fromkeys(THREAD_ID_RE.findall(summary_text)))[:5],
                "excerpt": excerpt(summary_text, terms, max_chars=max_chars),
            }
        )
        if len(rollout_hits) >= rollout_limit:
            break

    if not registry_hits:
        reasons.append("codex_memory_no_relevant_registry_hits")
    return {
        "memory_root": str(memory_root),
        "registry_path": short_path(registry_path),
        "registry_hits": registry_hits,
        "rollout_hits": rollout_hits,
        "reasons": reasons,
    }


def rally_context(workdir: Path, include_rally: bool) -> dict[str, Any]:
    reasons: list[str] = []
    coord_dir = workdir / ".build-loop" / "coordination"
    has_coord_files = coord_dir.is_dir() and any(coord_dir.glob("*.md"))
    state_path = workdir / ".build-loop" / "state.json"
    state_summary, _ = load_state_summary(state_path)
    has_execution = bool((state_summary or {}).get("execution"))

    if not include_rally and not has_coord_files and not has_execution:
        return {
            "checked": False,
            "status": None,
            "reasons": ["no_coordination_context_detected"],
        }

    script = HERE / "coordination_status.py"
    if not script.is_file():
        return {
            "checked": False,
            "status": None,
            "reasons": [f"missing: {script}"],
        }

    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--workdir",
                str(workdir),
                "--session-id",
                "context-bootstrap",
                "--json",
            ],
            cwd=str(workdir),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=4,
        )
    except subprocess.TimeoutExpired:
        return {
            "checked": True,
            "status": None,
            "reasons": ["coordination_status_timeout"],
        }
    except OSError as exc:
        return {
            "checked": True,
            "status": None,
            "reasons": [f"coordination_status_error: {exc}"],
        }

    if proc.returncode != 0:
        reasons.append(f"coordination_status_exit_{proc.returncode}: {proc.stderr.strip()[:400]}")
        return {"checked": True, "status": None, "reasons": reasons}
    try:
        status = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {
            "checked": True,
            "status": None,
            "reasons": [f"coordination_status_json_error: {exc}"],
        }
    # Strip the raw dirty-file list (often dozens of paths) — it's pure noise in a
    # MEMORY packet and belongs in the diff, not here (audit 2026-05-31). Keep a count.
    if isinstance(status, dict) and isinstance(status.get("dirty_files"), list):
        status["dirty_files_count"] = len(status["dirty_files"])
        status.pop("dirty_files", None)
    return {"checked": True, "status": status, "reasons": reasons}


def prior_art_context(
    workdir: Path,
    query: str,
    project: str,
    *,
    memory_root: Path | None = None,
    max_total_chars: int | None = None,
) -> dict[str, Any]:
    """P4 — Cross-project prior-art digest for Phase 1 Assess.

    Classifies the task's capability(ies) from ``query`` and surfaces prior
    implementations + linked decisions from OTHER projects in the
    build-loop-memory store. Designed to answer the cold "build semantic
    search" gap: the agent learns about atomize-news / atomize-ai / AIDA's
    prior approaches AND the "why" without the operator knowing to ask.

    Fail-soft contract:
      * ``BUILD_LOOP_PRIOR_ART=0`` disables the digest entirely (opt-out).
      * Missing classifier / engine / memory root → empty payload + reason.
      * Never raises; never blocks Phase 1.
    ``max_total_chars`` defaults to ``prior_art.DEFAULT_MAX_TOTAL_CHARS`` (4000)
    so the two modules stay in sync — never pass a raw magic number here.
    """
    if os.environ.get("BUILD_LOOP_PRIOR_ART") == "0":
        return {
            "enabled": False,
            "capabilities": [],
            "implementations": [],
            "decisions": [],
            "digest_text": "",
            "stats": {"impls": 0, "decisions": 0, "projects": [], "truncated": False},
            "reasons": ["prior_art_disabled_by_env"],
        }

    try:
        from capability_classifier import classify_envelope  # type: ignore  # noqa: PLC0415
        from prior_art import (  # type: ignore  # noqa: PLC0415
            DEFAULT_MAX_TOTAL_CHARS,
            build_prior_art,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "enabled": True,
            "capabilities": [],
            "implementations": [],
            "decisions": [],
            "digest_text": "",
            "stats": {"impls": 0, "decisions": 0, "projects": [], "truncated": False},
            "reasons": [f"prior_art_import_error: {exc}"],
        }

    resolved_max_chars: int = max_total_chars if max_total_chars is not None else DEFAULT_MAX_TOTAL_CHARS

    try:
        env = classify_envelope(query or workdir.name)
        digest = build_prior_art(
            query=query or workdir.name,
            capabilities=env["capabilities"],
            current_project=project,
            memory_root=memory_root,
            max_total_chars=resolved_max_chars,
            terms=env["terms"],
        )
    except Exception as exc:  # noqa: BLE001 — Phase 1 must never block
        return {
            "enabled": True,
            "capabilities": [],
            "implementations": [],
            "decisions": [],
            "digest_text": "",
            "stats": {"impls": 0, "decisions": 0, "projects": [], "truncated": False},
            "reasons": [f"prior_art_runtime_error: {exc}"],
        }
    digest["enabled"] = True
    digest["classifier_confidence"] = env.get("confidence")
    return digest


def staleness_context(workdir: Path, timeout: float = 5.0) -> dict[str, Any]:
    """Capture the freshness probes' signals so they reach the packet + brief.

    memory_staleness_check.py ([MEMORY OK] / [STALE …]) and stale_context_check.py
    both produce a usable freshness signal but previously never entered the packet —
    the agent had to run them as separate shell commands (audit 2026-05-31). Fail-soft:
    a probe error or timeout never blocks bootstrap.
    """
    out: dict[str, Any] = {}
    for key, name in (("memory", "memory_staleness_check.py"),
                      ("context", "stale_context_check.py")):
        script = HERE / name
        if not script.exists():
            out[key] = None
            continue
        try:
            proc = subprocess.run(
                [sys.executable, str(script), "--workdir", str(workdir)],
                capture_output=True, text=True, timeout=timeout,
            )
            out[key] = ((proc.stdout or proc.stderr or "").strip()[:300]) or None
        except (subprocess.TimeoutExpired, OSError) as exc:
            out[key] = f"error: {exc}"
    return out


def agent_brief(packet: dict[str, Any]) -> str:
    canonical = packet["sources"]["canonical_memory"]
    repo = packet["sources"]["repo_local"]
    codex = packet["sources"]["codex_memory"]
    rally = packet["sources"]["rally"]
    queues = packet.get("queues", {})
    lines = [
        "## Relevant Memory Context",
        f"- Project: {packet['project']} ({packet['workdir']})",
        f"- Query: {packet['query'] or '(empty)'}",
        f"- Canonical memory: {len(canonical.get('merged') or [])} merged hits; {sum(1 for f in canonical.get('files', []) if f.get('exists'))} files present; reasons={canonical.get('reasons') or []}",
        f"- Repo-local context: {sum(1 for f in repo.get('files', []) if f.get('exists'))} files present; {len(repo.get('coordination_files') or [])} coordination files.",
        f"- Codex memory: {len(codex.get('registry_hits') or [])} registry hits; {len(codex.get('rollout_hits') or [])} rollout summaries.",
        f"- Rally/coordination: {'checked' if rally.get('checked') else 'skipped'}; reasons={rally.get('reasons') or []}",
    ]

    staleness = packet.get("staleness") or {}
    if staleness.get("memory") or staleness.get("context"):
        lines.append(
            f"- Staleness: {staleness.get('memory') or 'memory:?'} | {staleness.get('context') or 'context:ok'}"
        )

    # Queue summary line — only when at least one queue has items.
    queue_parts = []
    for qname in QUEUE_NAMES:
        q = queues.get(qname, {})
        n = q.get("count", 0)
        if n:
            queue_parts.append(f"#{qname}={n}")
    if queue_parts:
        top_titles: list[str] = []
        for qname in QUEUE_NAMES:
            q = queues.get(qname, {})
            for item in q.get("top", [])[:1]:
                top_titles.append(item.get("title", ""))
        top_str = "; ".join(t for t in top_titles[:3] if t)
        lines.append(f"- Queues: {' '.join(queue_parts)}{' — top: ' + top_str if top_str else ''}")

    # Progressive lessons — top 3 names when present.
    lessons = packet.get("lessons_progressive", [])
    if lessons:
        lines.append("")
        lines.append("### Progressive Lessons")
        for lesson in lessons[:3]:
            desc = lesson.get("description") or lesson.get("snippet") or ""
            desc_short = (desc[:80] + "…") if len(desc) > 80 else desc
            lines.append(f"- {lesson['name']}: {desc_short}")

    if codex.get("registry_hits"):
        lines.append("")
        lines.append("### Top Codex Memory Hits")
        for hit in codex["registry_hits"][:3]:
            lines.append(
                f"- {hit['title']} ({hit['path']}:{hit['line_start']}-{hit['line_end']})"
            )

    # Prior-art cross-project digest (P4) — pointer-dense; full digest text
    # lives in packet["prior_art"]["digest_text"] for the orchestrator to
    # inline into intent.md. Here we just show that prior art exists so the
    # brief stays compact.
    prior = packet.get("prior_art") or {}
    stats = prior.get("stats") or {}
    if stats.get("impls") or stats.get("decisions"):
        lines.append("")
        lines.append("### Prior Art (cross-project)")
        caps = ", ".join(prior.get("capabilities") or []) or "(unclassified)"
        projs = ", ".join(stats.get("projects") or []) or "(none)"
        lines.append(
            f"- capability={caps} · impls={stats.get('impls', 0)} · "
            f"decisions={stats.get('decisions', 0)} · projects: {projs}"
        )
        lines.append("- Inline `packet.prior_art.digest_text` into intent.md.")
    return "\n".join(lines)


def build_packet(
    workdir: Path,
    query: str,
    limit: int = DEFAULT_LIMIT,
    codex_memory_root: Path | None = None,
    include_postgres: bool = False,
    include_debugger: bool = False,
    include_rally: bool = False,
    max_excerpt_chars: int = DEFAULT_MAX_EXCERPT_CHARS,
    rollout_limit: int = 3,
) -> dict[str, Any]:
    workdir = workdir.resolve()
    project = resolve_project(workdir)
    terms = token_set(query, workdir, project)
    memory_root = codex_memory_root or expand_path(
        os.environ.get("CODEX_MEMORY_ROOT", str(DEFAULT_CODEX_MEMORY_ROOT))
    )

    lessons, lesson_reasons = lessons_progressive_context(
        query=query, project=project, workdir=workdir, limit=5
    )

    packet: dict[str, Any] = {
        "generated_at": utc_now(),
        "workdir": str(workdir),
        "project": project,
        "query": query,
        "terms": terms,
        "queues": queue_context(workdir),
        "lessons_progressive": lessons,
        "prior_art": prior_art_context(
            workdir=workdir,
            query=query,
            project=project,
        ),
        "session_prefs": read_session_prefs(workdir),
        "staleness": staleness_context(workdir),
        "sources": {
            "canonical_memory": canonical_memory_context(
                workdir=workdir,
                query=query,
                project=project,
                terms=terms,
                limit=limit,
                include_postgres=include_postgres,
                include_debugger=include_debugger,
                max_chars=max_excerpt_chars,
            ),
            "repo_local": repo_local_context(
                workdir=workdir,
                terms=terms,
                max_chars=max_excerpt_chars,
            ),
            "codex_memory": codex_memory_context(
                memory_root=memory_root,
                workdir=workdir,
                project=project,
                terms=terms,
                limit=limit,
                rollout_limit=rollout_limit,
                max_chars=max_excerpt_chars,
            ),
            "rally": rally_context(workdir=workdir, include_rally=include_rally),
        },
    }
    # Merge lesson reasons into canonical_memory reasons for surfacing.
    if lesson_reasons:
        packet["sources"]["canonical_memory"].setdefault("reasons", []).extend(lesson_reasons)
    packet["agent_brief"] = agent_brief(packet)

    # f1 — deterministic prior-art delivery: write the digest into intent.md by
    # CODE so Phase 1 always has it, not just via the advisory brief pointer.
    digest_text = (packet.get("prior_art") or {}).get("digest_text") or ""
    write_prior_art_to_intent(workdir, digest_text)

    return packet


_PRIOR_ART_START = "<!-- prior-art:start -->"
_PRIOR_ART_END = "<!-- prior-art:end -->"


def write_prior_art_to_intent(workdir: Path, digest_text: str) -> bool:
    """Idempotently write (or replace) a delimited prior-art block in intent.md.

    Guards:
    * No-op when ``digest_text`` is empty.
    * No-op when ``<workdir>/.build-loop/`` does not exist (plugin repo guard).
    * Creates intent.md when absent.
    * Replaces the existing block on re-run (never duplicates).

    Returns True when the file was written/updated, False when skipped.
    Never raises — failure is logged to stderr and returns False.
    """
    if not digest_text:
        return False
    build_loop_dir = workdir / ".build-loop"
    if not build_loop_dir.is_dir():
        return False

    intent_path = build_loop_dir / "intent.md"
    block = f"{_PRIOR_ART_START}\n{digest_text.rstrip()}\n{_PRIOR_ART_END}\n"

    try:
        existing = intent_path.read_text(encoding="utf-8") if intent_path.exists() else ""
    except OSError as exc:
        import sys as _sys
        print(f"write_prior_art_to_intent: read failed: {exc}", file=_sys.stderr)
        return False

    if _PRIOR_ART_START in existing:
        # Replace the existing block (idempotent re-run).
        start_idx = existing.index(_PRIOR_ART_START)
        end_marker_idx = existing.find(_PRIOR_ART_END, start_idx)
        if end_marker_idx >= 0:
            after = existing[end_marker_idx + len(_PRIOR_ART_END):]
            # Trim one leading newline from what follows the end marker.
            if after.startswith("\n"):
                after = after[1:]
            new_content = existing[:start_idx] + block + after
        else:
            # Malformed (start without end) — replace from start to end of file.
            new_content = existing[:start_idx] + block
    else:
        # Append with a blank-line separator.
        separator = "\n" if existing and not existing.endswith("\n\n") else ""
        new_content = existing + separator + block

    try:
        tmp = intent_path.with_name(f".{intent_path.name}.prior-art.tmp")
        tmp.write_text(new_content, encoding="utf-8")
        os.replace(tmp, intent_path)
    except OSError as exc:
        import sys as _sys
        print(f"write_prior_art_to_intent: write failed: {exc}", file=_sys.stderr)
        return False
    return True


def write_packet(packet: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(f".{output.name}.tmp")
    tmp.write_text(json.dumps(packet, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default=os.getcwd())
    parser.add_argument("--query", default="")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--codex-memory-root", default=os.environ.get("CODEX_MEMORY_ROOT", str(DEFAULT_CODEX_MEMORY_ROOT)))
    parser.add_argument("--output", default="")
    parser.add_argument("--json", action="store_true", help="Print the full JSON packet.")
    parser.add_argument("--brief", action="store_true", help="Print only the agent brief.")
    parser.add_argument("--include-postgres", action="store_true", help="Include the semantic Postgres backend in canonical memory recall.")
    parser.add_argument("--include-debugger", action="store_true", help="Include native debugging incidents in canonical memory recall.")
    parser.add_argument("--include-rally", action="store_true", help="Force the Rally/coordination status check even when no coordination context is detected.")
    parser.add_argument("--max-excerpt-chars", type=int, default=DEFAULT_MAX_EXCERPT_CHARS)
    parser.add_argument("--rollout-limit", type=int, default=3)
    args = parser.parse_args(argv)

    packet = build_packet(
        workdir=expand_path(args.workdir),
        query=args.query,
        limit=args.limit,
        codex_memory_root=expand_path(args.codex_memory_root),
        include_postgres=args.include_postgres,
        include_debugger=args.include_debugger,
        include_rally=args.include_rally,
        max_excerpt_chars=args.max_excerpt_chars,
        rollout_limit=args.rollout_limit,
    )

    if args.output:
        write_packet(packet, expand_path(args.output))

    if args.brief and not args.json:
        print(packet["agent_brief"])
    else:
        print(json.dumps(packet, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
