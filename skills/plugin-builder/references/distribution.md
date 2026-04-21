# Plugin Distribution Guide

## Distribution Methods

### 1. Local Testing (--plugin-dir)
For development and testing:
```bash
claude --plugin-dir ./my-plugin
```
Plugins are used in-place, no caching. Changes picked up on restart.

### 2. GitHub Repository
Host your plugin as a public (or private) repo:
- Include a README.md at repo root (NOT inside skill folders)
- Add installation instructions
- Include example usage and screenshots
- Use semantic versioning with tags

### 3. Plugin Marketplace
Create a marketplace for organized distribution:

**Marketplace structure:**
```
my-marketplace/
├── marketplace.json           # Registry of available plugins
├── plugins/
│   ├── formatter/             # Plugin directories
│   │   ├── .claude-plugin/
│   │   │   └── plugin.json
│   │   └── ...
│   └── linter/
│       └── ...
└── README.md
```

**marketplace.json:**
```json
{
  "name": "My Team Marketplace",
  "version": "1.0.0",
  "plugins": [
    {
      "name": "formatter",
      "description": "Code formatting tools",
      "version": "1.2.0",
      "source": "./plugins/formatter"
    }
  ]
}
```

### 4. Official Anthropic Marketplace
Submit via in-app forms:
- Claude.ai: claude.ai/settings/plugins/submit
- Console: platform.claude.com/plugins/submit

## Installation Commands

```bash
# Install to user scope (default)
claude plugin install formatter@my-marketplace

# Install to project scope (shared with team)
claude plugin install formatter@my-marketplace --scope project

# Install to local scope (gitignored)
claude plugin install formatter@my-marketplace --scope local

# Uninstall
claude plugin uninstall formatter@my-marketplace

# Enable/disable without uninstalling
claude plugin enable formatter@my-marketplace
claude plugin disable formatter@my-marketplace

# Update to latest version
claude plugin update formatter@my-marketplace
```

## Version Management

**Format:** `MAJOR.MINOR.PATCH`

**Rules:**
- Start at `1.0.0` for first stable release
- Bump version in `plugin.json` before distributing changes
- Document changes in `CHANGELOG.md`
- Pre-release: `2.0.0-beta.1`

**Critical:** Claude Code uses the version to determine whether to update. If you change code but don't bump the version, existing users won't see changes due to caching.

Version can be set in either `plugin.json` or `marketplace.json`. If both are set, `plugin.json` takes priority.

## Team Marketplace Setup

For team-internal distribution:

1. Create a Git repository with `marketplace.json`
2. Add plugins as subdirectories
3. Configure in project `.claude/settings.json`:
```json
{
  "pluginMarketplaces": [
    "https://github.com/your-org/claude-plugins"
  ]
}
```
4. Team members can install via `/plugin` interface

## Plugin Caching Behavior

**Marketplace plugins** are copied to `~/.claude/plugins/cache`:
- External files (`../shared/`) won't be accessible
- Symlinks are followed during copy
- Changes require version bump to propagate

**--plugin-dir plugins** are used in-place:
- Changes take effect on restart
- No caching involved
- Good for development

## README Guidelines

For GitHub distribution, include:
1. What the plugin does (outcomes, not implementation)
2. Installation instructions
3. Component list (skills, agents, hooks)
4. Example usage with screenshots
5. Configuration options
6. Changelog

**Note:** README.md goes at the repo root, never inside skill folders.
