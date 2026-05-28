#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Compatibility wrapper for pruning Codex plugin cache versions."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prune_plugin_cache import main as prune_main  # noqa: E402


def main() -> int:
    args = sys.argv[1:]
    if "--host" not in args:
        args = ["--host", "codex", *args]
    sys.argv = [str(Path(__file__).with_name("prune_plugin_cache.py")), *args]
    return prune_main()


if __name__ == "__main__":
    sys.exit(main())
