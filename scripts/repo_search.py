#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Fast, deterministic repository search with a rebuildable local evidence index.

``query`` searches live content with ripgrep, then reads only selected files.
The JSON index contains pointers to existing Git, run, decision, and architecture
records. It is a cache under .build-loop/, never a second memory source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from _paths import memory_store_root
from memory_locator import query_terms
from project_resolver import resolve_project


INDEX_REL = Path(".build-loop/search/index.json")
INDEX_IGNORE = "/.build-loop/search/"
SCHEMA_VERSION = 1
MAX_READ_BYTES = 1 << 20
MAX_FALLBACK_FILES = 2000
MAX_QUERY_TERMS = 4
SKIP_DIRS = frozenset({
    ".git", ".build-loop", ".rally", ".navgator", "node_modules", ".next",
    ".venv", "venv", "dist", "build", "coverage", "__pycache__", ".ci-rally-apps",
})
SKIP_FILE_RE = re.compile(r"(?:^\.env(?:\.|$)|\.pem$|\.key$|\.p12$)", re.I)
LOCAL_DOCS = (
    ".build-loop/goal.md", ".build-loop/intent.md", ".build-loop/plan.md",
    ".build-loop/feedback.md", ".build-loop/architecture/handoff.md",
    ".build-loop/plans/DECISION-LOG.md",
)
LOCAL_DECISION_LOG = Path(".build-loop/plans/DECISION-LOG.md")


def _run(argv: list[str], repo: Path, *, timeout: float = 5) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(argv, cwd=repo, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _safe_relative(repo: Path, raw: str) -> str | None:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts or any(part in SKIP_DIRS for part in path.parts):
        return None
    if any(parent in {".claude", ".codex"} and child == "worktrees"
           for parent, child in zip(path.parts, path.parts[1:])):
        return None
    if SKIP_FILE_RE.search(path.name):
        return None
    resolved = (repo / path).resolve()
    if not resolved.is_relative_to(repo) or not resolved.is_file():
        return None
    return str(path).removeprefix("./").replace(os.sep, "/")


def _file_paths(repo: Path) -> tuple[list[str], str]:
    proc = _run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], repo)
    if proc is not None and proc.returncode == 0:
        paths = [_safe_relative(repo, os.fsdecode(raw)) for raw in proc.stdout.split(b"\0") if raw]
        return sorted({path for path in paths if path}), "git"
    paths: list[str] = []
    for directory, dirs, files in os.walk(repo):
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS
                         and not (name == "worktrees" and Path(directory).name in {".claude", ".codex"}))
        for name in sorted(files):
            relative = str((Path(directory) / name).relative_to(repo))
            if safe := _safe_relative(repo, relative):
                paths.append(safe)
    return paths, "python"


def _ignore_local_index(repo: Path) -> None:
    """Keep the derived cache out of Git without editing a project's .gitignore."""
    if not (repo / ".git").exists():
        return
    ignored = _run(["git", "check-ignore", "-q", str(INDEX_REL)], repo, timeout=2)
    if ignored is not None and ignored.returncode == 0:
        return
    location = _run(["git", "rev-parse", "--git-path", "info/exclude"], repo, timeout=2)
    if location is None or location.returncode != 0:
        raise RuntimeError("cannot locate Git's local exclude file for search cache")
    raw = location.stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        raise RuntimeError("Git returned no local exclude file for search cache")
    exclude = Path(raw)
    if not exclude.is_absolute():
        exclude = repo / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if INDEX_IGNORE not in existing.splitlines():
        with exclude.open("a", encoding="utf-8") as target:
            target.write(("\n" if existing and not existing.endswith("\n") else "") + INDEX_IGNORE + "\n")


def _head(repo: Path) -> str | None:
    proc = _run(["git", "rev-parse", "HEAD"], repo, timeout=2)
    return proc.stdout.decode().strip() if proc is not None and proc.returncode == 0 else None


def _decision_files(repo: Path) -> tuple[list[Path], Path, str]:
    root = memory_store_root(repo)
    project = resolve_project(repo)
    files: list[Path] = []
    folder = root / "projects" / project / "decisions"
    if folder.is_dir():
        files.extend(path for path in folder.glob("*.md")
                     if path.is_file() and not path.is_symlink() and path.name != "INDEX.md")
    return sorted(files), root, project


def _stamp(path: Path) -> str:
    try:
        stat = path.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return "missing"


def _source_state(repo: Path) -> tuple[dict[str, Any], list[str], list[Path], Path, str]:
    paths, path_engine = _file_paths(repo)
    decisions, memory_root, project = _decision_files(repo)
    local_sources = [repo / relative for relative in (
        ".build-loop/state.json", ".build-loop/architecture/index.json",
        ".build-loop/architecture/annotations.json", *LOCAL_DOCS,
    )]
    fingerprints = {str(path.relative_to(repo)): _stamp(path) for path in local_sources}
    fingerprints.update({f"memory:{path}": _stamp(path) for path in decisions})
    fingerprints["paths"] = hashlib.sha256("\0".join(paths).encode()).hexdigest()
    return {
        "head": _head(repo), "project": project, "path_engine": path_engine,
        "fingerprints": fingerprints,
    }, paths, decisions, memory_root, project


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _heading(path: Path) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as source:
            for number, line in enumerate(source):
                if number >= 80:
                    break
                if line.startswith("title:"):
                    return line.partition(":")[2].strip().strip('"\'') or path.stem
                if line.startswith("# "):
                    return line[2:].strip() or path.stem
    except OSError:
        pass
    return path.stem


def _entry(kind: str, path: str, title: str, *, summary: str = "", ref: str = "", source: str = "") -> dict[str, str]:
    return {"kind": kind, "path": path, "title": title[:240],
            "summary": summary[:320], "ref": ref, "source": source}


def _collect_entries(repo: Path, paths: list[str], decisions: list[Path], memory_root: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    entries = [_entry("file", path, Path(path).name, source="git-paths") for path in paths]
    coverage: dict[str, Any] = {"files": len(paths), "history_limit": 500}

    history = _run(["git", "log", "-n", "500", "--format=%H%x1f%ct%x1f%s"], repo, timeout=8)
    if history is not None and history.returncode == 0:
        for line in history.stdout.decode("utf-8", errors="replace").splitlines():
            parts = line.split("\x1f", 2)
            if len(parts) == 3:
                sha, date, subject = parts
                entries.append(_entry("change", f"git:{sha}", subject, summary=date, ref=sha, source="git-log"))
        coverage["changes"] = sum(item["kind"] == "change" for item in entries)
    else:
        coverage["changes"] = 0

    state = _json_object(repo / ".build-loop/state.json")
    runs = state.get("runs", [])
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("run_id") or "")
            if run_id:
                touched = run.get("filesTouched") or []
                touched_text = " ".join(str(path) for path in touched[:12]) if isinstance(touched, list) else ""
                entries.append(_entry("run", f".build-loop/state.json#runs/{run_id}",
                                      str(run.get("goal") or run_id),
                                      summary=f"{run.get('outcome') or ''} {touched_text}".strip(), ref=run_id,
                                      source="run-history"))
    coverage["runs"] = sum(item["kind"] == "run" for item in entries)

    for path in decisions:
        entries.append(_entry("decision", str(path), _heading(path),
                              source="canonical-memory"))
    coverage["decisions"] = len(decisions)
    coverage["memory_root"] = str(memory_root)

    architecture = _json_object(repo / ".build-loop/architecture/index.json")
    components = architecture.get("components", [])
    if isinstance(components, list):
        for component in components:
            if not isinstance(component, dict):
                continue
            metadata = component.get("metadata") or {}
            role = component.get("role") or {}
            relative = metadata.get("file") if isinstance(metadata, dict) else None
            if not isinstance(relative, str) or not _safe_relative(repo, relative):
                continue
            purpose = role.get("purpose") if isinstance(role, dict) else ""
            entries.append(_entry("structure", relative, str(component.get("name") or relative),
                                  summary=str(purpose or ""),
                                  ref=str(component.get("component_id") or ""),
                                  source="architecture-snapshot"))
    coverage["structure"] = sum(item["kind"] == "structure" for item in entries)

    annotations = _json_object(repo / ".build-loop/architecture/annotations.json")
    for annotation in annotations.get("annotations", []) if isinstance(annotations.get("annotations"), list) else []:
        if not isinstance(annotation, dict):
            continue
        relative = annotation.get("file")
        if isinstance(relative, str) and _safe_relative(repo, relative):
            entries.append(_entry("annotation", relative, str(annotation.get("summary") or ""),
                                  summary=" ".join(str(x) for x in annotation.get("keywords", []) if isinstance(x, str)),
                                  ref=str(annotation.get("line") or ""), source="architecture-annotations"))
    coverage["annotations"] = sum(item["kind"] == "annotation" for item in entries)

    for relative in LOCAL_DOCS:
        path = repo / relative
        if path.is_file():
            title = "Private running decision log" if relative == str(LOCAL_DECISION_LOG) else _heading(path)
            entries.append(_entry("local_doc", relative, title, source="repo-local"))
    coverage["local_docs"] = sum(item["kind"] == "local_doc" for item in entries)
    return entries, coverage


def build_index(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    state, paths, decisions, memory_root, project = _source_state(repo)
    entries, coverage = _collect_entries(repo, paths, decisions, memory_root)
    payload = {
        "schema_version": SCHEMA_VERSION, "repo_root": str(repo),
        "generated_at": int(time.time()), "project": project,
        "source_state": state, "coverage": coverage, "entries": entries,
    }
    target = repo / INDEX_REL
    _ignore_local_index(repo)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                     prefix="index-", suffix=".tmp", delete=False) as temporary:
        json.dump(payload, temporary, separators=(",", ":"))
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_name = temporary.name
    os.replace(temporary_name, target)
    return payload


def _load_index(repo: Path) -> dict[str, Any]:
    value = _json_object(repo / INDEX_REL)
    if value.get("schema_version") != SCHEMA_VERSION or value.get("repo_root") != str(repo):
        return {}
    return value


def _ensure_index(repo: Path) -> tuple[dict[str, Any], bool]:
    current, *_ = _source_state(repo)
    index = _load_index(repo)
    if index.get("source_state") == current:
        return index, False
    return build_index(repo), True


def _metadata_hits(entries: list[dict[str, str]], terms: list[str], kind: str) -> list[dict[str, Any]]:
    allowed = {
        "all": None, "content": set(), "change": {"change", "run"},
        "decision": {"decision"}, "structure": {"structure", "annotation", "file"},
        "run": {"run"},
    }[kind]
    hits: list[dict[str, Any]] = []
    for entry in entries:
        if allowed is not None and entry["kind"] not in allowed:
            continue
        fields = (entry["title"], entry["path"], entry["summary"], entry["ref"])
        matched = sum(any(term in field.casefold() for field in fields) for term in terms)
        if not matched:
            continue
        base = 3 if entry["kind"] == "file" else 10
        score = base + matched * 5 + (8 if matched == len(terms) else 0)
        hits.append({**entry, "score": score, "matched_terms": matched,
                     "freshness": "snapshot" if entry["source"].startswith("architecture") else "indexed"})
    return hits


def _rg_candidates(repo: Path, terms: list[str]) -> tuple[list[str], dict[str, Any]]:
    if not shutil.which("rg"):
        return _python_candidates(repo, terms, "rg_unavailable")
    selected: set[str] | None = None
    for term in sorted(terms, key=lambda item: (-len(item), item)):
        command = ["rg", "-l", "-0", "--hidden", "--fixed-strings", "--ignore-case", "--no-messages"]
        for directory in sorted(SKIP_DIRS):
            command.extend(("--glob", f"!**/{directory}/**"))
        for tree in (".claude", ".codex"):
            command.extend(("--glob", f"!**/{tree}/worktrees/**"))
        for glob in ("!**/.env*", "!**/*.pem", "!**/*.key", "!**/*.p12"):
            command.extend(("--glob", glob))
        command.extend(("-e", term, "."))
        proc = _run(command, repo, timeout=5)
        if proc is None or proc.returncode not in (0, 1):
            return _python_candidates(repo, terms, "rg_failed_or_timed_out")
        matches = {
            safe for raw in proc.stdout.split(b"\0") if raw
            if (safe := _safe_relative(repo, os.fsdecode(raw)))
        }
        selected = matches if selected is None else selected & matches
        if not selected:
            break
    return sorted(selected or []), {"engine": "ripgrep", "complete": True, "scanned_files": None, "reasons": []}


def _python_candidates(repo: Path, terms: list[str], reason: str) -> tuple[list[str], dict[str, Any]]:
    paths: list[str] = []
    scanned = 0
    complete = True
    oversized = 0
    limit_reached = False
    eligible, path_engine = _file_paths(repo)
    if (repo / ".git").exists() and path_engine != "git":
        return [], {"engine": "python", "complete": False, "scanned_files": 0,
                    "oversized_files": 0, "reasons": [reason, "git_ignore_inventory_unavailable"]}
    for safe in eligible:
        if scanned >= MAX_FALLBACK_FILES:
            complete = False
            limit_reached = True
            break
        scanned += 1
        path = repo / safe
        try:
            if path.stat().st_size > MAX_READ_BYTES:
                oversized += 1
                complete = False
                continue
            body = path.read_text(encoding="utf-8", errors="replace").casefold()
        except OSError:
            continue
        if all(term in body for term in terms):
            paths.append(safe)
    reasons = [reason]
    if oversized:
        reasons.append("oversized_files_skipped")
    if limit_reached:
        reasons.append("fallback_file_limit_reached")
    return paths, {"engine": "python", "complete": complete, "scanned_files": scanned,
                   "oversized_files": oversized, "reasons": reasons}


def _content_hits(repo: Path, terms: list[str], max_files: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates, receipt = _rg_candidates(repo, terms)
    candidates.sort(key=lambda path: (-sum(term in path.casefold() for term in terms), path))
    hits: list[dict[str, Any]] = []
    oversized = 0
    for relative in candidates[:max_files]:
        path = repo / relative
        try:
            if path.stat().st_size > MAX_READ_BYTES:
                oversized += 1
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        matched_lines = []
        for number, line in enumerate(lines, 1):
            count = sum(term in line.casefold() for term in terms)
            if count:
                matched_lines.append((count, number, line.strip()))
        for count, number, line in sorted(matched_lines, key=lambda item: (-item[0], item[1]))[:2]:
            hits.append({"kind": "content", "path": relative, "line": number,
                         "title": Path(relative).name, "snippet": line[:240],
                         "score": 12 + 6 * count, "matched_terms": count,
                         "source": "live-source", "freshness": "live"})
    receipt.update({"candidate_files": len(candidates), "inspected_files": min(len(candidates), max_files),
                    "truncated": len(candidates) > max_files,
                    "oversized_files": oversized + int(receipt.get("oversized_files") or 0)})
    if oversized:
        receipt["complete"] = False
        receipt["reasons"].append("oversized_candidates_skipped")
    if receipt["truncated"]:
        receipt["complete"] = False
        receipt["reasons"].append("candidate_limit_reached")
    return hits, receipt


def _decision_body_hits(repo: Path, terms: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Search canonical decisions and the explicit private local decision log."""
    files, _, _ = _decision_files(repo)
    selected = set(files)
    engine = "python"
    reasons: list[str] = []
    complete = True
    if selected and shutil.which("rg"):
        engine = "ripgrep"
        folder = files[0].parent
        command = ["rg", "-l", "-0", "--fixed-strings", "--ignore-case", "--glob", "*.md"]
        for term in terms:
            command.extend(("-e", term))
        proc = _run([*command, str(folder)], repo, timeout=3)
        if proc is None or proc.returncode not in (0, 1):
            engine = "python"
            reasons.append("decision_rg_failed_or_timed_out")
            selected = set(files)
        else:
            selected &= {Path(os.fsdecode(raw)) for raw in proc.stdout.split(b"\0") if raw}
    if engine == "python":
        selected = set()
        for path in files[:MAX_FALLBACK_FILES]:
            try:
                if path.stat().st_size > MAX_READ_BYTES:
                    complete = False
                    reasons.append("oversized_decision_skipped")
                    continue
                body = path.read_text(encoding="utf-8", errors="replace").casefold()
                if any(term in body for term in terms):
                    selected.add(path)
            except OSError:
                complete = False
                reasons.append("decision_unreadable")
                continue
        if len(files) > MAX_FALLBACK_FILES:
            complete = False
            reasons.append("decision_fallback_file_limit_reached")
    hits: list[dict[str, Any]] = []
    for path in sorted(selected):
        try:
            if path.stat().st_size > MAX_READ_BYTES:
                complete = False
                reasons.append("oversized_decision_skipped")
                continue
            body = path.read_text(encoding="utf-8", errors="replace").casefold()
        except OSError:
            complete = False
            reasons.append("decision_unreadable")
            continue
        matched = sum(term in body for term in terms)
        if not matched:
            continue
        title = _heading(path)
        title_matches = sum(term in title.casefold() for term in terms)
        hits.append({"kind": "decision", "path": str(path), "title": title,
                     "summary": "", "score": 24 + 6 * matched + 3 * title_matches
                     + (8 if matched == len(terms) else 0),
                     "matched_terms": matched,
                     "source": "canonical-memory-body", "freshness": "live"})
    local_log = repo / LOCAL_DECISION_LOG
    local_sections = 0
    if local_log.is_file():
        try:
            if local_log.stat().st_size > MAX_READ_BYTES:
                complete = False
                reasons.append("oversized_local_decision_log_skipped")
            else:
                lines = local_log.read_text(encoding="utf-8", errors="replace").splitlines()
                headings = [index for index, line in enumerate(lines) if line.startswith("## ")]
                local_sections = len(headings)
                for position, start in enumerate(headings):
                    end = headings[position + 1] if position + 1 < len(headings) else len(lines)
                    body = "\n".join(lines[start:end]).casefold()
                    matched = sum(term in body for term in terms)
                    if not matched:
                        continue
                    title = lines[start][3:].strip()
                    title_matches = sum(term in title.casefold() for term in terms)
                    hits.append({
                        "kind": "decision", "path": str(local_log), "line": start + 1,
                        "title": title, "summary": "", "matched_terms": matched,
                        "score": 24 + 6 * matched + 3 * title_matches
                                 + (8 if matched == len(terms) else 0),
                        "source": "repo-local-decision-log", "freshness": "live",
                    })
        except OSError:
            complete = False
            reasons.append("local_decision_log_unreadable")
    return hits, {"engine": engine, "searched_files": min(len(files), MAX_FALLBACK_FILES) if engine == "python" else len(files),
                  "matched_files": len(selected), "local_log_present": local_log.is_file(),
                  "local_sections": local_sections, "complete": complete,
                  "reasons": list(dict.fromkeys(reasons))}


def _select_hits(ordered: list[dict[str, Any]], kind: str, limit: int) -> list[dict[str, Any]]:
    if kind != "all":
        return ordered[:limit]
    groups = (
        {"content"}, {"decision"}, {"structure", "annotation"},
        {"change"}, {"run"}, {"local_doc"}, {"file"},
    )
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    for group in groups:
        for position, hit in enumerate(ordered):
            if position not in seen and hit["kind"] in group:
                selected.append(hit)
                seen.add(position)
                break
        if len(selected) >= limit:
            return selected[:limit]
    selected.extend(hit for position, hit in enumerate(ordered) if position not in seen)
    return selected[:limit]


def search(repo: Path, query: str, *, kind: str = "all", limit: int = 10, max_files: int = 40) -> dict[str, Any]:
    repo = repo.resolve()
    started = time.perf_counter()
    terms = sorted(query_terms(query), key=lambda term: (-len(term), term))[:MAX_QUERY_TERMS]
    if not terms:
        return {"query": query, "terms_used": [], "kind": kind, "hits": [],
                "total_ranked": 0, "match_counts": {},
                "index": {"path": str(repo / INDEX_REL), "used": False, "rebuilt": False},
                "content": {"engine": "skipped", "complete": True, "reasons": ["query_has_no_search_terms"]},
                "decisions": {"engine": "skipped", "complete": False,
                              "reasons": ["query_has_no_search_terms"]},
                "complete": False, "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
    index: dict[str, Any] = {}
    rebuilt = False
    if kind != "content":
        index, rebuilt = _ensure_index(repo)
    hits = _metadata_hits(index.get("entries", []), terms, kind)
    content_receipt: dict[str, Any] = {"engine": "skipped", "complete": True}
    if kind in {"all", "content"}:
        live_hits, content_receipt = _content_hits(repo, terms, max_files)
        hits.extend(live_hits)

    decision_receipt: dict[str, Any] = {"engine": "skipped", "reasons": []}
    if kind in {"all", "decision"}:
        decision_hits, decision_receipt = _decision_body_hits(repo, terms)
        hits.extend(decision_hits)

    unique: dict[tuple[str, str, int], dict[str, Any]] = {}
    for hit in hits:
        key = (str(hit["kind"]), str(hit["path"]), int(hit.get("line") or 0))
        if key not in unique or hit["score"] > unique[key]["score"]:
            unique[key] = hit
    ordered = sorted(unique.values(), key=lambda item: (-item["score"], item["path"], item.get("line", 0)))
    if kind == "decision" and len(terms) > 1 and any(hit["matched_terms"] >= 2 for hit in ordered):
        ordered = [hit for hit in ordered if hit["matched_terms"] >= 2]
    match_counts: dict[str, int] = {}
    for hit in ordered:
        match_counts[hit["kind"]] = match_counts.get(hit["kind"], 0) + 1
    return {
        "query": query, "terms_used": terms, "kind": kind,
        "hits": _select_hits(ordered, kind, limit), "total_ranked": len(ordered),
        "match_counts": match_counts,
        "index": {"path": str(repo / INDEX_REL), "used": kind != "content",
                  "rebuilt": rebuilt, "generated_at": index.get("generated_at"),
                  "coverage": index.get("coverage", {})},
        "content": content_receipt, "decisions": decision_receipt,
        "complete": bool(content_receipt["complete"] and decision_receipt.get("complete", True)),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("index", "query", "status"):
        command = sub.add_parser(name)
        command.add_argument("--workdir", type=Path, default=Path.cwd())
        command.add_argument("--json", action="store_true")
        if name == "query":
            command.add_argument("--query", required=True)
            command.add_argument("--kind", choices=("all", "content", "change", "decision", "structure", "run"), default="all")
            command.add_argument("--limit", type=int, default=10)
            command.add_argument("--max-files", type=int, default=40)
    args = parser.parse_args(argv)
    repo = args.workdir.resolve()
    if not repo.is_dir():
        parser.error(f"not a directory: {repo}")
    if args.command == "index":
        index = build_index(repo)
        output = {"index": str(repo / INDEX_REL), "project": index["project"],
                  "entries": len(index["entries"]), "coverage": index["coverage"]}
    elif args.command == "status":
        index = _load_index(repo)
        current, *_ = _source_state(repo)
        output = {"index": str(repo / INDEX_REL), "exists": bool(index),
                  "fresh": bool(index and index.get("source_state") == current),
                  "coverage": index.get("coverage", {})}
    else:
        if args.limit < 1 or args.max_files < 1:
            parser.error("--limit and --max-files must be positive")
        output = search(repo, args.query, kind=args.kind, limit=args.limit, max_files=args.max_files)
    if args.json:
        print(json.dumps(output, indent=2))
    elif args.command == "query":
        print(f"{len(output['hits'])} hits in {output['elapsed_ms']} ms"
              + (" (incomplete content coverage)" if not output["complete"] else ""))
        for hit in output["hits"]:
            location = hit["path"] + (f":{hit['line']}" if hit.get("line") else "")
            print(f"[{hit['kind']}] {location} — {hit.get('snippet') or hit['title']}")
    else:
        print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
