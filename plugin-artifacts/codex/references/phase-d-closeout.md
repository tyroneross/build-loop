<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase D: Closeout — full protocol

Extracted from `agents/build-orchestrator.md` §"Phase D: Closeout". The agent body keeps a tight summary + a pointer here. Load before running closeout.

## Summary

Closeout terminates live processes, reaps stale presence records, transactionally finalizes explicitly released run worktrees, archives the active coordination file, and posts a `run-closeout` phase record to the channel. SessionStart only reports stale candidates; it never owns deletion authority. Memory citation: `feedback_close_out_stops_the_watcher`. Constitution: `references/coordination-rules.md` §"Closeout hygiene".

If closeout or cleanup removes ephemeral project plans (`.build-loop/plan*.md`
or `.build-loop/plans/*.md`), archive each one first:

```bash
python3 scripts/archive_project_plan.py <plan> --workdir "$PWD" --remove-source --json
```

The archive target is `build-loop-memory/projects/<slug>/archive/plans/<date>/`.

Phase D runs by default at the end of every run, including when Phase 6 Learn is deferred. The only way to skip Phase D entirely is an explicit `closeout: false` in the dispatch envelope (used by debug-only runs); set this conservatively.

## Mandatory closeout sequence

Run after Phase 6 Learn if it ran; otherwise immediately after Review-G.

0. **Poll-gate — resolve your own open handoffs (pull-only enforcement)**: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rally_poll_gate.py check --tool "$TOOL_NAME" --workdir "$PWD"`. Rally is pull-only — posting a handoff never notifies you of the answer. Exit 3 = you still own an UNRESOLVED handoff you authored; **pull** the room and resolve it, or fall to its declared `fallback_plan`, before closing out — a run must not silently finish owing a peer an ack. To wait on a live target, poll with `rally_poll_gate.py wait --tool "$TOOL_NAME" --event-id <id> --timeout <s>` (exit 4 on timeout → falls to fallback and auto-records it). Rally lets only the TARGET resolve a handoff — when the target is unreachable, record your fallback with `rally_poll_gate.py dispose --tool "$TOOL_NAME" --event-id <id>` so the gate clears (otherwise an un-acked handoff would deadlock closeout forever). Fail-open on a rally outage (exit 0 + warning) — never wedges closeout. Applies to every agent (Claude, Codex, …). Protocol: `references/rally-point-protocol.md` §"Poll-after-post (pull-only enforcement)".
1. **Reap this session's presence**: `scripts/rally_point/lifecycle.reap_my_sessions(channel_dir, my_session_id)`. Deletes `<resolved-channel>/sessions/<my-session>.json`. Fire-and-forget — returns count reaped but the orchestrator never crashes on a permission/IO error.
2. **Reap stale peer presence (defense-in-depth)**: `scripts/rally_point/lifecycle.reap_stale_sessions(channel_dir, stale_after_seconds=3600)` removes any presence file whose mtime is older than 1 hour. Independent of `presence.reap_stale`'s 15-min heartbeat window.
3. **Stop coordination watchers**: SIGTERM any `coordination_watch.py --interval N` background processes started during this run. Track PIDs in `state.json.runs[N].watcherPids[]`; iterate + `os.kill(pid, SIGTERM)`. Errors swallowed.
3a. **Relinquish the leadership lease (G1)**: if this session holds the lead (`leadership.read_lead(channel_dir)["lead"]["session_id"] == my_session_id`), call `scripts/rally_point/leadership.relinquish_lead(channel_dir, session_id=my_session_id, app_slug=...)`. Frees the lead so the next run claims immediately rather than waiting for lease expiry; posts a `lead-relinquish` record. Fire-and-forget — a failed relinquish never blocks closeout (the lease expires on its own). Skip when a peer holds the lead.
4. **Collapse branches and worktrees** (merge winner first, then collapse):
   - For solo-on-main runs the work is already on `main`; nothing to merge. For multi-worktree runs, merge the winning/validated line(s) to `main` via the normal single-writer commit flow **before** calling collapse — collapse never merges, it only cleans up.
   - **Pre-merge conflict gate (advisory, warn-first):** before merging any worktree line to `main`, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rally_merge_gate.py check --tool "$TOOL_NAME" --workdir "$PWD" --base main`. Isolated worktrees already deconflict the *work*; this catches the *merge*: exit 3 = another agent holds an ACTIVE CLAIM on files you're about to merge — pull the room and sequence (let their claim/merge land first, or coordinate via `rally say`). Advisory only — does not block; fail-open on a rally/git outage (exit 0 + warning). Skip for solo-on-main runs (nothing to merge).
   - Confirm positive owner release before mutation: the worker/integrator has completed the handoff and no terminal is expected to continue using that worktree. Stop hooks and missing Rally/CWD evidence are not owner release.
   - From the integrating/primary worktree, run once per merged branch: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/collapse_run.py --workdir "$PWD" --run-id <exact-run-id> --branch <exact-branch> --strict --merged-only --owner-released --release-source phase-d-integrator --json`
   - The script creates and verifies a branch-specific bundle, writes a schema-v1 prepared receipt under `.build-loop/branch-closeout/`, projects the attempt to `createdRefs`/`branch_closeout`, rechecks lock/dirtiness/live-CWD/path/merge/OID evidence, removes the clean worktree without force, then rechecks the expected OID and uses Git's checked-out-aware safe branch deletion.
   - The JSON result (`{run_id, branch, bundle_path, bundle_verified, receipt_path, receipt_status, strict_success, deleted[], already_closed[], retained[], errors[]}`) feeds the run report's `## Branch hygiene` block.
   - `strict_success:true`, `bundle_verified:true`, a terminal receipt, and `errors:[]` are required before branch hygiene is called complete. Otherwise record branch hygiene as partial/failed and retain the ref/worktree; do not resolve the integration handoff as clean. Direct Rally resolve commands remain outside the receipt gate (`BUILDLOOP-COORD-001`).
   - An unmerged branch is retained. Use the explicit non-strict API only for legacy/operator-directed keep/surface disposition; background and integrator paths are merged-only.
5. **Archive the coordination file**: `mv .build-loop/coordination/<this-coord-file>.md .build-loop/coordination/archived/`. Preserves the durable record while clearing the active queue. Skip when no coord file was used or it was already archived; `state.json.runs[N].coordinationFile` tracks the path.
6. **Optional changes.jsonl rotation**: `scripts/rally_point/lifecycle.rotate_changes_log(channel_dir, max_mb=1, max_entries=500)`. Rotates when EITHER threshold is exceeded; returns the rotated-to path or `None`. Logged in `state.json.runs[N].channelRotated`.
7. **Final post (executable gate)**: `scripts/rally_point/post.post(channel_dir=..., kind="phase", run_id=<exact-run-id>, workdir=Path("$PWD"), payload={"phase": "run-closeout", "session_id": <id>, "coord_file": <archived-path>, "outcomes": {...}})`. Before any coordination write, `branch_closeout_gate.py` verifies the exact run ledger, receipt, bundle/OID, absent closed ref/path (or explicit retained disposition), and rejects the post on incomplete evidence. Solo-on-main runs with no attributable refs pass without a receipt. A successful post signals to peers + future readers that this run is done.
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
created N · closed M · retained R: [<branch-name>, ...]
· bundle-verified: yes|no · receipt: <path/status> · strict-success: yes|no
```

When collapse reports `retained` or `surfaced_unmerged` entries, surface them and require an explicit later disposition. When a run created zero refs (typical solo-on-main run), emit one line: `Branch hygiene: clean — no run-created branches/worktrees; on main.` Do not derive the run-wide `closeout_status` solely from branch cleanup; `runs[N].branch_closeout` and its receipt are the branch-hygiene projection.
