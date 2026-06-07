<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Capability Routing — orchestrator reference

Loaded on demand by the build-orchestrator agent when a phase needs a capability (UI build, debug, web-fetch, screenshot, migration, etc.).

## Routing protocol

1. Consult the Capability Routing table in `skills/build-loop/SKILL.md`.
2. If `availablePlugins.<flag>` is true → include `Invoke Skill("<plugin>:<skill>")` in the subagent prompt.
3. If a secondary plugin is available → include it as a fallback step.
4. If all flags are false → read the matching section of `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/fallbacks.md` and paste its content verbatim into the subagent prompt (subagents do not inherit Skill tool access).
5. Note the chosen tier in the Phase 4 Review sub-step F Report.

## Phase 3 routing — model-router consult per dispatch

Before each sub-agent dispatch in Phase 3, ask the router which provider/MCP tool fits:

```bash
TASK_ID="$(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/dispatch_identity.py --plain)"
DECISION=$(python3 ~/.claude/scripts/model-router.py \
  --task "<one-line task>" \
  --complexity auto \
  --phase execute \
  --task-id "$TASK_ID" \
  --json)
```

Resolve the final model id from the selected tier and repo overrides before
dispatch and before writing the cost-ledger row:

```bash
MODEL=$(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/model_overrides.py \
  --workdir "$PWD" \
  --tier code \
  --fallback sonnet \
  --plain)
```

Dispatch via the indicated `tool_call.name`:

- `mcp__ollama-local__cheap_complete` → free local Ollama (qwen2.5-coder for medium coding, llama3.2:3b for bounded classify/scan).
- `mcp__codex__codex` → second-opinion review when keywords match.
- `null` (provider=`claude`) → orchestrator handles it directly.

The cost ledger (`~/.bookmark/cost-ledger.jsonl`) auto-tags every MCP call with `$TASK_ID`. Inspect later:

```bash
python3 ~/.claude/scripts/cost-ledger-reader.py --by-task --since YYYY-MM-DD
```

When to skip the router: ambiguous tasks, novel-architecture work, or anything in Phases 1/2 (Assess/Plan) — those always belong to the lead orchestrator. See `skills/build-loop/SKILL.md` §"When to consult `model-router`" for the full policy.

## Trigger-Driven Routing (Phase 3 Execute + Phase 4 Review)

- If `triggers.structuredWriting` AND `availablePlugins.pyramidPrinciple`: the subagent writing copy, docs, or the scorecard loads `pyramid-principle:pyramid-principle-core` plus the length-matched skill (`pyramid-short-form`, `pyramid-long-form`, or `pyramid-presentation`). If the plugin is absent, paste `fallbacks.md#structured-writing` into the prompt.
- If `triggers.promptAuthoring`, first decide whether the prompt is load-bearing (see SKILL.md §Trigger Conditions, "Judgment: prompt-builder vs inline prompt"). If load-bearing AND `availablePlugins.promptBuilder`: the subagent authoring the prompt loads `prompt-builder:prompt-builder`. If absent, try personal `prompt-builder` skill via `Skill("prompt-builder")`, else paste `fallbacks.md#prompt`. If not load-bearing (one-shot orchestrator-to-Claude message, transient transform), craft an inline prompt directly.
- If `triggers.promptEditingExisting`: pause and ask the user with AskUserQuestion before running `prompt-builder` on a shipped prompt. Capture before and after in `.build-loop/prompts/<name>.v<n>.md`.

## Caller-detection caveats

Dead-code scans MUST verify callers via runtime invocation paths, not just static import grep. The following caller shapes are invisible to a naive `grep -r 'import build_acp' src/`:

| Caller shape | Example | Detection |
|---|---|---|
| `python -m <module>` | `python -m build_loop.architecture rules --json` | grep for `python -m` and the module name |
| argparse subcommand routing | `subprocess.run([sys.executable, "scripts/build_acp.py", ...])` | grep for the script's filename in `subprocess.run` calls |
| Slash commands | `/build-loop:scan` | grep `commands/*.md` for the script's CLI path |
| MCP tool dispatch | `mcp__plugin_X__tool` | grep `.mcp.json` and `mcpServers` configs |
| Hook-driven invocation | `hooks/hooks.json` `Stop` handlers | grep `hooks/*.json` for the script path |
| Skill-bundled invocation | `Skill("build-loop:foo")` running an internal script | read the SKILL.md body, not just frontmatter |

**Concrete failure**: priorities 1-7 of the architecture-awareness initiative flagged `scripts/build_acp.py` and `scripts/slice_acp.py` as dead code via static grep. They are reached via `subprocess.run([sys.executable, "scripts/build_acp.py", ...])` from the orchestrator, plus argparse subcommands. Caught only because the orchestrator verified callers before moving the files.

Locked by lesson `lesson-bl-cli-routed-callers` in `.episodic/architecture/lessons.json`.

**Operational rule**: before declaring any script "dead," run all six greps above. If any returns a hit, the script is live. If none returns a hit AND the script has a `__main__` block, treat as "candidate dead, requires user confirmation" — user may invoke it directly via the shell, which leaves no trace in source.
