# Fix: Stop hook `stop_finalize.sh` reports "returned empty" on every session stop

Date: 2026-06-25
Branch: `fix-stop-hook-run-entry`
Mode: build-loop debug/fix (Option B)

## Goal
Stop the recurring `write_run_entry/__main__.py returned empty (check stderr for details)`
status emitted by `scripts/hooks/stop_finalize.sh` on every Claude `Stop` event, and
have a session stop record an honest `runs[]` lifecycle marker (or cleanly skip with an
accurate status) — never a false error, never fabricated data.

## Confirmed repro (verified)
`stop_finalize.sh:136` invokes the run-entry writer with only `--workdir`:

    RUN_ID=$(python3 "$WRITE_ENTRY" --workdir "$WORKDIR" 2>/dev/null) || RUN_ID=""

`scripts/write_run_entry/__main__.py:66-67` declares `--goal` and `--outcome` as
`required=True`. argparse therefore exits 1 with
`error: the following arguments are required: --goal, --outcome`; `2>/dev/null`
swallows stderr; `RUN_ID` is empty; the hook reports the "returned empty" status
(`stop_finalize.sh:140`). Reproduced live both with stderr visible (exit 1) and
exactly as the hook calls it (RUN_ID empty).

## Decision: Option B (hook-side), evidence-grounded
**Do not call the rich orchestrator-only writer from the Stop hook.** Use the honest
minimal inline append the hook's own else-branch already implements.

### Why B over A — runs[]-consumption evidence
1. `recurring-pattern-detector` consumes the RICH shape (`phases[]` incl.
   `root_cause_layer`, `diagnosticCommands`, `filesTouched`, `manualInterventions`,
   `goal`). A Stop hook cannot know any of that truthfully. Option A would have the
   rich writer derive goal/outcome — but a Stop hook still has no real per-phase
   `root_cause_layer` / diagnostics / outcome to give it, so A would write a
   thin-but-rich-shaped entry that competes with the real recorder.
2. The modern Stop-time recorder already exists and is correct:
   `scripts/stop_closeout.py` (backed by `hooks/closeout.sh`, registered as the 5th
   Stop hook in `hooks/hooks.json`, tested by `test_stop_closeout.py`). It derives
   `goal` (from `goal.md`/run_label) and `outcome` (phase -> done|partial|blocked),
   writes via `append_run` with `source: append_run`, refuses to clobber a richer
   Review-G record, carries the honest floor `auditor_status:
   not-run:parent-must-dispatch`, and runs the judgment gate. This is exactly what
   `stop_finalize.sh`'s broken call was reaching for — already solved, honestly.
3. The orchestrator writes the RICH entry at Review-G/Report (`write_run_entry`
   with real `--goal`/`--outcome`/`--scope build` + auditor verdict). Reserving the
   rich writer for Review-G matches the design comment intent and avoids duplicating
   the goal/outcome-derivation logic `stop_closeout.py` already owns. KISS/DRY.

Conclusion: `stop_finalize.sh`'s remaining unique value is its Step 5 F-criteria
scorecard advisory and the rally self-release (line 264). Its run-entry job is
superseded by `closeout.sh`/`stop_closeout.py`. So drop the rich-writer call; keep an
honest minimal lifecycle marker for the `phase==report` path it gates on.

## Change (smallest correct)
`scripts/hooks/stop_finalize.sh` Step 4: replace the `if [ -f "$WRITE_ENTRY" ]`
rich-writer branch with the existing honest minimal inline append (the current
else-branch body), unconditionally. Drop the now-dead `$WRITE_ENTRY` resolution and
the false "returned empty" status string. The inline append is honest: it writes
`{run_id, date, session_id}` — a lifecycle boundary marker, no fabricated
goal/outcome/phases. Idempotency (Step 3) still guards double-append for the same
session.

## Tests
- Keep `scripts/test_write_run_entry.py` green (untouched writer -> green by construction).
- ADD a regression test to `scripts/hooks/test_hooks.sh` that drives the write path
  (the gap that let this drift silently): a `phase==report` state with a NEW session_id
  must (a) emit valid JSON, (b) NOT contain "returned empty", (c) append an honest
  `{run_id, date, session_id}` entry for that session to `runs[]`.
- Broad regression: run the writer test, the hook test, and `test_stop_closeout.py`.

## Risks / blast radius
- Single shell file + one test file. No Python source touched. No public contract change.
- runs[] shape from the Stop path is unchanged vs the hook's existing else-branch
  behavior (the minimal marker), so consumers are unaffected.
- Double-record concern: `stop_finalize` (key: run-<date>-<sess8>) and
  `stop_closeout` (key: execution.build_loop_id) use different run_ids, so on a
  `phase==report` run both may append a marker. This is pre-existing behavior (the
  else-branch already does this when the writer is absent) and `stop_closeout` defers
  to richer Review-G records; not introduced by this fix. Noted, not expanded.

## Reversibility
Branch-isolated; `git checkout main` reverts. NO push.
