#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Karpathy LLM-Wiki lifecycle states for build-loop-memory entries.

Reuses existing primitives — does NOT reinvent:
  * decision rot           → ``detect_decision_rot.detect_rot``
  * supersession           → ``supersede_decision.find_decision_file`` + caller
  * revoke                 → ``revoke_decision`` (frontmatter ``revoked: true``)

For non-decision entries (lessons, gotchas, debug-incidents, ...) state is
purely structural:

  * **draft**        — body is empty (less than 40 non-whitespace chars).
  * **active**       — clean: non-empty body AND no source-hash mismatch.
  * **stale**        — source hash recorded in frontmatter doesn't match the
                       body's current hash (the lesson cites stale source).
  * **contradicted** — frontmatter carries ``status: superseded`` or there's
                       a sibling ``_history/<id>-vN.md`` for decisions.
  * **archived**     — frontmatter ``status: rejected``  OR  ``revoked: true``
                       OR the file lives under a ``_history/`` directory.

Frontmatter update is surgical: it sets ``lifecycle_state: <state>`` and
``lifecycle_reason: <reason>`` without rewriting the entire file. Use
``dry_run=True`` to compute the transition without disk I/O.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # scripts/

# Public state vocabulary.
LIFECYCLE_STATES = ("draft", "active", "stale", "contradicted", "archived")

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_DRAFT_THRESHOLD_CHARS = 40


@dataclass
class StateClassification:
    """The classified state for a single entry, with the trigger reason."""
    state: str
    reason: str
    source_hash: str
    previous_state: str | None = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "reason": self.reason,
            "source_hash": self.source_hash,
            "previous_state": self.previous_state,
        }


@dataclass
class StateTransition:
    """A proposed state transition for a single on-disk entry."""
    path: str
    classification: StateClassification

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            **self.classification.to_dict(),
        }


# ---------------------------------------------------------------------------
# Frontmatter helpers.
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    fm_raw = m.group(1)
    body = _FM_RE.sub("", text, count=1)
    fm: dict = {}
    for line in fm_raw.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        # Normalise booleans for convenience.
        if v.lower() in ("true", "false"):
            fm[k] = v.lower() == "true"
        else:
            fm[k] = v
    return fm, body


def _emit_frontmatter(fm: dict) -> str:
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif v is None:
            lines.append(f"{k}: null")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---\n")
    return "\n".join(lines)


def compute_source_hash(body: str) -> str:
    """Stable SHA-256 of the body text (whitespace-normalised)."""
    norm = re.sub(r"\s+", " ", body).strip().encode("utf-8")
    return hashlib.sha256(norm).hexdigest()


def _is_archived_path(path: Path) -> bool:
    return "_history" in path.parts or "archive" in path.parts


# ---------------------------------------------------------------------------
# Classification.
# ---------------------------------------------------------------------------


def classify_state(
    path: str | Path,
    *,
    threshold_days: int = 90,
    now: datetime | None = None,
) -> StateClassification:
    """Classify a single on-disk memory entry.

    Order of checks (first match wins):
      1. archived  — file in _history/ or archive/ tree, OR fm.revoked,
                     OR fm.status == 'rejected'
      2. contradicted — fm.status == 'superseded'
      3. stale       — fm.source_hash present AND mismatches current body hash;
                       OR (decision-typed) age > threshold_days
      4. draft       — body shorter than _DRAFT_THRESHOLD_CHARS chars
      5. active      — default
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        # Treat unreadable as archived — it's effectively gone.
        return StateClassification(
            state="archived",
            reason="unreadable",
            source_hash="",
        )

    fm, body = _parse_frontmatter(text)
    body_hash = compute_source_hash(body)
    prev_state = fm.get("lifecycle_state")

    if _is_archived_path(p) or fm.get("revoked") is True or fm.get("status") == "rejected":
        return StateClassification(
            state="archived",
            reason="archived-path-or-status",
            source_hash=body_hash,
            previous_state=prev_state,
        )
    if fm.get("status") == "superseded":
        return StateClassification(
            state="contradicted",
            reason="superseded-status",
            source_hash=body_hash,
            previous_state=prev_state,
        )

    # Stale: source-hash mismatch OR (decision) age > threshold.
    fm_hash = fm.get("source_hash")
    if fm_hash and fm_hash != body_hash:
        return StateClassification(
            state="stale",
            reason="source-hash-mismatch",
            source_hash=body_hash,
            previous_state=prev_state,
        )

    if fm.get("type") == "decision":
        # Decision rot — reuse the same age semantics as detect_decision_rot.
        date_field = fm.get("last_validated") or fm.get("date")
        if date_field:
            try:
                if len(str(date_field)) == 10:
                    ts = datetime.strptime(str(date_field), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                else:
                    ts = datetime.fromisoformat(str(date_field).replace("Z", "+00:00"))
                now_ts = now or datetime.now(timezone.utc)
                age = (now_ts - ts).days
                if age >= threshold_days:
                    return StateClassification(
                        state="stale",
                        reason=f"decision-rot:{age}d",
                        source_hash=body_hash,
                        previous_state=prev_state,
                    )
            except ValueError:
                pass

    if len(re.sub(r"\s", "", body)) < _DRAFT_THRESHOLD_CHARS:
        return StateClassification(
            state="draft",
            reason="body-too-short",
            source_hash=body_hash,
            previous_state=prev_state,
        )

    return StateClassification(
        state="active",
        reason="clean",
        source_hash=body_hash,
        previous_state=prev_state,
    )


def list_lifecycle_transitions(
    workdir: str | Path = ".",
    *,
    memory_root: str | Path | None = None,
    threshold_days: int = 90,
    projects: Iterable[str] | None = None,
    only_changed: bool = False,
) -> list[StateTransition]:
    """Walk project + top-level memory; classify each entry; return transitions.

    ``only_changed=True`` filters to entries whose new state differs from
    the existing ``lifecycle_state`` frontmatter — useful for "what would
    change" reports.
    """
    if memory_root is None:
        try:
            from _paths import memory_store_root  # type: ignore  # noqa: PLC0415
            memory_root = memory_store_root()
        except Exception:
            memory_root = Path(workdir) / "build-loop-memory"
    root = Path(memory_root)
    out: list[StateTransition] = []
    # Walk both project lanes and top-level lanes.
    walked: list[Path] = []
    projects_root = root / "projects"
    target_projects = set(projects) if projects is not None else None
    if projects_root.exists():
        for project_dir in sorted(projects_root.iterdir()):
            if not project_dir.is_dir():
                continue
            if target_projects is not None and project_dir.name not in target_projects:
                continue
            for sublane in ("lessons", "debugging", "architecture", "design", "product", "decisions"):
                lane_dir = project_dir / sublane
                if not lane_dir.exists():
                    continue
                for f in sorted(lane_dir.rglob("*.md")):
                    if f.name.startswith("INDEX") or f.name.startswith("TELEMETRY"):
                        continue
                    walked.append(f)
    for top in ("lessons", "debugging", "architecture", "design", "product"):
        top_dir = root / top
        if not top_dir.exists():
            continue
        for f in sorted(top_dir.glob("*.md")):
            if f.name.startswith("INDEX") or f.name.startswith("TELEMETRY"):
                continue
            walked.append(f)

    for f in walked:
        c = classify_state(f, threshold_days=threshold_days)
        if only_changed and c.previous_state == c.state:
            continue
        out.append(StateTransition(path=str(f), classification=c))
    return out


def apply_state_to_frontmatter(
    path: str | Path,
    state: str,
    *,
    reason: str,
    dry_run: bool = False,
) -> dict:
    """Surgically write ``lifecycle_state`` + ``lifecycle_reason`` into the
    frontmatter of ``path`` without rewriting the body or other keys.

    ``dry_run`` returns the would-be frontmatter without disk I/O.
    """
    if state not in LIFECYCLE_STATES:
        raise ValueError(
            f"unknown lifecycle state {state!r}; valid: {LIFECYCLE_STATES}"
        )
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    fm["lifecycle_state"] = state
    fm["lifecycle_reason"] = reason
    fm["lifecycle_transitioned_at"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    new_text = _emit_frontmatter(fm) + body.lstrip("\n")
    if not dry_run:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, p)
    return fm
