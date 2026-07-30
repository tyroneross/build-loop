#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""transcript_pattern_miner.__main__ — CLI entry point.

Mines local Claude Code session transcripts for recurring patterns that may be
worth promoting to skills, agents, hooks, or feedback notes. Pure stdlib + regex.
No network calls. No LLM calls. Output is a markdown report and a candidates JSON.

Verified data layout (2026-05-01):
  Sessions live at  ~/.claude/projects/<project-slug>/<session-uuid>.jsonl
  (NOT in a `sessions/` subdirectory.) Each line is a JSON object with a
  `type` field. Relevant types:

    user         — message from user OR carrying tool_result blocks back to model
                   message.role == "user"
                   message.content: str | list[ {type: "text"|"tool_result", ...} ]
                   tool_result.content: str | list[ {type: "text", text: str} ]
    assistant    — model output
                   message.content: list[ {type: "text"|"thinking"|"tool_use", ...} ]
                   tool_use: {name, input}
    system       — tool/CLI system events
    attachment, file-history-snapshot, queue-operation, last-prompt,
    permission-mode  — meta-events, mostly ignored by this miner

  Common top-level fields on user/assistant: timestamp (ISO8601), cwd, sessionId,
  uuid, parentUuid, gitBranch, version.

This miner reads JSONL line-by-line, never holds a whole session in memory beyond
small extracted aggregates, and writes only to ~/.build-loop/transcript-patterns/.

Categories:
  1. Recurring user corrections   (heuristic + n-gram clustering, 3+ occurrences)
  2. Repeated tool sequences      (length 3-6 sequences, 3+ sessions)
  3. Cross-project file patterns  (files touched in 3+ projects, or churn within one)
  4. Manual command rituals       (Bash invocations normalized, 5+ across sessions)
  5. Secrets observed             (rotation tracker — full values surfaced)

CLI:
  python3 transcript-pattern-miner.py            # last 7 days
  python3 transcript-pattern-miner.py --days 30
  python3 transcript-pattern-miner.py --all
  python3 transcript-pattern-miner.py --force    # ignore .processed.json cache

Privacy (single-user context, see feedback_single_user_transcripts.md):
  Quotes are capped at 300 chars (raised from 80; thin previews under-judged
  intent). Secrets are surfaced in full so the user can rotate them. Output
  remains local-only — no network egress, no auto-publish. Do NOT adapt this
  miner for multi-user environments without restoring the privacy caps.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from .io_cache import file_signature, load_processed, save_processed
from .session import process_session_file
from .categories import (
    cluster_corrections,
    cross_project_files,
    manual_command_rituals,
    repeated_tool_sequences,
    test_pattern_outcomes,
)
from .secrets_scan import secrets_observed
from .report import append_outcomes_jsonl, build_candidates, render_report

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

HOME = Path.home()
SESSIONS_DIR = HOME / ".claude" / "projects" / "-Users-tyroneross"
OUT_DIR = HOME / ".build-loop" / "transcript-patterns"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--days", type=int, default=7, help="Window in days (default 7)")
    p.add_argument("--all", action="store_true", help="Scan full history")
    p.add_argument("--force", action="store_true", help="Reprocess files in cache")
    p.add_argument(
        "--sessions-dir",
        default=str(SESSIONS_DIR),
        help="Override sessions root (for testing)",
    )
    p.add_argument(
        "--out-dir",
        default=str(OUT_DIR),
        help="Override output dir (for testing)",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    args = parse_args(argv)
    sessions_dir = Path(args.sessions_dir).expanduser()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    processed_path = out_dir / ".processed.json"
    candidates_path = out_dir / ".candidates.json"

    if not sessions_dir.exists():
        print(f"sessions dir not found: {sessions_dir}", file=sys.stderr)
        return 2

    now = dt.datetime.now(dt.timezone.utc)
    if args.all:
        cutoff: dt.datetime | None = None
        window_label = "all history"
    else:
        cutoff = now - dt.timedelta(days=args.days)
        window_label = f"last {args.days} day(s)"

    processed = {} if args.force else load_processed(processed_path)

    jsonl_files = sorted(sessions_dir.glob("*.jsonl"))
    aggs = []
    sessions_scanned = 0
    sessions_in_window = 0

    for path in jsonl_files:
        sig = file_signature(path)
        st = path.stat()
        mtime = dt.datetime.fromtimestamp(st.st_mtime, tz=dt.timezone.utc)
        if cutoff is not None and mtime < cutoff:
            processed[str(path)] = sig
            continue
        sessions_scanned += 1
        agg = process_session_file(path, cutoff)
        if agg is None:
            processed[str(path)] = sig
            continue
        sessions_in_window += 1
        aggs.append(agg)
        processed[str(path)] = sig

    save_processed(processed_path, processed)

    corrections = cluster_corrections(aggs)
    sequences = repeated_tool_sequences(aggs)
    cross_files, churn_files = cross_project_files(aggs)
    rituals = manual_command_rituals(aggs)
    secrets = secrets_observed(aggs)
    per_invocation, test_table = test_pattern_outcomes(aggs)
    outcomes_jsonl = out_dir / ".outcomes.jsonl"
    rows_written = append_outcomes_jsonl(outcomes_jsonl, per_invocation)

    report = render_report(
        window_label=window_label,
        sessions_scanned=sessions_scanned,
        sessions_in_window=sessions_in_window,
        corrections=corrections,
        sequences=sequences,
        cross_files=cross_files,
        churn_files=churn_files,
        rituals=rituals,
        secrets=secrets,
        test_table=test_table,
        test_invocation_count=len(per_invocation),
        test_outcomes_jsonl_path=outcomes_jsonl,
        generated_at=now,
    )

    today = now.date().isoformat()
    report_path = out_dir / f"{today}.md"
    report_path.write_text(report)

    candidates = build_candidates(corrections, sequences, rituals, cross_files)
    candidates_path.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "window_label": window_label,
        "candidates": candidates,
    }, indent=2))

    # Brief stdout summary so caller (cron, agent, human) sees something useful.
    print(f"transcript-pattern-miner: window={window_label}")
    print(f"  sessions scanned: {sessions_scanned} (in window: {sessions_in_window})")
    print(f"  correction clusters: {len(corrections)}")
    print(f"  repeated sequences: {len(sequences)}")
    print(f"  cross-project files: {len(cross_files)}, churn files: {len(churn_files)}")
    print(f"  command rituals: {len(rituals)}")
    print(f"  distinct secrets observed: {len(secrets)}")
    print(f"  test invocations detected: {len(per_invocation)} ({rows_written} rows appended to .outcomes.jsonl)")
    if test_table:
        for r in test_table[:3]:
            print(f"    {r['category']}: count={r['count']} POS={r['POSITIVE']} MIX={r['MIXED']} REW={r['REWORK']} NS={r['NO_SIGNAL']}")
    print(f"  report: {report_path}")
    print(f"  candidates: {candidates_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
