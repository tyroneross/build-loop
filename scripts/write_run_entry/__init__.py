#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""write_run_entry package — deterministic Review-F writer for build-loop.

Runnable as:
  python3 scripts/write_run_entry/__main__.py --workdir <dir> ...  (canonical)
  python3 -m write_run_entry --workdir <dir> ...   (with scripts/ on sys.path)

Public API (consumed by other scripts):
  update_execution_state(state_path, action, ...)
  compute_run_id(goal, now=None)

Public constants (consumed by tests and external callers):
  EXECUTION_SCHEMA_VERSION  — int, current execution-state schema version
  EXECUTION_VALID_ACTIONS   — set[str], allowed action values for update_execution_state
  EXECUTION_VALID_PHASES    — set[str], allowed phase values
  EXECUTION_RETURN_STATUSES — set[str], allowed return-chunk status values
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the package directory is on sys.path so that flat sibling imports
# (``from iohelpers import ...`` etc.) inside sub-modules resolve correctly
# when the package is imported via ``python3 -m write_run_entry`` or
# ``from write_run_entry import ...``.
_PKG_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _PKG_DIR.parent
for _d in (str(_PKG_DIR), str(_SCRIPTS_DIR)):
    if _d not in sys.path:
        sys.path.insert(0, _d)

# Re-export the names callers import from the old flat module.
from execstate import (  # type: ignore  # noqa: E402,F401
    EXECUTION_RETURN_STATUSES,
    EXECUTION_SCHEMA_VERSION,
    EXECUTION_VALID_ACTIONS,
    EXECUTION_VALID_PHASES,
    update_execution_state,
)
from idtime import compute_run_id  # type: ignore  # noqa: E402,F401
