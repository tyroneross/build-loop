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
