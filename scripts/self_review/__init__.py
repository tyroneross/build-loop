#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""self_review package — deterministic data-gatherer for build-loop periodic self-review.

Runnable as:
  python3 scripts/self_review/__main__.py --mode {light|deep} ...  (canonical)
  python3 -m self_review --mode {light|deep} ...   (with scripts/ on sys.path)
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the package directory is on sys.path so that flat sibling imports
# (``from gather import run_miner``) inside __main__.py resolve correctly
# when the package is imported via ``python3 -m self_review``.
_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))
