#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Argument parsing for the decision writer CLI.

Flag set, defaults, choices, and help text are byte-for-byte identical to the
historical flat module so ``--help`` and argument validation are unchanged.
"""
from __future__ import annotations

import argparse

from constants import (
    DEFAULT_EMBEDDING_MODEL_VERSION,
    VALID_CONFIDENCE_SOURCES,
    VALID_CONFIDENCES,
    VALID_DOMAINS,
    VALID_GOALS,
    VALID_STATUSES,
    VALID_TASK_CATEGORIES,
    VALID_TOOLS,
)


def split_csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Atomic decision writer for repo-local episodic memory.")
    p.add_argument("--workdir", default=".", help="Project root used for project/taxonomy resolution")
    p.add_argument("--title", required=True)
    p.add_argument("--decision", required=True, help="One-sentence decision body")
    p.add_argument("--context", default="")
    p.add_argument("--alternatives", default="")
    p.add_argument("--consequences", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--tags", required=True, help="Comma-separated tag list")
    p.add_argument("--primary-tag", required=True)
    p.add_argument("--entity", required=True)
    p.add_argument("--confidence", required=True, choices=sorted(VALID_CONFIDENCES))
    p.add_argument("--status", default="accepted", choices=sorted(VALID_STATUSES))
    p.add_argument("--source", default="manual")
    p.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to today (UTC)")
    p.add_argument("--related-runs", default="", help="Comma-separated run_ids")
    p.add_argument("--related-decisions", default="", help="Comma-separated decision IDs")
    p.add_argument("--supersedes", default=None, help="Decision ID this replaces (overrides confidence ladder)")
    p.add_argument("--bookmark-snapshot-id", default=None)
    p.add_argument("--captured-turn-excerpt", default=None)

    # v2 metadata (design §15). All optional at the CLI; defaults applied
    # in `_apply_v2_defaults` so legacy callers continue to work.
    p.add_argument("--project", default=None, help="Repo-scoped project name. Default: derived from --entity prefix or $CLAUDE_PROJECT_DIR basename.")
    p.add_argument("--tool", default=None, help=f"Authoring tool. One of: {sorted(VALID_TOOLS)}. Default: 'claude-code' (manual MADRs use 'manual').")
    p.add_argument("--model", default=None, help="Free-form model ID. Default: 'claude-opus-4-7'.")
    p.add_argument("--task-category", default=None, help=f"Task category. One of: {sorted(VALID_TASK_CATEGORIES)}. Default: 'unknown'.")
    p.add_argument("--author", default=None, help="Free-form author identifier. Default: $USER env var, else 'unknown'.")
    p.add_argument("--files-touched", default=None, help="Comma-separated repo-relative paths. Default: empty list (or git diff if --infer-files-touched).")
    p.add_argument("--infer-files-touched", action="store_true", help="Populate files_touched from `git diff --name-only HEAD~1 HEAD` when no --files-touched is given.")
    p.add_argument("--closing-commit", default=None, help="Git SHA that closed this decision. Default: null.")
    p.add_argument("--last-validated", default=None, help="ISO date for last_validated. Default: null.")
    p.add_argument("--last-accessed", default=None, help="ISO date for last_accessed. Default: null.")

    # v3 metadata (design §16). All optional at the CLI; defaults applied
    # in `apply_v3_defaults` so legacy callers continue to work.
    p.add_argument(
        "--confidence-source",
        default=None,
        help=(
            "Who asserted the fact (decoupled from `source`, which is *how* it was captured). "
            f"One of: {sorted(VALID_CONFIDENCE_SOURCES)}. Default: derived from --source "
            "(manual→user_statement, auto-*→ai_inference, migration→external_import)."
        ),
    )
    p.add_argument(
        "--confirmation-count",
        default=None,
        help="Times this memory was successfully acted upon. Integer >= 0. Default: 0.",
    )
    p.add_argument(
        "--valid-until",
        default=None,
        help="Explicit expiration as ISO date (YYYY-MM-DD or full ISO-8601). Default: null.",
    )
    p.add_argument(
        "--causal-parent-id",
        default=None,
        help="Decision id this one was caused by. Enables decision-chain queries. Default: null.",
    )
    p.add_argument(
        "--embedding-model-version",
        default=None,
        help=(
            f"Model that produced the embedding stored alongside this fact. Default: "
            f"$EMBED_MODEL env var if set, else {DEFAULT_EMBEDDING_MODEL_VERSION!r}. "
            "Triggers re-embed when the model id changes."
        ),
    )
    p.add_argument(
        "--domain",
        default=None,
        help=f"Subject domain (stricter MECE than primary_tag). One of: {sorted(VALID_DOMAINS)}. Default: 'unknown'.",
    )
    p.add_argument(
        "--goal",
        default=None,
        help=f"Why the work was done. One of: {sorted(VALID_GOALS)}. Default: 'unknown'.",
    )

    # DB dual-write (Phase 2)
    p.add_argument("--db", dest="db", action="store_true", default=True, help="Enable Postgres dual-write (default)")
    p.add_argument("--no-db", dest="db", action="store_false", help="Skip DB dual-write")
    p.add_argument(
        "--schema",
        default=None,
        help=(
            "Postgres schema for this project. Default: $AGENT_MEMORY_SCHEMA "
            "or 'personal_memory'. During the dual-write transitional window "
            "(AGENT_MEMORY_DUAL_WRITE=1) the legacy 'build_loop_memory' schema "
            "is also written to."
        ),
    )
    p.add_argument(
        "--embed-model",
        default="bge-m3",
        help=(
            "Legacy flag kept for back-compat. Embedding model is now selected by "
            "$EMBED_BACKEND ('mlx' default, 'ollama' fallback) and $EMBED_MODEL via "
            "scripts/embed_backend.py. Ollama default is bge-m3 (Phase A); MLX default "
            "is mxbai-embed-large-v1. Both produce 1024-dim vectors but live in "
            "DIFFERENT vector spaces — never mix without re-embedding."
        ),
    )
    return p.parse_args(argv)
