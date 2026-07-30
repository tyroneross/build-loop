#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""transcript-pattern-miner.py — thin shim; implementation lives in transcript_pattern_miner/.

Preserved entry point for cron jobs, LaunchAgent plists, and agent invocations.
CLI contract is identical to the original monolithic script.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from transcript_pattern_miner.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
