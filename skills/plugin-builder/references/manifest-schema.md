# Plugin Manifest Schema Reference

The `.claude-plugin/plugin.json` file defines plugin metadata and configuration.

The manifest is **optional**. If omitted, Claude Code auto-discovers components in default locations and derives the plugin name from the directory name.

## Complete Schema

```json
{
  "name": "plugin-name",
  "version": "1.2.0",
  "description": "Brief plugin description",
  "author": {
    "name": "Author Name",
    "email": "author@example.com",
    "url": "https://github.com/author"
  },
  "homepage": "https://docs.example.com/plugin",
  "repository": "https://github.com/author/plugin",
  "license": "MIT",
  "keywords": ["keyword1", "keyword2"],
  "commands": ["./custom/commands/special.md"],
  "agents": "./custom/agents/",
  "skills": "./custom/skills/",
  "hooks": "./config/hooks.json",
  "mcpServers": "./mcp-config.json",
  "outputStyles": "./styles/",
  "lspServers": "./.lsp.json"
}
```

## Required Fields

Only `name` is required if including a manifest.

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `name` | string | Unique identifier (kebab-case, no spaces) | `"deployment-tools"` |

## Metadata Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `version` | string | Semantic version | `"2.1.0"` |
| `description` | string | Brief explanation | `"Deployment automation tools"` |
| `author` | object | `{name, email?, url?}` | `{"name": "Dev Team"}` |
| `homepage` | string | Documentation URL | `"https://docs.example.com"` |
| `repository` | string | Source code URL | `"https://github.com/user/plugin"` |
| `license` | string | License identifier | `"MIT"`, `"Apache-2.0"` |
| `keywords` | array | Discovery tags | `["deployment", "ci-cd"]` |

## Component Path Fields

| Field | Type | Description |
|-------|------|-------------|
| `commands` | string or array | Additional command files/directories |
| `agents` | string or array | Additional agent files |
| `skills` | string or array | Additional skill directories |
| `hooks` | string, array, or object | Hook config paths or inline config |
| `mcpServers` | string, array, or object | MCP config paths or inline config |
| `outputStyles` | string or array | Output style files/directories |
| `lspServers` | string, array, or object | LSP server configs |

## Path Behavior Rules

- Custom paths **supplement** default directories — they don't replace them
- All paths must be relative to plugin root and start with `./`
- Multiple paths can be specified as arrays:

```json
{
  "commands": [
    "./specialized/deploy.md",
    "./utilities/batch-process.md"
  ],
  "agents": [
    "./custom-agents/reviewer.md",
    "./custom-agents/tester.md"
  ]
}
```

## Installation Scopes

| Scope | Settings file | Use case |
|-------|--------------|----------|
| `user` | `~/.claude/settings.json` | Personal, all projects (default) |
| `project` | `.claude/settings.json` | Team, shared via version control |
| `local` | `.claude/settings.local.json` | Project-specific, gitignored |
| `managed` | Managed settings | Org-wide (read-only, update only) |

## Version Management

Format: `MAJOR.MINOR.PATCH`
- **MAJOR:** Breaking changes (incompatible API changes)
- **MINOR:** New features (backward-compatible)
- **PATCH:** Bug fixes (backward-compatible)

Start at `1.0.0` for first stable release. Pre-release: `2.0.0-beta.1`.

**Important:** Claude Code uses the version to determine updates. If code changes but version doesn't bump, existing users won't see changes due to caching.

## Plugin Caching

Marketplace plugins are copied to `~/.claude/plugins/cache` for security. This means:
- Paths traversing outside plugin root (`../shared-utils`) won't work after install
- Use symlinks for external dependencies (they're honored during copy)
- `--plugin-dir` plugins are used in-place (no caching)
