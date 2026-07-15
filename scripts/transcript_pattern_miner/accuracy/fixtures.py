#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Labeled fixture for the 5 detector accuracy harness.

Each fixture group contains POSITIVE cases that MUST fire and BOUNDARY NEGATIVES
that MUST NOT fire, enabling precision/recall scoring against ground truth.

Ground-truth labels are embedded as metadata in the returned dicts alongside the
SessionAggregate lists. Format per category:

    {
        "positives": list[SessionAggregate],   # detector MUST surface a result
        "negatives": list[SessionAggregate],   # detector MUST NOT surface a result
        "positive_keys": ...,                  # what to look for in output (category-specific)
        "negative_keys": ...,                  # what must be absent
    }

For cross_project_files we also need to express churn vs cross as separate groups.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..session import SessionAggregate

# ---------------------------------------------------------------------------
# Shared timestamps (deterministic — no wall-clock dependency)
# ---------------------------------------------------------------------------

_TS = dt.datetime(2026, 1, 15, 10, 0, 0, tzinfo=dt.timezone.utc)


def _make_agg(session_id: str) -> SessionAggregate:
    agg = SessionAggregate(session_id)
    agg.first_ts = _TS
    agg.last_ts = _TS
    return agg


# ---------------------------------------------------------------------------
# 1. cluster_corrections
#    Rule (categories.py:15-67):
#      - user_messages entries filtered by CORRECTION_RE
#      - ≥3 tokens per message
#      - clustered when ≥2 shared 3-grams with cluster representative
#      - cluster surfaces when ≥3 members
# ---------------------------------------------------------------------------

# Correction texts that share 3-gram spine: ("no", "actually", "you") and
# ("actually", "you", "should")
_CORR_TEXTS = [
    "no actually you should stop using pip",
    "no actually you should stop installing pip",
    "no actually you should stop running pip commands",
    "no actually you should stop relying on pip here",
    "no actually you should stop it with pip",
]

# Two-member cluster — just below the ≥3 threshold.
# These two messages share 3-grams with each other but the group has only 2 members.
# IMPORTANT: use a completely different text spine from _CORR_TEXTS to avoid
# accidentally clustering with other negative groups when run in isolation.
_CORR_NEG2_TEXTS = [
    "wrong direction please revert the database schema changes",
    "wrong direction please revert the entire database migration",
]

# One-off correction — single member, orthogonal text spine
_CORR_ONEOFF = "stop this is not what i asked for at all"

# Correction text with <3 tokens — excluded by the token gate
_CORR_SHORT = "no wrong"


def make_cluster_corrections_fixture() -> dict[str, Any]:
    """Return positives (cluster fires) and negatives (cluster must not fire).

    Each negative group is stored separately so the eval can test each in isolation
    (prevents accidentally combining negative groups into a spurious cluster).
    """

    # POSITIVES: 5-message cluster across sessions, all ≥2 shared 3-grams
    pos_sessions = []
    for i, text in enumerate(_CORR_TEXTS):
        agg = _make_agg(f"corr-pos-{i}")
        # user_messages: (ts, text, proj)
        agg.user_messages.append((_TS, text, f"proj-{i % 3}"))
        pos_sessions.append(agg)

    # NEGATIVES group A: only 2 members with same 3-gram spine — below threshold
    neg_two = []
    for i, text in enumerate(_CORR_NEG2_TEXTS):
        agg = _make_agg(f"corr-neg2-{i}")
        agg.user_messages.append((_TS, text, "proj-x"))
        neg_two.append(agg)

    # NEGATIVES group B: single one-off (orthogonal text — won't cluster)
    neg_oneoff = _make_agg("corr-neg-oneoff")
    neg_oneoff.user_messages.append((_TS, _CORR_ONEOFF, "proj-x"))

    # NEGATIVES group C: short text (< 3 tokens) — token gate blocks it
    neg_short = _make_agg("corr-neg-short")
    neg_short.user_messages.append((_TS, _CORR_SHORT, "proj-x"))

    # NEGATIVES group D: no CORRECTION_RE match — a normal user message
    neg_normal = _make_agg("corr-neg-normal")
    neg_normal.user_messages.append((_TS, "please add a test for this edge case", "proj-x"))

    return {
        "positives": pos_sessions,
        # Each group tested in isolation by the eval
        "neg_two": neg_two,
        "neg_oneoff": [neg_oneoff],
        "neg_short": [neg_short],
        "neg_normal": [neg_normal],
        # Convenience: all negatives combined (valid since text spines are disjoint)
        "negatives": neg_two + [neg_oneoff, neg_short, neg_normal],
        "description": "cluster_corrections: ≥3 member cluster fires; 2-member / 1-off / short / non-correction must not",
    }


# ---------------------------------------------------------------------------
# 2. repeated_tool_sequences
#    Rule (categories.py:70-96):
#      - length 3..6 sub-sequences across ≥3 sessions
#      - skips windows where all tools identical
#      - deduplicates same window within one session
# ---------------------------------------------------------------------------

_POS_SEQUENCE = ["Read:file_path", "Edit:file_path", "Bash:command"]  # length 3, varied


def make_repeated_tool_sequences_fixture() -> dict[str, Any]:
    """Return positives (sequence fires in ≥3 sessions) and negatives (only 2 sessions)."""

    # POSITIVES: same 3-tool sequence in 3 different sessions
    pos_sessions = []
    for i in range(3):
        agg = _make_agg(f"seq-pos-{i}")
        agg.tool_sequence = list(_POS_SEQUENCE)
        pos_sessions.append(agg)

    # NEGATIVES: same sequence but only in 2 sessions — just below threshold
    neg_two = []
    for i in range(2):
        agg = _make_agg(f"seq-neg2-{i}")
        agg.tool_sequence = list(_POS_SEQUENCE)
        neg_two.append(agg)

    # NEGATIVES: sequence where all tools are identical — skipped by the set(window)==1 guard
    neg_uniform = _make_agg("seq-neg-uniform")
    neg_uniform.tool_sequence = ["Read:file_path", "Read:file_path", "Read:file_path"]

    # NEGATIVES: sequence shorter than 3 — no 3-gram windows possible
    neg_short = _make_agg("seq-neg-short")
    neg_short.tool_sequence = ["Read:file_path", "Edit:file_path"]

    # NEGATIVES: empty tool sequence
    neg_empty = _make_agg("seq-neg-empty")
    neg_empty.tool_sequence = []

    return {
        "positives": pos_sessions,
        "negatives": neg_two + [neg_uniform, neg_short, neg_empty],
        "description": "repeated_tool_sequences: ≥3 sessions fires; 2 sessions / uniform / short / empty must not",
    }


# ---------------------------------------------------------------------------
# 3. cross_project_files
#    Rule (categories.py:99-125):
#      cross: file in ≥3 projects
#      churn: file with ≥5 touches in one project (file_count_per_project)
# ---------------------------------------------------------------------------

_CROSS_FILE = "/Users/devuser/dev/git-folder/shared-config/settings.json"
_CHURN_FILE = "/Users/devuser/dev/git-folder/project-a/src/frequently-edited.ts"
_CHURN_PROJ = "project-a"


def make_cross_project_files_fixture() -> dict[str, Any]:
    """
    Returns:
        positives_cross: sessions containing a file touched in ≥3 distinct projects
        positives_churn: sessions giving a file ≥5 touches in one project
        negatives_cross: file in only 2 projects — just below threshold
        negatives_churn: file with only 4 touches — just below churn threshold
    """

    # POSITIVES cross: _CROSS_FILE touched in 3 different projects
    pos_cross = []
    for proj in ("proj-A", "proj-B", "proj-C"):
        agg = _make_agg(f"cross-pos-{proj}")
        agg.files_touched = [(proj, _CROSS_FILE)]
        pos_cross.append(agg)

    # POSITIVES churn: _CHURN_FILE touched 5 times in _CHURN_PROJ
    pos_churn = []
    for i in range(5):
        agg = _make_agg(f"churn-pos-{i}")
        agg.files_touched = [(_CHURN_PROJ, _CHURN_FILE)]
        pos_churn.append(agg)

    # NEGATIVES cross: file in only 2 projects — just below ≥3 threshold
    neg_cross_2proj = []
    for proj in ("proj-X", "proj-Y"):
        agg = _make_agg(f"cross-neg2-{proj}")
        agg.files_touched = [(proj, "/some/unique/file.json")]
        neg_cross_2proj.append(agg)

    # NEGATIVES churn: file with only 4 touches — just below ≥5 threshold
    neg_churn_4 = []
    for i in range(4):
        agg = _make_agg(f"churn-neg4-{i}")
        agg.files_touched = [("proj-B", "/some/project/rarely-touched.ts")]
        neg_churn_4.append(agg)

    return {
        "positives_cross": pos_cross,
        "positives_churn": pos_churn,
        "negatives_cross": neg_cross_2proj,
        "negatives_churn": neg_churn_4,
        "cross_file": _CROSS_FILE,
        "churn_file": _CHURN_FILE,
        "churn_proj": _CHURN_PROJ,
        "description": "cross_project_files: file in ≥3 projects fires cross; ≥5 touches fires churn; 2-proj/4-touch must not",
    }


# ---------------------------------------------------------------------------
# 4. manual_command_rituals
#    Rule (categories.py:128-145):
#      bash command shape count ≥5 fires
# ---------------------------------------------------------------------------

_RITUAL_SHAPE = "git status --short"


def make_manual_command_rituals_fixture() -> dict[str, Any]:
    """Return sessions whose bash_commands, when aggregated, fire or don't fire."""

    # POSITIVES: _RITUAL_SHAPE appears 5 times total across sessions
    pos_sessions = []
    for i in range(5):
        agg = _make_agg(f"ritual-pos-{i}")
        agg.bash_commands = [_RITUAL_SHAPE]
        pos_sessions.append(agg)

    # NEGATIVES: shape appears only 4 times — just below ≥5 threshold
    neg_four = []
    for i in range(4):
        agg = _make_agg(f"ritual-neg4-{i}")
        agg.bash_commands = ["git status --short"]
        neg_four.append(agg)

    # NEGATIVES: different shapes (never the same one 5 times)
    neg_varied = []
    for i, shape in enumerate(["git log --oneline", "git diff --stat", "ls -la", "pwd"]):
        agg = _make_agg(f"ritual-neg-varied-{i}")
        agg.bash_commands = [shape]
        neg_varied.append(agg)

    # NEGATIVES: empty bash_commands
    neg_empty = _make_agg("ritual-neg-empty")
    neg_empty.bash_commands = []

    return {
        "positives": pos_sessions,
        "negatives": neg_four + neg_varied + [neg_empty],
        "description": "manual_command_rituals: same shape ≥5 fires; same shape 4 / varied / empty must not",
    }


# ---------------------------------------------------------------------------
# 5. test_pattern_outcomes
#    Rule (categories.py:148-193):
#      Classifies each test_invocation via classify_outcome().
#      No threshold to fire — any invocation produces output.
#      Boundary: empty test_invocations → no rows.
# ---------------------------------------------------------------------------

def make_test_pattern_outcomes_fixture() -> dict[str, Any]:
    """Return sessions with / without test_invocations."""

    # POSITIVES: session with a test invocation that will produce output rows
    pos = _make_agg("outcomes-pos-1")
    pos.test_invocations = [{
        "category": "B_runner",
        "subtype": "pytest",
        "evidence": "pytest scripts/ -q",
        "event_idx": 0,
        "ts": _TS,
        "proj": "proj-a",
        "tool_use_id": None,
    }]
    pos.events = [{
        "idx": 0,
        "kind": "assistant_tool",
        "ts": _TS,
        "text": "",
        "tool_name": "Bash",
        "tool_input": {"command": "pytest scripts/ -q"},
        "tool_use_id": None,
        "is_error": None,
        "proj": "proj-a",
    }]

    # NEGATIVES: session with no test invocations
    neg_empty = _make_agg("outcomes-neg-empty")
    neg_empty.test_invocations = []

    return {
        "positives": [pos],
        "negatives": [neg_empty],
        "description": "test_pattern_outcomes: any test_invocation produces rows; empty produces nothing",
    }
