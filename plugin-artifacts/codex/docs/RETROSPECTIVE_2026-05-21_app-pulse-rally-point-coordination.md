# Retrospective: App Pulse Rally-Point Coordination

**Date:** 2026-05-21
**Scope:** Build-loop cross-host coordination, App Pulse, `/agent-rally-point`, Codex-to-Claude Code dogfood on example app.

## Bottom Line

Build-loop should treat `~/.build-loop/apps/<app-slug>/` as the app's single rally point and publish a lightweight `phase=rally-start` signal at the beginning of every non-trivial build-loop run, before any passive coordination poll.

The current system has the right primitives, but the start path is split. App Pulse can hold live presence, durable events, and peer discovery. `coordination_status.py` can cheaply summarize that state. However, a passive status read does not make the reader visible. In this dogfood run, Codex initially checked the channel but did not pulse itself into it, so Claude Code correctly saw no Codex coordination signal.

## What Went Well

### 1. The shared channel model worked once a write happened

After Codex explicitly wrote presence and a handoff to `~/.build-loop/apps/example-ios-app`, Claude Code could see the same app slug, channel revision, and active peer state. That validates the core App Pulse design: one app channel, shared across hosts, with lightweight records instead of direct agent-to-agent messaging.

Why it matters: the coordination surface does not require Claude and Codex to talk to each other directly. They only need to agree on the app slug and channel protocol.

### 2. `coordination_status.py` was the right first sensor

The status script gave a cheap answer: active peers, unresolved verdicts, dirty files, revision, and required action. It avoided rereading full coordination markdown until the channel had something meaningful to inspect.

Why it matters: coordination should not add a large token tax to solo work. The sensor-first pattern is still right.

### 3. The dogfood exposed a real product gap

The initial failure was not a random operator miss. It revealed a design ambiguity: "check coordination" and "announce coordination" are different operations. The system had a clear read path but lacked a lightweight write path for "I am here, but I do not yet need a full coordination file."

Why it matters: the resulting `coordination_rally.py` and `/agent-rally-point announce` command improve the product because they fill a missing state transition rather than adding more prose to the existing instructions.

### 4. Cache parity became visible

Claude Code had build-loop `0.12.10`, while Codex was still loading older cached build-loop skills. That explained why the expected coordination behavior did not automatically exist on the Codex side.

Why it matters: source correctness is not runtime correctness. Any cross-host coordination feature needs a cache-sync check as part of release and dogfood validation.

## What Went Poorly

### 1. Passive reads were mistaken for coordination

Codex initially verified that the example app channel was absent and reported `status: clear`. That was accurate as a read, but it did not create any signal for Claude Code to find. From Claude's perspective, Codex was invisible.

Root cause: `coordination_status.py` is a sensor, not a publisher. It reads App Pulse and git state. It does not write presence.

### 2. The docs and tests disagree about solo-run visibility

`references/app-pulse-protocol.md` and `references/multi-session-coordination.md` say Phase 1 writes presence at the start of a run. `agents/build-orchestrator.md` says the Phase 1 trigger happens after presence is written. But `scripts/test_orchestrator_auto_invoke.py` models solo mode as "no coord file write, no presence write, no channel post."

Root cause: the implementation model collapsed "no durable coordination file" into "no visible start signal." Those should be separate decisions.

### 3. The rally point was overloaded with the coordination file

Before `announce`, `/agent-rally-point` effectively meant `status`, `init`, or docs. `init` creates a durable `.build-loop/coordination/*.md` file, which is appropriate for gated multi-agent work but too heavy for merely saying "this host is active and ready to coordinate."

Root cause: build-loop had a full coordination ledger and a cheap sensor, but no middle state.

### 4. Fire-and-forget writes can hide local permission failures

App Pulse intentionally swallows write failures so coordination cannot block host work. That is correct for app work, but weak for diagnostics. In a sandboxed Codex session, a write to `~/.build-loop/apps` can fail silently unless the caller verifies that `channel_revision` advanced.

Root cause: operational writes and validation writes have different needs. Production coordination should be non-blocking. Dogfood validation needs explicit proof.

### 5. Codex had no true automatic start hook

Claude's plugin has `SessionStart` hooks. The current Codex manifest exposes skills and MCP servers, but not an equivalent hook path. In Codex, automatic coordination is only reliable if the installed skill instructions cause the first model action to run the rally preflight, or if the user invokes a wrapper command.

Root cause: "available to Codex" is not the same as "executed by Codex at session start."

## Why The Recommendation Improved

The original response path was reactive: check whether App Pulse showed a conflict, then proceed if the channel was clear. That recommendation was incomplete because an empty channel can mean either "no peers exist" or "peers exist but have not pulsed."

The improved recommendation is proactive: publish a lightweight rally signal first, then read the channel. That turns "absence of evidence" into a meaningful state. Once every build-loop run announces itself, a clear channel becomes stronger evidence that no live peer is working under the same app slug.

## Claude Code Reconciliation

Claude Code's follow-up retrospective sharpens the recommendation. The main correction is that build-loop already has a repo-local pointer at `.build-loop/coordination/active.json`, written by `coordination_bootstrap.py` and read by `coordination_status.py`. What is missing is a channel-level pointer under `~/.build-loop/apps/<app-slug>/` that binds the App Pulse channel to the live coordination file.

Confirmed from runtime state:

- The example app channel has `changes.jsonl`, `revision`, `revision.lock`, and `sessions/`, but no channel `active.json`.
- `scripts/app_pulse/` has no `write_active` or `read_active_pointer` helper. The only active pointer code is repo-local in `coordination_bootstrap.py`.
- example app commits `353d562` and `3545cba` landed at 2026-05-21 09:44 Pacific, before Codex's 09:50 App Pulse post that declared no app-file ownership. That proves channel ownership claims can be stale or incomplete unless the rally-start path inspects recent commits and current repo state before posting.
- Current source `coordination_status.py` returns `warn` for overlaps, unresolved verdicts, or dirty files, not peer count alone. Claude's observed "warn on 4 peers" is still useful dogfood evidence, but the implementation task should first reproduce the exact code path before changing status semantics.

The strongest combined conclusion: App Pulse needs a start-of-run publisher plus an active pointer. MECE validation should be applied to handoff/claim payloads, but not blindly to every `post()` call.

## Recommendation

### 1. Make the app channel the single rally point

Use `~/.build-loop/apps/<app-slug>/` as the one rally point for the app. Do not create one rally directory per run. Put session and run records under that channel.

Why this helps: the app slug already solves the main lookup problem. A new build-loop run can derive the slug from `git rev-parse --git-common-dir` and know exactly where to look, across the main checkout, worktrees, Claude Code, and Codex.

### 2. Add a mandatory lightweight `rally-start` at non-trivial run start

At Phase 1 preamble, before the first passive status poll, write:

- `sessions/<session-id>.json` with `phase="assess"` or `phase="rally-point"`.
- One durable `changes.jsonl` record using the existing schema: `kind="phase"` with `payload.phase="rally-start"`.
- The run identity: `run_id`, `session_id`, `tool`, `model`, `cwd`, `app_slug`, `started_at`, `scope`, `files_in_flight=[]`.

Do this even when there are no peers. Do not create a coordination markdown file yet.

Why this helps: every active build-loop run becomes discoverable. Solo work stays lightweight, but other hosts no longer see an empty channel when a peer has only performed passive checks.

### 3. Keep `coordination_rally.py` as the publisher primitive

Use `coordination_rally.py` for lightweight start and manual `/agent-rally-point announce` flows. Use `coordination_bootstrap.py` only when the run needs a durable coordination file.

Why this helps: it creates a clean state model:

- `status`: read only.
- `announce` / `rally-start`: visible presence, no ledger.
- `init` / `bootstrap`: durable coordination ledger with verdict gates.

### 4. Add a channel-level active pointer

Add a small mutable pointer under the App Pulse channel:

```text
~/.build-loop/apps/<app-slug>/active.json
```

Shape:

```json
{
  "schema_version": "1.0",
  "app_slug": "example-ios-app",
  "current_run_id": "run-20260521-0950",
  "latest_session_id": "codex-...",
  "status": "active",
  "coordination_file": null,
  "latest_revision": 3,
  "started_at": "2026-05-21T09:50:00-07:00",
  "updated_at": "2026-05-21T09:52:00-07:00",
  "expires_after_seconds": 900
}
```

Why this helps: new sessions need a deterministic "most recent rally" without scanning an unbounded `changes.jsonl`. The append-only log remains the audit trail. The channel-level pointer is only the fast index. The repo-local `.build-loop/coordination/active.json` remains useful as the default coordination-file pointer inside the checkout; the channel-level `active.json` answers the cross-host question: "which coordination file is live for this app channel right now?"

### 5. Promote to a coordination file only on risk triggers

Keep the default rally lightweight. Promote to `.build-loop/coordination/<topic>-<date>.md` only when one of these happens:

- A live peer is present and files overlap.
- The user explicitly asks two hosts to coordinate.
- A subagent/peer write handoff is about to happen.
- A verifier verdict must gate progress.
- The run is about to commit, version bump, release, archive, or delete shared files.

Why this helps: simple work does not create coordination-file noise, but real multi-agent work gets the durable ledger, active pointer, and verdict-gating semantics.

### 6. Fix the solo-mode contract and tests

Update `scripts/test_orchestrator_auto_invoke.py` so solo mode means "no coordination file and no gated ledger," not "no presence and no channel post." The expected solo start should become:

```json
{
  "action": "rally_start",
  "mode": "solo",
  "presence_should_be_written": true,
  "post_kind": "phase",
  "payload_phase": "rally-start",
  "coordination_file": null
}
```

Why this helps: the tests will enforce the distinction that failed in dogfood. A future refactor cannot accidentally turn initial coordination back into a passive read.

### 7. Make Codex automatic through a preflight, not only instructions

For Codex, the reliable path is:

1. Keep the Codex plugin cache current with the Claude cache.
2. Add a Codex-facing first-action rule in the build-loop skill: before any non-trivial build-loop work, run `coordination_rally.py --workdir "$PWD" --tool codex --phase assess`.
3. Add a small wrapper command or preflight script that Codex can run directly when a build-loop skill activates.
4. If Codex later supports plugin `SessionStart` hooks, move the read-only restore check and rally-start write into that hook.

Why this helps: a hook is the only true automatic start mechanism. Until Codex exposes that surface, the next best option is a deterministic first tool call baked into the Codex skill and validated by cache-sync tests.

### 8. Validate handoff claims at the coordination layer

Validate MECE ownership packets for `kind="handoff"` payloads, especially actions like `claim`, `bootstrapped`, `joining`, and `rally-point`. Do not apply MECE validation to `kind="phase"` or `kind="feedback"`.

Why this helps: Claude's strongest finding was that stated ownership can drift from actual behavior. A handoff/claim payload without `owns`, `does_not_own`, `interface_contract`, and `integration_checkpoint` should be flagged immediately. The safest migration is warn-first for one release, then make `coordination_rally.py` and `coordination_bootstrap.py` reject malformed handoff claims. The low-level `post()` helper should remain fire-and-forget, but it can expose validation warnings or call a validator in advisory mode.

### 9. Add verification mode for dogfood

Keep normal App Pulse writes fire-and-forget, but give `coordination_rally.py` a validation mode:

```bash
python3 scripts/coordination_rally.py --workdir "$PWD" --tool codex --verify --json
```

The verify mode should fail or mark `posted=false` when `channel_revision` is null or the just-written session cannot be read back.

Why this helps: production coordination remains non-blocking, while dogfood and release validation can prove the signal actually landed.

## Recommended Start Algorithm

```text
1. Resolve app slug from cwd with channel_paths.app_slug().
2. Generate or recover run_id:
   - reuse .build-loop/state.json.active_run_id on resume;
   - otherwise create run-<date>-<short-topic>.
3. Generate session_id for this host process.
4. Publish rally-start:
   - write presence;
   - append `kind=phase` with `payload.phase=rally-start` through post();
   - update channel active.json.
5. Run coordination_status.py.
6. If status is clear, continue solo with visible presence.
7. If peers or active coord file exist, join or promote:
   - existing coord file: post joined-existing-coord;
   - peers without coord file and no overlap: stay lightweight;
   - overlap or verifier gate: bootstrap durable coord file.
8. At phase starts, refresh presence and post phase changes.
9. At closeout, post run-closeout and mark channel active.json closed or expired.
```

## Tracking Across Multiple Sessions

Use two IDs, not one:

- `run_id`: stable logical build. Survives compaction, resumed sessions, and host changes.
- `session_id`: one live host process. Expires by heartbeat and can be reaped.

The rally point is one app-level channel. The channel contains many session records and many run events. The channel `active.json` pointer names the most recent active run, while `changes.jsonl` keeps the history. This gives a new build-loop run a deterministic recovery path:

1. Resolve slug.
2. Read channel `active.json`.
3. Reap stale presence.
4. Read recent `changes.jsonl` from `latest_revision` or the last cursor.
5. Decide whether to resume, join, promote, or start a fresh rally.

If channel `active.json` is missing or stale, rebuild it from the tail of `changes.jsonl` by finding the newest non-closeout record with a `coord_file`, then confirm the repo-local coordination file still exists. That keeps `active.json` a derived cache, not the only source of truth.

## Options Considered

| Option | Assessment |
|---|---|
| Status poll only | Too weak. It preserves low token cost, but passive readers stay invisible. |
| Always create coordination markdown | Too heavy. It makes every solo run look like gated multi-agent work and creates cleanup burden. |
| SessionStart hook only | Good for Claude Code, incomplete for Codex because the current Codex plugin surface does not expose equivalent hooks. |
| External DB or daemon | Overkill. App Pulse already has enough local primitives and intentionally avoids daemon coupling. |
| App channel plus rally-start plus channel active.json | Best fit. It is lightweight, discoverable, cross-host, and promotes to durable coordination only when risk justifies it. |

## Implementation Sequence

1. Update orchestrator docs and tests so solo mode writes rally-start but not a coordination file.
2. Extend `coordination_rally.py` with `--verify` and optional `--record-kind phase --record-phase rally-start`.
3. Add `scripts/app_pulse/active.py` or equivalent helper for channel `active.json`, including lock-protected write, read, stale check, closeout clear, and tail-rebuild fallback.
4. Wire Phase 1 Assess to call the rally publisher before `coordination_status.py`.
5. Update `/agent-rally-point` docs: `status` reads, `announce` publishes, `init` promotes.
6. Add handoff MECE validation in warn-first mode for `kind=handoff` payloads, then enforce through `coordination_rally.py` and `coordination_bootstrap.py`.
7. Reproduce or retire the "warn on peer count alone" finding with a test. Desired semantics: peers are informational unless they overlap owned files, leave unresolved verdicts, or create dirty-file risk.
8. Add cache parity validation for Claude and Codex before claiming release completion.
9. Run a live Codex-to-Claude and Claude-to-Codex App Pulse smoke on a throwaway app slug and on one real app slug.

## Success Criteria

- A new Codex build-loop run on example app creates a visible App Pulse rally record before any app file is edited.
- A Claude Code run started after that can see the Codex session with only `coordination_status.py`.
- A no-peer solo run creates no `.build-loop/coordination/*.md` file.
- A peer overlap promotes to a durable coordination file and writes/updates the active pointer.
- Stale sessions disappear after the heartbeat window, but the latest closed run remains discoverable in `changes.jsonl`.
- Codex and Claude installed caches both contain the same coordination scripts and command docs.
