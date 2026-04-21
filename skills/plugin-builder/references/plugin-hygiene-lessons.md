# Plugin Hygiene — Lessons Learned

Real-world incidents from building and maintaining the `rosslabs-ai-toolkit` marketplace. Each lesson names the specific failure mode and the rule that prevents recurrence.

## 1. Duplicate hook declarations produce `/doctor` errors

**What happened.** Four plugins (research, spectra, showcase, replit-migrate) declared `"hooks": "./hooks/hooks.json"` in `plugin.json`. Claude Code auto-loads `hooks/hooks.json` by convention, so the explicit declaration registered the file twice, producing:

> Hook load failed: Duplicate hooks file detected: ./hooks/hooks.json resolves to already-loaded file …

**Rule.** Only declare `hooks` in the manifest when the file is at a non-standard path. Same for `mcpServers` (`.mcp.json` auto-loads) and `lsp` (`.lsp.json` auto-loads). Explicit declarations *supplement* auto-discovery, they do not replace it — declaring a default-path file guarantees duplication.

## 2. `type: "prompt"` on `PostToolUse:Bash` is always wrong

**What happened.** `showcase` plugin shipped a PostToolUse:Bash hook with `type: "prompt"` intended to suggest `/showcase:capture` after successful builds. In practice, Claude Code evaluated the prompt after every bash command — `ls`, `grep`, `cat` — producing a stream of "PostToolUse:Bash hook stopped continuation" messages tied to the negative condition ("this isn't a build so don't mention capture"). The user perceived it as aggressive blocking.

**Rule.** `PostToolUse:Bash` fires on every shell command. A `type: "prompt"` hook there turns every command into an LLM evaluation step — expensive in tokens, disruptive in UX. Use `type: "command"` with silent `exit 0` for conditional reminders, or move the reminder to `SessionStart` where it fires once per session. Reserve `type: "prompt"` for truly low-frequency events.

Applies equally to: `UserPromptSubmit`, `PreToolUse:Bash`, any matcher that fires per-turn.

## 3. Marketplace renames leave install records behind forever

**What happened.** The marketplace `RossLabs-claude-plugins` was renamed to `rosslabs-ai-toolkit` (kebab-case is required by Claude Code's marketplace schema, 2026-04-20). Plugins previously installed from the old name kept their install records in `~/.claude/plugins/installed_plugins.json` pointing at cache paths under `RossLabs-claude-plugins/` that no longer received updates. The resulting state:
- Same plugin, two install keys (old + new)
- Stop hooks from the old install pointing at paths like `/Users/.../claude-code-debugger/claude-code-debugger/1.8.0` that didn't exist
- `/plugin` UI showed both as "installed"

**Rule.** Renaming a marketplace is a full migration, not a metadata change. Checklist:
1. `/plugin` → uninstall every plugin from the old marketplace name
2. Remove the old marketplace from `extraKnownMarketplaces` in `settings.json`
3. Re-add the new marketplace
4. Reinstall each plugin from the new name
5. Audit `installed_plugins.json` for any remaining old-name keys — remove them only if the uninstall didn't

Document the rename in a feedback memory so future sessions handle it correctly.

## 4. `@local` and `@marketplace` for the same plugin is a footgun

**What happened.** Local development on `bookmark`, `showcase`, `mockup-gallery`, and `NavGator` registered each as `@local` directory installs. The same plugins also existed in the `rosslabs-ai-toolkit` marketplace. Both installs stayed "enabled" simultaneously — both MCP servers started, duplicate slash commands registered, hooks fired twice. The Installed view showed "bookmark MCP · failed" twice, one per copy.

**Rule.** Pick one at a time. When iterating on source, disable the marketplace install. When consuming normally, disable the `@local`. The `enabledPlugins` map in `settings.json` is the source of truth — set the non-active source to `false`.

Better: use `EnterWorktree` or a separate test project directory for plugin development, so the live user environment is not polluted with dev installs.

## 5. Cache directories are downstream; commit source before expecting changes to persist

**What happened.** Editing hook files under `~/.claude/plugins/cache/rosslabs-ai-toolkit/showcase/0.1.1/hooks/hooks.json` took effect immediately, but the next marketplace sync (pulling the GitHub repo) overwrote the edit with the repo's original file. Hours of debugging lost to the illusion of a persistent fix.

**Rule.** Cache is regenerated from the marketplace's upstream repo. Always edit the source (`~/Desktop/git-folder/RossLabs-AI-Toolkit/plugins/<plugin>/…`), commit, push. If you need an immediate fix in the current session, edit both the source and the cache — the cache copy keeps the session alive until you push, then the next sync reconciles.

The marketplace repo is the source of truth. Nothing under `~/.claude/plugins/cache/` is authored — it's all generated.

## 6. Aggregator marketplaces hide their own update failures

**What happened.** When a plugin inside `rosslabs-ai-toolkit` ships a fix, the user has to run `/plugin` update on the aggregator, not on the individual plugin. Updates to individual plugins in the aggregator are not auto-pulled when only the plugin's version bumps — the aggregator's own marketplace.json must reflect the new version. Forgetting to update `marketplace.json` means users install stale plugin versions even though the GitHub repo has the fix.

**Rule.** When shipping a plugin in an aggregator marketplace, always update three things: (a) the plugin's own `version` in `plugin.json`, (b) the marketplace entry for that plugin in `.claude-plugin/marketplace.json`, (c) the README.md list showing installed plugins. If any of the three are missing, users get inconsistent views. See `feedback_rosslabs_toolkit_sync.md` for the full skill.

## 7. `.mcp.json` MUST wrap servers in `"mcpServers"`

**What happened.** `navgator` and `showcase` shipped a flat `.mcp.json`:
```json
{ "navgator": { "command": "node", "args": [...] } }
```
instead of the required wrapped form:
```json
{ "mcpServers": { "navgator": { "command": "node", "args": [...] } } }
```
The flat form silently loaded without errors in `/doctor`, but the MCP server never started. `/mcp` showed "plugin:gator:navgator · failed" with no helpful error. Node would try to launch and immediately fail because Claude Code couldn't locate the server definition.

**Rule.** `.mcp.json` top-level must be `{"mcpServers": {...}}`. Inline manifest declarations (`"mcpServers": {...}` in `plugin.json`) use the same structure minus the outer object. Only `/mcp` shows this failure, not `/doctor` — always open both when verifying plugin health.

## 8. Marketplace sync omits `dist/` and `node_modules/`

**What happened.** Plugins that require a TypeScript build (`tsc` producing `dist/mcp/server.js`) were synced into `~/.claude/plugins/cache/` without their `dist/` directories. The cached `plugin.json` pointed at `${CLAUDE_PLUGIN_ROOT}/dist/mcp/server.js` which didn't exist. MCP failed silently at startup. Same for `node_modules/` needed by the compiled output.

**Rule.** Either:
- Ship pre-bundled output (e.g. `tsup` producing a single file that bundles all deps) so the plugin doesn't need `node_modules`
- OR ensure the marketplace publish process includes `dist/` in the plugin's repo (not gitignored) and runs `npm install --production` as a postinstall step in the plugin's cache directory
- OR use a postinstall hook that rebuilds on install (acceptable but slow)

ibr uses `tsup` and ships a 525KB bundled `dist/mcp/server.js` that runs standalone — this is the cleanest pattern. showcase, navgator, spectra use `tsc` which requires `node_modules/` at runtime — fragile.

## 9. `/doctor` catches manifest issues; `/plugin` and `/mcp` catch install/runtime issues

Use both. `/doctor` surfaces load-time failures (bad hooks, broken manifests, missing commands). `/plugin` (Installed tab) surfaces runtime issues (MCP servers that won't start, duplicate installs). They report different layers and will not overlap.

## 8. Never commit `settings.json` changes to a plugin

**Relevant to plugin authors.** A plugin's `settings.json` sets *default* settings — values Claude Code merges into the user's config. Writing absolute paths, your local API keys, or your personal `enabledPlugins` map into a plugin's `settings.json` ships your machine's state to every user. Plugin-level `settings.json` should only contain defaults the user is expected to override (usually empty or near-empty).

## 9. Removing a marketplace from `known_marketplaces.json` is not enough

**What happened (2026-04-21).** After a marketplace consolidation, editing `~/.claude/plugins/known_marketplaces.json` and running `/reload-plugins` made the removed marketplaces come right back. Five "zombie" marketplaces kept auto-re-registering on every reload: `bookmark`, `interface-built-right`, `mockup-gallery`, `navgator`, `build-loop`.

**Rule.** Claude Code re-seeds `known_marketplaces.json` from multiple persistent sources on every reload. To fully remove a marketplace, clean all of:

| Location | What it does | How to clean |
|---|---|---|
| `~/.claude/settings.json` → `extraKnownMarketplaces` | User-level persistent marketplace definitions. Re-registers on every reload. | Delete the entry. Highest-priority cleanup target. |
| `~/.claude/settings.json` → `enabledPlugins` | Keys like `"plugin@marketplace": true` also re-register the marketplace implicitly. | Delete dead entries. |
| `~/.claude/settings.json` → top-level `plugins` array (deprecated) | Legacy paths like `".../bookmark/.claude-plugin"` re-add plugins and their marketplace. | Remove or replace with empty array. |
| `~/.claude/plugins/.install-manifests/<plugin>@<marketplace>.json` | Per-install manifests with hashes. Each file implicitly keeps its marketplace registered. | Archive or delete the manifest files. |
| `~/.claude/plugins/marketplaces/<name>/` | Physical clone of a git-sourced marketplace. Presence can trigger auto-registration. | Archive the directory. |
| Project-scope `.claude/settings.json` → `extraKnownMarketplaces` | Team-level injection that re-registers when you trust the folder. | Audit `git-folder/*/.claude/settings.json`. |
| `~/.claude/plugins/known_marketplaces.json` | The runtime registry. Rewritten each reload from the sources above. | Clean this LAST so there's nothing to rewrite it from. |

Cleanup ordering matters: purge the re-seeding sources first, only then rewrite `known_marketplaces.json`. Otherwise the next reload resurrects everything.

## 10. Partial cache dirs from interrupted updates confuse plugin resolution

**What happened.** Plugins had two cache directories for the same plugin, e.g. `claude-code-debugger/1.8.0/` (complete) and `claude-code-debugger/1.8.1/` (partial — missing `dist/` and `node_modules/`). Claude Code saw 1.8.1 as the "installed version" per `installed_plugins.json` but the `installPath` still pointed at 1.8.0. Additionally, the newer directory was incomplete so even if Claude tried to use it, the MCP server failed to start.

**Rule.**
- **Verify `installed_plugins.json` version matches installPath** — if `version: "1.8.1"` but installPath ends in `/1.8.0/`, something is stale. Align them.
- **Audit for incomplete cache dirs:** for each `<plugin>/<version>/`, check `dist/` and `node_modules/` presence if the plugin needs them. Missing = delete the incomplete dir.
- **Do not manually `cp` files between version dirs.** Either let `/plugin update` regenerate cleanly, or remove the bad version and let Claude Code re-fetch.

Quick audit:
```bash
for p in ~/.claude/plugins/cache/*/*/; do
  name=$(basename $(dirname $p))
  ver=$(basename $p)
  nm=$([ -d "$p/node_modules" ] && echo y || echo n)
  dist=$([ -d "$p/dist" ] && echo y || echo n)
  echo "$name/$ver: node_modules=$nm dist=$dist"
done
```

## 11. `${CLAUDE_PLUGIN_DATA}` is the right home for build artifacts

**Context.** TypeScript plugins that bundle with `tsc` need `node_modules/` at runtime. The marketplace sync doesn't include `node_modules/`, so cached plugins arrive without dependencies and MCP servers fail at startup.

**Rule.** Three correct patterns, in order of preference:

1. **Bundle with `tsup`** — single-file `dist/mcp/server.js` that embeds all deps. No `node_modules/` needed at runtime. IBR follows this pattern. Ship `dist/` in git (don't gitignore it).
2. **SessionStart hook with `${CLAUDE_PLUGIN_DATA}`** — install deps once into the persistent data dir, not the cache. Survives plugin updates.
   ```json
   {
     "hooks": {
       "SessionStart": [{
         "hooks": [{
           "type": "command",
           "command": "diff -q \"${CLAUDE_PLUGIN_ROOT}/package.json\" \"${CLAUDE_PLUGIN_DATA}/package.json\" >/dev/null 2>&1 || (cd \"${CLAUDE_PLUGIN_DATA}\" && cp \"${CLAUDE_PLUGIN_ROOT}/package.json\" . && npm install)"
         }]
       }]
     }
   }
   ```
   Then point MCP at the bundled script with `NODE_PATH=${CLAUDE_PLUGIN_DATA}/node_modules`.
3. **Commit `dist/` + use pure-stdlib server** — smallest deliverable but only viable for servers with zero runtime deps.

`${CLAUDE_PLUGIN_ROOT}` changes on every plugin update; data there doesn't survive. `${CLAUDE_PLUGIN_DATA}` persists at `~/.claude/plugins/data/<id>/`.

## 12. Reserved marketplace names

**Context.** Claude Code rejects these names at publish/sync time:
- `claude-code-marketplace`, `claude-code-plugins`, `claude-plugins-official`
- `anthropic-marketplace`, `anthropic-plugins`
- `agent-skills`, `knowledge-work-plugins`, `life-sciences`
- Any name that impersonates the above (`official-claude-plugins`, `anthropic-tools-v2`, etc.)

**Rule.** Use a clearly-original kebab-case name that identifies you or your team. Validate before publishing: the `claude.ai` marketplace sync rejects non-kebab-case names silently even when the local `/plugin` flow accepts them.

## 13. Testing an MCP server without Claude Code

**Rule.** You can verify a Claude Code plugin's MCP server is healthy without any plugin machinery by sending the `initialize` RPC directly:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | \
  CLAUDE_PLUGIN_ROOT=/path/to/plugin \
  node /path/to/plugin/dist/mcp/server.js
```

A healthy server responds with a single-line JSON-RPC `result` containing `protocolVersion`, `serverInfo.name`, `serverInfo.version`, and `capabilities`. If you see nothing, a stack trace, or `EACCES`/`MODULE_NOT_FOUND`, the server has a real failure.

This distinguishes "server works but Claude Code's UI shows stale failure" from "server actually broken." Extremely useful for debugging the `/plugin` "Needs attention" pane — which sometimes caches failure status across reloads.

## 14. Kebab-case and reserved-name checks happen at different stages

**Rule.** Anthropic's `claude.ai` marketplace sync is stricter than the local `/plugin` install flow:
- Local flow: accepts `UpperCase`, `under_scores`, even short paths. Shows warnings but loads.
- claude.ai sync: rejects non-kebab-case plugin or marketplace names with no override.

Check kebab-case for both `marketplace.json.name` and every `plugins[].name` before publishing. The fastest way to catch this: run `claude plugin validate .` in the marketplace root.

## Preflight checklist before shipping a plugin change

- [ ] `plugin.json` declares only non-default paths for `hooks`, `mcpServers`, `lsp`
- [ ] `plugin.json` lives at `.claude-plugin/plugin.json` (not plugin root)
- [ ] Version bumped in `plugin.json`
- [ ] No `type: "prompt"` hooks on per-turn events (PostToolUse:Bash, UserPromptSubmit, PreToolUse:Bash)
- [ ] No absolute paths — use `${CLAUDE_PLUGIN_ROOT}`
- [ ] No personal values in `settings.json`
- [ ] `.mcp.json` uses `{"mcpServers": {...}}` wrapper (not flat form)
- [ ] MCP server responds to `initialize` RPC when launched directly
- [ ] `dist/` is in git (not gitignored) OR bundled via `tsup` OR rebuilt via SessionStart hook with `${CLAUDE_PLUGIN_DATA}`
- [ ] Plugin name and marketplace name are kebab-case
- [ ] If in an aggregator: marketplace.json version matches plugin.json, README.md updated
- [ ] Test with `claude --plugin-dir ./my-plugin` in a scratch directory before committing
- [ ] `claude plugin validate .` passes in the marketplace root
- [ ] `jq` the `installed_plugins.json` audit command on your own machine — no duplicates for this plugin
