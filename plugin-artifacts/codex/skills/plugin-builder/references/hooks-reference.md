<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Hooks Reference

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

## Reliability: minimal PATH, fail-open, advisory-only

Hooks fire in a **subprocess with a minimal, non-interactive PATH** — typically `/usr/bin:/bin`, *not* your login shell's PATH. Binaries you installed to `~/.local/bin`, a Node version-manager dir, Homebrew, etc. are **not on PATH** inside a hook. This is the #1 cause of `exit code 127` (command not found) hook failures.

Three rules for any hook that calls an external binary (`node`, `jq`, a project CLI):

1. **Resolve binaries absolutely or guard every call.** Don't trust inherited PATH. Either hardcode/derive an absolute path (`RALLY_BIN`, `"$(command -v node || echo /opt/homebrew/bin/node)"`), or `command -v <bin> >/dev/null 2>&1 || exit 0` before using it.

2. **Fail open for real — and test it.** A hook whose tooling is missing/slow must `exit 0` with no output, never abort. Watch for `set -euo pipefail` + an **unguarded** binary in a command substitution: `meta="$(printf '%s' "$x" | node -e '…')"` aborts the *whole script* with 127 the instant `node` isn't found — even if a later line has `|| true`. Guarding one line doesn't make the script fail-open. Verify under the real hook environment:
   ```bash
   printf '{"tool_input":{"file_path":"/tmp/x"}}' | env -i PATH=/usr/bin:/bin bash hooks/my-hook.sh before-write; echo "exit=$?"
   # MUST print exit=0
   ```

3. **Advisory hooks must not enforce.** A coordination/lint/reminder hook should emit `additionalContext` (SessionStart/UserPromptSubmit, added to context) or `systemMessage`, never `permissionDecision:"deny"` / `decision:"block"`. Reserve blocking (`exit 2`, deny/block) for explicit safety/security/integrity gates, and gate any hard-block behind an opt-in env flag so the default never surprises an agent. Note SessionStart/Notification/Setup **cannot** block regardless.

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

### Save Context on Stop
```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [{
          "type": "prompt",
          "prompt": "Write a brief summary of this session's task, progress, and decisions to .claude/bookmarks/context.md"
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
