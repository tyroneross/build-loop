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
prove the other one shipped. For the build-loop-wide package standard, also
load `../../../references/npm-package-publishing.md`.

### npmjs Release Standard

Every npmjs package publish uses provenance. Preferred path: trusted publishing
with OIDC, using explicit `npm publish --provenance` without a stored npm token.
Fallback path: a scoped npm access token plus `npm publish --provenance`. Do not add or edit an
npmjs publish workflow that omits provenance unless the user explicitly accepts
that exception.
If an npm token is pasted into chat, logs, docs, or shell history, treat it as
compromised. Do not use it for publishing; revoke it and return to the Trusted
Publisher path.

Use npm CLI v11 command docs as the command reference for release work. The
standard command set is:

```bash
npm whoami --registry=https://registry.npmjs.org
npm view @scope/package version dist-tags --registry=https://registry.npmjs.org --json
npx -y npm@11 pack --dry-run --json --registry=https://registry.npmjs.org
npm publish --dry-run --provenance --access public --registry=https://registry.npmjs.org
npm publish --provenance --access public --registry=https://registry.npmjs.org
npm token list
npm audit signatures
```

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
  `publish-npm.yml`.
- Environment is blank unless the workflow uses a GitHub environment.
- Allowed actions include `npm publish`.

The workflow should use a GitHub-hosted runner, `permissions: id-token: write`,
`actions/checkout`, and `actions/setup-node` with `registry-url` set to the
target registry. Do not set `NODE_AUTH_TOKEN` for trusted-publisher npmjs
publishes. Remove obsolete `always-auth` inputs when using modern
`setup-node`; use an explicit package-manager cache setting if the default
cache detection is noisy.

Run `npm publish --dry-run --provenance --access public --registry=https://registry.npmjs.org`
as a packaging check, but do not treat it as proof that the Trusted Publisher
mapping is valid. A real publish can still fail after a successful dry-run when
the npm package settings do not match the GitHub workflow. After publishing,
verify the registry metadata includes
`dist.attestations.provenance.predicateType = https://slsa.dev/provenance/v1`.
If the real publish step prints the final `+ @scope/package@version` line but
the immediate metadata check returns `E404`, do not rerun the same publish.
npmjs metadata can lag for a few minutes after acceptance; poll `npm view` or
use a verify-only workflow path.

### Access Token Fallback Gate

Trusted publishing is preferred because it removes long-lived npm secrets from
CI and automatically generates provenance attestations. Use an npm access token
only when the user explicitly chooses a fallback after the trusted-publisher
path is blocked. Token fallback is still a provenance publish; it is not a
non-provenance shortcut.

If token fallback is approved:

- Use a granular access token, not a legacy token.
- Grant read/write only to the package or scope being published; do not grant
  organization access and assume it allows package publishing.
- Set an expiration date and record the rotation/removal follow-up.
- Leave 2FA bypass off unless CI publishing cannot work without it and the user
  accepts that exception. npm documents that Bypass 2FA takes precedence over
  account-level and package-level 2FA, so record and rotate/revoke the exception.
- Store the value as a GitHub secret such as `NPM_TOKEN`; never commit it to
  `.npmrc`, workflow files, docs, or shell history examples.
- If a token value is pasted into chat, logs, docs, or shell history, treat it as
  compromised: do not use it; revoke it and create a fresh token.
- Run `npm token list` to audit tokens and revoke the fallback token after the
  release if it is no longer needed.

Token fallback workflow steps should set:

```yaml
permissions:
  contents: read
  id-token: write
```

and publish with:

```bash
npm publish --provenance --access public --registry=https://registry.npmjs.org
```

using `NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}`.

### Provenance Gate

For all npmjs packages, publish with provenance unless there is an explicit
reason not to and that exception is recorded in the release notes. Provenance
requires:

- A supported cloud CI/CD provider and a cloud-hosted runner.
- npm CLI `11.5.1+` for Trusted Publisher flows; prefer current npm.
- `package.json.repository` set to the public source repository and matching the
  repository used by the workflow.
- `permissions.id-token: write` in the workflow.

Trusted Publisher and token fallback publishes both use explicit `--provenance`
on every real npmjs `npm publish` command. After install, downstream consumers
can verify registry signatures and attestations with:

```bash
npm audit signatures
```

### Failure Triage

- If GitHub Packages publishes and installs but npmjs fails, report split
  registry status instead of changing package code by default.
- If npmjs fails with `E404` or "not found or you do not have permission" on the
  final `PUT`, first inspect the npm Trusted Publisher owner/repo/workflow and
  package access. After fixing npm settings, rerun the failed workflow:

```bash
gh run rerun <run-id> --failed
```

- If npmjs succeeds through the final `+ @scope/package@version` line but the
  post-publish metadata check returns `E404`, treat it as a visibility lag until
  registry polling proves otherwise. Do not rerun the publish for that version;
  rerun a verify-only path or poll `npm view`.
- Use local npm login or token publishing only as an explicit fallback decision,
  because it bypasses the trusted-publisher/provenance path.

### Official References

- npm CLI v11 command index:
  `https://docs.npmjs.com/cli/v11/commands/npm`
- npm CLI publish command:
  `https://docs.npmjs.com/cli/v11/commands/npm-publish`
- npm Trusted Publishers:
  `https://docs.npmjs.com/trusted-publishers#supported-cicd-providers`
- npm access tokens:
  `https://docs.npmjs.com/about-access-tokens`
- npm provenance:
  `https://docs.npmjs.com/generating-provenance-statements`
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
