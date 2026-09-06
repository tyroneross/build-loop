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

Manual npmjs metadata verification without publishing:

```bash
gh workflow run publish-npmjs.yml --ref main -f verify_only=true
```

## The release chain, and the two links that do not cascade

Push → release-please opens a release PR → the weekly cut merges it → release-please
tags and creates the GitHub Release → publish. Two hops in that chain are silent
unless dispatched explicitly, because **GitHub creates no workflow run for a push or
release made with `GITHUB_TOKEN`**:

- merge → release-please: the cut's `gh pr merge` pushes to main with `GITHUB_TOKEN`,
  so release-please never runs on the merge commit. The cut must run
  `gh workflow run release-please.yml --ref main` after the merge (needs
  `actions: write`). Proven on NavGator 2026-09-06: PR #7 merged, zero runs for the
  merge commit `ba0b4d4b`, no Release.
- Release → publish: release-please creates the Release with `GITHUB_TOKEN`, so
  `on: release` never fires; release-please.yml dispatches the publish workflows at
  the tag. `workflow_dispatch` IS delivered for `GITHUB_TOKEN` callers.

Do not cut a Release until the npmjs side is proven (below); a Release with no package
behind it is what the 0.42.5 gap looked like.

## Diagnosing a trusted-publishing failure

Read the npmjs OIDC exchange answer, not the publish exit code. NavGator's
`publish.yml` carries a `Diagnose npm trusted-publisher binding` step that mints the
id-token, prints its claims, and POSTs
`/-/npm/v1/oidc/token/exchange/package/<scope>%2f<name>`; copy it into any publish
workflow that fails. What the answers mean (verified 2026-09-06):

| npmjs answer | meaning | fix |
|---|---|---|
| `404 {"message":"OIDC token exchange error - package not found"}` | **no Trusted Publisher record exists for this package** on npmjs.com; the workflows can be perfect | create the record (web UI + 2FA; no CLI can) |
| a claims mismatch message naming a field | record exists, a field differs | match the printed claims: repository is case-sensitive (`NavGator`), workflow is the filename only, environment blank unless the job declares one |
| exchange 200, publish still refused | allowed actions may be stage-only | enable `npm publish` on the record, or switch the workflow to `npm stage publish` (npm ≥ 12) and approve with 2FA |

Two things that look like evidence and are not:

- **"Signed provenance statement" does not prove OIDC authenticated.** With
  `publishConfig.provenance: true` npm signs unconditionally, then PUTs with whatever
  credential it has.
- **`E404 Not Found - PUT` is a masked auth failure**, not a registry refusal.
  `actions/setup-node` with `registry-url` exports a placeholder
  `NODE_AUTH_TOKEN=XXXXX-…`; that non-empty value passes npm's no-credentials guard, so
  the PUT goes out with garbage and npmjs answers 404 for an unauthorized write to an
  existing package. Without the placeholder the same fault reports `ENEEDAUTH`.

Preconditions the docs state and this estate has tripped on: `repository.url` in
`package.json` must exactly match the GitHub repository (persona-lab shipped with none);
trusted publishing cannot create a package, so a package's first version needs a one-time
login or token; a Trusted Publisher record is per package, so "wired on the GitHub side"
for 14 repos means nothing until 14 records exist on npmjs.com.

Local tokens are not a fallback you can assume: on 2026-09-06 both the `~/.npmrc` token
and the Secrets Vault `npmjs` entry returned 401. `npm login --auth-type=web` prints a
login URL that works from a non-interactive shell; a human completes it in the browser.

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

If a real publish step succeeds but the immediate `npm view` returns `E404`, do
not rerun the real publish for the same version. npmjs metadata can lag for a
few minutes after the successful `+ @scope/package@version` publish line. Poll
the registry and use a verify-only workflow path when available.

The registry is the source of truth. Provenance is present when npm metadata includes:

```text
dist.attestations.provenance.predicateType = https://slsa.dev/provenance/v1
```

## GitHub Packages Is Separate

GitHub Packages publishes target `https://npm.pkg.github.com` and need their own workflow and install smoke. A successful GitHub Packages publish does not prove npmjs published, and npmjs provenance rules do not apply to GitHub Packages publishes.

## Memory

Canonical reusable lesson: `build-loop-memory/lessons/references/npm-oidc-trusted-publishing.md`.
