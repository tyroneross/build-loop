# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Capture a research finding as a date-stamped reference file.

Routes through the canonical ``memory_writer.write()`` so every captured
reference carries provenance frontmatter AND the global freshness/audit ledger
entry — no ad-hoc file writes. Adds reference-specific frontmatter:
``retrieved_at``, ``refresh_after`` (per content class), ``content_class``,
``source_urls`` (each tagged with a tier), and ``informed_decision``.

References land in the project's ``research`` sublane of the central memory
store as ``<YYYY-MM-DD>-reference-<slug>.md``. The store is uncommitted by
default (the user opts into git for it), so the corpus grows without polluting
consumer repos.

Stdlib only. Imports ``memory_writer`` and ``_paths`` from the sibling
``scripts/`` directory.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import memory_writer as mw  # type: ignore  # noqa: E402

from .horizons import classify_content_class, default_refresh_days


def _slugify(text: str) -> str:
    import re
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "untitled"


def _normalize_sources(source_urls: Any) -> list[dict[str, str]]:
    """Normalize the source-URL input into a list of ``{url, tier}`` dicts.

    Accepts:
      * a list of dicts ``{"url": ..., "tier": ...}`` (passed through)
      * a list of strings (tier defaults to ``"T?"`` — unknown)
      * a list of ``[url, tier]`` pairs
    Empty/None yields an empty list. Tier strings are upper-cased and stripped.
    """
    out: list[dict[str, str]] = []
    if not source_urls:
        return out
    for item in source_urls:
        if isinstance(item, dict):
            url = str(item.get("url") or "").strip()
            tier = str(item.get("tier") or "T?").strip().upper() or "T?"
        elif isinstance(item, (list, tuple)) and len(item) >= 1:
            url = str(item[0]).strip()
            tier = str(item[1]).strip().upper() if len(item) > 1 and item[1] else "T?"
        else:
            url = str(item).strip()
            tier = "T?"
        if url:
            out.append({"url": url, "tier": tier})
    return out


def build_reference_body(
    *,
    topic: str,
    findings: str,
    sources: list[dict[str, str]],
    informed_decision: str,
    content_class: str,
    retrieved_at: str,
    refresh_after_days: int,
) -> str:
    """Render the human-readable reference body (markdown).

    Findings are the EXTRACTED knowledge, not raw HTML — the caller is
    responsible for distilling. The body restates the temporal metadata in prose
    so a reader scanning the file (not the frontmatter) still sees retrieval date
    and refresh horizon.
    """
    refresh_on = date.fromordinal(
        date.fromisoformat(retrieved_at).toordinal() + refresh_after_days
    ).isoformat()
    lines: list[str] = [
        f"# Reference: {topic}",
        "",
        f"- Retrieved: {retrieved_at}",
        f"- Content class: `{content_class}` · refresh horizon: {refresh_after_days} days "
        f"(refresh after **{refresh_on}**)",
        "",
        "## Sources",
    ]
    if sources:
        for s in sources:
            lines.append(f"- [{s['tier']}] {s['url']}")
    else:
        lines.append("- (no source URLs recorded)")
    lines += [
        "",
        "## Findings",
        findings.strip() or "(no findings recorded)",
        "",
        "## Informed decision",
        informed_decision.strip() or "(decision context not recorded)",
    ]
    return "\n".join(lines) + "\n"


def capture_reference(
    *,
    workdir: Path | str,
    topic: str,
    findings: str,
    source_urls: Any = None,
    informed_decision: str = "",
    run_id: str,
    host: str = "claude_code",
    content_class: str | None = None,
    refresh_after_days: int | None = None,
    retrieved_at: str | None = None,
    project: str | None = None,
    memory_dir: Path | None = None,
) -> dict[str, Any]:
    """Capture a research finding as a reference file via the canonical writer.

    Required: ``workdir``, ``topic``, ``findings``, ``run_id``.

    ``content_class`` is inferred from topic + sources when omitted.
    ``refresh_after_days`` defaults to the class horizon when omitted.
    ``retrieved_at`` defaults to today (ISO ``YYYY-MM-DD``).

    Returns ``{path, frontmatter, content_class, refresh_after, retrieved_at}``.

    The write goes to the project ``research`` sublane unless ``memory_dir`` is
    given (tests pass a sandbox dir directly). Routing through
    ``memory_writer.write`` means provenance + the global freshness ledger are
    handled for free.
    """
    workdir = Path(workdir)
    retrieved_at = retrieved_at or date.today().isoformat()
    sources = _normalize_sources(source_urls)

    resolved_class = content_class or classify_content_class(
        topic=topic, source_urls=[s["url"] for s in sources]
    )
    horizon = (
        refresh_after_days
        if refresh_after_days is not None
        else default_refresh_days(resolved_class)
    )

    slug = _slugify(topic)
    # Date-prefixed, reference-* naming class (recognized by scan_reference_lane
    # and reference_activation_audit).
    file_rel = f"{retrieved_at}-reference-{slug}.md"

    body = build_reference_body(
        topic=topic,
        findings=findings,
        sources=sources,
        informed_decision=informed_decision,
        content_class=resolved_class,
        retrieved_at=retrieved_at,
        refresh_after_days=horizon,
    )

    extra_frontmatter: dict[str, Any] = {
        "retrieved_at": retrieved_at,
        "refresh_after": horizon,
        "content_class": resolved_class,
        "source_urls": sources,
        "informed_decision": informed_decision or "",
        "why_durable": "captured research/web finding for reuse + freshness tracking",
        "phase": "research-capture",
    }

    # Resolve the write lane. Default = project research sublane.
    if memory_dir is not None:
        target_dir = Path(memory_dir)
        scope = "project"
        resolved_project = project
    else:
        from _paths import project_research_dir  # type: ignore  # noqa: PLC0415
        from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415

        resolved_project = project or resolve_project(workdir)
        target_dir = project_research_dir(resolved_project)
        scope = "project"

    description = (
        f"{topic} ({resolved_class}, retrieved {retrieved_at}, "
        f"refresh after {horizon}d)"
    )[:200]

    fm = mw.write(
        target_dir,
        file_rel=file_rel,
        body=body,
        name=f"reference-{slug}",
        description=description,
        type_="reference",
        run_id=run_id,
        workdir=str(workdir),
        host=host,
        extra_frontmatter=extra_frontmatter,
        scope=scope,
        project=resolved_project,
    )

    return {
        "path": str(target_dir / file_rel),
        "frontmatter": fm,
        "content_class": resolved_class,
        "refresh_after": horizon,
        "retrieved_at": retrieved_at,
    }
