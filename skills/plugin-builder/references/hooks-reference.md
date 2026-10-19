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

## Every host: environment and failure policy

These rules hold for Claude Code, Codex, Cursor and Muse Code. Host differences follow in the next section.

1. **Assume the short PATH.** A hook inherits the environment of whatever started the host. Started from a terminal, that is your shell's PATH. A macOS app started from the Dock or by launchd gets `PATH=/usr/bin:/bin:/usr/sbin:/sbin` (measured 2026-10-19 on the running Cursor desktop app). The same hook can therefore pass from a terminal and exit 127 from the app. Resolve every binary absolutely, through the host's root variable, or behind `command -v`. Binaries in `~/.local/bin`, Homebrew or a Node version manager are the usual 127.

2. **Test the declared failure policy under that PATH.** An optional or advisory hook must continue with valid output when its tooling is missing or slow. A required safety check must report that it could not run and refuse the governed action; it never turns missing evidence into permission. `set -euo pipefail` plus an unguarded binary in a command substitution (`meta="$(node -e '…')"`) aborts the whole script with 127, even when a later line has `|| true`:
   ```bash
   printf '{"tool_input":{"file_path":"/tmp/x"}}' | env -i HOME="$HOME" PATH=/usr/bin:/bin:/usr/sbin:/sbin bash hooks/my-hook.sh; echo "exit=$?"
   # An advisory hook MUST print exit=0
   ```

3. **Advisory hooks never enforce.** Coordination, lint and reminder hooks emit context or diagnostics, never a deny or block. Blocking is reserved for explicit safety, security or integrity gates, or an explicit opt-in.

4. **Bound every hook that blocks, in seconds.** Claude Code, Codex and Cursor read seconds; Muse Code's unit is unverified. A millisecond-shaped value (5000) means 83 minutes; `scripts/hook_budget_lint.py` flags it. Set the outer `timeout` from measured runtime under load, with headroom: too short a value discards the hook's output (AI Assistant's 3s budget dropped routing context mid-session, commit cca7f61). A handler that caps its own work internally may omit `timeout` and fall back to the host default; HB002 still reports it, so record the inner bound next to the hook.

5. **Do not depend on sibling order.** Claude Code, Codex and Cursor run all matching hooks from every config layer, and Claude Code and Codex document running them concurrently. When decisions conflict, the most restrictive one wins.

6. **Keep logic in one version-controlled script.** Each host's config is a thin entry that calls it by absolute or root-variable path, and the script finds its own files with `realpath "$0"`. Installed copies drift silently from the code they call (plugin-hygiene-lessons.md §17).

## Host differences

Verify each host separately; a hook proven in one host proves nothing in another. Sources were read on 2026-10-19 and carry no version stamp, so re-check them before relying on a default.

| | Claude Code | Codex | Cursor | Muse Code |
|---|---|---|---|---|
| Config | `~/.claude/settings.json`, `.claude/settings{,.local}.json`, managed policy, plugin `hooks/hooks.json` | `~/.codex/hooks.json` or `[hooks]` in `config.toml`, `<repo>/.codex/`, plugins, managed dir | `~/.cursor/hooks.json`, `<project>/.cursor/hooks.json`, team dashboard, enterprise file | `hooks` key in `~/.config/muse/settings.json` (needs `schema_version`), plugin `hooks/hooks.json`, `managed_hooks_path` |
| Event names | PascalCase (`PreToolUse`, `Stop`) | PascalCase, plus `PermissionRequest`, `PostCompact`, `Interrupt` | camelCase (`preToolUse`, `beforeShellExecution`, `beforeSubmitPrompt`, `stop`) | Claude names, per third-party reports |
| Default timeout (s) | 600; 30 on `UserPromptSubmit` | 600; 1 on `SessionEnd`/`Interrupt` (max 3) | undocumented, so always declare one | unverified |
| Block | exit 2, `permissionDecision: "deny"`, `decision: "block"` | the same three; `ask` in PreToolUse is unsupported | exit 2 or `permission: "deny"` | exit 2 or Claude's JSON, per third-party reports |
| On crash/timeout | non-blocking error (exit 1 included); the action continues unless `onFailure: "block"` (v2.1.295+) | reported failed; the action continues | continues, unless `failClosed: true`; invalid JSON blocks permission hooks | unverified |
| Trust gate | none | new or changed hooks are skipped until trusted (by hash); project hooks need a trusted `.codex/` | none documented | project layer gated on workspace trust (inferred from the 1.4.11 binary) |
| Variables | `CLAUDE_PROJECT_DIR`, `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA` | `PLUGIN_ROOT`, `PLUGIN_DATA`, plus `CLAUDE_*` aliases; cwd is the session cwd | `CURSOR_PROJECT_DIR`, `CLAUDE_PROJECT_DIR` alias; cwd depends on the config source | `managed_hooks_env_vars` |
| Notes | `deny > defer > ask > allow`; settings deny/ask rules still apply; check `stop_hook_active` (8-continuation backstop) | `commandWindows` overrides on Windows | loads Claude Code hooks; `loop_limit` 5 (Claude hooks: none); desktop app gets the short PATH | shell tool is `bash` (lowercase) in matchers |

"Spark" names a model, not a host. Meta's Muse Spark runs inside Muse Code, and OpenAI's Codex-Spark runs inside Codex, so their hooks follow those columns.

Easy Terminal panes add `EASY_TERMINAL_PANE_ID`, `EASY_TERMINAL_PTYD_BIN` and `PTYD_SOCKET_PATH` to every agent they start. Outside Easy Terminal these are unset, so guard them: `[ -x "${EASY_TERMINAL_PTYD_BIN:-}" ] && "$EASY_TERMINAL_PTYD_BIN" …; exit 0`.

Sources: [Claude Code hooks](https://code.claude.com/docs/en/hooks), [Codex hooks](https://learn.chatgpt.com/docs/hooks), [Cursor hooks](https://cursor.com/docs/agent/hooks). Muse Code has no official hook reference on record; its column comes from the installed 1.4.11 binary and third-party reports ([Arcjet](https://docs.arcjet.com/coding-agents/muse-code/), [Agentic Control Plane](https://agenticcontrolplane.com/blog/muse-code-acp-integration)), which disagree on whether project `.muse/hooks.json` loads.

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
| Hook not firing | Event name in the host's casing (Claude/Codex PascalCase, Cursor camelCase)? Codex: is the hook trusted? |
| Script not executing | Is it executable? (`chmod +x`) |
| Script can't find files | Using `${CLAUDE_PLUGIN_ROOT}`? |
| Matcher not matching | Tool name correct and case-sensitive? |
| Prompt hook not working | Valid prompt with `$ARGUMENTS` if needed? |
| `exit code 127` on every fire | A binary the script calls isn't on the PATH the host was started with (short PATH in GUI apps). Resolve it absolutely or `command -v`-guard it; test under `env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin`. |
| Hook aborts instead of failing open | `set -e` + an unguarded command substitution. Guarding one line ≠ fail-open. |
| Path-with-spaces → 127 | Known issue (anthropics/claude-code #5648); quote the path in the command string. |
| Advisory hook blocking edits | It's emitting `deny`/`block`; switch to `additionalContext`/`systemMessage` and reserve blocking for safety gates. |
