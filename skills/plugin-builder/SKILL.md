---
name: plugin-builder
description: This skill should be used when the user asks to "create a plugin", "build a plugin", "scaffold a plugin", "make a Claude Code plugin", "plugin structure", "plugin.json", "convert to plugin", "migrate to plugin", "package as plugin", or needs guidance on plugin directory layout, manifest configuration, component organization, hooks, MCP servers, agents, LSP servers, testing, or distribution for Claude Code plugins.
---

# Plugin Builder

Build Claude Code plugins following official documentation and best practices.

## When to Use Plugins vs Standalone

| Approach | Skill names | Best for |
|----------|-------------|----------|
| **Standalone** (`.claude/`) | `/hello` | Personal workflows, single-project, quick experiments |
| **Plugin** (`.claude-plugin/plugin.json`) | `/plugin-name:hello` | Sharing with team, distributing, versioned, reusable across projects |

**Use standalone when:** Single project, personal, experimenting, want short names.
**Use plugins when:** Sharing with team/community, need same skills across projects, want version control.

## Plugin Creation Workflow

### Step 1: Create the Directory Structure

```bash
mkdir -p my-plugin/.claude-plugin
mkdir -p my-plugin/{commands,agents,skills,hooks,scripts}
```

**Standard layout:**
```
my-plugin/
├── .claude-plugin/
│   └── plugin.json          # ONLY manifest here
├── commands/                 # Slash commands (*.md files)
├── agents/                   # Subagent definitions (*.md files)
├── skills/                   # Skills (subdirs with SKILL.md)
│   └── my-skill/
│       ├── SKILL.md
│       └── references/
├── hooks/
│   └── hooks.json            # Event handlers
├── scripts/                  # Utility scripts
├── .mcp.json                 # MCP server configs
├── .lsp.json                 # LSP server configs
├── settings.json             # Default settings
└── CHANGELOG.md
```

**CRITICAL:** Components go at plugin root, NOT inside `.claude-plugin/`. Only `plugin.json` goes in `.claude-plugin/`.

### Step 2: Create the Manifest

Create `.claude-plugin/plugin.json`:

```json
{
  "name": "my-plugin",
  "description": "Brief description of what the plugin does",
  "version": "1.0.0",
  "author": {
    "name": "Your Name"
  }
}
```

**`name` is the only required field.** It becomes the namespace prefix for all components (`/my-plugin:skill-name`).

**Naming rules:**
- kebab-case, no spaces
- Used as namespace for all skills/commands
- Cannot use "claude" or "anthropic"

For the complete manifest schema with all optional fields, see `references/manifest-schema.md`.

### Step 3: Add Components

#### Skills (Recommended)
Create a subdirectory under `skills/` with a `SKILL.md`:

```
skills/code-review/
└── SKILL.md
```

Skills are auto-discovered — no manifest entry needed. See the `skill-builder` skill for detailed SKILL.md guidance.

#### Commands
Simple markdown files in `commands/`:

```markdown
---
description: Greet the user
---

Greet the user warmly and ask how you can help.
```

#### Agents
Markdown files in `agents/` defining subagent behavior:

```markdown
---
name: security-reviewer
description: Reviews code for security vulnerabilities
---

Detailed system prompt for the agent...
```

#### Hooks
Create `hooks/hooks.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/scripts/format.sh"
          }
        ]
      }
    ]
  }
}
```

**Hook types:** `command` (shell), `prompt` (LLM evaluation), `agent` (agentic verification)

**Available events:** PreToolUse, PostToolUse, PostToolUseFailure, UserPromptSubmit, Stop, SubagentStop, SessionStart, SessionEnd, PreCompact, Notification, TaskCompleted, TeammateIdle, PermissionRequest, SubagentStart

For detailed hooks configuration, see `references/hooks-reference.md`.

#### MCP Servers
Create `.mcp.json` at plugin root:

```json
{
  "mcpServers": {
    "my-service": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/my-server",
      "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config.json"]
    }
  }
}
```

Always use `${CLAUDE_PLUGIN_ROOT}` for paths — plugins are cached to a different location after install.

#### LSP Servers
Create `.lsp.json` at plugin root:

```json
{
  "go": {
    "command": "gopls",
    "args": ["serve"],
    "extensionToLanguage": { ".go": "go" }
  }
}
```

Users must install the language server binary separately.

#### Default Settings
Create `settings.json` to activate a default agent:

```json
{
  "agent": "security-reviewer"
}
```

### Step 4: Test Locally

```bash
claude --plugin-dir ./my-plugin
```

**Test each component:**
- Skills: `/my-plugin:skill-name`
- Commands: `/my-plugin:command-name`
- Agents: Check `/agents`
- Hooks: Trigger the relevant events
- MCP: Verify tools appear

Load multiple plugins: `claude --plugin-dir ./plugin-one --plugin-dir ./plugin-two`

**Debug issues:** `claude --debug` shows plugin loading details, errors, registration.

### Step 5: Distribute

**Version management:** Semantic versioning (MAJOR.MINOR.PATCH). Bump version before distributing — users won't see changes without a version bump due to caching.

**Distribution options:**
1. Host on GitHub with README and installation guide
2. Create a marketplace (see `references/distribution.md`)
3. Submit to official Anthropic marketplace

## Key Environment Variables

| Variable | Description |
|----------|-------------|
| `${CLAUDE_PLUGIN_ROOT}` | Absolute path to plugin directory. Use in hooks, MCP, scripts. |

## Auto-Discovery Rules

Claude Code automatically discovers components in default locations:
- `commands/` → `*.md` files become slash commands
- `agents/` → `*.md` files become subagents
- `skills/` → subdirectories with `SKILL.md` become skills
- `hooks/hooks.json` → hook configurations
- `.mcp.json` → MCP server definitions
- `.lsp.json` → LSP server configurations

Custom paths in `plugin.json` **supplement** defaults, they don't replace them.

## Converting Standalone to Plugin

1. Create plugin structure with `.claude-plugin/plugin.json`
2. Copy `.claude/commands/` → `my-plugin/commands/`
3. Copy `.claude/agents/` → `my-plugin/agents/`
4. Copy `.claude/skills/` → `my-plugin/skills/`
5. Move hooks from `settings.json` to `hooks/hooks.json`
6. Test with `claude --plugin-dir ./my-plugin`

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Components inside `.claude-plugin/` | Move to plugin root |
| Absolute paths in hooks/MCP | Use `${CLAUDE_PLUGIN_ROOT}` |
| Script not executable | `chmod +x scripts/*.sh` |
| Hook event name wrong case | Use PascalCase: `PostToolUse` not `postToolUse` |
| Version not bumped | Users won't see updates without version change |
| Path traversal (`../shared/`) | Won't work after install — use symlinks if needed |
| Redeclaring `"hooks": "./hooks/hooks.json"` in manifest | Remove it. `hooks/hooks.json` is auto-loaded; redeclaring produces `Duplicate hooks file detected` in `/doctor`. Same for `.mcp.json` at default path. Only declare when using a non-standard path. |
| `type: "prompt"` hooks on high-frequency events | Never use `type: "prompt"` on `PostToolUse:Bash` or `UserPromptSubmit`. They fire on every tool call — LLM must evaluate the prompt each time, which spams "hook stopped continuation" messages and costs tokens. Use `type: "command"` with silent exit (exit 0) for conditional nudges; `type: "prompt"` is only OK on low-frequency events like `SessionStart`. |
| Identical hook in source repo and marketplace aggregator | Edit the source repo manifest — cache under `~/.claude/plugins/cache/` is regenerated from the marketplace repo on every sync, overwriting local edits. Commit + push before expecting changes to persist. |
| Flat `.mcp.json` without `mcpServers` wrapper | Always wrap: `{"mcpServers": {"<name>": {...}}}`. Flat form `{"<name>": {...}}` silently passes `/doctor` but fails at MCP startup — only visible in `/mcp`. |
| Plugin ships without pre-built `dist/` | Either bundle with `tsup` (single-file output, no runtime deps) OR commit `dist/` to the repo OR add a postinstall rebuild. `tsc`-only output that depends on `node_modules/` will fail when the marketplace sync excludes those dirs. |

## Plugin Hygiene (Preventing Install Chaos)

Accumulating installs cause MCP server conflicts, phantom Stop-hook errors, and /doctor warnings. Rules:

**One canonical marketplace per plugin.** If a plugin is shipped via an aggregator (e.g. `rosslabs-ai-toolkit`), do not also register a per-plugin marketplace pointing at the same repo. `extraKnownMarketplaces` in `settings.json` bloats when every source gets added.

**Never `@local` + `@marketplace` for the same plugin concurrently.** When iterating in the source directory, disable the marketplace install first. Dual installs both start MCP servers, both compete for the same `.mcp.json` tools, and one always fails.

**Renaming a marketplace is a full migration.** When a marketplace is renamed (e.g. `RossLabs-claude-plugins` → `rosslabs-ai-toolkit`, kebab-case is required by the schema), every plugin installed from the old name stays in `installed_plugins.json` with a stale install path forever. Uninstall every plugin from the old marketplace, then reinstall from the new one. Edit `installed_plugins.json` by hand only as a last resort — corruption bricks the plugin system.

**Audit periodically:**
```bash
jq 'keys | group_by(split("@")[0]) | map(select(length > 1))' \
  ~/.claude/plugins/installed_plugins.json
```
Returns plugins with multiple install sources. Anything in that list is a duplicate.

**`extraKnownMarketplaces` hygiene.** Each entry is a registered marketplace that `/plugin` can pull from. If you added a directory source for local dev and later moved to the aggregator, remove the dev entry.

## Additional Resources

For detailed reference material, consult:
- **`references/manifest-schema.md`** — Complete plugin.json schema with all fields
- **`references/hooks-reference.md`** — All hook events, types, matchers, and patterns
- **`references/components-guide.md`** — Detailed guide for each component type
- **`references/distribution.md`** — Marketplace creation, versioning, and sharing
- **`references/plugin-hygiene-lessons.md`** — Real-world lessons from the RossLabs-AI-Toolkit marketplace rename and related duplicate-install incidents
