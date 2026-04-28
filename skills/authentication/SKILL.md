---
name: authentication
description: Use for Google OAuth, Google sign-in, Google Maps/Places/Geocoding/Directions, Google Calendar sync, Cloud Console setup, redirect_uri_mismatch, invalid_grant, OAuth callbacks, API keys, service accounts.
---

# Authentication

Reference library for wiring external authentication and authorization into a web or mobile app. Each service has its own reference file — load the one you need, don't pre-load all.

## When to use this skill

- Setting up third-party sign-in (Google, GitHub, etc.)
- Wiring API keys / service accounts / OAuth callbacks
- Debugging `redirect_uri_mismatch`, `invalid_grant`, token refresh loops
- Migrating away from legacy auth SDKs (e.g., `gapi.auth2`, `google.maps.Marker`)

## Routing table

| If the user is asking about... | Load |
|---|---|
| Google OAuth sign-in | `references/google-oauth-setup.md` + `references/google-cloud-console.md` |
| Google Maps in a UI | `references/google-maps.md` |
| Google Places API (New) | `references/google-places.md` |
| Google Geocoding / Directions | `references/google-geocoding-directions.md` |
| Google Calendar sync | `references/google-calendar-sync.md` |
| Broad Google Cloud Console walkthrough (4-layer mental model: identity, APIs, credentials, quotas) | `references/google-cloud-console.md` |
| Real Google build incident lessons (Trip Planner, Next.js + Supabase) | `references/google-lessons-travel-planner.md` |

Services not yet documented (planned as additional references): GitHub OAuth, generic OAuth 2.0 callbacks, service-account key handling, API key rotation. Add them as `references/<service>-<topic>.md` following the same pattern.

## Universal auth footguns (applies to any service)

1. **Redirect URIs are exact-match.** `https://app.com/cb` ≠ `https://app.com/cb/` ≠ `http://app.com/cb`. Register every variant you use in dev and prod.
2. **Don't store tokens in localStorage for SPAs that redirect.** Use httpOnly cookies or the platform's session store.
3. **Token refresh fails silently when the refresh token is expired/rotated.** Log refresh failures explicitly; don't swallow them.
4. **Scopes are sticky across authorizations.** If you add a scope later, existing users need to re-consent — the original grant doesn't auto-upgrade.
5. **"Invalid grant" usually means one of: clock skew, expired refresh token, revoked authorization, or JWT signature mismatch.** Check system clock first.
6. **Service accounts ≠ user accounts.** Service accounts act on their own identity with their own quota. User-delegated access (domain-wide delegation) is a separate flow with separate risks.

## Pattern: adding a new auth reference

When expanding this skill:
1. Add `references/<service>-<topic>.md` with a clear `## When to use` section at top.
2. Add a row to the routing table above.
3. If the service has a shared footgun class (e.g., OAuth redirect URIs), cross-link to the universal footguns section instead of repeating.
4. Do **not** promote a single-service reference to a top-level skill unless it grows past ~500 lines *and* has multiple distinct sub-topics. Single services belong as references.

## History

Originally imported from a standalone `google-cloud-console` skill (2025, Trip Planner build). Converted to a general `authentication` parent skill on 2026-04-21 so GitHub OAuth, generic OAuth 2.0, and API-key patterns can share the routing layer and universal footgun section without each becoming its own island.
