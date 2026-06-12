<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase D: Closeout — full protocol

Extracted from `agents/build-orchestrator.md` §"Phase D: Closeout". The agent body keeps a tight summary + a pointer here. Load before running closeout.

## Summary

Closeout terminates live processes, reaps stale presence records, force-removes dispatch worktrees, archives the active coordination file, and posts a `run-closeout` phase record to the channel. This is automated, not operator-discipline-dependent. Skipping it leaves ghost-peer signals that the next run has to debug. Memory citation: `feedback_close_out_stops_the_watcher`. Constitution: `references/coordination-rules.md` §"Closeout hygiene".

If closeout or cleanup removes ephemeral project plans (`.build-loop/plan*.md`
or `.build-loop/plans/*.md`), archive each one first:

```bash
python3 scripts/archive_project_plan.py <plan> --workdir "$PWD" --remove-source --json
```

The archive target is `build-loop-memory/projects/<slug>/archive/plans/<date>/`.

Phase D runs by default at the end of every run, including when Phase 6 Learn is deferred. The only way to skip Phase D entirely is an explicit `closeout: false` in the dispatch envelope (used by debug-only runs); set this conservatively.

## Mandatory closeout sequence

Run after Phase 6 Learn if it ran; otherwise immediately after Review-G.

0. **Poll-gate — resolve your own open handoffs (pull-only enforcement)**: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rally_poll_gate.py check --tool "$TOOL_NAME" --workdir "$PWD"`. Rally is pull-only — posting a handoff never notifies you of the answer. Exit 3 = you still own an UNRESOLVED handoff you authored; **pull** the room and resolve it, or fall to its declared `fallback_plan`, before closing out — a run must not silently finish owing a peer an ack. To wait on a live target, poll with `rally_poll_gate.py wait --tool "$TOOL_NAME" --event-id <id> --timeout <s>` (exit 4 on timeout → fall to fallback). Fail-open on a rally outage (exit 0 + warning) — never wedges closeout. Applies to every agent (Claude, Codex, …). Protocol: `references/rally-point-protocol.md` §"Poll-after-post (pull-only enforcement)".
1. **Reap this session's presence**: `scripts/rally_point/lifecycle.reap_my_sessions(channel_dir, my_session_id)`. Deletes `<resolved-channel>/sessions/<my-session>.json`. Fire-and-forget — returns count reaped but the orchestrator never crashes on a permission/IO error.
2. **Reap stale peer presence (defense-in-depth)**: `scripts/rally_point/lifecycle.reap_stale_sessions(channel_dir, stale_after_seconds=3600)` removes any presence file whose mtime is older than 1 hour. Independent of `presence.reap_stale`'s 15-min heartbeat window.
3. **Stop coordination watchers**: SIGTERM any `coordination_watch.py --interval N` background processes started during this run. Track PIDs in `state.json.runs[N].watcherPids[]`; iterate + `os.kill(pid, SIGTERM)`. Errors swallowed.
3a. **Relinquish the leadership lease (G1)**: if this session holds the lead (`leadership.read_lead(channel_dir)["lead"]["session_id"] == my_session_id`), call `scripts/rally_point/leadership.relinquish_lead(channel_dir, session_id=my_session_id, app_slug=...)`. Frees the lead so the next run claims immediately rather than waiting for lease expiry; posts a `lead-relinquish` record. Fire-and-forget — a failed relinquish never blocks closeout (the lease expires on its own). Skip when a peer holds the lead.
4. **Collapse branches and worktrees** (merge winner first, then collapse):
   - For solo-on-main runs the work is already on `main`; nothing to merge. For multi-worktree runs, merge the winning/validated line(s) to `main` via the normal single-writer commit flow **before** calling collapse — collapse never merges, it only cleans up.
   - Then run: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/collapse_run.py --workdir "$PWD" --run-id latest --json`
   - The script bundles all run-created refs to `.build-loop/bundles/` first (reversibility), then per ref: MERGED → deletes the branch + removes worktree folder; UNMERGED+`review_hold` → keeps the branch ref, removes the worktree folder (→ `kept_for_review`); UNMERGED+no-hold → keeps the branch ref, removes the worktree folder (→ `surfaced_unmerged`).
   - The JSON result (`{run_id, bundle_path, deleted[], kept_for_review[], surfaced_unmerged[], errors[], dry_run}`) feeds the run report's `## Branch hygiene` block (see below).
   - Fail-soft: errors in `errors[]` are logged; do not block closeout on a failed remove.
5. **Archive the coordination file**: `mv .build-loop/coordination/<this-coord-file>.md .build-loop/coordination/archived/`. Preserves the durable record while clearing the active queue. Skip when no coord file was used or it was already archived; `state.json.runs[N].coordinationFile` tracks the path.
6. **Optional changes.jsonl rotation**: `scripts/rally_point/lifecycle.rotate_changes_log(channel_dir, max_mb=1, max_entries=500)`. Rotates when EITHER threshold is exceeded; returns the rotated-to path or `None`. Logged in `state.json.runs[N].channelRotated`.
7. **Final post**: `scripts/rally_point/post.post(channel_dir=..., kind="phase", payload={"phase": "run-closeout", "session_id": <id>, "coord_file": <archived-path>, "outcomes": {...}})`. Signals to peers + future readers that this run is done; readers know to skip its presence/changes when scoping new work.
8. **State tracking**: write `state.json.runs[N].closeout_status` ∈ {`completed`, `partial`, `failed`} with per-step outcomes. The run report (Review-G) includes a closeout summary line; future-session pattern-miners and Phase 6 Learn use the per-step outcomes to detect chronic closeout failures.
9. **Release briefed push-hold**: run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/push_hold.py --release-if-briefed --reason "run closed, no blocking findings" --json`. This command is safe to run unconditionally — it is a no-op when no marker is present (`noop_absent`), when the source is `review-a` (`noop_review_a`, that path owns its own release), or when an unresolved blocking verdict still exists in state.json (`noop_blocking_verdict`). It only removes a marker with `source: orchestrator` or `source: manual` when `detect_blocking_verdict` returns None. This ensures a briefed do-not-push hold is never stranded past the end of its run.

## Push readiness guidance

When closeout needs to answer "is this ready to push?", use
`references/push-readiness-checklist.md`. It is advisory, not a blocking gate:
the checklist shapes the recommendation and evidence readout, while
`deployment_policy.py`, `autonomy_gate.py`, `push_hold.py`, protected-branch
rules, and explicit operator confirmation remain the authorities for whether a
push command may actually run.

## `## Branch hygiene` report block

Every run's final report carries this section, sourced from collapse_run.py's JSON output:

```
## Branch hygiene
created N · merged-to-main M (deleted) · kept-for-review R: [<branch-name>, ...]
· surfaced-unmerged U: [<branch-name>, ...] (ask keep/discard) · bundle: <path>
```

When collapse reported `surfaced_unmerged` entries, surface them in the report and ask the operator to keep or discard each. When a run created zero refs (typical solo-on-main run), emit one line: `Branch hygiene: clean — no run-created branches/worktrees; on main.`
