<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Hooks Reference

The examples below illustrate individual Claude configurations; they are not a default bundle to install. Verify another host's paths and decision protocol separately.

## Keep the active hook set small

Use the smallest set that covers demonstrated needs. A fixed hook-count limit does not establish usefulness or cost. Apply this checklist when changing a registration or handler; it is design guidance, not another runtime hook or approval gate.

- **Reuse before adding.** Name the observed failure, existing owner and missing coverage. Extend that owner when its responsibility fits. Use a skill or explicit task for judgment and optional work.
- **Inspect effective registrations.** Check relevant user, project, plugin and skill layers for overlapping triggers, repeated checks and conflicting argument/output rewrites. Keep one owner for each rewrite. Confirm actual host behavior before crediting deduplication.
- **Share setup without creating a giant dispatcher.** Reuse existing parsing, validation and receipts when they represent the same rule. Keep independent checks understandable and avoid hidden ordering dependencies between sibling handlers.
- **Keep frequent events cheap.** Match only relevant events, tools, operations and paths; return early otherwise. Keep installations, broad scans, full test suites and model reviews out of routine per-tool work. Put necessary expensive work at its explicit acceptance or CI boundary; keep required pre-action decisions synchronous.
- **Budget the combined effect.** Measure representative end-to-end delay, process launches and injected context across the active handlers. Bound startup and children. Keep routine success quiet; show new actionable failures and retain diagnostic evidence without repeating unchanged warnings.
- **Retire obsolete or duplicated work.** Revisit a hook when its prerequisite, host behavior or protected workflow changes. Remove it only after confirming another control covers the required behavior, or that the behavior is no longer required. Preserve recovery information and verify required safety checks still hold.

Before promoting an uncertain advisory check to a block, inspect false positives and missed cases on realistic inputs. Existing safety requirements retain their governing policy. For a practical admission decision, name what this change adds that the current set cannot do, why its trigger is narrow enough, and what observation would justify removing it. Keep that explanation in the existing plan or review rather than creating a separate registry.

These choices extend the granular-hook recommendation at 59:21–59:59 in the [Oct. 13 Claude Code: Advanced webinar](https://anthropic.ondemand.goldcast.io/on-demand/26b3d349-9fd9-4fa3-b122-f5b2f07c17a5) and the existing dispatcher/deadline controls. The official [Claude reference](https://code.claude.com/docs/en/hooks) also documents concurrent matching handlers and cautions against multiple handlers rewriting the same tool input. Verify each host separately.

## Hook Configuration Format

Hooks live in `hooks/hooks.json`:

```json
{
  "hooks": {
    "EventName": [
      {
        "matcher": "ToolPattern",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/scripts/my-script.sh"
          }
        ]
      }
    ]
  }
}
```

## Available Events

| Event | Trigger | Common Uses |
|-------|---------|-------------|
| `PreToolUse` | Before Claude uses any tool | Validate inputs, block dangerous commands |
| `PostToolUse` | After successful tool use | Format code, lint, log changes |
| `PostToolUseFailure` | After tool execution fails | Error recovery, retry logic |
| `UserPromptSubmit` | When user submits a prompt | Track activity, preprocess |
| `PermissionRequest` | When permission dialog shown | Auto-approve trusted patterns |
| `Stop` | When Claude attempts to stop | Write summaries, save context |
| `SubagentStart` | When subagent starts | Configure subagent behavior |
| `SubagentStop` | When subagent attempts to stop | Aggregate results |
| `SessionStart` | At session beginning | Restore context, initialize state |
| `SessionEnd` | At session end | Cleanup, save state |
| `PreCompact` | Before conversation compaction | Save important context |
| `Notification` | When Claude sends notifications | Forward to external services |
| `TaskCompleted` | When task marked complete | Verify completion, trigger follow-ups |
| `TeammateIdle` | When team agent about to idle | Redistribute work |

## Hook Types

### Command Hooks
Execute shell commands or scripts:

```json
{
  "type": "command",
  "command": "${CLAUDE_PLUGIN_ROOT}/scripts/format-code.sh"
}
```

The command receives hook input as JSON on stdin. Use `jq` to extract fields:
```json
{
  "type": "command",
  "command": "jq -r '.tool_input.file_path' | xargs npm run lint:fix"
}
```

### Prompt Hooks
Evaluate a prompt with an LLM:

```json
{
  "type": "prompt",
  "prompt": "Review the changes made and verify they follow project conventions. $ARGUMENTS"
}
```

### Agent Hooks
Run an agentic verifier with tools:

```json
{
  "type": "agent",
  "prompt": "Verify the implementation is complete and correct."
}
```

## Matchers

Matchers filter which tools trigger a hook:

```json
{
  "matcher": "Write|Edit",
  "hooks": [...]
}
```

- Use `|` for OR: `"Write|Edit"`
- Omit matcher to trigger on all tools for that event
- Tool names are case-sensitive

## Environment Variables

`${CLAUDE_PLUGIN_ROOT}` — Always use this for plugin paths. Resolves to the actual plugin directory regardless of installation location.

## Script Requirements

1. Must be executable: `chmod +x scripts/my-script.sh`
2. Include shebang line: `#!/bin/bash` or `#!/usr/bin/env bash`
3. Use `${CLAUDE_PLUGIN_ROOT}` for paths
4. Test manually before integrating

## Reliability: minimal PATH and explicit failure policy

Hooks fire in a **subprocess with a minimal, non-interactive PATH** — typically `/usr/bin:/bin`, *not* your login shell's PATH. Binaries you installed to `~/.local/bin`, a Node version-manager dir, Homebrew, etc. are **not on PATH** inside a hook. This is the #1 cause of `exit code 127` (command not found) hook failures.

Four rules for any hook that calls an external binary (`node`, `jq`, a project CLI):

1. **Resolve binaries absolutely or handle their absence.** Don't trust inherited PATH. Derive an absolute path or check `command -v <bin>` before using it. An optional/advisory handler may skip unavailable tooling; a required safety check must report that it could not run and hold/refuse the governed action through the host's supported mechanism.

2. **Test the declared failure policy.** Optional/advisory hooks should continue with host-valid output when tooling is missing/slow. Required safety checks must not silently turn missing evidence into permission. Watch for `set -euo pipefail` + an **unguarded** binary in a command substitution: `meta="$(printf '%s' "$x" | node -e '…')"` aborts the *whole script* with 127 the instant `node` isn't found — even if a later line has `|| true`. Guarding one line doesn't establish the whole handler's behavior. Verify an advisory handler under its real environment:
   ```bash
   printf '{"tool_input":{"file_path":"/tmp/x"}}' | env -i PATH=/usr/bin:/bin bash hooks/my-hook.sh before-write; echo "exit=$?"
   # MUST print exit=0
   ```

3. **Advisory hooks must not enforce.** A coordination/lint/reminder hook should emit host-supported context or diagnostics, never `permissionDecision:"deny"` / `decision:"block"`. Reserve blocking for explicit safety/security/integrity requirements or an explicit opt-in. Existing mandatory gates retain their governing policy; this guidance does not make them optional. Check whether the chosen event supports the intended decision.

4. **Resolve your own path at runtime if installed out-of-tree.** If a host wrapper references a versioned/cache path (`${CLAUDE_PLUGIN_ROOT}`, `~/.codex/…`), prefer a thin shim that `exec`s the version-controlled script, or `realpath "$0"` inside the script — so the hook can't desync from the code it's supposed to run (see plugin-hygiene-lessons.md §17).

## Common Patterns

### Auto-Format on Write
```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [{
          "type": "command",
          "command": "jq -r '.tool_input.file_path' | xargs npx prettier --write"
        }]
      }
    ]
  }
}
```

### Restore Context on Session Start
```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [{
          "type": "command",
          "command": "${CLAUDE_PLUGIN_ROOT}/scripts/restore-context.sh"
        }]
      }
    ]
  }
}
```

### Block Dangerous Commands
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{
          "type": "command",
          "command": "${CLAUDE_PLUGIN_ROOT}/scripts/check-safety.sh"
        }]
      }
    ]
  }
}
```

## Troubleshooting

| Issue | Check |
|-------|-------|
| Hook not firing | Event name correct? (PascalCase) |
| Script not executing | Is it executable? (`chmod +x`) |
| Script can't find files | Using `${CLAUDE_PLUGIN_ROOT}`? |
| Matcher not matching | Tool name correct and case-sensitive? |
| Prompt hook not working | Valid prompt with `$ARGUMENTS` if needed? |
| `exit code 127` on every fire | A binary the script calls (`node`/`jq`/CLI) isn't on the hook's minimal PATH. Resolve it absolutely or `command -v`-guard it; test under `env -i PATH=/usr/bin:/bin`. |
| Hook aborts instead of failing open | `set -e` + an unguarded command substitution. Guarding one line ≠ fail-open. |
| Path-with-spaces → 127 | Known issue (anthropics/claude-code #5648); quote the path in the command string. |
| Advisory hook blocking edits | It's emitting `deny`/`block`; switch to `additionalContext`/`systemMessage` and reserve blocking for safety gates. |
