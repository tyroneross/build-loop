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
        for path in sorted(coord_dir.glob("*.md"))[:8]:
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
        memory_root=memory_store_root(),
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
    return {"checked": True, "status": status, "reasons": reasons}


def agent_brief(packet: dict[str, Any]) -> str:
    canonical = packet["sources"]["canonical_memory"]
    repo = packet["sources"]["repo_local"]
    codex = packet["sources"]["codex_memory"]
    rally = packet["sources"]["rally"]
    lines = [
        "## Relevant Memory Context",
        f"- Project: {packet['project']} ({packet['workdir']})",
        f"- Query: {packet['query'] or '(empty)'}",
        f"- Canonical memory: {len(canonical.get('merged') or [])} merged hits; {sum(1 for f in canonical.get('files', []) if f.get('exists'))} files present; reasons={canonical.get('reasons') or []}",
        f"- Repo-local context: {sum(1 for f in repo.get('files', []) if f.get('exists'))} files present; {len(repo.get('coordination_files') or [])} coordination files.",
        f"- Codex memory: {len(codex.get('registry_hits') or [])} registry hits; {len(codex.get('rollout_hits') or [])} rollout summaries.",
        f"- Rally/coordination: {'checked' if rally.get('checked') else 'skipped'}; reasons={rally.get('reasons') or []}",
    ]
    if codex.get("registry_hits"):
        lines.append("")
        lines.append("### Top Codex Memory Hits")
        for hit in codex["registry_hits"][:3]:
            lines.append(
                f"- {hit['title']} ({hit['path']}:{hit['line_start']}-{hit['line_end']})"
            )
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

    packet: dict[str, Any] = {
        "generated_at": utc_now(),
        "workdir": str(workdir),
        "project": project,
        "query": query,
        "terms": terms,
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
    packet["agent_brief"] = agent_brief(packet)
    return packet


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
    parser.add_argument("--include-debugger", action="store_true", help="Include the debugger MCP backend in canonical memory recall.")
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
