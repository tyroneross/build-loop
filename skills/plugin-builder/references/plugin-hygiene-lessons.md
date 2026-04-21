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

## Preflight checklist before shipping a plugin change

- [ ] `plugin.json` declares only non-default paths for `hooks`, `mcpServers`, `lsp`
- [ ] Version bumped in `plugin.json`
- [ ] No `type: "prompt"` hooks on per-turn events (PostToolUse:Bash, UserPromptSubmit, PreToolUse:Bash)
- [ ] No absolute paths — use `${CLAUDE_PLUGIN_ROOT}`
- [ ] No personal values in `settings.json`
- [ ] If in an aggregator: marketplace.json version matches, README.md updated
- [ ] Test with `claude --plugin-dir ./my-plugin` in a scratch directory before committing
- [ ] `jq` the `installed_plugins.json` audit command on your own machine — no duplicates for this plugin
