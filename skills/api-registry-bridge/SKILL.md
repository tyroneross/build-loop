---
name: api-registry-bridge
description: Use when Phase 1 Assess or Phase 5 Iterate detects a new API dependency, API config fails, or the user asks to "register this API" or "check the API registry". Consults api-registry plugin; degrades gracefully if plugin is absent.
user-invocable: false
---

# api-registry-bridge (inside build-loop)

## Activation

- **Assess phase:** new API detected in dep diff (new package added to `package.json` / `requirements.txt`) or new vendor-prefixed env key in `.env.example`.
- **Iterate phase:** API config attempt fails with 401/403/404/timeout/auth error.

## Contract

1. Check `~/.api-registry/registry.db` exists.
   - If **not**: log `api-registry not present — skipping source verification` and continue build-loop flow. No failure.

2. Extract service name from failure message or dep diff.

3. Invoke `/api-registry:lookup <name>`.
   - If `found: true` and `deprecated_notes` present: HALT iteration; surface the warning to the user BEFORE retry.
   - If `stale_warning: true`: suggest `/api-registry:refresh <name>` but don't block.

4. If config question remains, invoke `/api-registry:docs <name> <specific config question>`.

5. If lookup returns `found: false`: prompt user once per build-loop run to add the service. Don't re-prompt on every iteration.

## What this does NOT do

- Does not modify the registry.
- Does not block the build if api-registry is absent.
- Does not fabricate URLs.
