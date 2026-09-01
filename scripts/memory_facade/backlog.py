#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Recall backlog items from canonical and personal-mirror stores by ID."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .common import _parse_iso, _q_match


def _store_module():
    path = Path(__file__).resolve().parents[1] / "backlog.py"
    spec = importlib.util.spec_from_file_location("_memory_facade_backlog_store", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load backlog store: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _memory_root() -> Path:
    override = os.environ.get("BUILD_LOOP_MEMORY_DIR")
    return Path(override).expanduser() if override else Path.home() / "dev" / "git-folder" / "build-loop-memory"


def read_backlog(
    workdir: Path,
    query: str,
    limit: int,
    project: Optional[str],
) -> tuple[List[Dict[str, Any]], List[str]]:
    """Return a canonical-first union; mirror-only records stay visible."""
    reasons: list[str] = []
    try:
        store = _store_module()
        slug = project or store.project_slug(workdir)
        canonical_all = store.load_items(workdir, include_archive=True)
        canonical = [item for item in canonical_all if not item.get("_archived")]
        canonical_ids = {
            str(item.get("id")) for item in canonical_all if item.get("id")
        }
        mirror_dir = _memory_root() / "projects" / slug / "backlog"
        mirror = store._load_mirror_items(mirror_dir)
    except Exception as exc:  # noqa: BLE001 - recall degrades gracefully
        return [], [f"backlog_unavailable: {exc}"]

    union: dict[str, dict[str, Any]] = {}
    for source, rows in (("canonical", canonical), ("mirror", mirror)):
        for item in rows:
            iid = str(item.get("id") or "")
            if not iid or iid in union or (source == "mirror" and iid in canonical_ids):
                continue
            body = str(item.get("_body") or "")
            searchable = " ".join(
                str(item.get(key) or "")
                for key in (
                    "id", "title", "bucket", "workstream", "area", "type",
                    "entities", "related_to", "decision_options", "decision_impacts",
                )
            ) + " " + body
            if not _q_match(searchable, query):
                continue
            row = {
                # Every other backend stamps its lane here, and the merge layer
                # plus every consumer reads it. Omitting it made backlog rows the
                # only ones arriving with `_kind` None, which is a silent
                # attribution hole rather than a crash.
                "_kind": "backlog",
                "id": iid,
                "title": item.get("title"),
                "status": item.get("status"),
                "priority": item.get("priority"),
                "type": item.get("type"),
                "bucket": item.get("bucket"),
                "workstream": item.get("workstream"),
                "area": item.get("area"),
                "related_to": item.get("related_to") or [],
                "decision_options": item.get("decision_options") or [],
                "decision_impacts": item.get("decision_impacts") or [],
                "body": body,
                "path": item.get("_path"),
                "source": source,
                "needs_reconcile": source == "mirror",
                "_recency_ts": _parse_iso(item.get("updated") or item.get("created")) or 0,
            }
            union[iid] = row

    rows = sorted(union.values(), key=lambda row: row.get("_recency_ts") or 0, reverse=True)
    mirror_only = sum(1 for row in rows if row["needs_reconcile"])
    if mirror_only:
        reasons.append(f"backlog_mirror_only: {mirror_only} item(s) need reconcile")
    return rows[:limit], reasons
