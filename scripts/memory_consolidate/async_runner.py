#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Async consolidation runner — distill → promote → lifecycle → backlinks.

MemTier Phase 2a — runs OFF the Stop / Phase-6 hot path. Designed to be
invoked by cron, a background watcher, or a manual ``memory_consolidate
async`` invocation. NEVER imported by ``intake.py`` or ``place.py``; a
guard test in ``test_async_runner.py`` proves it.

Each pass is structurally independent: a failure in one arm does not
stop the others. Returns a structured run report (dict) so the caller
(or Phase 6 Learn) can decide what landed.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # scripts/

# Lazy-import the four arms so a single broken arm doesn't kill the runner.
def _import_arms():
    from memory_consolidate import distill, promote, lifecycle, backlinks
    return distill, promote, lifecycle, backlinks


@dataclass
class AsyncReport:
    distill_clusters: int = 0
    distill_packets: list[dict] = field(default_factory=list)
    promotion_candidates: int = 0
    promotion_accepted: int = 0
    promotion_rejected: int = 0
    promotion_decisions: list[dict] = field(default_factory=list)
    lifecycle_transitions: int = 0
    lifecycle_changes: list[dict] = field(default_factory=list)
    backlink_entries_touched: int = 0
    backlink_links_added: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "distill_clusters": self.distill_clusters,
            "distill_packets": self.distill_packets,
            "promotion_candidates": self.promotion_candidates,
            "promotion_accepted": self.promotion_accepted,
            "promotion_rejected": self.promotion_rejected,
            "promotion_decisions": self.promotion_decisions,
            "lifecycle_transitions": self.lifecycle_transitions,
            "lifecycle_changes": self.lifecycle_changes,
            "backlink_entries_touched": self.backlink_entries_touched,
            "backlink_links_added": self.backlink_links_added,
            "errors": self.errors,
            "duration_seconds": self.duration_seconds,
        }


def run_async(
    workdir: str | Path = ".",
    *,
    memory_root: str | Path | None = None,
    min_projects: int = 2,
    similarity_threshold: float = 0.55,
    write: bool = True,
    similarity_fn: Any = None,
    siblings_fn: Any = None,
    related_fn: Any = None,
    embed_fn: Any = None,
) -> AsyncReport:
    """Run the four arms in dependency order: distill → promote → lifecycle → backlinks.

    ``write=False`` runs every arm but stops short of writing distilled / promoted
    entries to disk; lifecycle frontmatter writes are also suppressed. Backlink
    writes obey the same flag. Useful for dry-run reports.

    Injectable callbacks parallel the four capabilities' test seams — production
    callers leave them ``None`` and recall is sourced from P1.
    """
    start = time.monotonic()
    distill_mod, promote_mod, lifecycle_mod, backlinks_mod = _import_arms()
    report = AsyncReport()

    # ----- 1. distill -----
    try:
        refs = distill_mod.find_distill_candidates(workdir)
        clusters = distill_mod.cluster_similar(
            refs, threshold=similarity_threshold,
            similarity_fn=similarity_fn, embed_fn=embed_fn,
        )
        report.distill_clusters = len(clusters)
        for c in clusters:
            packet = distill_mod.prepare_distill_packet(c)
            report.distill_packets.append(packet.to_dict())
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"distill: {exc}")

    # ----- 2. promote -----
    try:
        cands = promote_mod.find_promotion_candidates(
            workdir=workdir, memory_root=memory_root,
            min_projects=min_projects,
            threshold=similarity_threshold,
            siblings_fn=siblings_fn, embed_fn=embed_fn,
        )
        report.promotion_candidates = len(cands)
        for cand in cands:
            packet = promote_mod.prepare_promotion_packet(cand, min_projects=min_projects)
            if packet.gate.accepted:
                report.promotion_accepted += 1
            else:
                report.promotion_rejected += 1
            report.promotion_decisions.append({
                "source_path": cand.source_path,
                "gate": packet.gate.to_dict(),
                "suggested_decision": packet.suggested_decision,
            })
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"promote: {exc}")

    # ----- 3. lifecycle -----
    try:
        transitions = lifecycle_mod.list_lifecycle_transitions(
            workdir=workdir, memory_root=memory_root, only_changed=True,
        )
        report.lifecycle_transitions = len(transitions)
        for t in transitions:
            report.lifecycle_changes.append(t.to_dict())
            if write:
                try:
                    lifecycle_mod.apply_state_to_frontmatter(
                        t.path,
                        t.classification.state,
                        reason=t.classification.reason,
                    )
                except OSError as exc:
                    report.errors.append(f"lifecycle write {t.path}: {exc}")
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"lifecycle: {exc}")

    # ----- 4. backlinks -----
    try:
        # Walk every memory entry, propose backlinks via related_fn (or P1).
        if memory_root is None:
            try:
                from _paths import memory_store_root  # type: ignore  # noqa: PLC0415
                memroot = memory_store_root()
            except Exception:
                memroot = Path(workdir) / "build-loop-memory"
        else:
            memroot = Path(memory_root)
        for f in _walk_all_memory(memroot):
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            fm = _parse_fm(text)
            own_name = fm.get("name")
            project = _project_from_path(f, memroot)
            suggs = backlinks_mod.propose_backlinks(
                text, own_name=own_name, project=project,
                related_fn=related_fn, embed_fn=embed_fn,
            )
            if not suggs:
                continue
            report.backlink_entries_touched += 1
            report.backlink_links_added += len(suggs)
            if write:
                try:
                    backlinks_mod.write_backlinks_footer(f, suggs)
                except OSError as exc:
                    report.errors.append(f"backlinks write {f}: {exc}")
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"backlinks: {exc}")

    report.duration_seconds = round(time.monotonic() - start, 4)
    return report


# ---------------------------------------------------------------------------
# Internal helpers (small, kept here to avoid bloating sibling modules).
# ---------------------------------------------------------------------------

import re as _re


def _walk_all_memory(memroot: Path):
    if not memroot.exists():
        return
    # Project lanes
    projects_root = memroot / "projects"
    if projects_root.exists():
        for project_dir in sorted(projects_root.iterdir()):
            if not project_dir.is_dir():
                continue
            for sublane in ("lessons", "debugging", "architecture", "design", "product", "decisions"):
                d = project_dir / sublane
                if not d.exists():
                    continue
                for f in sorted(d.rglob("*.md")):
                    if f.name.startswith(("INDEX", "TELEMETRY")):
                        continue
                    yield f
    # Top-level lanes.
    for lane in ("lessons", "debugging", "architecture", "design", "product"):
        d = memroot / lane
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            if f.name.startswith(("INDEX", "TELEMETRY")):
                continue
            yield f


_FM_RE = _re.compile(r"^---\n(.*?)\n---\n", _re.DOTALL)


def _parse_fm(text: str) -> dict:
    m = _FM_RE.match(text)
    if not m:
        return {}
    fm: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def _project_from_path(p: Path, root: Path) -> str | None:
    try:
        rel = p.relative_to(root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) >= 3 and parts[0] == "projects":
        return parts[1]
    return None
