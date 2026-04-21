# Plugin Components Guide

## Overview

| Component | Location | File format | Auto-discovered |
|-----------|----------|-------------|-----------------|
| Skills | `skills/` | Subdirs with `SKILL.md` | Yes |
| Commands | `commands/` | `*.md` files | Yes |
| Agents | `agents/` | `*.md` files | Yes |
| Hooks | `hooks/hooks.json` | JSON | Referenced in manifest |
| MCP Servers | `.mcp.json` | JSON | Referenced in manifest |
| LSP Servers | `.lsp.json` | JSON | Referenced in manifest |
| Settings | `settings.json` | JSON | Auto-loaded |

---

## Skills

Skills are the recommended way to add capabilities. Each skill is a directory containing a `SKILL.md`.

### Structure
```
skills/
├── code-review/
│   ├── SKILL.md
│   ├── references/
│   │   └── patterns.md
│   └── scripts/
│       └── validate.sh
└── deploy/
    └── SKILL.md
```

### SKILL.md Format
```yaml
---
name: code-review
description: Reviews code for best practices and potential issues.
  Use when reviewing code, checking PRs, or analyzing code quality.
---

When reviewing code, check for:
1. Code organization and structure
2. Error handling
3. Security concerns
4. Test coverage
```

### Key Points
- Folder name becomes skill name, prefixed with plugin namespace
- Auto-discovered from `skills/` directory
- Can include supporting files (references/, scripts/, assets/)
- Use the `skill-builder` skill for detailed SKILL.md guidance

---

## Commands

Simple markdown files that create slash commands. Legacy approach — prefer skills for new development.

### Structure
```
commands/
├── status.md
└── logs.md
```

### Format
```markdown
---
description: Show project status
disable-model-invocation: true
---

Check the current project status:
1. Run git status
2. Check for pending changes
3. Report any issues
```

### Key Points
- File name becomes command name (e.g., `status.md` → `/plugin-name:status`)
- Same frontmatter fields as skills
- No supporting files — everything in one markdown file
- Skills take precedence if both share the same name

---

## Agents

Specialized subagents Claude can invoke for specific tasks.

### Structure
```
agents/
├── security-reviewer.md
├── performance-tester.md
└── compliance-checker.md
```

### Format
```markdown
---
name: security-reviewer
description: Reviews code for security vulnerabilities and OWASP top 10 issues.
  Invoke when the user asks to review security, check for vulnerabilities,
  or audit code safety.
---

# Security Reviewer Agent

Analyze code for security issues including:
- SQL injection
- XSS vulnerabilities
- Authentication flaws
- Authorization bypasses
- Sensitive data exposure

For each issue found, provide:
1. Severity (Critical/High/Medium/Low)
2. Location (file:line)
3. Description of the vulnerability
4. Recommended fix with code example
```

### Key Points
- Appear in `/agents` interface
- Claude invokes automatically based on task context
- Can also be invoked manually by users
- The markdown body becomes the agent's system prompt

---

## MCP Servers

Connect Claude to external tools and services.

### Configuration (`.mcp.json`)
```json
{
  "mcpServers": {
    "my-database": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server",
      "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config.json"],
      "env": {
        "DB_PATH": "${CLAUDE_PLUGIN_ROOT}/data"
      }
    },
    "external-api": {
      "command": "npx",
      "args": ["@company/mcp-server", "--plugin-mode"],
      "cwd": "${CLAUDE_PLUGIN_ROOT}"
    }
  }
}
```

### Key Points
- Start automatically when plugin is enabled
- Tools appear as standard MCP tools in Claude's toolkit
- Always use `${CLAUDE_PLUGIN_ROOT}` for paths
- Can be configured independently of user MCP servers

---

## LSP Servers

Give Claude real-time code intelligence (diagnostics, go to definition, find references).

### Configuration (`.lsp.json`)
```json
{
  "go": {
    "command": "gopls",
    "args": ["serve"],
    "extensionToLanguage": {
      ".go": "go"
    }
  }
}
```

### Required Fields
| Field | Description |
|-------|-------------|
| `command` | LSP binary to execute (must be in PATH) |
| `extensionToLanguage` | Maps file extensions to language identifiers |

### Optional Fields
| Field | Description |
|-------|-------------|
| `args` | Command-line arguments |
| `transport` | `stdio` (default) or `socket` |
| `env` | Environment variables |
| `initializationOptions` | Server initialization options |
| `settings` | Workspace settings |
| `restartOnCrash` | Auto-restart on crash |
| `maxRestarts` | Maximum restart attempts |

### Key Points
- Users must install the language server binary separately
- For common languages, install pre-built LSP plugins from official marketplace
- Create custom LSP plugins only for languages not covered

---

## Default Settings (`settings.json`)

Apply default configuration when the plugin is enabled.

```json
{
  "agent": "security-reviewer"
}
```

Currently only the `agent` key is supported. Setting `agent` activates one of the plugin's custom agents as the main thread, applying its system prompt, tool restrictions, and model.

`settings.json` takes priority over `settings` declared in `plugin.json`. Unknown keys are silently ignored.
