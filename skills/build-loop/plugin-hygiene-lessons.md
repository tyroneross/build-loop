# Plugin Hygiene — Build-Loop Lessons

When build-loop phases produce, modify, or ship Claude Code plugins, apply these rules. Each is traceable to a real failure in the `rosslabs-ai-toolkit` marketplace.

## Assess phase

Before modifying a plugin, check for duplicate installs:

```bash
jq 'keys | group_by(split("@")[0]) | map(select(length > 1))' \
  ~/.claude/plugins/installed_plugins.json
```

Any duplicate entry means the user has the same plugin installed from multiple sources (e.g. `@local` + `@marketplace`). Edits to one source don't reach the other. **Flag this in the Assess report and offer to consolidate before proceeding.**

## Plan phase

When the plan touches `plugin.json`, require an explicit field inventory. Do not write these fields if the referenced file is at its default path:

| Field | Auto-loaded path | When to declare |
|-------|------------------|-----------------|
| `hooks` | `hooks/hooks.json` | Only for non-standard path |
| `mcpServers` | `.mcp.json` | Only for non-standard path or inline definitions |
| `lsp` | `.lsp.json` | Only for non-standard path |

Declaring a default-path file produces `Duplicate hooks file detected` errors in `/doctor`. Fact-check this in Review-D.

## Execute phase

**Never emit `type: "prompt"` hooks on per-turn events.** PostToolUse:Bash, UserPromptSubmit, and PreToolUse:Bash fire on every tool call. A prompt hook there runs the LLM on every event — expensive in tokens, disruptive in UX (streams "hook stopped continuation" messages). If a plugin needs conditional nudges, use `type: "command"` with silent exit 0.

Allowed locations for `type: "prompt"` hooks:
- SessionStart (fires once per session)
- Stop (fires once per turn-end)
- PreCompact (fires once per compaction)

## Review phase (sub-step D: Fact-Check)

Add to the manifest-drift check:
1. Grep plugin.json files in the diff for `"hooks":`, `"mcpServers":`, `"lsp":`
2. For each match, verify the referenced file is NOT at the auto-loaded default path
3. Grep hook files for `"type": "prompt"` inside `PostToolUse`, `PreToolUse`, `UserPromptSubmit`
4. If the plugin ships in an aggregator marketplace, verify marketplace.json version matches plugin.json version
5. **`.mcp.json` schema**: verify top-level key is `"mcpServers"`. Flat form `{"<name>": {...}}` silently passes `/doctor` but fails at MCP startup — only `/mcp` surfaces the failure. Correct form is `{"mcpServers": {"<name>": {...}}}`.
6. **Build artifacts**: if plugin.json's `mcpServers.*.args` references `${CLAUDE_PLUGIN_ROOT}/dist/...`, verify `dist/` is not gitignored and is checked into the repo. Alternative: plugin uses `tsup` to bundle into a single self-contained file (preferred — ibr's pattern).

## Iterate phase

If `/doctor` still reports errors after Review, common root causes in order of likelihood:

1. Duplicate hooks/mcpServers declaration → remove field from manifest
2. `type: "prompt"` on high-frequency event → change to command or move to SessionStart
3. Stale install record → user needs to `/plugin` uninstall old-marketplace copy
4. Cache regenerated from marketplace, overwriting local fix → commit+push source repo, re-sync

## Marketplace rename checklist

If build-loop is executing a marketplace rename (e.g. detected in the Assess phase via `extraKnownMarketplaces` showing old + new names simultaneously), generate this task list:

- [ ] `/plugin` → uninstall every plugin from the old marketplace name
- [ ] Remove old marketplace entry from `settings.json` → `extraKnownMarketplaces`
- [ ] Re-add new marketplace (kebab-case name — Anthropic schema requires it)
- [ ] Reinstall each plugin from the new name
- [ ] Audit `installed_plugins.json` for residual old-name keys
- [ ] Update any CLAUDE.md memory referencing the old name
- [ ] Update README.md in the marketplace repo
- [ ] Bump marketplace.json version

The rename is a full migration, not a metadata change. Partial renames produce permanent stale install paths.

## Source-of-truth flow

```
~/Desktop/git-folder/<plugin-repo>/            ← author-owned, edit here, commit
       │
       ▼ (git push)
GitHub: tyroneross/<marketplace>/              ← marketplace pulls from here
       │
       ▼ (plugin sync)
~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/   ← generated, never edit long-term
       │
       ▼ (runtime)
Claude Code session
```

Build-loop must edit the source repo, not the cache. Cache edits survive until the next sync, then vanish. If an edit MUST land this session, edit both source and cache, then commit source before Review ends.

## References

- `~/.claude/skills/plugin-builder/references/plugin-hygiene-lessons.md` — full incident log
- `~/.claude/projects/-Users-tyroneross/memory/feedback_hook_design.md` — hook design rules
- `~/.claude/projects/-Users-tyroneross/memory/feedback_rosslabs_toolkit_sync.md` — marketplace rename sync
