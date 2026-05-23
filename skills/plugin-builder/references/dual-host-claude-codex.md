<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross | SPDX-License-Identifier: Apache-2.0 -->

# Dual-Host Plugin Pattern: Claude Code + Codex

Every plugin in the `rosslabs-ai-toolkit` marketplace ships to **both** Claude Code and Codex from a single repository. This reference covers the shared structure, per-host manifests, and what stays agent-neutral.

## Why dual-host

Codex (OpenAI's coding agent CLI) and Claude Code have converged on plugins as the packaging unit. The internals (skills, MCP servers, markdown commands) are largely agent-neutral. The per-host differences are thin: a manifest file per host, slightly different naming conventions, separate install surfaces.

Shipping to both from one repo means:
- One source of truth for skills, MCP tools, prompts
- One CI/release pipeline
- Users on either host get fixes at the same time

## Repo layout

```
my-plugin/
├── .claude-plugin/
│   └── plugin.json              # Claude Code manifest
├── .codex-plugin/
│   └── plugin.json              # Codex manifest (this file)
├── .agents/                     # Codex workspace-install metadata (optional)
│   └── plugins/
│       └── marketplace.json
├── skills/                      # Agent-neutral — both hosts load from here
│   └── <skill-name>/
│       └── SKILL.md
├── commands/                    # Mostly agent-neutral markdown
├── agents/                      # Claude-specific (Codex ignores)
├── hooks/                       # Claude-specific (Codex has its own hook system)
├── .mcp.json                    # Agent-neutral — both hosts load MCP from here
└── package.json
```

**Agent-neutral surfaces** (one copy, both hosts consume):
- `skills/*/SKILL.md` — markdown with YAML frontmatter
- `.mcp.json` — MCP server configuration
- `commands/*.md` — when they're pure prompts without Claude-only frontmatter

**Claude-specific** (lives in standard Claude Code paths, Codex ignores):
- `.claude-plugin/plugin.json`
- `agents/*.md` with Claude frontmatter (`model: sonnet`, `isolation: worktree`, etc.)
- `hooks/hooks.json` with Claude hook events (`PostToolUse`, `Stop`, etc.)

**Codex-specific**:
- `.codex-plugin/plugin.json`
- `.agents/plugins/marketplace.json` (workspace-level install metadata)

## `.codex-plugin/plugin.json` — required shape

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "Brief description matching what you shipped to Claude",
  "author": {
    "name": "Your Name",
    "url": "https://github.com/you"
  },
  "homepage": "https://github.com/you/my-plugin#readme",
  "repository": "https://github.com/you/my-plugin",
  "license": "MIT",
  "keywords": ["tag1", "tag2"],
  "skills": "./skills",
  "mcpServers": "./.mcp.json",
  "interface": {
    "displayName": "My Plugin",
    "shortDescription": "One-line description shown in Codex marketplace UI.",
    "longDescription": "Full paragraph shown on the plugin detail page. Describe what it does and when a user should install it.",
    "developerName": "Your Company",
    "category": "Coding",
    "capabilities": [
      "Read",
      "Write"
    ]
  }
}
```

### Field rules

| Field | Required | Rule |
|---|---|---|
| `name` | yes | kebab-case. Match the Claude plugin name — users shouldn't see two different names for the same plugin. |
| `version` | yes | semver. Keep in sync with `.claude-plugin/plugin.json` version — users think of it as one plugin. |
| `description` | yes | One-sentence. Matches the Claude manifest's description for consistency. |
| `skills` | yes if skills exist | Always `"./skills"` — same path Claude uses. |
| `mcpServers` | yes if MCP exists | Always `"./.mcp.json"` — same file Claude uses. |
| `commands` | optional | Only if you have agent-neutral markdown commands to expose. |
| `interface.displayName` | yes | Title case; this is what users see in the Codex marketplace. |
| `interface.shortDescription` | yes | Under ~150 chars. Marketplace card text. |
| `interface.longDescription` | yes | Full plugin-detail page. Can reuse `description` if the plugin is simple. |
| `interface.developerName` | yes | Company/team name. All Ross Labs plugins use `"Ross Labs"`. |
| `interface.category` | yes | One of: `Coding`, `Productivity`, `Content`, `Research`, `Design`, etc. Pick from Codex's current list. |
| `interface.capabilities` | yes | Array of coarse permissions Codex should surface at install: `Read`, `Write`. |

## `.agents/plugins/marketplace.json` — workspace install metadata (optional)

This file lets Codex install the plugin from the **local workspace** (`./`) without publishing to a registry. Use it for plugins under active local development.

```json
{
  "name": "my-plugin-local-workspace",
  "interface": { "displayName": "My Plugin Workspace" },
  "plugins": [
    {
      "name": "my-plugin",
      "source": { "source": "local", "path": "./" },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Coding"
    }
  ]
}
```

If you only distribute via the public GitHub marketplace (not local workspace), you can skip this file.

## README pattern — announce the Codex surface

When you add Codex support to an existing Claude plugin, append a short section to the README so existing users know the Codex install surface exists. Used verbatim across rosslabs-ai-toolkit plugins:

```markdown
## Codex

This package ships an additive Codex plugin surface alongside the existing
Claude Code package. The Claude package remains authoritative for Claude
behavior; the Codex package adds a parallel `.codex-plugin/plugin.json`
install surface without changing the Claude runtime.

Package root for Codex installs:
- the repository root (`.`)

Primary Codex surface:
- skills from `./skills` when present
- MCP config from `./.mcp.json` when present

Install the package from this package root using your current Codex plugin
install flow. The Codex package is additive only: Claude-specific hooks,
slash commands, and agent wiring remain unchanged for Claude Code.
```

## package.json scripts pattern

When the plugin has install scripts (most do), pair them so users can pick their host. From NavGator:

```json
{
  "scripts": {
    "install:claude": "bash scripts/install-plugin.sh --global",
    "install:codex": "bash scripts/install-codex-plugin.sh --user",
    "install:codex-workspace": "bash scripts/install-codex-plugin.sh --workspace"
  },
  "files": [
    "dist/",
    "skills/",
    ".claude-plugin/",
    ".codex-plugin/",
    ".agents/",
    "scripts/install-plugin.sh",
    "scripts/install-codex-plugin.sh"
  ]
}
```

The `files` array is what ships to npm — make sure both manifest directories and both install scripts are listed.

## What stays the same

Do not duplicate content:

- **Skills**: one `skills/<name>/SKILL.md`, both hosts invoke it. YAML frontmatter that's Claude-specific (like `disable-model-invocation`) is silently ignored by Codex.
- **MCP servers**: one `.mcp.json`, both hosts launch the same server with the same `${CLAUDE_PLUGIN_ROOT}` substitution (Codex uses the same env-var name).
- **Commands as markdown**: if your `commands/*.md` is a pure prompt without Claude frontmatter, both hosts can read it.
- **`package.json`**: one source of truth for scripts and `files[]`.

## What to duplicate minimally

- **Plugin manifest** (`.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`) — two files, same `name` / `version` / `description` / `keywords`. Codex adds an `interface` block; Claude doesn't need it.
- **README** — one README, add the Codex section at the bottom.

## What stays Claude-only

- `agents/*.md` with Claude subagent frontmatter (Codex has no equivalent yet)
- `hooks/hooks.json` with Claude hook events (Codex has a separate hook system — port if you want equivalent behavior, but don't expect parity)

## Common mistakes

| Mistake | Fix |
|---|---|
| Divergent `name` or `version` between Claude and Codex manifests | Keep them identical — same plugin, same version |
| Duplicating `skills/` under `.codex-plugin/skills/` | One `skills/` at repo root, both manifests point to it via `"skills": "./skills"` |
| Forgetting `.codex-plugin/` in `package.json` `files[]` | Add it — otherwise `npm publish` ships a broken package for Codex users |
| `interface.capabilities` claims more than the plugin uses | Codex surfaces this at install time; claiming unused permissions looks worse, not better |
| Missing `interface` block entirely | Codex marketplace UI will show raw name/description with no formatting; always include the block |
| README doesn't mention Codex | Existing users don't discover the new install surface |

## Preflight checklist (supplements the plugin-builder checklist)

- [ ] `.codex-plugin/plugin.json` exists and validates as JSON
- [ ] `name` and `version` match `.claude-plugin/plugin.json`
- [ ] `interface.displayName`, `shortDescription`, `longDescription`, `developerName`, `category`, `capabilities` all set
- [ ] `skills` and `mcpServers` paths point to the repo-root directories (same ones Claude uses)
- [ ] `package.json` `files[]` includes `.codex-plugin/` and `.agents/` if used
- [ ] README has the Codex section so existing users know about the install surface
- [ ] Install scripts exist for both hosts (`install:claude`, `install:codex`) when package.json has a `scripts` section
