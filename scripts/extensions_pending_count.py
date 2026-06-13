#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""extensions_pending_count.py — count pending drafts (for the session-start nudge). Fail-open to 0."""
from __future__ import annotations
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
from extensions_paths import pending_dir  # noqa: E402

def pending_count() -> int:
    d = pending_dir() / "skills"
    try:
        return sum(1 for p in d.iterdir() if p.is_dir() and (p / "SKILL.md").exists()) if d.is_dir() else 0
    except OSError:
        return 0

if __name__ == "__main__":
    print(pending_count())
