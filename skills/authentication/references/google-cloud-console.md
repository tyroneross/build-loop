---
name: google-cloud-console
description: Use when setting up Google Cloud Console for a web app — Google OAuth sign-in, Maps JavaScript API, Places API (New), Geocoding, Directions, or Google Calendar sync. Covers console setup, credentials, scopes, common footguns, and Next.js 16 / Supabase patterns. Trigger on "google oauth", "google sign in", "google maps", "places api", "google calendar sync", "google cloud credentials".
---

# Google Cloud Console — Auth + Maps + Calendar

Hard-won reference for wiring Google Cloud services into a web app. Lessons baked in from a real Next.js + Supabase build (Trip Planner, 2025). Every footgun here was paid for once already — don't pay for it again.

## When to Use

- Setting up Google sign-in (OAuth 2.0) for a web or mobile app
- Adding Google Maps, Places, Geocoding, or Directions to a UI
- Syncing Google Calendar events to/from your app
- Debugging auth redirect loops, `redirect_uri_mismatch`, or `invalid_grant`
- Migrating from legacy Places API / `gapi.auth2` / `google.maps.Marker`

## Core Mental Model

Google Cloud Console is four layered concerns — get them in this order:

1. **Project** — a container. APIs are enabled _per project_.
2. **APIs** — enable only the ones you need (billing consequences).
3. **Credentials** — OAuth client IDs (for user auth) OR API keys (for Maps) OR service accounts (server-to-server).
4. **OAuth Consent Screen** — what the user sees on the consent dialog. Required before OAuth works.

A single project can hold multiple credentials. Use **separate projects for dev and prod** — not just separate credentials. This keeps quotas, billing, and consent-screen state isolated.

## Decision Matrix

| Need | Credential type | Key doc |
|------|-----------------|---------|
| "Sign in with Google" button | OAuth 2.0 client (Web application) | [OAuth web server](https://developers.google.com/identity/protocols/oauth2/web-server) |
| Embed a map in a web page | API key (HTTP referrer restricted) | [Load Maps JS API](https://developers.google.com/maps/documentation/javascript/load-maps-js-api) |
| Search places / autocomplete | API key (same one if same project) | [Places API New](https://developers.google.com/maps/documentation/places/web-service/overview) |
| Read user's Google Calendar | OAuth client + Calendar scope | [Calendar auth](https://developers.google.com/workspace/calendar/api/auth) |
| Server-to-server Calendar (Workspace) | Service account + domain-wide delegation | [Service accounts](https://developers.google.com/identity/protocols/oauth2/service-account) |
| Native mobile sign-in | OAuth client (iOS/Android) + PKCE | [Mobile OAuth](https://developers.google.com/identity/protocols/oauth2/native-app) |

## Setup Order (Do NOT skip steps)

```
1. Create project (dev + prod, separate)
2. Enable required APIs (per project)
3. Configure OAuth consent screen (BEFORE creating OAuth client)
4. Create credentials (OAuth client / API key / service account)
5. Restrict credentials (referrer, IP, or scope)
6. Add credentials to app env vars (NEVER commit)
7. Configure app-side redirect URIs to match EXACTLY
8. Test in dev → test in prod-like env → ship
```

Details per surface are in `references/` — load progressively.

## References (Progressive Disclosure)

Load only what you need:

- **`references/oauth-setup.md`** — OAuth consent screen fields, web-app client creation, Supabase Google provider wiring, Next.js 16 cookie API, CSRF via state param, redirect URI rules. Start here for "add sign in with Google".
- **`references/maps.md`** — Maps JS API loader, `importLibrary()` async pattern, Advanced Markers (Marker is deprecated), Map ID requirement, key restriction recipe.
- **`references/places.md`** — Places API (New) v1 — Text Search, Nearby, Place Details. Field masks (cost control). Migration from legacy. Autocomplete.
- **`references/geocoding-directions.md`** — Geocoding + Directions + Distance Matrix, rate limits, caching strategy.
- **`references/calendar-sync.md`** — Google Calendar API v3 scopes, incremental sync with `syncToken`, 410 recovery, refresh-token storage, watch channels for push.
- **`references/lessons-travel-planner.md`** — Real failure modes from production: Supabase SSR cookie bug, production-only redirect loops, route-group manifest conflicts, credential rotation pain.

## The 10 Footguns (Read This Section Every Time)

These are the failures that cost days in the Trip Planner build. Check for each when something breaks.

1. **Supabase SSR cookie API mismatch (Next.js 15+)** — Must use `getAll`/`setAll`, NOT `get`/`set`/`remove`. Works in dev, fails in prod. Error surfaces as "Cannot read properties of undefined (reading 'get')" in Supabase SSR. See `references/oauth-setup.md#nextjs-cookies`.

2. **`redirect_uri_mismatch`** — The redirect URI in Google Cloud Console must match the one your app sends **exactly** (no trailing slash, correct protocol, correct port). For Supabase it must be `https://<project>.supabase.co/auth/v1/callback` — **not** your app's URL.

3. **OAuth consent screen in "Testing" mode** — Until you publish it, only listed test users can sign in. Publishing triggers verification if you use sensitive scopes (like Calendar).

4. **No OAuth consent screen at all** — You cannot create a Web-app OAuth client until the consent screen is configured. The console lets you start and then fails silently on some fields.

5. **Map ID missing for Advanced Markers** — `google.maps.Marker` is deprecated (Feb 2024). `AdvancedMarkerElement` requires a **Map ID** on the `Map` constructor. Without it, markers silently don't render.

6. **Legacy Places API enabled instead of Places API (New)** — Legacy can no longer be enabled for new projects. If you followed a 2023 tutorial it won't work. Use the New API with `FieldMask` headers.

7. **API key with no referrer restriction** — Anyone who views source grabs your key and racks up billing. Always restrict Maps/Places keys by HTTP referrer (`https://your-domain.com/*`) and API (only the APIs you actually use).

8. **Client secret in `NEXT_PUBLIC_*`** — Easy to do by mistake; exposes the secret to the browser. Client secrets go in server-only env vars. API keys for Maps JS are public by design (protected by referrer restriction, not secrecy).

9. **Route-group conflicts cause Vercel build failures** — `app/page.tsx` + `app/(app)/page.tsx` both existing generates the `client-reference-manifest.js` at the wrong path. Builds locally, fails on Vercel. Pick one.

10. **`invalid_grant` on token refresh** — User revoked access, or the refresh token wasn't stored (Google only returns it on the _first_ consent unless you pass `prompt=consent` or `access_type=offline`). Always request `access_type=offline&prompt=consent` for first consent if you need durable calendar access.

## Required Env Vars (Web App Template)

```env
# Supabase (if using)
NEXT_PUBLIC_SUPABASE_URL=https://xxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=ey...

# Google OAuth (server-side only — Supabase handles the flow)
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...

# Google Maps (browser-exposed, protected by HTTP referrer restriction)
NEXT_PUBLIC_GOOGLE_MAPS_API_KEY=AIza...
NEXT_PUBLIC_GOOGLE_MAPS_MAP_ID=...     # required for Advanced Markers

# App URLs — MUST match what's in Google Cloud Console + Supabase
NEXT_PUBLIC_APP_URL=https://your-domain.com
NEXT_PUBLIC_SITE_URL=https://your-domain.com  # must equal APP_URL in prod
```

Add a boot-time validator that refuses to run if any are missing or contain `your_`/`demo_`/`development`. See `references/oauth-setup.md#env-validation`.

## Quick Verification Commands

Before claiming auth works:

```bash
# 1. Build succeeds
npm run build

# 2. Manifest file exists where Vercel expects it
ls .next/server/app/**/page_client-reference-manifest.js

# 3. Sign-in flow end to end (manual)
#    - open /signin
#    - click Google
#    - grant consent
#    - land on /dashboard
#    - hard refresh — session persists
#    - close browser, reopen — session persists for 7 days
#    - sign out — cookies cleared, /dashboard redirects to /signin
```

"Build passed" ≠ "auth works". "Sign-in UI appears" ≠ "session persists". Test the full loop.

## Honest Certainty Markers

When wiring this up, report state with markers — NEVER "100% complete":

- ✅ Console configured (project exists, APIs enabled, consent screen published, credentials created)
- ✅ App env vars set (validated at boot)
- ✅ Sign-in UI renders
- ⚠️ Sign-in flow works in dev — not yet tested in prod
- ❓ Refresh-token storage — need to verify on 2nd-day session

## Latest Docs (April 2026)

Bookmark these — Google rewrites them regularly, and old blog posts lie.

- [OAuth 2.0 for Web Server Apps](https://developers.google.com/identity/protocols/oauth2/web-server) — the canonical auth flow doc
- [Using OAuth 2.0 (index)](https://developers.google.com/identity/protocols/oauth2) — scope & flow overview
- [Load Maps JS API](https://developers.google.com/maps/documentation/javascript/load-maps-js-api) — modern async loader
- [Advanced Markers migration](https://developers.google.com/maps/documentation/javascript/advanced-markers/migration) — required migration from deprecated `Marker`
- [Places API (New) overview](https://developers.google.com/maps/documentation/places/web-service/overview) — legacy is end-of-life for new projects
- [Calendar API sync guide](https://developers.google.com/workspace/calendar/api/guides/sync) — incremental sync tokens
- [Calendar API scopes](https://developers.google.com/workspace/calendar/api/auth) — pick the narrowest scope

When in doubt, use Context7 MCP (`context7__query-docs`) against library IDs for `@googlemaps/js-api-loader`, `@supabase/ssr`, `googleapis`.
