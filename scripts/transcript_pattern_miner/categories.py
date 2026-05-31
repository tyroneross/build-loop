#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""categories — the 5 mining categories + test-pattern outcome aggregation."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from .session import SessionAggregate, TEST_CATEGORIES, classify_outcome
from .textproc import CORRECTION_RE, truncate


def cluster_corrections(aggs: list[SessionAggregate]) -> list[dict[str, Any]]:
    """Cluster user corrections by 3-gram overlap. Surface clusters with 3+ members."""
    candidates: list[dict[str, Any]] = []
    for agg in aggs:
        for ts, text, proj in agg.user_messages:
            if not CORRECTION_RE.search(text):
                continue
            quote = truncate(text, 300)
            tokens = re.findall(r"[a-z0-9']+", text.lower())
            if len(tokens) < 3:
                continue
            grams = {tuple(tokens[i: i + 3]) for i in range(len(tokens) - 2)}
            candidates.append({
                "ts": ts,
                "quote": quote,
                "grams": grams,
                "session": agg.session_id,
                "project": proj,
            })

    # Union-find-ish clustering by shared 3-grams (>=2 shared).
    clusters: list[list[dict[str, Any]]] = []
    for c in candidates:
        placed = False
        for cl in clusters:
            rep = cl[0]
            if len(c["grams"] & rep["grams"]) >= 2:
                cl.append(c)
                placed = True
                break
        if not placed:
            clusters.append([c])

    out: list[dict[str, Any]] = []
    for cl in clusters:
        if len(cl) < 3:
            continue
        timestamps = [c["ts"] for c in cl if c["ts"]]
        first_seen = min(timestamps) if timestamps else None
        last_seen = max(timestamps) if timestamps else None
        projects = sorted({c["project"] for c in cl})
        sessions = sorted({c["session"] for c in cl})
        rep_quote = sorted({c["quote"] for c in cl}, key=len)[0]
        out.append({
            "count": len(cl),
            "first_seen": first_seen.isoformat() if first_seen else None,
            "last_seen": last_seen.isoformat() if last_seen else None,
            "representative_quote": rep_quote,
            "projects": projects,
            "session_count": len(sessions),
        })
    out.sort(key=lambda d: (-d["count"], d.get("last_seen") or ""))
    return out


def repeated_tool_sequences(aggs: list[SessionAggregate]) -> list[dict[str, Any]]:
    """Find length-3..6 sub-sequences that recur across 3+ sessions."""
    counts: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for agg in aggs:
        seq = agg.tool_sequence
        for length in (3, 4, 5, 6):
            seen_in_session: set[tuple[str, ...]] = set()
            for i in range(len(seq) - length + 1):
                window = tuple(seq[i: i + length])
                if len(set(window)) == 1:
                    continue
                if window in seen_in_session:
                    continue
                seen_in_session.add(window)
                counts[window].add(agg.session_id)

    out: list[dict[str, Any]] = []
    for window, sessions in counts.items():
        if len(sessions) < 3:
            continue
        out.append({
            "sequence": list(window),
            "session_count": len(sessions),
            "sample_sessions": sorted(sessions)[:3],
        })
    out.sort(key=lambda d: (-d["session_count"], -len(d["sequence"])))
    return out[:20]


def cross_project_files(
    aggs: list[SessionAggregate],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (cross-project files in 3+ projects, churn files inside one project)."""
    file_to_projects: dict[str, set[str]] = defaultdict(set)
    file_count_per_project: dict[tuple[str, str], int] = Counter()
    for agg in aggs:
        for proj, fp in agg.files_touched:
            file_to_projects[fp].add(proj)
            file_count_per_project[(proj, fp)] += 1

    cross: list[dict[str, Any]] = []
    for fp, projects in file_to_projects.items():
        if len(projects) >= 3:
            cross.append({
                "file": fp,
                "projects": sorted(projects),
                "project_count": len(projects),
            })
    cross.sort(key=lambda d: -d["project_count"])

    churn: list[dict[str, Any]] = []
    for (proj, fp), n in file_count_per_project.items():
        if n >= 5:
            churn.append({"project": proj, "file": fp, "touches": n})
    churn.sort(key=lambda d: -d["touches"])
    return cross[:15], churn[:15]


def manual_command_rituals(aggs: list[SessionAggregate]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    sessions_for_shape: dict[str, set[str]] = defaultdict(set)
    for agg in aggs:
        for shape in agg.bash_commands:
            counter[shape] += 1
            sessions_for_shape[shape].add(agg.session_id)
    out: list[dict[str, Any]] = []
    for shape, n in counter.items():
        if n < 5:
            continue
        out.append({
            "command_shape": shape,
            "count": n,
            "session_count": len(sessions_for_shape[shape]),
        })
    out.sort(key=lambda d: -d["count"])
    return out[:20]


def test_pattern_outcomes(
    aggs: list[SessionAggregate],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Returns (per_invocation_log, aggregate_table).
    per_invocation_log entries are the rows we persist to .outcomes.jsonl.
    aggregate_table is one row per category for the markdown report.
    """
    per_invocation: list[dict[str, Any]] = []
    cat_counts: dict[str, dict[str, Any]] = {
        c: {"count": 0, "POSITIVE": 0, "MIXED": 0, "REWORK": 0, "NO_SIGNAL": 0,
            "projects": Counter()} for c in TEST_CATEGORIES
    }
    for agg in aggs:
        for inv in agg.test_invocations:
            outcome, ev_quote = classify_outcome(agg, inv)
            cat = inv["category"]
            cat_counts[cat]["count"] += 1
            cat_counts[cat][outcome] += 1
            cat_counts[cat]["projects"][inv["proj"]] += 1
            per_invocation.append({
                "timestamp": inv["ts"].isoformat() if inv["ts"] else None,
                "session_id": agg.session_id,
                "test_category": cat,
                "pattern_subtype": inv["subtype"],
                "outcome_class": outcome,
                "evidence_quote": ev_quote[:300],
                "project": inv["proj"],
            })
    table: list[dict[str, Any]] = []
    for cat in TEST_CATEGORIES:
        d = cat_counts[cat]
        if d["count"] == 0:
            continue
        top_proj = d["projects"].most_common(1)[0][0] if d["projects"] else "?"
        table.append({
            "category": cat, "count": d["count"],
            "POSITIVE": d["POSITIVE"], "MIXED": d["MIXED"],
            "REWORK": d["REWORK"], "NO_SIGNAL": d["NO_SIGNAL"],
            "top_project": top_proj,
        })
    table.sort(key=lambda r: -r["count"])
    return per_invocation, table
