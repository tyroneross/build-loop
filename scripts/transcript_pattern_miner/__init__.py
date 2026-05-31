#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""transcript_pattern_miner — folder-per-capability package.

The hyphenated entry script (transcript-pattern-miner.py) is a thin shim that
inserts scripts/ onto sys.path and delegates here.

Public surface for importlib-based invocation (used by the test suite):
  from transcript_pattern_miner.__main__ import main
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the package's parent (scripts/) importable for relative sibling deps.
_PKG_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _PKG_DIR.parent
for _p in (str(_PKG_DIR), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from .__main__ import main  # noqa: E402,F401

__all__ = ["main"]
