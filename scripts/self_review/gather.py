#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""gather.py — transcript mining helpers for the self_review package.

Invokes transcript-pattern-miner.py as a subprocess and returns the parsed
corrections / rituals / sequences dict.  No LLM calls, no network, stdlib only.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# scripts/ directory — one level above this package
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_candidates(candidates_path: Path, errors: list[str]) -> list[dict[str, Any]]:
    """Parse candidates JSON from the miner's output file."""
    if not candidates_path.exists():
        return []
    try:
        data = json.loads(candidates_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(f"could not parse miner candidates: {exc}")
        return []
    return data.get("candidates") or []


def _invoke_miner(
    miner: Path,
    window_days: int,
    tmp_out: Path,
    errors: list[str],
) -> subprocess.CompletedProcess | None:
    """Run the miner subprocess; return result or None on failure."""
    try:
        return subprocess.run(
            [
                sys.executable,
                str(miner),
                "--days", str(window_days),
                "--out-dir", str(tmp_out),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        errors.append(f"miner subprocess error: {exc}")
    except subprocess.TimeoutExpired:
        errors.append("miner timed out after 120s")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"miner unexpected error: {exc}")
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_miner(
    workdir: Path,
    window_days: int,
    errors: list[str],
) -> dict[str, Any]:
    """Invoke transcript-pattern-miner.py as a subprocess; parse its output.

    Returns dict with keys: corrections, rituals, sequences.
    Fail-soft: any subprocess error is recorded in errors[] and empty results returned.
    """
    miner = _SCRIPTS_DIR / "transcript-pattern-miner.py"
    empty: dict[str, Any] = {"corrections": [], "rituals": [], "sequences": []}

    if not miner.exists():
        errors.append(f"miner absent: {miner}")
        return empty

    tmp_out = workdir / ".build-loop" / "_self_review_miner_tmp"
    tmp_out.mkdir(parents=True, exist_ok=True)

    result = _invoke_miner(miner, window_days, tmp_out, errors)
    if result is None:
        return empty

    if result.returncode not in (0, 2):
        errors.append(
            f"miner exited {result.returncode}: "
            + (result.stderr or result.stdout or "")[:400]
        )
        return empty

    candidates = _read_candidates(tmp_out / ".candidates.json", errors)
    corrections = [c for c in candidates if c.get("shape") == "user_correction_cluster"]
    rituals = [c for c in candidates if c.get("shape") == "bash_ritual"]
    sequences = [c for c in candidates if c.get("shape") == "repeated_tool_sequence"]

    return {"corrections": corrections, "rituals": rituals, "sequences": sequences}
