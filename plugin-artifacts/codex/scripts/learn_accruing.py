#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""learn_accruing.py — EC-01: Phase-6 ``accruing`` fires transcript mining.

When Phase 6 Learn returns ``accruing`` (``runs[] < 3``) the run used to idle — the
correct response is to MINE toward the n=3 threshold, not treat accruing as terminal.
This is the non-gating bridge: on accruing, ``fire`` runs the existing transcript
pattern miner (stdlib, no LLM, no network) and records a pointer under
``.build-loop/learn/pending/``. The NEXT run's Phase 6 calls ``read_pending`` before
applying the n<3 gate, so accrued signal is available earlier.

Fire-and-continue: every path is best-effort and returns a summary dict. It NEVER
raises and NEVER gates the run (the miner failing must not fail Learn).

Reuses ``scripts/transcript_pattern_miner`` (``--out-dir``) rather than
re-implementing mining — one source of truth (KISS/DRY).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _pending_dir(workdir: Path) -> Path:
    return workdir / ".build-loop" / "learn" / "pending"


def fire(workdir: Path, days: int = 7) -> dict:
    """Run the miner into the pending dir. Best-effort, non-gating."""
    pending = _pending_dir(workdir)
    summary: dict = {"fired": False, "pending_dir": str(pending), "candidates": 0}
    try:
        pending.mkdir(parents=True, exist_ok=True)
        # The miner writes .candidates.json into its --out-dir.
        proc = subprocess.run(
            [sys.executable, "-m", "transcript_pattern_miner",
             "--days", str(days), "--out-dir", str(pending)],
            cwd=str(HERE), capture_output=True, text=True, timeout=120,
        )
        summary["fired"] = True
        summary["rc"] = proc.returncode
        cand = pending / ".candidates.json"
        if cand.exists():
            try:
                data = json.loads(cand.read_text())
                summary["candidates"] = len(data) if isinstance(data, list) else len(data.get("candidates", []))
            except Exception:
                pass
        # Stamp a manifest so read_pending can report freshness.
        (pending / "manifest.json").write_text(json.dumps({
            "fired_at": datetime.now(timezone.utc).isoformat(),
            "days": days,
            "candidates": summary["candidates"],
        }, indent=2))
    except Exception as exc:  # noqa: BLE001 — never gate Learn
        summary["error"] = repr(exc)
    return summary


def read_pending(workdir: Path) -> dict:
    """Return accrued candidates for the next run's Phase 6, before the n<3 gate."""
    pending = _pending_dir(workdir)
    out: dict = {"exists": False, "candidates": [], "manifest": None}
    try:
        cand = pending / ".candidates.json"
        if cand.exists():
            data = json.loads(cand.read_text())
            out["candidates"] = data if isinstance(data, list) else data.get("candidates", [])
            out["exists"] = True
        man = pending / "manifest.json"
        if man.exists():
            out["manifest"] = json.loads(man.read_text())
    except Exception as exc:  # noqa: BLE001
        out["error"] = repr(exc)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="learn_accruing", description=__doc__)
    ap.add_argument("mode", choices=["fire", "read"], help="fire the miner or read pending")
    ap.add_argument("--workdir", default=".", help="build-loop project workdir")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    workdir = Path(args.workdir).expanduser().resolve()
    result = fire(workdir, days=args.days) if args.mode == "fire" else read_pending(workdir)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
