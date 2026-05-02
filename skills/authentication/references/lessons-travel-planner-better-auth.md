# Travel Planner — Better Auth lessons

## When to use

Read this when you're hitting a class of problem the Travel Planner build hit during the migration from `@supabase/ssr` to Better Auth + Drizzle + Neon. Each section is a real incident or near-miss documented at the time. Diagnostic-style — symptom first, cause, what to look for, how it was fixed.

For runnable Better Auth config, see `better-auth-setup.md`.
For the Google-API-side lessons (Calendar, Places, Maps, Cloud Console), see the older `google-lessons-travel-planner.md`.

## Incident: Production sign-in works, then the next request 401s

**Symptom.** Sign-in completes successfully, browser redirects to the app, the dashboard renders briefly, then every subsequent API call returns 401. Hard refresh shows logged-out state.

**Cause.** `BETTER_AUTH_URL` was set to `https://my-app.vercel.app` but the actual production deployment was reachable at a custom CNAME `https://app.example.com`. Better Auth's cookie was scoped to `my-app.vercel.app`; the browser was on `app.example.com`; cookies didn't apply.

**What to look for.**
- `set-cookie` response header on `/api/auth/callback/google` lists a `Domain=` that doesn't match the URL bar
- Sign-in works on the `*.vercel.app` URL but not on the custom domain
- `Network` tab shows session reads returning empty before any API call

**Fix.** Boot-time validation that asserts `process.env.BETTER_AUTH_URL` matches `process.env.VERCEL_URL` (with `https://` prefix) OR a known custom domain. Crash the boot if it doesn't — silent mismatch was the killer here.

Cross-reference: Universal footgun #7 (`NEXT_PUBLIC_APP_URL` / `BETTER_AUTH_URL` mismatch on Vercel CNAMEs) in `../SKILL.md`.

## Incident: Calendar API call fails with `invalid_grant` after 7 days

**Symptom.** Family Calendar feature works for a week after the user signs in. Then every Google Calendar API call returns `invalid_grant`. Re-signing in fixes it for another week.

**Cause.** Google access tokens expire in ~60 minutes. Without a `refresh_token`, the access token can't be silently renewed. The `refresh_token` is only granted on first consent — second sign-in (without `prompt: 'consent'`) returns no `refresh_token`, and the row in `account.refresh_token` is null.

**What to look for.**
- `account.refresh_token` is null in your DB for users who can't make Google API calls
- `account.expires_at` is in the past
- Google Cloud Console "Credentials" page shows OAuth consent has been granted, but the app never asked for offline access

**Fix.** Better Auth Google provider config:

```ts
google: {
  clientId: process.env.GOOGLE_CLIENT_ID!,
  clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
  accessType: 'offline',           // requests refresh_token
  prompt: 'select_account consent', // forces consent screen every sign-in
  scope: ['openid', 'email', 'profile', 'https://www.googleapis.com/auth/calendar.app.created'],
}
```

Both `accessType` AND `prompt` are required. `accessType: 'offline'` alone won't issue a `refresh_token` if Google thinks the user has already consented to offline access in a previous session.

Verify before shipping by signing in twice (sign out between) and confirming `account.refresh_token` is non-null both times.

Cross-reference: Universal footgun #8 in `../SKILL.md`.

## Incident: Family Calendar creation triggers a second consent prompt

**Symptom.** User signs in to Travel Planner (Google OAuth, basic scopes). Clicks "Create Family Calendar." Browser redirects back to Google for ANOTHER consent screen requesting Calendar scope. Half of users abandon at this second prompt.

**Cause.** The initial sign-in only requested `openid email profile`. Calendar scope (`https://www.googleapis.com/auth/calendar.app.created`) was added in a later `auth.api.linkOAuth` call.

**Fix.** Pre-approve every scope you'll EVER need at sign-in time:

```ts
scope: [
  'openid',
  'email',
  'profile',
  'https://www.googleapis.com/auth/calendar.app.created',
]
```

Trade-off: more scopes = scarier consent screen at first sign-in. But far better than losing 50% of users at a second prompt mid-flow.

If a scope is genuinely optional or rare, defer it — but acknowledge you'll lose some users at the second prompt. Document the choice.

## Incident: User A could query User B's data

**Symptom.** Found during a code review pre-launch. The `getCamps()` route handler did `db.select().from(camps).where(eq(camps.id, request.body.campId))` — no user check.

**Cause.** Better Auth gives you a `session.user.id`, but it does NOT enforce per-row authorization. Every query has to manually scope to the session's user id. With dozens of route handlers, "manually" inevitably misses spots — exactly what happened here.

**Fix.** A single `dbForUser(userId)` factory in `lib/db/index.ts` that returns CRUD methods pre-bound to that user. Route handlers MUST go through it. Code review can grep for any `from(schema.X)` outside `dbForUser` to find violations.

```ts
const userDb = dbForUser(session.user.id)
const camp = await userDb.camps.get(campId) // returns [] if user doesn't own it
```

The grep audit caught 7 violations that had passed PR review.

Cross-reference: Universal footgun #9 in `../SKILL.md`. Implementation pattern in `better-auth-setup.md`.

## Incident: `sameSite: 'strict'` broke Google sign-in

**Symptom.** Tried to harden cookies by setting `sameSite: 'strict'`. Google sign-in stopped working — the OAuth callback could not see the auth cookie.

**Cause.** `sameSite: 'strict'` blocks cookies on cross-site redirect responses. The Google OAuth callback IS a cross-site redirect. Cookie isn't sent → Better Auth treats the callback as a fresh session → no link between callback and the sign-in attempt.

**Fix.** `sameSite: 'lax'` is the correct value for cookies that need to survive OAuth redirects. Better Auth's default is correct; the override was the bug.

```ts
// betterAuth({ ... advancedCookies: { sameSite: 'lax' } })  // default — leave it
```

Cross-reference: Universal footgun #11 in `../SKILL.md`.

## Migration narrative: `@supabase/ssr` → Better Auth

The Travel Planner migration took ~2 weeks part-time. Order of operations:

1. **Stand up Better Auth in parallel**, on `/api/auth/[...all]`. Supabase auth still active on the old `/auth/callback`. App routes fall back to either depending on a feature flag.
2. **Migrate user records** with a one-time script. Map Supabase `auth.users` → Better Auth `user`, add corresponding `account` rows for OAuth provider linkage. Test on a copy of prod first.
3. **Switch the route handler** from Supabase callbacks to `app/api/auth/[...all]/route.ts`. Update sign-in / sign-out buttons to call `authClient.signIn` / `signOut`.
4. **Delete Supabase-specific cookie code.** This was the biggest source of bugs — orphaned `getAll`/`setAll` adapters, leftover `createServerClient` imports.
5. **Replace RLS-based auth with `dbForUser(userId)`.** Audit every route handler. The migration triggered the IDOR-pattern adoption.

The biggest risks were in step 2 (schema mismatch) and step 4 (latent dead code). Spent the most time there.

## What we'd do differently next time

- **Boot-time URL validation from day one.** The Vercel CNAME mismatch ate a deploy.
- **Pre-approve scopes at sign-in.** The mid-flow consent prompt cost users.
- **Adopt `dbForUser` from the first route handler.** Retrofitting it is fine, but you'll find IDORs you didn't expect.
- **Don't override Better Auth cookie defaults.** They're correct; "hardening" introduced the `sameSite: 'strict'` regression.
- **Test sign-in twice (sign out between)** before declaring the OAuth flow shipped — that's the only way to catch missing `refresh_token` on second consent.
