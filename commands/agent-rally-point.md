---
description: "Inspect or invoke build-loop's multi-session coordination (App Pulse + per-run coord file). Subcommands: status (default), watch, announce, init, docs, help."
allowed-tools: Bash, Read
argument-hint: "[status|watch|announce|init|docs|help] [args]"
model: inherit
---

Parse `{{ARGUMENTS}}` as `<subcommand> [args...]`. **If `<subcommand>` is omitted or empty, default to `status`** (the no-args case — most common interactive use).

## Subcommands

### `status` (default — no-args runs this)

Cheap (~100-token) sensor poll. Reports active peer sessions, unresolved verifier verdicts, dirty files, and the active coord file path. Run this BEFORE any step-boundary decision (next-step recommendation, subagent dispatch, commit, version bump, archive/delete).

Executes:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/coordination_status.py \
  --workdir "$PWD" \
  --session-id "user-rally-$(date +%s)" \
  --json
```

(If the user passed `--coordination-file=<path>` in subcommand args, forward it.)

Example output (clear):

```json
{
  "status": "clear",
  "active_peers": [],
  "unresolved": [],
  "coordination_file": null
}
```

Example output (warn — peer overlap on owned files):

```json
{
  "status": "warn",
  "active_peers": [{"session_id": "codex-...", "tool": "codex", "phase": "review"}],
  "overlaps": [{"peer": "codex-...", "files": ["scripts/foo.py"], "severity": "warning"}],
  "required_action": "review_peer_overlap_or_dirty_files"
}
```

### `watch`

Continuous cheap sensor loop. Use this while waiting on another coding host,
during active shared-file work, or whenever an inbox message is expected. The
watcher prints only state transitions, revision changes, dirty-file risk, and
inbox unread count.

Executes:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/coordination_watch.py \
  --workdir "$PWD" \
  --session-id "user-rally-$(date +%s)" \
  --tool "claude_code" \
  --interval 5 \
  --jsonl \
  --baseline-current
```

If the user passed `--tool=<name>`, `--files-in-flight=<csv>`, or
`--coordination-file=<path>` in subcommand args, forward them. Claude Code uses
`--tool claude_code`; Codex uses `--tool codex`; other hosts should choose a
stable lowercase tool id.

When the watcher emits an event with a higher `revision`,
`direct_inbox_unread_count > 0`, or `broadcast_inbox_unread_count > 0`, run
`status`, read the addressed inbox plus `inbox/all.jsonl`, and respond through
the channel before continuing.

### `announce [message]`

Publish lightweight App Pulse presence + a `kind=handoff` rally record for the current app slug, without creating a durable coordination file. Use this when another host (Codex, Claude Code, CI verifier) needs to see that this agent is present before work ownership is split, or when dogfooding coordination from outside a full build-loop run.

Executes:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/coordination_rally.py \
  --workdir "$PWD" \
  --session-id "user-rally-$(date +%s)" \
  --tool "claude_code" \
  --model "inherit" \
  --to "peer" \
  --message "<message or 'Agent is present and ready to coordinate.'>" \
  --json
```

If the user passed `--owns=<csv>` or `--does-not-own=<csv>` in subcommand args, forward those values as `--owns` / `--does-not-own`. If omitted, the rally is presence-only and owns no files.

Example output:

```json
{
  "action": "rally-point-posted",
  "app_slug": "speaksavvy-ios",
  "channel_revision": 1,
  "presence_written": true
}
```

### `init <topic> <scope-one-liner>`

Bootstrap a NEW coord file at `.build-loop/coordination/<topic>-YYYY-MM-DD.md` from `references/coordination-file-template.md`. Writes own presence, posts a `kind=handoff` record so peers see it. **Idempotent and atomic** (per v0.12.10): if the coord file already exists OR a concurrent peer creates it between our check and our write, joins (writes presence + posts `phase=joined-existing-coord`) instead of overwriting.

Executes:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/coordination_bootstrap.py \
  --workdir "$PWD" \
  --topic "<topic-slug>" \
  --scope "<scope-one-liner>" \
  --session-id "user-rally-$(date +%s)" \
  --json
```

Example invocation: `/agent-rally-point init v0130-feature-x "Add new feature X across orchestrator + bootstrap + tests"`

Example output:

```json
{
  "coord_file": ".build-loop/coordination/v0130-feature-x-2026-05-21.md",
  "action": "bootstrapped",
  "channel_revision": 65,
  "session_id": "user-rally-1779320000"
}
```

### `docs`

Prints the binding coordination constitution at `references/coordination-rules.md`. Use this when onboarding a peer (Codex, second Claude session, CI verifier) so they read the same rules — the verdict-gating operating rule, the `post()` helper mandate, MECE packets for every write-handoff, release-surface verification, Phase D closeout.

Executes:

```bash
cat ${CLAUDE_PLUGIN_ROOT}/references/coordination-rules.md
```

### `help`

Shows the subcommand reference table (replaces what the no-args case used to do pre-v0.12.10).

Outputs:

```
/agent-rally-point — inspect or invoke build-loop's multi-session coordination

Subcommands:
  status (default)        Sensor poll: active peers, unresolved verdicts, coord file
  watch                   Continuous sensor loop for peer/inbox changes
  announce [message]      Publish App Pulse presence + handoff without coord file
  init <topic> <scope>    Bootstrap a coord file from template; atomic + idempotent
  docs                    Print the binding coordination constitution
  help                    This message

No-args invocation runs `status` (the most common interactive use).

When auto-invoke is enough:
  The build-orchestrator agent auto-invokes coordination at three trigger
  points (Phase 1 Assess preamble, Phase 3 chunk-close, Phase 4 Review-A).
  See agents/build-orchestrator.md §"Auto-invoke coordination". Use `watch`
  when waiting on an async peer response or targeted inbox message. Use
  `status`, `announce`, or `init` for manual peer setup, debugging
  coordination state, or onboarding a fresh verifier session.
```

## Dispatch

Based on the parsed subcommand (or `status` when `{{ARGUMENTS}}` is empty), run the corresponding bash command above using the Bash tool. Quote the result and surface key fields (status / coord_file / unresolved) in the response.

For `status`, if `unresolved: []` is non-empty, hold and resolve them before the user's intended step proceeds.
