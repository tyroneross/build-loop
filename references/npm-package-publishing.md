<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# npm Package Publishing

Use this reference when build-loop creates, audits, or publishes npm packages.

## npmjs Standard

Prefer GitHub Actions OIDC Trusted Publisher for public npmjs packages.

Required workflow shape:

```yaml
permissions:
  contents: read
  id-token: write

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-node@v6
        with:
          node-version: "24"
          registry-url: "https://registry.npmjs.org"
          package-manager-cache: false
      - run: npm ci
      - run: npm run build
      - run: npm publish --provenance --access public --registry https://registry.npmjs.org
```

Rules:
- Do not set `NODE_AUTH_TOKEN` for npmjs Trusted Publisher publishes.
- Do not use npm tokens pasted into chat, logs, docs, or shell history. Treat them as compromised and revoke them.
- Use `--provenance` on the real publish command and on publish dry-runs.
- Use `--access public` for scoped public packages.
- Use explicit `--registry https://registry.npmjs.org` when `package.json#publishConfig.registry` points at GitHub Packages or any other registry.
- Leave the npmjs Trusted Publisher environment blank unless the GitHub Actions job declares an `environment:` with that exact name.
- Register the Trusted Publisher on npmjs with exact owner/repo, exact workflow filename, and allowed action `npm publish`.

## Token Fallback

Token fallback is only for blocked Trusted Publisher paths. Use a new granular
read/write token limited to the package or scope being published, set an
expiration date, and store it directly as a GitHub secret. Do not route token
values through chat, docs, issue comments, logs, or copied shell commands.

If Bypass 2FA is enabled on a granular token, treat that token as a high-risk CI
credential. npm documents that Bypass 2FA takes precedence over account-level and
package-level 2FA settings, so this is an explicit exception that must be
recorded and rotated or revoked after use.

For build-loop's npmjs fallback workflow, the secret name is `NPM_TOKEN`, and
the workflow filename is `.github/workflows/publish-npmjs.yml`. Keep this
separate from `.github/workflows/publish-npm.yml`, which publishes to GitHub
Packages with `secrets.GITHUB_TOKEN`.

Manual npmjs publish run:

```bash
gh workflow run publish-npmjs.yml --ref main -f dry_run=false
```

Manual npmjs dry-run:

```bash
gh workflow run publish-npmjs.yml --ref main -f dry_run=true
```

## Validation

Before tag or publish:

```bash
npm pack --dry-run --json --registry=https://registry.npmjs.org
npm publish --dry-run --provenance --access public --registry=https://registry.npmjs.org
```

After publish:

```bash
npm view @scope/package@1.2.3 dist-tags dist.attestations --json --registry=https://registry.npmjs.org
```

The registry is the source of truth. Provenance is present when npm metadata includes:

```text
dist.attestations.provenance.predicateType = https://slsa.dev/provenance/v1
```

## GitHub Packages Is Separate

GitHub Packages publishes target `https://npm.pkg.github.com` and need their own workflow and install smoke. A successful GitHub Packages publish does not prove npmjs published, and npmjs provenance rules do not apply to GitHub Packages publishes.

## Memory

Canonical reusable lesson: `build-loop-memory/lessons/references/npm-oidc-trusted-publishing.md`.
