#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""CLI for the tier-1 correction + lesson scanner.

Writes raw Candidate records to `.build-loop/pending-lessons/<ts>-<hash>.md`
with YAML frontmatter so downstream consumers (SessionStart surface, host-
agent refinement, optional Ollama distill) can read them uniformly.

Hook contract — never fails the session:
  - Any error logs and exits 0
  - `.build-loop/.no-capture` (per-session opt-out) → clean exit 0
  - Wall-clock budget (`SCAN_CORRECTIONS_BUDGET_S`, default 10s) governs the
    write loop; `SCAN_CORRECTIONS_MAX_BYTES` (default 100 MB) caps detection —
    transcripts larger than the limit are skipped with a log message.

Usage:
  python3 -m scan_corrections --workdir <repo> --transcript $CLAUDE_TRANSCRIPT_PATH
  python3 -m scan_corrections --workdir <repo> --text-turns-file <path>  # testing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # scripts/ on path

from scan_corrections.detect import Candidate, detect_candidates  # noqa: E402

DEFAULT_BUDGET_S = 10
PENDING_DIRNAME = "pending-lessons"
MAX_TRANSCRIPT_BYTES = int(os.environ.get("SCAN_CORRECTIONS_MAX_BYTES", str(100 * 1024 * 1024)))


def log(msg: str) -> None:
    print(f"[scan_corrections] {msg}", file=sys.stderr)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts_for_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _emit_frontmatter(cand: Candidate, *, source: str) -> str:
    """Render a Candidate as YAML frontmatter + body. No PyYAML dep."""
    # title: a readable excerpt shown by context_bootstrap.py's queue surfacer,
    # which reads title:/name: instead of the timestamp-hash filename.
    title_excerpt = cand.quote[:80].replace("\n", " ").strip()
    lines = [
        "---",
        f"id: {cand.id_hash}",
        f"kind: {cand.kind}",
        f"signal_type: {cand.signal_type}",
        f"confidence: {cand.confidence}",
        f"scope: {cand.scope}",
        f"turn_index: {cand.turn_index}",
        f"captured_chars: {cand.captured_chars}",
        f"tier: 1-deterministic",
        f"source: {source}",
        f"title: {title_excerpt!r}",
        f"captured_at: {_iso_now()}",
    ]
    if cand.extras:
        lines.append("extras:")
        for k, v in cand.extras.items():
            lines.append(f"  {k}: {json.dumps(v)}")
    lines.append("---")
    lines.append("")
    lines.append("## Quote")
    lines.append("")
    lines.append("> " + cand.quote.replace("\n", "\n> "))
    lines.append("")
    lines.append("## Context (±200 chars)")
    lines.append("")
    lines.append("```")
    lines.append(cand.context)
    lines.append("```")
    lines.append("")
    lines.append("## Next action for the host agent")
    lines.append("")
    lines.append(
        "Refine this candidate into a durable record. Decide kind (decision|lesson|feedback) "
        "and scope (project|global). If aligned with build-loop-memory contract, promote via "
        "`scripts/memory_writer.py` or `scripts/write_decision/__main__.py`. If not actionable, "
        "delete this file."
    )
    return "\n".join(lines) + "\n"


def _write_candidate(pending_dir: Path, cand: Candidate, *, source: str) -> Path | None:
    """Atomic write of one Candidate. Returns the written path or None on duplicate."""
    pending_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{_ts_for_filename()}-{cand.kind}-{cand.id_hash}.md"
    target = pending_dir / fname

    # Dedup: scan existing files for the same id_hash so re-runs don't proliferate.
    for existing in pending_dir.glob(f"*-{cand.id_hash}.md"):
        # Same hash already written; skip.
        return None
    # Also dedup against promoted/archived siblings if present.
    for sub in ("promoted", "discarded"):
        d = pending_dir / sub
        if d.is_dir():
            for existing in d.glob(f"*-{cand.id_hash}.md"):
                return None

    body = _emit_frontmatter(cand, source=source)
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, target)
    return target


def _load_text_turns(path: Path) -> list[str]:
    """Testing surface — one user turn per line, or a JSON array."""
    raw = path.read_text(encoding="utf-8")
    raw = raw.strip()
    if raw.startswith("["):
        return json.loads(raw)
    return [ln for ln in raw.splitlines() if ln.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scan_corrections", description=__doc__)
    ap.add_argument("--workdir", default=".", help="Project root (resolves .build-loop/)")
    ap.add_argument("--transcript", default=None, help="Path to Claude Code transcript JSONL")
    ap.add_argument("--text-turns-file", default=None, help="Testing: file of user turns (one per line or JSON array)")
    ap.add_argument("--source", default="stop-hook", help="Provenance tag for written candidates")
    ap.add_argument("--print-json", action="store_true", help="Also print Candidates to stdout as JSON")
    ap.add_argument("--strict", action="store_true", help="Exit non-zero on error (CI)")
    args = ap.parse_args(argv)

    start = time.monotonic()
    budget_s = int(os.environ.get("SCAN_CORRECTIONS_BUDGET_S", DEFAULT_BUDGET_S))

    workdir = Path(args.workdir).resolve()
    no_capture = workdir / ".build-loop" / ".no-capture"
    if no_capture.exists():
        log("opt-out via .build-loop/.no-capture; skipping")
        return 0

    pending_dir = workdir / ".build-loop" / PENDING_DIRNAME

    try:
        if args.text_turns_file:
            turns = _load_text_turns(Path(args.text_turns_file))
            candidates = detect_candidates(text_turns=turns)
        elif args.transcript:
            transcript_path = Path(args.transcript)
            try:
                size = transcript_path.stat().st_size
            except OSError:
                size = 0
            if size > MAX_TRANSCRIPT_BYTES:
                log(
                    f"transcript too large ({size} bytes > {MAX_TRANSCRIPT_BYTES} limit); "
                    "skipping detection. Set SCAN_CORRECTIONS_MAX_BYTES to override."
                )
                return 0
            candidates = detect_candidates(transcript_path)
        else:
            log("no --transcript and no --text-turns-file; nothing to do")
            return 0
    except Exception as e:  # noqa: BLE001
        log(f"detection error (swallowed): {e}")
        return 1 if args.strict else 0

    written: list[str] = []
    skipped_dup = 0
    for cand in candidates:
        if time.monotonic() - start > budget_s:
            log(f"budget exceeded ({budget_s}s); bailing partial (written={len(written)})")
            break
        p = _write_candidate(pending_dir, cand, source=args.source)
        if p is None:
            skipped_dup += 1
            continue
        written.append(str(p.relative_to(workdir)))

    log(
        f"done — candidates={len(candidates)} written={len(written)} "
        f"dup_skipped={skipped_dup} pending_dir={pending_dir.relative_to(workdir)}"
    )

    if args.print_json:
        payload = {
            "candidates": [c.to_dict() for c in candidates],
            "written": written,
            "skipped_dup": skipped_dup,
            "pending_dir": str(pending_dir),
        }
        print(json.dumps(payload, indent=2))

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"unexpected error (swallowed for hook safety): {e}")
        sys.exit(0)
