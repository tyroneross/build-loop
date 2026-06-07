#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Cross-project prior-art digest for Phase 1 Assess (P4).

Surfaces, in Phase 1, the implementations and decisions from OTHER projects
that match the task's classified capability. The target scenario from
``bl-memory-overhaul-plan``: a cold task "build semantic search" produces
a digest naming atomize-news / atomize-ai / AIDA's prior impls AND the
decisions that explain them ("why") — without the human knowing to ask.

Design (KISS+DRY):

* **Reuses existing infrastructure**. Dense retrieval flows through
  ``memory_facade.recall(kind="semantic", project=None)`` — the P1 hybrid
  tier with embedding rerank. Decision scanning walks
  ``projects/<slug>/decisions/`` directly via ``_paths`` helpers.
* **Cross-project scope by construction**. Recall passes ``project=None``
  (all projects); the decisions scan iterates every ``projects/*``
  directory except the current one.
* **Host-LLM compliant**. No vendor API call. Returns structured records
  the host coding agent's LLM consumes via the bootstrap packet.
* **Compact digest**. Hard cap on item count + per-item char length so the
  Phase 1 packet never floods context.
* **Absence-tolerant**. Empty memory / no host classifier / missing
  packages all degrade silently — the digest becomes ``[]`` with a
  ``reasons[]`` entry. Never raises.

Public API::

    build_prior_art(
        query: str,
        capabilities: list[str],
        current_project: str,
        *, memory_root: Path | None = None,
        max_impls: int = 5,
        max_per_capability: int = 3,
        max_excerpt_chars: int = 280,
        max_total_chars: int = 4000,
    ) -> dict[str, Any]

Envelope shape::

    {
      "capabilities":      ["semantic-search", ...],
      "implementations":   [{project, source, snippet, capability, score, kind}, ...],
      "decisions":         [{project, title, path, snippet, capability}, ...],
      "digest_text":       "## Prior Art Across Projects\\n- ...",
      "stats":             {impls: int, decisions: int, projects: list[str], truncated: bool},
      "reasons":           [str, ...],
    }
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


# --------------------------------------------------------------------------
# Lazy imports (degrade gracefully if any optional dep missing).
# --------------------------------------------------------------------------

def _safe_recall(query: str, limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    """Hybrid semantic recall across ALL projects. Empty on any failure."""
    reasons: list[str] = []
    try:
        from memory_facade import recall as recall_memory  # type: ignore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return [], [f"memory_facade_import_error: {exc}"]
    try:
        # kind="semantic" -> the SQLite+Postgres tier where the P1 hybrid
        # rerank lives. project=None -> ALL projects (cross-project).
        envelope = recall_memory(
            query=query,
            kind="semantic",
            project=None,
            limit=limit,
            skip_postgres=False,
        )
    except Exception as exc:  # noqa: BLE001
        return [], [f"recall_error: {exc}"]
    results = envelope.get("results_by_kind", {}).get("semantic", []) or []
    reasons.extend(envelope.get("reasons", []) or [])
    return results, reasons


def _memory_root(override: Path | None = None) -> Path:
    if override is not None:
        return Path(override)
    # Honour env vars used by other build-loop memory helpers.
    raw = (
        os.environ.get("BUILD_LOOP_MEMORY_STORE_ROOT")
        or os.environ.get("BUILD_LOOP_MEMORY_ROOT")
        or os.environ.get("AGENT_MEMORY_ROOT")
        or "~/dev/git-folder/build-loop-memory"
    )
    return Path(os.path.expanduser(raw))


# --------------------------------------------------------------------------
# Frontmatter parsing (mini — we only need title/date/tags).
# --------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_FM_KEY = re.compile(r"^\s*([a-zA-Z0-9_]+)\s*:\s*(.+?)\s*$")


def _read_text(path: Path, max_chars: int = 8000) -> str | None:
    try:
        return path.read_text(encoding="utf-8")[:max_chars]
    except OSError:
        return None


def _parse_fm(text: str) -> dict[str, str]:
    m = _FM_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        km = _FM_KEY.match(line)
        if km:
            val = km.group(2).strip().strip('"').strip("'")
            out[km.group(1)] = val
    return out


def _short_excerpt(text: str, terms: Iterable[str], max_chars: int) -> str:
    """Center the excerpt on the first matching term; trim to ``max_chars``.

    Strips the frontmatter block when present so excerpts don't waste budget
    on yaml.
    """
    body = text
    m = _FM_RE.match(body)
    if m:
        body = body[m.end():]
    body = body.strip()
    if len(body) <= max_chars:
        return body
    lower = body.lower()
    start = 0
    for term in terms:
        if not term:
            continue
        i = lower.find(term.lower())
        if i >= 0:
            start = max(0, i - max_chars // 4)
            break
    end = min(len(body), start + max_chars)
    chunk = body[start:end].strip()
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(body) else ""
    return f"{prefix}{chunk}{suffix}"


# --------------------------------------------------------------------------
# Capability matching.
# --------------------------------------------------------------------------

def _capability_match_text(text: str, capability: str, terms: list[str]) -> bool:
    """Return True if ``text`` looks like a hit for ``capability``.

    We re-use the classifier's synonym table so impl scoring stays consistent
    with intent classification. If the classifier isn't importable we fall
    back to a plain-substring check on the capability + terms.
    """
    try:
        from capability_classifier import (  # type: ignore  # noqa: PLC0415
            CAPABILITY_SYNONYMS,
            _score_capability,
        )
    except Exception:  # noqa: BLE001
        text_lower = text.lower()
        if capability.replace("-", " ") in text_lower:
            return True
        return any(t in text_lower for t in terms if len(t) >= 4)

    phrases = CAPABILITY_SYNONYMS.get(capability, [])
    if not phrases:
        return False
    return _score_capability(text.lower(), phrases) > 0


# --------------------------------------------------------------------------
# Decision scanning (cross-project).
# --------------------------------------------------------------------------

def _iter_other_projects(memory_root: Path, current_project: str) -> list[Path]:
    """Return ``projects/*`` directories EXCEPT the current one and reserved names.

    Reserved: ``_unscoped`` (the catch-all lane) and ``_unsorted`` (intake bin).
    """
    reserved = {current_project, "_unscoped", "_unsorted"}
    projects_dir = memory_root / "projects"
    if not projects_dir.is_dir():
        return []
    out: list[Path] = []
    try:
        for entry in sorted(projects_dir.iterdir()):
            if entry.is_dir() and entry.name not in reserved and not entry.name.startswith("."):
                out.append(entry)
    except OSError:
        return out
    return out


def _scan_decisions(
    project_dir: Path,
    capability: str,
    terms: list[str],
    *,
    max_per_project: int = 2,
    max_excerpt_chars: int = 280,
) -> list[dict[str, Any]]:
    """Scan ``projects/<slug>/decisions/`` for capability-tagged decisions."""
    decisions_dir = project_dir / "decisions"
    if not decisions_dir.is_dir():
        return []
    project_name = project_dir.name
    candidates: list[tuple[float, dict[str, Any]]] = []
    try:
        files = sorted(decisions_dir.glob("*.md"))
    except OSError:
        return []
    for path in files:
        text = _read_text(path)
        if not text:
            continue
        # Cheap signal: filename or content mentions the capability/terms.
        if not _capability_match_text(text, capability, terms):
            continue
        fm = _parse_fm(text)
        title = fm.get("title") or path.stem
        date = fm.get("date") or fm.get("created") or ""
        # Score: more term hits = higher score (date is a tie-breaker).
        text_lower = text.lower()
        score = sum(text_lower.count(t.lower()) for t in terms if len(t) >= 4)
        # +1 baseline so a capability-only match still ranks above nothing.
        score = score + 1.0
        candidates.append(
            (
                score,
                {
                    "project": project_name,
                    "title": title,
                    "date": date,
                    "path": str(path.relative_to(project_dir.parent.parent)),
                    "snippet": _short_excerpt(text, terms + [capability.replace("-", " ")], max_excerpt_chars),
                    "capability": capability,
                },
            )
        )
    candidates.sort(key=lambda x: (-x[0], x[1].get("date") or "", x[1].get("title") or ""))
    return [d for _s, d in candidates[:max_per_project]]


def _scan_lessons(
    project_dir: Path,
    capability: str,
    terms: list[str],
    *,
    max_per_project: int = 2,
    max_excerpt_chars: int = 280,
) -> list[dict[str, Any]]:
    """Scan ``projects/<slug>/lessons/`` as a cheap impl-signal fallback.

    Project lessons frequently document "we used X for Y" — useful prior
    art when the dense semantic tier is empty (cold install) or lacks rows
    for this fleet. Same scoring shape as decisions, but tagged kind=lesson.
    """
    lessons_dir = project_dir / "lessons"
    if not lessons_dir.is_dir():
        return []
    project_name = project_dir.name
    candidates: list[tuple[float, dict[str, Any]]] = []
    try:
        files = sorted(lessons_dir.glob("*.md"))
    except OSError:
        return []
    for path in files:
        text = _read_text(path)
        if not text:
            continue
        if not _capability_match_text(text, capability, terms):
            continue
        fm = _parse_fm(text)
        title = fm.get("name") or fm.get("title") or path.stem
        date = fm.get("created_at") or fm.get("date") or ""
        text_lower = text.lower()
        score = sum(text_lower.count(t.lower()) for t in terms if len(t) >= 4) + 1.0
        candidates.append(
            (
                score,
                {
                    "project": project_name,
                    "source": str(path.relative_to(project_dir.parent.parent)),
                    "title": title,
                    "snippet": _short_excerpt(text, terms + [capability.replace("-", " ")], max_excerpt_chars),
                    "capability": capability,
                    "kind": "lesson",
                    "date": date,
                },
            )
        )
    candidates.sort(key=lambda x: (-x[0], x[1].get("date") or "", x[1].get("title") or ""))
    return [d for _s, d in candidates[:max_per_project]]


# --------------------------------------------------------------------------
# Impl signals from the semantic tier (P1 hybrid).
# --------------------------------------------------------------------------

def _semantic_hits_as_impls(
    capability: str,
    terms: list[str],
    current_project: str,
    *,
    limit: int = 8,
    max_excerpt_chars: int = 280,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Pull cross-project impls from the dense semantic tier (P1 hybrid).

    The query string is the capability + key terms. We filter out hits
    belonging to the CURRENT project so the digest is genuinely
    cross-project.
    """
    query = " ".join([capability.replace("-", " ")] + terms[:5])
    rows, reasons = _safe_recall(query, limit=limit)
    impls: list[dict[str, Any]] = []
    for row in rows:
        proj = (row.get("project") or row.get("_project") or "").strip()
        if proj == current_project:
            continue
        if not proj or proj in {"_unscoped", "_unsorted"}:
            # Drop catch-all lane noise from cross-project digest.
            continue
        snippet = (
            row.get("object")
            or row.get("snippet")
            or row.get("excerpt")
            or row.get("body")
            or ""
        )
        impls.append(
            {
                "project": proj,
                "source": row.get("subject") or row.get("source_path") or row.get("path") or "",
                "snippet": _short_excerpt(str(snippet), terms + [capability.replace("-", " ")], max_excerpt_chars),
                "capability": capability,
                "kind": "semantic",
                "score": row.get("score") or row.get("confidence") or 0,
            }
        )
    return impls, reasons


# --------------------------------------------------------------------------
# Public API.
# --------------------------------------------------------------------------

DEFAULT_MAX_IMPLS = 5
DEFAULT_MAX_DECISIONS = 5
DEFAULT_MAX_PER_CAPABILITY = 3
DEFAULT_MAX_EXCERPT_CHARS = 280
DEFAULT_MAX_TOTAL_CHARS = 4000


def build_prior_art(
    query: str,
    capabilities: list[str],
    current_project: str,
    *,
    memory_root: Path | None = None,
    max_impls: int = DEFAULT_MAX_IMPLS,
    max_decisions: int = DEFAULT_MAX_DECISIONS,
    max_per_capability: int = DEFAULT_MAX_PER_CAPABILITY,
    max_excerpt_chars: int = DEFAULT_MAX_EXCERPT_CHARS,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
    terms: list[str] | None = None,
) -> dict[str, Any]:
    """Build a compact cross-project prior-art digest.

    ``capabilities`` is the list of classified capability tags from
    ``capability_classifier.classify(query)``. Each tag drives one
    cross-project sweep through (a) the dense semantic tier and (b) every
    other project's ``decisions/`` and ``lessons/`` folders.

    The result is a structured envelope PLUS a markdown ``digest_text``
    block ready to inline in the bootstrap brief / intent.md. Hard char
    cap protects Phase 1 from context flooding.
    """
    reasons: list[str] = []
    out_impls: list[dict[str, Any]] = []
    out_decisions: list[dict[str, Any]] = []

    if not capabilities:
        return {
            "capabilities": [],
            "implementations": [],
            "decisions": [],
            "digest_text": "",
            "stats": {"impls": 0, "decisions": 0, "projects": [], "truncated": False},
            "reasons": ["no_capabilities_classified"],
        }

    root = _memory_root(memory_root)
    if not (root / "projects").is_dir():
        reasons.append(f"missing_projects_root: {root / 'projects'}")
        # No projects to scan AND semantic tier may still answer; keep going.

    # Local-term derivation (kept independent of the bootstrap's term set
    # so prior_art can be called standalone).
    if terms is None:
        try:
            from capability_classifier import extract_terms  # type: ignore  # noqa: PLC0415
            terms = extract_terms(query)
        except Exception:  # noqa: BLE001
            terms = []
    terms = [t for t in (terms or []) if t]

    other_projects = _iter_other_projects(root, current_project)
    seen_projects: set[str] = set()

    # ---- one sweep per capability ----------------------------------------
    for cap in capabilities:
        cap_impls: list[dict[str, Any]] = []
        cap_decisions: list[dict[str, Any]] = []

        # (a) dense semantic tier — P1 hybrid recall, cross-project.
        sem_impls, sem_reasons = _semantic_hits_as_impls(
            cap, terms, current_project,
            limit=max_per_capability * 3,
            max_excerpt_chars=max_excerpt_chars,
        )
        cap_impls.extend(sem_impls[:max_per_capability])
        reasons.extend(sem_reasons)

        # (b) project-by-project decisions scan — the "why" for each impl.
        # (c) project-by-project lessons scan — extra impl-signal fallback.
        for proj_dir in other_projects:
            cap_decisions.extend(
                _scan_decisions(
                    proj_dir, cap, terms,
                    max_per_project=2,
                    max_excerpt_chars=max_excerpt_chars,
                )
            )
            if len(cap_impls) < max_per_capability:
                cap_impls.extend(
                    _scan_lessons(
                        proj_dir, cap, terms,
                        max_per_project=2,
                        max_excerpt_chars=max_excerpt_chars,
                    )
                )

        # Cap per-capability output (impls + decisions independently).
        cap_impls = cap_impls[: max_per_capability]
        cap_decisions = cap_decisions[: max_per_capability]
        out_impls.extend(cap_impls)
        out_decisions.extend(cap_decisions)
        for item in cap_impls + cap_decisions:
            p = item.get("project")
            if p:
                seen_projects.add(p)

    # Global cap (compactness).
    out_impls = out_impls[:max_impls]
    out_decisions = out_decisions[:max_decisions]

    # Build the markdown digest under the total char budget.
    digest_text, truncated = _render_digest(
        capabilities=capabilities,
        impls=out_impls,
        decisions=out_decisions,
        max_total_chars=max_total_chars,
    )

    if not out_impls and not out_decisions:
        reasons.append("no_prior_art_found")

    return {
        "capabilities": capabilities,
        "implementations": out_impls,
        "decisions": out_decisions,
        "digest_text": digest_text,
        "stats": {
            "impls": len(out_impls),
            "decisions": len(out_decisions),
            "projects": sorted(seen_projects),
            "truncated": truncated,
        },
        "reasons": reasons,
    }


def _render_digest(
    *,
    capabilities: list[str],
    impls: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    max_total_chars: int,
) -> tuple[str, bool]:
    """Render the markdown digest with a hard size cap.

    The shape mirrors the bootstrap's existing brief — Headline + bullets
    grouped by impl then decision. We stream into a buffer and stop when
    we hit ``max_total_chars`` so the digest never floods context.
    """
    if not impls and not decisions:
        return "", False

    cap_str = ", ".join(capabilities) if capabilities else "(unclassified)"
    parts: list[str] = [
        "## Prior Art Across Projects",
        f"_capability: {cap_str}_",
        "",
    ]

    def _budget_ok() -> bool:
        return sum(len(p) + 1 for p in parts) < max_total_chars

    truncated = False

    if impls:
        parts.append("### Implementations")
        for impl in impls:
            kind = impl.get('kind', 'semantic')
            suffix = " _(lesson — verify intent before reusing)_" if kind == "lesson" else ""
            line = (
                f"- **{impl.get('project', '?')}** ({impl.get('capability', '?')}, "
                f"{kind}): "
                f"`{impl.get('source') or '(no source)'}` — "
                f"{(impl.get('snippet') or '').splitlines()[0][:200]}"
                f"{suffix}"
            )
            if not _budget_ok():
                truncated = True
                break
            parts.append(line)
        parts.append("")

    if decisions and _budget_ok():
        parts.append("### Decisions (the why)")
        for dec in decisions:
            head = (
                f"- **{dec.get('project', '?')}** — *{dec.get('title', '?')}* "
                f"(`{dec.get('path', '?')}`)"
            )
            snippet = (dec.get("snippet") or "").strip().splitlines()
            preview = snippet[0][:200] if snippet else ""
            line = head + (f"\n  > {preview}" if preview else "")
            if not _budget_ok():
                truncated = True
                break
            parts.append(line)

    text = "\n".join(parts).rstrip() + "\n"
    if len(text) > max_total_chars:
        text = text[: max_total_chars - 32].rstrip() + "\n…\n_[truncated for context budget]_\n"
        truncated = True
    return text, truncated


# --------------------------------------------------------------------------
# CLI — useful for ad-hoc inspection and standalone testing.
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="Free-text task intent.")
    parser.add_argument("--current-project", default="_unscoped")
    parser.add_argument("--memory-root", default=None)
    parser.add_argument("--max-impls", type=int, default=DEFAULT_MAX_IMPLS)
    parser.add_argument("--max-decisions", type=int, default=DEFAULT_MAX_DECISIONS)
    parser.add_argument("--max-total-chars", type=int, default=DEFAULT_MAX_TOTAL_CHARS)
    parser.add_argument("--brief", action="store_true", help="Print only digest_text.")
    args = parser.parse_args(argv)

    try:
        from capability_classifier import classify_envelope  # type: ignore  # noqa: PLC0415
        env = classify_envelope(args.query)
        capabilities = env["capabilities"]
        terms = env["terms"]
    except Exception:  # noqa: BLE001
        capabilities = []
        terms = []

    digest = build_prior_art(
        query=args.query,
        capabilities=capabilities,
        current_project=args.current_project,
        memory_root=Path(os.path.expanduser(args.memory_root)) if args.memory_root else None,
        max_impls=args.max_impls,
        max_decisions=args.max_decisions,
        max_total_chars=args.max_total_chars,
        terms=terms,
    )
    if args.brief:
        print(digest["digest_text"])
    else:
        print(json.dumps(digest, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
