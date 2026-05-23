---
name: api-registry-bridge
description: Use when Phase 1 Assess or Phase 5 Iterate detects a new API dependency, API config fails, or the user asks to "register this API" or "check the API registry". Consults api-registry plugin; degrades gracefully if plugin is absent.
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross | SPDX-License-Identifier: Apache-2.0 -->

# api-registry-bridge (inside build-loop)

## Activation

- **Assess phase:** new API detected in dep diff (new package added to `package.json` / `requirements.txt`) or new vendor-prefixed env key in `.env.example`. Also runs unconditionally as a cheap doc-freshness check (see §"Phase 1 doc-freshness check").
- **Iterate phase:** API config attempt fails with 401/403/404/timeout/auth error.

## Contract

1. Check `~/.api-registry/registry.db` exists.
   - If **not**: log `api-registry not present — skipping source verification` and continue build-loop flow. No failure.

2. Extract service name from failure message or dep diff.

3. Invoke `/api-registry:lookup <name>`.
   - If `found: true` and `deprecated_notes` present: HALT iteration; surface the warning to the user BEFORE retry.
   - If `stale_warning: true`: suggest `/api-registry:refresh <name>` but don't block.
   - If `cooldown.install_blocked: true`: see §"New-dependency cooldown".

4. If config question remains, invoke `/api-registry:docs <name> <specific config question>`.
   - api-registry answers from its local doc cache first; Context7 is the fallback path only.

5. If lookup returns `found: false`: prompt user once per build-loop run to add the service. Don't re-prompt on every iteration.

## Phase 1 doc-freshness check

At the start of Phase 1 Assess, after the registry-present check:

1. Read `~/.api-registry/staleness.json` (written by api-registry's SessionStart hook / `staleness.ts --marker`).
   - Absent or unreadable → skip silently.
2. If any `stale[]` entry names a service relevant to this build (its package appears in the dep manifest, or it is a `protocol`-category source and the build touches MCP), the doc is stale (`last_checked` > 7 days).
3. **Refresh stale docs in-session before planning**: for each relevant stale doc, invoke `/api-registry:docs <service> <topic>` so the cache is re-verified/re-curated before the plan is drafted. This keeps Phase 2 planning grounded in current docs, not a stale cache.
4. This is advisory — never blocks the build. Log what was refreshed; route the summary to the run report.

## New-dependency cooldown

When Assess detects a newly added third-party package:

1. `/api-registry:lookup <name>` returns a `cooldown` block.
2. If `cooldown.install_blocked: true` (latest version released < 7 days ago, service not `author_owned`):
   - Surface the `cooldown.reason` to the user.
   - The `pre_bash_dependency_cooldown.sh` PreToolUse hook is the enforcement point — it already rewrites/denies fresh installs at the Bash boundary. The registry verdict is the *advisory* signal that explains *why* a hook rewrite happened; it does not replace the hook.
   - `author_owned: true` services (`@tyroneross/*` scope + the user's own projects) are exempt — `cooldown.install_blocked` is always `false` for them, matching the hook's allowlist.
3. The 7-day registry cooldown window and the hook's 7-day install cooldown are deliberately the same number (supply-chain dwell time). If they ever diverge, the hook is authoritative for *enforcement*; the registry is authoritative for *explanation*.

## What this does NOT do

- Does not modify the registry (except via `/api-registry:docs`, which re-curates the cache by design — that is the cache staying fresh, not the bridge mutating registry metadata).
- Does not block the build if api-registry is absent.
- Does not fabricate URLs.
- Does not duplicate the cooldown *enforcement* — that is `pre_bash_dependency_cooldown.sh`'s job.
