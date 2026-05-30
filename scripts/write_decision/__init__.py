#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""write_decision package — atomic decision writer for repo-local episodic memory.

Runnable as:
  python3 scripts/write_decision/__main__.py --title … --decision … ...  (canonical CLI)

Importable as (with scripts/ on sys.path — the historical flat-module surface):
  from write_decision import parse_frontmatter, emit_frontmatter, slugify, ...

This ``__init__`` re-exports every name that downstream scripts imported from
the old flat ``write_decision.py`` so the split is transparent to consumers
(revoke_decision, scan_transcript_for_decisions, recall, sync_db_from_files,
validate_knowledge, regenerate_knowledge_index, migrate_*, the metadata tests,
etc.). The intra-package modules use flat sibling imports, so the package dir
and scripts/ are inserted onto sys.path here.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the package dir (flat sibling imports) AND scripts/ (top-level deps
# like _paths, atomic_io, db, embed_backend) are importable, regardless of how
# the package was first imported.
_PKG_DIR = Path(__file__).resolve().parent
for _p in (str(_PKG_DIR), str(_PKG_DIR.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---- constants / vocab / lock / atomic primitives ----
from constants import (  # noqa: E402,F401
    CONFIDENCE_ORDER,
    DEFAULT_EMBEDDING_MODEL_VERSION,
    LOCK_TIMEOUT_S,
    LockedFile,
    VALID_CONFIDENCE_SOURCES,
    VALID_CONFIDENCES,
    VALID_DOMAINS,
    VALID_EVENT_KINDS,
    VALID_GOALS,
    VALID_SOURCES,
    VALID_STATUSES,
    VALID_TASK_CATEGORIES,
    VALID_TOOLS,
    VALID_TYPES,
    atomic_write_bytes,
    log,
)

# ---- frontmatter parse/emit ----
from frontmatter import (  # noqa: E402,F401
    _FM_RE,
    _parse_yaml_value,
    _yaml_emit_scalar,
    _yaml_emit_scalar_for_list,
    _yaml_emit_value,
    emit_frontmatter,
    parse_frontmatter,
)

# ---- taxonomy ----
from taxonomy import load_taxonomy, validate_tags  # noqa: E402,F401

# ---- ids / discovery / topic identity ----
from ids import (  # noqa: E402,F401
    canonical_decision_id,
    find_same_topic,
    list_decisions,
    next_id,
    slugify,
)

# ---- schema defaults + validators + MADR render ----
from schema import (  # noqa: E402,F401
    apply_v2_defaults,
    apply_v3_defaults,
    render_madr,
    validate_v2,
    validate_v3,
)

# ---- file side effects ----
from io_ops import (  # noqa: E402,F401
    append_event,
    archive_to_history,
    iso_utc,
    regenerate_index,
)
# Back-compat aliases for the historical private helper names.
from io_ops import archive_to_history as _archive_to_history  # noqa: E402,F401
from io_ops import iso_utc as _iso_utc  # noqa: E402,F401

# ---- db dual-write + embed/legacy shims ----
from dbwrite import (  # noqa: E402,F401
    _confidence_to_float,
    db_dualwrite,
    ollama_embed,
    psql_run,
)

# ---- CLI + pipeline ----
from cli import parse_args, split_csv  # noqa: E402,F401
from writer import main, main as write_decision_main  # noqa: E402,F401

__all__ = [
    "CONFIDENCE_ORDER",
    "DEFAULT_EMBEDDING_MODEL_VERSION",
    "LOCK_TIMEOUT_S",
    "LockedFile",
    "VALID_CONFIDENCE_SOURCES",
    "VALID_CONFIDENCES",
    "VALID_DOMAINS",
    "VALID_EVENT_KINDS",
    "VALID_GOALS",
    "VALID_SOURCES",
    "VALID_STATUSES",
    "VALID_TASK_CATEGORIES",
    "VALID_TOOLS",
    "VALID_TYPES",
    "atomic_write_bytes",
    "log",
    "_FM_RE",
    "emit_frontmatter",
    "parse_frontmatter",
    "load_taxonomy",
    "validate_tags",
    "canonical_decision_id",
    "find_same_topic",
    "list_decisions",
    "next_id",
    "slugify",
    "apply_v2_defaults",
    "apply_v3_defaults",
    "render_madr",
    "validate_v2",
    "validate_v3",
    "append_event",
    "regenerate_index",
    "db_dualwrite",
    "ollama_embed",
    "psql_run",
    "parse_args",
    "split_csv",
    "main",
    "write_decision_main",
]
