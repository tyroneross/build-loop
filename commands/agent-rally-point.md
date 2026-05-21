---
description: "Inspect or invoke build-loop's multi-session coordination (App Pulse + per-run coord file). Subcommands: status (default), init, docs."
allowed-tools: Bash, Read
argument-hint: "[status|init|docs] [args]"
model: inherit
---

{{#if ARGUMENTS}}

Parse `{{ARGUMENTS}}` as `<subcommand> [args...]`. If `<subcommand>` is omitted, default to `status`.

## Subcommands

### `status` (default)

Cheap (~100-token) sensor poll. Reports active peer sessions, unresolved verifier verdicts, dirty files, and the active coord file path. Run this BEFORE any step-boundary decision (next-step recommendation, subagent dispatch, commit, version bump, archive/delete).

Executes:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/coordination_status.py \
  --workdir "$PWD" \
  --session-id "user-rally-$(date +%s)" \
  --coordination-file <auto-detect or pass --coordination-file=<path>> \
  --json
```

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

### `init <topic> <scope-one-liner>`

Bootstrap a NEW coord file at `.build-loop/coordination/<topic>-YYYY-MM-DD.md` from `references/coordination-file-template.md`. Writes own presence, posts a `kind=handoff` record so peers see it. Idempotent: if the coord file already exists, joins (writes presence + posts `phase=joined-existing-coord`) instead of overwriting.

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

## Dispatch

Based on the parsed subcommand, run the corresponding bash command above using the Bash tool. Quote the result and surface key fields (status / coord_file / unresolved) in the response.

For `status`, if `unresolved: []` is non-empty, hold and resolve them before the user's intended step proceeds.

{{else}}

**`/agent-rally-point`** — inspect or invoke build-loop's multi-session coordination.

## Subcommands

| Subcommand | Use when |
|---|---|
| `status` (default) | Before any step boundary: are peers active? Are there unresolved verifier verdicts? Default if no subcommand. |
| `init <topic> <scope>` | Start a NEW coordinated run; bootstraps `.build-loop/coordination/<topic>-YYYY-MM-DD.md` from the template, writes presence, posts `kind=handoff`. Idempotent. |
| `docs` | Print `references/coordination-rules.md` — the binding constitution (verdict-gating, `post()` helper, MECE packets, closeout). |

## Examples

- `/agent-rally-point` → status (default)
- `/agent-rally-point status` → explicit status
- `/agent-rally-point init v0130-feature-x "Add X across orchestrator + tests"` → bootstrap a coord file
- `/agent-rally-point docs` → print the constitution

## When auto-invoke is enough

The `build-orchestrator` agent auto-invokes coordination at three trigger points (Phase 1 Assess preamble, Phase 3 chunk-close, Phase 4 Review-A) — see `agents/build-orchestrator.md` §"Auto-invoke coordination". `/agent-rally-point` is for cases where you want to inspect or bootstrap from outside a build-loop run (manual peer setup, debugging coordination state, onboarding a fresh verifier session).

{{/if}}
