# Authoritative Sources for Claude Code Plugin Development

When the plugin-builder skill or any build-loop phase needs to verify a claim about plugin behavior, cite from this list. T1 sources override training data — if the docs changed, the docs win.

## Tier 1 — Anthropic official (ground truth)

| Topic | URL |
|---|---|
| Plugins reference (full schema, all components) | https://code.claude.com/docs/en/plugins-reference |
| Plugin marketplaces (how to create/host/publish) | https://code.claude.com/docs/en/plugin-marketplaces |
| Plugins tutorial | https://code.claude.com/docs/en/plugins |
| Discover and install plugins | https://code.claude.com/docs/en/discover-plugins |
| Plugin dependencies | https://code.claude.com/docs/en/plugin-dependencies |
| Settings (enabledPlugins, extraKnownMarketplaces, strictKnownMarketplaces) | https://code.claude.com/docs/en/settings |
| Hooks reference | https://code.claude.com/docs/en/hooks |
| Skills | https://code.claude.com/docs/en/skills |
| Subagents | https://code.claude.com/docs/en/sub-agents |
| Tools reference (Monitor, etc.) | https://code.claude.com/docs/en/tools-reference |

Anthropic also ships a documentation index at https://code.claude.com/docs/llms.txt — fetch it first if you don't know the exact page.

## Tier 1 — MCP protocol

| Topic | URL |
|---|---|
| Model Context Protocol spec | https://modelcontextprotocol.io/ |
| MCP specification (versioned) | https://spec.modelcontextprotocol.io/ |
| TypeScript SDK | https://github.com/modelcontextprotocol/typescript-sdk |
| Python SDK | https://github.com/modelcontextprotocol/python-sdk |

## Tier 2 — Working plugin examples (reference implementations)

Use these when you want to see a specific pattern in production code. Prefer bundled-with-`tsup` plugins as the cleanest MCP packaging pattern.

| Pattern | Repo |
|---|---|
| `tsup`-bundled MCP server (single-file `dist/`, no runtime deps) | https://github.com/tyroneross/interface-built-right |
| Aggregator marketplace with 16 symlinked plugins | https://github.com/tyroneross/RossLabs-AI-Toolkit |
| Session-continuity plugin (hooks + MCP + CLI) | https://github.com/tyroneross/bookmark |
| Architecture-scan plugin (CLI-heavy, TypeScript + tsc) | https://github.com/tyroneross/NavGator |
| Build-loop orchestrator plugin (multiple subagents + skills) | https://github.com/tyroneross/build-loop |
| Anthropic's official plugins | https://github.com/anthropics/claude-plugins-official |

## Validation tooling

| Tool | What it checks |
|---|---|
| `claude plugin validate <path>` | `plugin.json`, skill/agent/command frontmatter, `hooks/hooks.json` schema |
| `claude --debug` at startup | Plugin load errors, MCP init errors |
| `/plugin` → "Needs attention" tab | Runtime MCP failures |
| `/mcp` | Live MCP server status (more detail than `/plugin`) |
| `/doctor` | Manifest drift, duplicate hook declarations |

## Sanity tests

**MCP server standalone**:
```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | \
  CLAUDE_PLUGIN_ROOT=<plugin-path> node <plugin-path>/dist/mcp/server.js
```

**Duplicate-install audit**:
```bash
jq 'keys | group_by(split("@")[0]) | map(select(length > 1))' \
  ~/.claude/plugins/installed_plugins.json
```

**Cache completeness audit**:
```bash
for p in ~/.claude/plugins/cache/*/*/; do
  name=$(basename $(dirname $p))
  ver=$(basename $p)
  nm=$([ -d "$p/node_modules" ] && echo y || echo n)
  dist=$([ -d "$p/dist" ] && echo y || echo n)
  echo "$name/$ver: node_modules=$nm dist=$dist"
done
```

**Zombie-marketplace audit** (after a cleanup, verify nothing re-seeds):
```bash
ls ~/.claude/plugins/.install-manifests/
ls ~/.claude/plugins/marketplaces/
jq '.extraKnownMarketplaces | keys' ~/.claude/settings.json
jq 'keys' ~/.claude/plugins/known_marketplaces.json
```

## Behavior rules worth remembering

These are extracted from the docs above; cite the URL when quoting.

1. **Plugin manifest is optional.** If omitted, Claude Code auto-discovers components and derives the name from the directory. Use a manifest only when you need metadata or custom component paths. ([plugins-reference](https://code.claude.com/docs/en/plugins-reference#plugin-manifest-schema))
2. **`name` is the only required manifest field.** Kebab-case, no spaces.
3. **`${CLAUDE_PLUGIN_ROOT}`** — path to plugin install dir. Changes on every plugin update.
4. **`${CLAUDE_PLUGIN_DATA}`** — persistent state dir at `~/.claude/plugins/data/<id>/`. Survives updates. Right place for `node_modules/`, virtualenvs, generated code.
5. **Plugin cache lives at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`.** Each version is a separate dir. Orphaned versions are removed 7 days after an update or uninstall.
6. **Path traversal (`../shared-utils`) fails after install** — cached plugins can't see files outside their dir. Use symlinks if needed; they're preserved in the cache.
7. **Relative paths** in the manifest must start with `./` and be relative to the plugin root.
8. **Marketplace schema** requires `name` (kebab-case), `owner.name`, and `plugins[]`. Reserved names: see lesson #12 in `plugin-hygiene-lessons.md`.
9. **`strict: true`** (default) means `plugin.json` is authoritative. Marketplace entry can supplement. `strict: false` = marketplace entry is the full definition.
10. **Version precedence** — `plugin.json.version` wins over `marketplace.json` plugin entry silently. Set in one place only; for relative-path plugins, set in the marketplace; for everything else, in `plugin.json`.

Always verify before acting: open the relevant page above, quote the section, then apply.
