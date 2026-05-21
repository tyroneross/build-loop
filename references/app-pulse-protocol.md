# App Pulse — orchestrator presence/phase protocol (Stage 1)

The per-app shared channel (`~/.build-loop/apps/<slug>/`) carries what
concurrent build-loop sessions (Claude **and** Codex, any checkout) are
doing and how the app just changed. The orchestrator is one of three
tool-agnostic capture mechanisms (the other two: the git post-commit
hook, and — Stage 2 — the enriched arch scan). Checkpoint-poll only, no
daemon (D3). Awareness only, never a lock (D4).

## Slug (D1, worktree-aware)

`scripts/app_pulse/channel_paths.app_slug(cwd=<repo>)` resolves the slug
from `git rev-parse --git-common-dir` → canonical-repo basename, so the
**main checkout and every `git worktree` of the same repo share one
channel** (the exact concurrent scenario this targets — agent dispatches
run under `isolation: "worktree"`). Falls back to memory's
`derive_slug_from_cwd` only outside a git repo. `<slug>/workers`
sub-component convention preserved.

## When the orchestrator writes

| Trigger | Action |
|---|---|
| Phase 1 preamble (once) | `presence.write_presence(...)` — session_id, tool, model, run_id, app_slug, phase=`assess`, files_in_flight=`[]` |
| Every phase-start | `post.post(kind="phase", payload={"phase": <name>}, ...)`, then `checkpoint.checkpoint_read(...)` |
| files owned for the phase change | refresh `presence.write_presence` with `files_in_flight` |
| Run complete | last presence write (the reaper clears it after the heartbeat window — no explicit unregister needed) |

All writes are fire-and-forget (atomic JSON tmp+rename / JSONL
O_APPEND, errors swallowed). The `revision` bump is the only locked
write (short-timeout `fcntl`, skip-on-timeout). None can block or fail a
host action.

Use `scripts/app_pulse/post.py` for all new change records. It wraps the
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
- `active_peers[]` → one line per live peer: tool, run_id, phase.

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
  --owned-files .build-loop/coordination/current-owned-files.txt \
  --interval 3 \
  --jsonl \
  --baseline-current
```

The scripts emit compact `clear | warn | blocked` state plus inbox unread
count. AI should read full coordination context only when the script reports
`warn` or `blocked`, a target inbox changes, a step
moves to `verification-pending`, or the next action is a commit, version bump,
archive/delete, or shared/high-risk file edit.

## Graceful absence

An absent channel/dir yields an empty envelope (`changed: false`,
empty lists, `arch_digest: null`), creates nothing implicitly, and
never errors — zero regression for repos that have never seen App Pulse.

## Non-goal guard

Records and the envelope carry structure/data-flow only. No
call-frequency / invocation-count field is ever written or surfaced
(asserted in `scripts/app_pulse/test_changes.py` and
`test_checkpoint.py`).
