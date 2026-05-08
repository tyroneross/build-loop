#!/usr/bin/env python3
"""M4 — Best-effort Stop hook annotation for crash detection.

When invoked from Claude Code's Stop hook, this script atomically annotates
.build-loop/state.json.execution with crashed_at + crash_signal IF the
execution block exists and phase != 'report'. Otherwise it exits 0
without touching the file.

This is the SECONDARY crash-detection signal. The primary signal is
heartbeat staleness checked at next-run start (resume_resolver.py). The
Stop hook is best-effort: a 529 mid-tool-stream may not flush this hook,
network drops and SIGKILL won't either. When it fires, it's a cleaner
signal than heartbeat staleness; when it doesn't, the heartbeat path
still works.

Per build-loop's hook design rules, this script:
  - exits 0 always (never blocks Stop)
  - writes nothing to stdout (Stop hooks must not produce stdout)
  - is fire-and-forget (timeout already enforced by hook config)
  - does no LLM work, no network, no expensive computation

Zero deps. Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def annotate_if_incomplete(workdir: Path, signal: str = "stop_hook") -> bool:
    """Annotate state.json.execution with crashed_at + crash_signal if incomplete.

    Returns True if an annotation was written, False otherwise. Never raises;
    swallows all I/O errors per Stop-hook discipline.
    """
    state_path = workdir / ".build-loop" / "state.json"
    if not state_path.exists():
        return False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(state, dict):
        return False
    execution = state.get("execution")
    if not isinstance(execution, dict):
        return False
    if execution.get("phase") == "report":
        # Clean exit — not a crash
        return False
    execution["crashed_at"] = _iso_utc()
    execution["crash_signal"] = signal
    state["execution"] = execution
    payload = (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        fd, tmp = tempfile.mkstemp(prefix=state_path.name + ".tmp.", dir=str(state_path.parent))
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, state_path)
    except OSError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Best-effort Stop hook crash annotation (M4).")
    p.add_argument("--workdir", required=True)
    p.add_argument("--mark-incomplete-as-crashed", action="store_true",
                   help="Annotate state.json.execution.crashed_at if phase != 'report' (default behavior)")
    p.add_argument("--signal", default="stop_hook", help="Value for crash_signal field (default: stop_hook)")
    args = p.parse_args(argv)
    try:
        annotate_if_incomplete(Path(args.workdir).resolve(), signal=args.signal)
    except Exception:
        # Stop-hook discipline: never raise out, never block Stop, always exit 0
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
