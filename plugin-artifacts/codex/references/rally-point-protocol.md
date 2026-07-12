<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Rally Point — orchestrator presence/phase protocol (Stage 1)

The per-app shared channel carries what concurrent build-loop sessions
(Claude **and** Codex, any checkout) are doing and how the app just changed.
Resolve it with `scripts/rally_point/discovery_bridge.py` before writing:
native `agent-rally-point` installs use `~/.agent-rally-point/apps/<repo-id>/`;
the embedded build-loop fallback now uses the same root with a local
`<slug>` when native discovery is unavailable.
The orchestrator is one of three tool-agnostic capture mechanisms (the other
two: the git post-commit hook, and — Stage 2 — the enriched arch scan).
Checkpoint-poll only, no daemon (D3). Awareness only, never a lock (D4).

## Naming and legacy alias

**Rally Point is canonical.** New code, docs, commands, and tests use
`scripts/rally_point/` and `/agent-rally-point`.

`scripts/app_pulse/` is a deprecated alias boundary for one release cycle.
It contains routing shims only; every legacy module import forwards to the
matching `scripts/rally_point` module and emits a `DeprecationWarning`.
This includes `scripts.app_pulse.*`, `app_pulse.*`, and old bare imports from
callers that put `scripts/app_pulse` directly on `sys.path`. Do not add runtime
behavior there.

## Slug (D1, worktree-aware)

`scripts/rally_point/discovery_bridge.resolve(workdir=<repo>)` resolves the
canonical channel envelope used by writers and status readers. Its fallback
calls `channel_paths.app_slug(cwd=<repo>)`, which derives a worktree-aware slug
from `git rev-parse --git-common-dir` so the **main checkout and every
`git worktree` of the same repo share one channel** (the exact concurrent
scenario this targets — agent dispatches run under `isolation: "worktree"`).
Outside a git repo, fallback slug derivation delegates to memory's
`derive_slug_from_cwd`. `<slug>/workers` sub-component convention preserved.

## When the orchestrator writes

| Trigger | Action |
|---|---|
| Phase 1 preamble (once) | `build_loop_id.generate_or_resume(...)` before any Rally Point write, then `presence.write_presence(...)` — session_id, tool, model, run_id, app_slug, phase=`assess`, files_in_flight=`[]`; writers attach top-level `build_loop_id` + `build_loop_run_label` |
| Every phase-start | `post.post(kind="phase", payload={"phase": <name>}, ...)`, then `checkpoint.checkpoint_read(...)` |
| files owned for the phase change | refresh `presence.write_presence` with `files_in_flight` |
| Long-running task start and every 10 minutes | `agent_rally.py heartbeat --task-ref <claim-or-run-id> --progress <short update>` |
| Runtime-changing dogfood stage | create `scripts/dogfood_reload_checkpoint.py` checkpoint, post Rally handoff with checkpoint path/instructions, and wait for ACKs or recorded fallback before the next stage |
| Run complete | last presence write (the reaper clears it after the heartbeat window — no explicit unregister needed) |

All writes are fire-and-forget (atomic JSON tmp+rename / JSONL
O_APPEND, errors swallowed). The `revision` bump is the only locked
write (short-timeout `fcntl`, skip-on-timeout). None can block or fail a
host action.

Use `scripts/rally_point/post.py` for all new change records. It wraps the
revision bump and `changes.jsonl` append in the canonical order; do not call
`changes.append_change(...)` directly from new orchestration code unless the
caller has already handled the revision bump.

## Reading & surfacing

`checkpoint_read(channel_dir, session_id=..., my_files=[...])` returns:

```
{session_id, revision, changed, new_changes[], active_peers[],
 arch_digest|null, reactions[]}
```

When `changed` is true, surface a compact block:

- `reactions[].type == "reinstall"` (a peer `dep-change`) → "Peer changed
  a dependency manifest — reinstall before building."
- `reactions[].type == "re-baseline"` (`arch-scan-complete`) →
  "Architecture surface changed — re-baseline the scout cache."
- `reactions[].type == "soft-claim"` → "⚠️ Peer `<peer>` owns
  `<files>` (Phase `<phase>`). Coordinate — this is a WARNING, not a
  block (D4). Proceed with awareness."
- `active_peers[]` → one line per live peer: tool, `build_loop_run_label` when present, run_id, phase.

`arch_digest` is `null` in Stage 1 (the digest is published in Stage 2,
D2). The reader advances **only its own cursor**; it never locks the
change log and never blocks.

## Script-First Coordination Checks

During active coding, agents should not reread the full coordination markdown
on every check. Run the deterministic status script first:

```bash
python3 scripts/coordination_status.py \
  --workdir "$PWD" \
  --session-id "$SESSION_ID" \
  --owned-files .build-loop/coordination/current-owned-files.txt \
  --json
```

For high-overlap or long-running work, use the watcher:

```bash
python3 scripts/coordination_watch.py \
  --workdir "$PWD" \
  --session-id "$SESSION_ID" \
  --tool "$TOOL_NAME" \
  --task-ref "$TASK_REF" \
  --owned-files .build-loop/coordination/current-owned-files.txt \
  --interval 3 \
  --jsonl \
  --baseline-current
```

The scripts emit compact `clear | warn | blocked` state plus session-ack-aware
inbox unread count and task-heartbeat health. Presence answers whether a
session is live; task heartbeat answers whether it is still on the expected
task and when its next check-in is due. After acting on an inbox payload, call
`agent_rally.py ack-inbox` so old notes stop surfacing as new doorbells. AI
should read full coordination context only when the script reports `warn` or
`blocked`, a target inbox changes, a task heartbeat goes stale/wrong-task/blocked, a step
moves to `verification-pending`, or the next action is a commit, version bump,
archive/delete, or shared/high-risk file edit.

## Poll-after-post (pull-only enforcement)

Rally is **pull-only**: posting a `say`/`handoff` does NOT notify you when the
target answers — you learn only by **pulling** the room. A *pull* is one fetch
(`rally recent`/`rally room`); *polling* is pulling on an interval until ack or
timeout. `rally inject --handoff --require-ack` already enforces the live-session
path, but a handoff to a peer that is NOT an injectable session (idle, or
reading the room async) has no built-in wait — so the discipline below is
**enforced by a gate every agent runs**, not left to memory.

- **After posting a handoff, pull at least once.** While waiting on the ack,
  poll: `python3 scripts/rally_poll_gate.py wait --tool "$TOOL_NAME" --event-id <id> --timeout <s>`.
  On timeout (exit 4) the target is treated as unreachable → **fall to the
  handoff's declared `fallback_plan`**, do not block forever. Rally lets only the
  TARGET resolve a handoff, so record the fallback with `rally_poll_gate.py
  dispose --tool "$TOOL_NAME" --event-id <id>` (the `wait` timeout does this
  automatically) — otherwise the completion gate below would deadlock on a
  handoff you can never clear.
- **Completion gate (before-complete / Phase D Closeout):**
  `python3 scripts/rally_poll_gate.py check --tool "$TOOL_NAME" --workdir "$PWD"`.
  Exit 3 means you still own an UNRESOLVED handoff you authored — pull/resolve it
  (or fall to fallback) before declaring the run complete. Session-agnostic:
  catches the forgot-to-pull case whether or not the target was ever injectable.
  Fail-open on a rally outage (exit 0 + warning) so it never wedges a build.

This applies to every agent (Claude, Codex, …) — the gate is the app logic, so
no one has to remember to pull.

## Graceful absence

An absent channel/dir yields an empty envelope (`changed: false`,
empty lists, `arch_digest: null`), creates nothing implicitly, and
never errors — zero regression for repos that have never seen Rally Point.

## Non-goal guard

Records and the envelope carry structure/data-flow only. No
call-frequency / invocation-count field is ever written or surfaced
(asserted in `scripts/rally_point/test_changes.py` and
`test_checkpoint.py`).
