---
name: authentication
description: Use for auth wiring — Better Auth (Drizzle/Neon, magic links, social), Supabase Auth (SSR getAll/setAll), Google OAuth + Cloud Console + Maps/Places/Calendar, Resend (transactional, magic-link/OTP, webhooks). Debug redirect_uri_mismatch, invalid_grant, refresh_token, session callback, IDOR, magic-link expiry.
---

# Authentication

Reference library for wiring external authentication and authorization into a web or mobile app. Each service has its own reference file — load the one you need, don't pre-load all.

**Stack default for new Next.js work**: Neon + Better Auth + Drizzle. Supabase is covered for legacy projects and as an alternate. Magic-link delivery and transactional email default to Resend.

## When to use this skill

- Setting up third-party sign-in (Google, Apple, magic-link, etc.) on a Better Auth or Supabase backend
- Wiring API keys / service accounts / OAuth callbacks
- Debugging `redirect_uri_mismatch`, `invalid_grant`, token refresh loops, session callback failures
- Sending magic-link / OTP emails and handling delivery webhooks
- SSR cookie handling and the Supabase `getAll`/`setAll` ≥0.3 breaking change
- Migrating away from legacy auth SDKs (e.g., `gapi.auth2`, `@supabase/ssr` <0.3)

## Routing table

| If the user is asking about... | Load | Context7 lib id |
|---|---|---|
| Better Auth setup, Drizzle adapter, social providers, refresh_token | `references/better-auth-setup.md` | `/better-auth/better-auth` |
| Magic links / OTP via Better Auth | `references/better-auth-magic-link.md` | `/better-auth/better-auth` |
| Travel Planner Better Auth real-build lessons | `references/lessons-travel-planner-better-auth.md` | — |
| Supabase Auth (SSR cookies, RLS, env validation, when-to-use vs Better Auth) | `references/supabase-auth.md` | `/supabase/supabase` |
| Resend transactional + Better Auth `sendVerificationEmail` integration + webhook signatures | `references/resend-email.md` | `/websites/resend` |
| Resend OTP / magic-link delivery + retries + bounce handling | `references/resend-otp-magic-link.md` | `/websites/resend`, `/resend/resend-skills` |
| Google OAuth sign-in | `references/google-oauth-setup.md` + `references/google-cloud-console.md` | — |
| Google Maps in a UI | `references/google-maps.md` | — |
| Google Places API (New) | `references/google-places.md` | — |
| Google Geocoding / Directions | `references/google-geocoding-directions.md` | — |
| Google Calendar sync | `references/google-calendar-sync.md` | — |
| Broad Google Cloud Console walkthrough (4-layer mental model: identity, APIs, credentials, quotas) | `references/google-cloud-console.md` | — |
| Real Google build incident lessons (Trip Planner, Next.js + Supabase) | `references/google-lessons-travel-planner.md` | — |

Services not yet documented (planned as additional references): GitHub OAuth, generic OAuth 2.0 callbacks, service-account key handling, API key rotation. Add them as `references/<service>-<topic>.md` following the same pattern.

## Universal auth footguns (applies to any service)

1. **Redirect URIs are exact-match.** `https://app.com/cb` ≠ `https://app.com/cb/` ≠ `http://app.com/cb`. Register every variant you use in dev and prod.
2. **Don't store tokens in localStorage for SPAs that redirect.** Use httpOnly cookies or the platform's session store.
3. **Token refresh fails silently when the refresh token is expired/rotated.** Log refresh failures explicitly; don't swallow them.
4. **Scopes are sticky across authorizations.** If you add a scope later, existing users need to re-consent — the original grant doesn't auto-upgrade.
5. **"Invalid grant" usually means one of: clock skew, expired refresh token, revoked authorization, or JWT signature mismatch.** Check system clock first.
6. **Service accounts ≠ user accounts.** Service accounts act on their own identity with their own quota. User-delegated access (domain-wide delegation) is a separate flow with separate risks.
7. **`NEXT_PUBLIC_APP_URL` / `BETTER_AUTH_URL` mismatch on Vercel CNAMEs.** Preview/production URLs diverge from the registered redirect; bind detection to the actual `VERCEL_URL` fallback and verify at boot. Travel Planner shipped a production-only auth failure from this exact mismatch.
8. **Refresh-token guarantee on Google.** Better Auth's Google provider must explicitly request `accessType: 'offline'` AND `prompt: 'select_account consent'` — otherwise `refresh_token` is missing on second consent. Verify the `account.refresh_token` and `account.expires_at` columns populate before shipping. (See `references/better-auth-setup.md`.)
9. **IDOR via shared DB connection.** Per-request `dbForUser(userId)` wrappers (or RLS) — never trust client-supplied user IDs in queries. Travel Planner's `lib/db/index.ts` is the canonical pattern.
10. **Magic-link expiry & idempotency.** Default 10 min; single-use; rate-limit by email; log delivery failures (Resend bounces ≠ user error). atomize-ai's `magicLink({ expiresIn: 60 * 10 })` is the reference config.
11. **Cookie config is non-negotiable.** `httpOnly: true`, `secure: true` (prod), `sameSite: 'lax'` for OAuth redirects. `sameSite: 'strict'` breaks Google callback. Better Auth defaults are correct; override with care.

## Pattern: adding a new auth reference

When expanding this skill:
1. Add `references/<service>-<topic>.md` with a clear `## When to use` section at top.
2. Add a row to the routing table above.
3. If the service has a shared footgun class (e.g., OAuth redirect URIs), cross-link to the universal footguns section instead of repeating.
4. Do **not** promote a single-service reference to a top-level skill unless it grows past ~500 lines *and* has multiple distinct sub-topics. Single services belong as references.

## Looking up current API surface

Auth SDKs change frequently — Better Auth in particular adds plugins and provider options on a fast cadence. Before writing config or debugging a session callback, fetch live docs via the Context7 MCP (mirrors the pattern in `building-with-deepagents/SKILL.md:33`):

1. **Resolve** the library id once per session:
   `mcp__plugin_context7_context7__resolve-library-id("better-auth")` → `/better-auth/better-auth`
2. **Query** with the resolved id and a focused topic:
   `mcp__plugin_context7_context7__query-docs` with `library: "/better-auth/better-auth"` and `topic: "magic link plugin config"` (or `"social provider refresh_token"`, `"SSR getAll setAll"`, `"webhook signature verification"`).

Known Context7 ids (verified 2026-05-02): `/better-auth/better-auth`, `/supabase/supabase`, `/websites/resend`, `/resend/resend-skills`. If a query returns thin results, retry with the alternate (`/resend/resend-skills` is better tuned for agent-flavored questions; `/websites/resend` covers the broader SDK surface).

## History

Originally imported from a standalone `google-cloud-console` skill (2025, Trip Planner build). Converted to a general `authentication` parent skill on 2026-04-21 so GitHub OAuth, generic OAuth 2.0, and API-key patterns can share the routing layer and universal footgun section without each becoming its own island.

Extended on 2026-05-02 to multi-provider coverage: added Better Auth (Drizzle/Neon, magic-link, Google + Apple social), Supabase Auth (legacy + alternate), and Resend (transactional, magic-link/OTP, webhook verification). Lessons harvested from Travel Planner, atomize-ai, ProductPilot, and super_news. Adopted the Context7 lookup pattern so SKILL.md stays light while doc depth lives in references and the live Context7 index.
