#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Find a small set of canonical memory files without an LLM.

The generated ``indexes/INDEX.jsonl`` is the fast path. When that index is
missing, stale, or produces no confident match, one ripgrep pass finds
candidate Markdown files and this module ranks only those candidates.

The locator never mutates the memory index. Its output is a retrieval receipt:
the caller gets paths to read, the engine used, latency, and a telemetry
correlation id.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _paths import memory_store_root  # type: ignore  # noqa: E402
from project_resolver import resolve_project  # type: ignore  # noqa: E402


DEFAULT_LIMIT = 5
MAX_FALLBACK_CANDIDATES = 500
INDEX_RELATIVE_PATH = Path("indexes/INDEX.jsonl")
UPDATE_LEDGER_RELATIVE_PATH = Path("indexes/updates.jsonl")
GLOBAL_PROJECTS = {"", "_global", "_unscoped", "global"}
CANONICAL_LANES = (
    "decisions",
    "design",
    "debugging",
    "docs",
    "experiments",
    "followups",
    "lessons",
    "model-profiles",
    "plugins",
    "product",
    "references",
    "research",
)
SKIP_NAMES = {"INDEX.md", "MEMORY.md", "README.md", "TELEMETRY.jsonl"}
SKIP_DIRS = {"archive", "indexes", "raw", "raw-originals"}
STOPWORDS = {
    "about", "after", "again", "also", "and", "been", "being", "build", "could",
    "can", "does", "fix", "for", "from", "get", "have", "how", "into", "make",
    "memory", "need", "new", "other", "run", "should", "that", "the", "their",
    "them", "then", "there", "these", "this", "use", "using", "want", "what",
    "when", "where", "which", "why", "with", "would",
}
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*", re.IGNORECASE)


def query_terms(query: str) -> list[str]:
    """Return stable, de-duplicated keyword terms for deterministic matching."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in TOKEN_RE.findall(query.lower()):
        for term in re.split(r"[._/-]+", raw):
            if len(term) < 2 or term in STOPWORDS or term in seen:
                continue
            seen.add(term)
            out.append(term)
    return out


def _read_index(index_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    reasons: list[str] = []
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return [], [f"index_missing: {index_path}"]
    except OSError as exc:
        return [], [f"index_read_error: {index_path}: {exc}"]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            reasons.append(f"index_malformed_row: {line_number}: {exc}")
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows, reasons


def _index_is_fresh(root: Path, index_path: Path, project: str | None) -> tuple[bool, str | None]:
    if not index_path.is_file():
        return False, f"index_missing: {index_path}"
    ledger = root / UPDATE_LEDGER_RELATIVE_PATH
    try:
        if ledger.is_file() and ledger.stat().st_mtime_ns > index_path.stat().st_mtime_ns:
            return False, f"index_older_than_update_ledger: {ledger}"
        index_mtime = index_path.stat().st_mtime_ns
        for lane_root in _search_roots(root, project):
            if lane_root.stat().st_mtime_ns > index_mtime:
                return False, f"index_older_than_lane_directory: {lane_root}"
    except OSError as exc:
        return False, f"index_freshness_error: {exc}"
    return True, None


def _safe_index_path(root: Path, relative: str) -> Path | None:
    candidate = Path(relative)
    if not relative or candidate.is_absolute():
        return None
    resolved = (root / candidate).resolve()
    return resolved if resolved.is_relative_to(root) else None


def _project_allowed(row_project: str, project: str | None) -> bool:
    if not project or project in GLOBAL_PROJECTS:
        return row_project in GLOBAL_PROJECTS
    return row_project in GLOBAL_PROJECTS or row_project == project


def _score_fields(row: dict[str, Any], terms: list[str], body: str = "") -> tuple[int, float, list[str]]:
    fields = (
        ("path", str(row.get("canonical_path") or row.get("path") or "").lower(), 9),
        ("title", str(row.get("title") or "").lower(), 7),
        ("tags", " ".join(str(v) for v in (row.get("tags") or [])).lower(), 6),
        ("id", str(row.get("id") or "").lower(), 4),
        ("project", str(row.get("project") or "").lower(), 3),
        ("type", str(row.get("type") or "").lower(), 2),
    )
    score = 0
    matched_terms: set[str] = set()
    matched: list[str] = []
    body_lower = body.lower() if body else ""

    def contains(field_text: str, term: str) -> bool:
        if len(term) >= 3:
            return term in field_text
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", field_text) is not None

    for term in terms:
        for field_name, field_text, weight in fields:
            if contains(field_text, term):
                score += weight
                matched_terms.add(term)
                matched.append(f"{field_name}:{term}")
                break
        else:
            count = len(re.findall(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", body_lower)) if body_lower else 0
            if count:
                score += min(count, 3)
                matched_terms.add(term)
                matched.append(f"body:{term}")
    coverage = len(matched_terms) / len(terms) if terms else 0.0
    score += round(coverage * 10)
    return score, coverage, matched


def _result(row: dict[str, Any], root: Path, score: int, coverage: float, matched: list[str]) -> dict[str, Any]:
    rel = str(row.get("canonical_path") or row.get("path") or "")
    absolute = _safe_index_path(root, rel)
    return {
        "path": rel,
        "absolute_path": str(absolute) if absolute is not None else "",
        "id": str(row.get("id") or rel),
        "title": str(row.get("title") or Path(rel).stem),
        "project": str(row.get("project") or "_global"),
        "type": str(row.get("type") or "memory"),
        "score": score,
        "coverage": round(coverage, 3),
        "matched": matched,
    }


def _rank_index(
    rows: Iterable[dict[str, Any]],
    *,
    root: Path,
    terms: list[str],
    project: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for row in rows:
        status = str(row.get("status") or "active").lower()
        row_project = str(row.get("project") or "_global")
        if status in {"archived", "deleted", "inactive", "superseded"}:
            continue
        if not _project_allowed(row_project, project):
            continue
        if _safe_index_path(root, str(row.get("canonical_path") or row.get("path") or "")) is None:
            continue
        score, coverage, matched = _score_fields(row, terms)
        matched_count = round(coverage * len(terms))
        required_matches = min(3, max(1, (len(terms) + 2) // 4))
        if matched_count < required_matches or score < 8:
            continue
        ranked.append(_result(row, root, score, coverage, matched))
    ranked.sort(key=lambda item: (-item["score"], -item["coverage"], item["path"]))
    return ranked[:limit]


def _result_files_match_index(results: list[dict[str, Any]], rows_by_path: dict[str, dict[str, Any]], root: Path) -> bool:
    """Validate only the files about to be returned; keep the fast path cheap."""
    for result in results:
        row = rows_by_path.get(result["path"], {})
        safe_path = _safe_index_path(root, result["path"])
        if safe_path is None or not safe_path.is_file():
            return False
        expected = str(row.get("checksum") or "")
        if not expected:
            return False
        try:
            actual = hashlib.sha256(safe_path.read_bytes()).hexdigest()
        except OSError:
            return False
        if actual != expected:
            return False
    return True


def _search_roots(root: Path, project: str | None) -> list[Path]:
    roots = [root / lane for lane in CANONICAL_LANES]
    if project and project not in GLOBAL_PROJECTS:
        projects_root = (root / "projects").resolve()
        project_root = (projects_root / project).resolve()
        if project_root != projects_root and project_root.is_relative_to(projects_root):
            roots.extend(project_root / lane for lane in CANONICAL_LANES)
    return [path for path in roots if path.is_dir()]


def _allowed_markdown(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return (
        path.suffix.lower() == ".md"
        and path.name not in SKIP_NAMES
        and not any(part in SKIP_DIRS for part in relative.parts[:-1])
    )


def _rg_candidates(root: Path, project: str | None, terms: list[str]) -> tuple[list[Path], str, list[str]]:
    search_roots = _search_roots(root, project)
    if not search_roots:
        return [], "python-scan", ["canonical_search_roots_missing"]
    rg = shutil.which("rg")
    if rg and terms:
        pattern = "|".join(re.escape(term) for term in terms)
        command = [
            rg, "--files-with-matches", "--ignore-case", "--max-count", "1",
            "--glob", "*.md", "--glob", "!**/archive/**", "--glob", "!**/indexes/**",
            "--glob", "!**/raw/**", "--glob", "!**/raw-originals/**", "-e", pattern,
            *[str(path) for path in search_roots],
        ]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=3.0, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            rg_reason = f"rg_unavailable: {exc}"
        else:
            if proc.returncode in {0, 1}:
                paths = [Path(line) for line in proc.stdout.splitlines() if line.strip()]
                allowed = [path for path in paths if _allowed_markdown(path, root)]
                allowed.sort(
                    key=lambda path: (
                        -sum(term in str(path).lower() for term in terms),
                        str(path),
                    )
                )
                return allowed[:MAX_FALLBACK_CANDIDATES], "rg", []
            rg_reason = f"rg_failed: exit={proc.returncode}: {proc.stderr.strip()}"
    else:
        rg_reason = "rg_not_installed" if not rg else "query_has_no_terms"

    candidates: list[Path] = []
    for search_root in search_roots:
        try:
            for path in search_root.rglob("*.md"):
                if _allowed_markdown(path, root):
                    candidates.append(path)
        except OSError:
            continue
    candidates.sort(
        key=lambda path: (
            -sum(term in str(path).lower() for term in terms),
            str(path),
        )
    )
    return candidates[:MAX_FALLBACK_CANDIDATES], "python-scan", [rg_reason]


def _fallback_row(path: Path, root: Path) -> tuple[dict[str, Any], str]:
    rel = path.relative_to(root)
    project = "_global"
    lane_index = 0
    if len(rel.parts) >= 4 and rel.parts[0] == "projects":
        project = rel.parts[1]
        lane_index = 2
    lane = rel.parts[lane_index] if len(rel.parts) > lane_index else "memory"
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    title = path.stem
    for line in text.splitlines()[:80]:
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return {
        "canonical_path": str(rel),
        "id": str(rel.with_suffix("")).replace("/", "-"),
        "title": title,
        "project": project,
        "type": lane.removesuffix("s") or "memory",
        "status": "active",
        "tags": [],
    }, text


def _rank_fallback(
    paths: Iterable[Path],
    *,
    root: Path,
    terms: list[str],
    project: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for path in paths:
        row, body = _fallback_row(path, root)
        if not _project_allowed(str(row["project"]), project):
            continue
        score, coverage, matched = _score_fields(row, terms, body)
        matched_count = round(coverage * len(terms))
        required_matches = min(3, max(1, (len(terms) + 2) // 4))
        if matched_count < required_matches or score < 6:
            continue
        ranked.append(_result(row, root, score, coverage, matched))
    ranked.sort(key=lambda item: (-item["score"], -item["coverage"], item["path"]))
    return ranked[:limit]


def locate(
    query: str,
    *,
    project: str | None = None,
    limit: int = DEFAULT_LIMIT,
    memory_root: Path | None = None,
    telemetry_path: Path | None = None,
    emit_telemetry: bool = True,
) -> dict[str, Any]:
    """Return ranked canonical file paths and a deterministic retrieval receipt."""
    started = time.perf_counter()
    root = (memory_root or memory_store_root()).expanduser().resolve()
    terms = query_terms(query)
    index_path = root / INDEX_RELATIVE_PATH
    rows, reasons = _read_index(index_path)
    index_fresh, freshness_reason = _index_is_fresh(root, index_path, project)
    if freshness_reason:
        reasons.append(freshness_reason)

    results: list[dict[str, Any]] = []
    engine = "index-jsonl"
    if terms and rows and index_fresh:
        results = _rank_index(rows, root=root, terms=terms, project=project, limit=limit)
        rows_by_path = {
            str(row.get("canonical_path") or row.get("path") or ""): row
            for row in rows
        }
        if results and not _result_files_match_index(results, rows_by_path, root):
            results = []
            index_fresh = False
            reasons.append("ranked_index_file_missing_or_checksum_changed")

    if not terms:
        engine = "none"
        reasons.append("query_has_no_terms")
    elif not results:
        candidates, engine, fallback_reasons = _rg_candidates(root, project, terms)
        reasons.extend(fallback_reasons)
        results = _rank_fallback(candidates, root=root, terms=terms, project=project, limit=limit)

    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    receipt: dict[str, Any] = {
        "query": query,
        "project": project,
        "engine": engine,
        "index_fresh": index_fresh,
        "latency_ms": latency_ms,
        "results": results,
        "reasons": list(dict.fromkeys(reasons)),
        "telemetry_correlation_id": None,
        "use_tracking": "caller emits memory-use after opening returned paths",
    }
    if emit_telemetry and (telemetry_path is not None or root.exists()):
        try:
            import memory_telemetry

            receipt["telemetry_correlation_id"] = memory_telemetry.emit_read(
                phase="1-assess",
                reader="memory_locator",
                query=query,
                memory_ids_seen=[item["id"] for item in results],
                reason="ranked canonical file paths",
                telemetry_path=telemetry_path,
                engine=engine,
                returned_paths=[item["path"] for item in results],
                latency_ms=latency_ms,
                zero_result=not results,
            )
        except Exception as exc:  # noqa: BLE001 - locator must remain usable
            receipt["reasons"].append(f"telemetry_error: {exc}")
    elif emit_telemetry:
        receipt["reasons"].append("telemetry_skipped_memory_root_missing")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="keywords describing the memory to find")
    parser.add_argument("--project", help="canonical project id; defaults from --workdir")
    parser.add_argument("--workdir", type=Path, default=Path.cwd(), help="repo used to resolve project")
    parser.add_argument("--memory-root", type=Path, help="override build-loop-memory root")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--json", action="store_true", help="print the full retrieval receipt")
    parser.add_argument("--no-telemetry", action="store_true", help="do not emit a retrieval receipt row")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project = args.project or resolve_project(args.workdir.resolve())
    receipt = locate(
        args.query,
        project=project,
        limit=max(1, args.limit),
        memory_root=args.memory_root,
        emit_telemetry=not args.no_telemetry,
    )
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        for result in receipt["results"]:
            print(result["absolute_path"])
    return 0 if receipt["results"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
