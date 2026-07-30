#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""secrets_scan — pattern-based secret detection and rotation-tracker aggregation."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .session import SessionAggregate

# ---------------------------------------------------------------------------
# Secret patterns — more-specific first
# ---------------------------------------------------------------------------

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_\-]{40,}")),
    ("openai", re.compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}")),
    ("github_classic", re.compile(r"gh[ps]_[A-Za-z0-9]{36,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("github_oauth", re.compile(r"gho_[A-Za-z0-9]{36,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("pem", re.compile(r"-----BEGIN [A-Z ]+-----")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}")),
]

# Generic high-entropy: only flag if a credential keyword precedes within 30 chars.
GENERIC_SECRET_RE = re.compile(
    r"(?i)(token|secret|key|password|credential|api[_\-]?key|bearer)"
    r"[^A-Za-z0-9]{1,30}([A-Za-z0-9_\-]{40,})"
)


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def truncate_secret(s: str, n: int = 12) -> str:
    return s[:n] + "…" if len(s) > n else s


def scan_secrets(text: str) -> list[tuple[str, str]]:
    """Return list of (kind, value) found. Dedupes within one text."""
    if not text:
        return []
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, rx in SECRET_PATTERNS:
        for m in rx.finditer(text):
            v = m.group(0)
            key = (kind, v)
            if key not in seen:
                seen.add(key)
                found.append(key)
    for m in GENERIC_SECRET_RE.finditer(text):
        v = m.group(2)
        # skip if already caught by a specific pattern
        if any(v == fv for _, fv in found):
            continue
        key = ("generic", v)
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


def secrets_observed(aggs: list["SessionAggregate"]) -> list[dict[str, Any]]:
    """Aggregate unique secret values into a rotation tracker."""
    by_value: dict[tuple[str, str], dict[str, Any]] = {}
    for agg in aggs:
        for kind, val, proj, ts in agg.secret_hits:
            key = (kind, val)
            entry = by_value.setdefault(key, {
                "kind": kind,
                "preview": val,  # full value — single-user rotation tracking
                "first_seen": ts,
                "last_seen": ts,
                "session_ids": set(),
                "projects": set(),
            })
            if ts is not None:
                if entry["first_seen"] is None or ts < entry["first_seen"]:
                    entry["first_seen"] = ts
                if entry["last_seen"] is None or ts > entry["last_seen"]:
                    entry["last_seen"] = ts
            entry["session_ids"].add(agg.session_id)
            entry["projects"].add(proj)

    out: list[dict[str, Any]] = []
    for entry in by_value.values():
        out.append({
            "kind": entry["kind"],
            "preview": entry["preview"],
            "first_seen": entry["first_seen"].isoformat() if entry["first_seen"] else None,
            "last_seen": entry["last_seen"].isoformat() if entry["last_seen"] else None,
            "session_count": len(entry["session_ids"]),
            "projects": sorted(entry["projects"]),
        })
    out.sort(key=lambda d: (d.get("last_seen") or "", d["kind"]), reverse=True)
    return out
