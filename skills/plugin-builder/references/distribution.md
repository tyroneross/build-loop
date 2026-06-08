<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

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

## npm Package Publishing

Use this checklist when a plugin also ships as an npm package. Keep npmjs and
GitHub Packages as separate release surfaces; a pass on one registry does not
prove the other one shipped.

### Registry Rules

- Public npmjs publishes must target `https://registry.npmjs.org`. If
  `package.json` has `publishConfig.registry` set to GitHub Packages, pass
  `--registry=https://registry.npmjs.org` in the npmjs workflow and validation
  commands.
- GitHub Packages publishes target `https://npm.pkg.github.com` and need their
  own install smoke. Do not report "published" without naming which registry
  passed.
- Validate the final registry state after publish:

```bash
npm view @scope/package version dist-tags --registry=https://registry.npmjs.org --json
npm view @scope/package version --registry=https://npm.pkg.github.com
```

### Package Surface Gate

Run the pack inventory before any tag or publish:

```bash
npx -y npm@11 pack --dry-run --json --registry=https://registry.npmjs.org
```

Review the JSON, not just the exit code:

- Required files are present: `dist/` or runtime entrypoints, `bin` targets,
  `.claude-plugin/`, `.codex-plugin/`, `skills/`, install scripts, README, and
  license.
- Generated caches are absent: `.build/`, `node_modules/`, simulator or local
  runtime build output, local config, credentials, and large derived artifacts.
- The tarball size is plausible for the package. A sudden large tarball is a
  release blocker until the included file list is explained.
- `package-lock.json`, if present, has the same package version as
  `package.json`.

### npmjs Trusted Publisher Gate

For npmjs tokenless publishing from GitHub Actions, verify the npm package's
Trusted Publisher settings before the real publish:

- Provider: GitHub Actions.
- Owner/user or organization and repository exactly match the GitHub repo.
- Workflow filename exactly matches the publish workflow, for example
  `publish-npmjs.yml`.
- Environment is blank unless the workflow uses a GitHub environment.
- Allowed actions include `npm publish`.

The workflow should use a GitHub-hosted runner, `permissions: id-token: write`,
`actions/checkout`, and `actions/setup-node` with `registry-url` set to the
target registry. Do not set `NODE_AUTH_TOKEN` for trusted-publisher npmjs
publishes. Remove obsolete `always-auth` inputs when using modern
`setup-node`; use an explicit package-manager cache setting if the default
cache detection is noisy.

Run `npm publish --dry-run --access public --registry=https://registry.npmjs.org`
as a packaging check, but do not treat it as proof that the Trusted Publisher
mapping is valid. A real publish can still fail after a successful dry-run when
the npm package settings do not match the GitHub workflow.

### Failure Triage

- If GitHub Packages publishes and installs but npmjs fails, report split
  registry status instead of changing package code by default.
- If npmjs fails with `E404` or "not found or you do not have permission" on the
  final `PUT`, first inspect the npm Trusted Publisher owner/repo/workflow and
  package access. After fixing npm settings, rerun the failed workflow:

```bash
gh run rerun <run-id> --failed
```

- Use local npm login or token publishing only as an explicit fallback decision,
  because it bypasses the trusted-publisher/provenance path.

### Official References

- npm CLI publish command:
  `https://docs.npmjs.com/cli/v11/commands/npm-publish`
- npm Trusted Publishers:
  `https://docs.npmjs.com/trusted-publishers#supported-cicd-providers`
- GitHub Actions setup-node trusted-publisher OIDC:
  `https://github.com/actions/setup-node/blob/main/docs/advanced-usage.md#publishing-to-npm-with-trusted-publisher-oidc`

## README Guidelines

For GitHub distribution, include:
1. What the plugin does (outcomes, not implementation)
2. Installation instructions
3. Component list (skills, agents, hooks)
4. Example usage with screenshots
5. Configuration options
6. Changelog

**Note:** README.md goes at the repo root, never inside skill folders.
