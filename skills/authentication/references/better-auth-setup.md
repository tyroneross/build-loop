# Better Auth — Setup

## When to use

- New Next.js project on Neon + Drizzle (the documented stack default)
- Adding social providers (Google, Apple) to an existing Better Auth instance
- Wiring an iOS/mobile client via the Bearer plugin
- Verifying that Google sign-in produces a usable `refresh_token`
- Migrating away from `@supabase/ssr` (Travel Planner did this — see `lessons-travel-planner-better-auth.md`)

For magic-link / OTP setup, see `better-auth-magic-link.md`.
For runtime API doc lookups, use `mcp__plugin_context7_context7__query-docs` with library `/better-auth/better-auth`.

## Server instance (Drizzle + Neon + Google + Apple)

Pattern from `Travel Planner/lib/auth.ts` (production-grade, multi-platform). Adjust provider list to your needs.

```ts
// lib/auth.ts
import { betterAuth } from 'better-auth'
import { drizzleAdapter } from 'better-auth/adapters/drizzle'
import { bearer } from 'better-auth/plugins'
import { db } from '@/lib/db'

const TRUSTED_ORIGINS = [
  process.env.BETTER_AUTH_URL ?? 'http://localhost:3000',
  'http://localhost:3000',
  // Add native URL schemes here for mobile callbacks, e.g.:
  // 'travelplanner://auth-callback',
]

export const auth = betterAuth({
  database: drizzleAdapter(db, { provider: 'pg' }),
  secret: process.env.BETTER_AUTH_SECRET!,
  baseURL: process.env.BETTER_AUTH_URL ?? 'http://localhost:3000',
  trustedOrigins: TRUSTED_ORIGINS,
  socialProviders: {
    google: {
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
      // CRITICAL: both are required to guarantee refresh_token on every sign-in.
      // See Universal footgun #8 in SKILL.md.
      accessType: 'offline',
      prompt: 'select_account consent',
      scope: [
        'openid',
        'email',
        'profile',
        // Pre-approve any scopes you'll need later so the user doesn't hit a
        // second consent screen mid-flow:
        // 'https://www.googleapis.com/auth/calendar.app.created',
      ],
    },
  },
  plugins: [bearer()], // Bearer plugin only needed if you have a mobile/native client
})

export type Session = typeof auth.$Infer.Session
```

### Apple provider (conditional)

For mobile apps with Apple Sign-In, register the provider only when `APPLE_CLIENT_ID` is present so unconfigured environments fall back gracefully:

```ts
const appleProvider = (() => {
  const clientId = process.env.APPLE_CLIENT_ID
  if (!clientId) return undefined
  return {
    clientId,
    // For iOS native ID-token sign-in, clientSecret is OPTIONAL.
    // For web OAuth code-exchange, clientSecret is REQUIRED (a JWT signed with
    // your .p8 private key; max 6-month lifetime — pre-generate or sign at startup).
    ...(process.env.APPLE_CLIENT_SECRET && { clientSecret: process.env.APPLE_CLIENT_SECRET }),
    ...(process.env.APPLE_APP_BUNDLE_IDENTIFIER && {
      appBundleIdentifier: process.env.APPLE_APP_BUNDLE_IDENTIFIER,
    }),
  }
})()

// then inside socialProviders:
//   ...(appleProvider ? { apple: appleProvider } : {}),
```

## Catch-all route handler (Next.js App Router)

```ts
// app/api/auth/[...all]/route.ts
import { auth } from '@/lib/auth'
import { toNextJsHandler } from 'better-auth/next-js'

export const { GET, POST } = toNextJsHandler(auth)
```

That single file replaces every Supabase callback / NextAuth route handler.

## Client (React)

Two patterns ship in the wild. Pick by need:

**Minimal (Travel Planner)** — uses `window.location.origin` by default, no `basePath` override:

```ts
// lib/auth-client.ts
'use client'
import { createAuthClient } from 'better-auth/react'

export const authClient = createAuthClient({
  baseURL: process.env.NEXT_PUBLIC_BETTER_AUTH_URL ?? '',
})

export const { signIn, signUp, signOut, useSession } = authClient
```

**Explicit basePath + `credentials: include` (ProductPilot)** — needed when the client and API are on different origins, or when you've mounted the handler under a non-default path:

```ts
// client/src/lib/auth.ts
import { createAuthClient } from 'better-auth/react'
import { magicLinkClient } from 'better-auth/client/plugins'

const authOrigin =
  typeof window !== 'undefined'
    ? window.location.origin
    : 'http://localhost:3000'

export const authClient = createAuthClient({
  baseURL: authOrigin,
  basePath: '/api/auth',
  fetchOptions: {
    credentials: 'include' as RequestCredentials,
  },
  plugins: [magicLinkClient()],
})
```

## Feature flag with anonymous fallback (atomize-ai pattern)

When auth is being introduced incrementally and existing routes were written assuming an anonymous user, this gate keeps everything working until you flip `ENABLE_AUTH=true`:

```ts
// lib/auth.ts (continued)
import { headers } from 'next/headers'

export const ENABLE_AUTH = process.env.ENABLE_AUTH === 'true'

export async function getUserIdFromSession(): Promise<string> {
  if (!ENABLE_AUTH) return 'anonymous'
  try {
    const session = await auth.api.getSession({ headers: await headers() })
    return session?.user?.id ?? 'anonymous'
  } catch (error) {
    console.warn('[auth] getUserIdFromSession failed, falling back to anonymous:', error)
    return 'anonymous'
  }
}
```

Important: this is a transitional pattern. Remove it before declaring auth "shipped" — `anonymous` user IDs are not a real auth boundary.

## IDOR guard — `dbForUser(userId)`

Better Auth gives you a session, but it does NOT enforce per-row authorization. Every query must scope to the session's user id. Travel Planner's `lib/db/index.ts` exports a `dbForUser(userId)` helper that returns CRUD methods pre-bound to that user:

```ts
// lib/db/index.ts (sketch)
export function dbForUser(userId: string) {
  return {
    camps: {
      list: () =>
        db.select().from(schema.camps).where(eq(schema.camps.userId, userId)),
      get: (id: string) =>
        db
          .select()
          .from(schema.camps)
          .where(and(eq(schema.camps.userId, userId), eq(schema.camps.id, id)))
          .limit(1),
      create: (input: Omit<typeof schema.camps.$inferInsert, 'userId'>) =>
        db.insert(schema.camps).values({ ...input, userId }).returning(),
      // update / remove follow the same and(eq(userId), eq(id)) pattern
    },
    // ... one block per table
  }
}
```

Call sites read like `dbForUser(session.user.id).camps.list()` — there's no way to forget the user filter, and a code review can grep for `from(schema.X)` outside `dbForUser` to spot violations.

## Verification checklist (before declaring auth shipped)

1. Sign in via Google. Confirm in DB:
   - `user.id` is set
   - `account.refresh_token` is non-null
   - `account.access_token` is set with a future `expires_at`
2. Sign out, sign back in. Confirm `refresh_token` is still set (not cleared by re-consent).
3. Wait past the access-token expiry. Confirm the next API call refreshes silently (Better Auth handles this if `accessType: 'offline'` is set).
4. Verify cookies are `httpOnly`, `secure` (prod), `sameSite: 'lax'`. (See Universal footgun #11.)
5. In production, confirm `BETTER_AUTH_URL` matches the actual Vercel URL — boot-time validation recommended (Universal footgun #7).
6. Hit a protected API route from a logged-out browser; confirm 401, not anonymous fallback (unless `ENABLE_AUTH=false` is intentional).

## Required env vars

```env
BETTER_AUTH_SECRET=<32-byte random string>
BETTER_AUTH_URL=https://your-app.com   # MUST match production URL exactly
NEXT_PUBLIC_BETTER_AUTH_URL=https://your-app.com   # client uses this
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
# Optional Apple:
APPLE_CLIENT_ID=...
APPLE_CLIENT_SECRET=...   # web flow only; iOS native skips
APPLE_APP_BUNDLE_IDENTIFIER=com.example.app
```

For Drizzle schema, see Better Auth docs: `mcp__plugin_context7_context7__query-docs` with `library: "/better-auth/better-auth"` and `topic: "drizzle schema"`.

## Cross-references

- Universal footguns #7 (Vercel URL mismatch), #8 (refresh_token guarantee), #9 (IDOR guard), #11 (cookie config) in `../SKILL.md`
- `lessons-travel-planner-better-auth.md` — incident-style narrative of these issues hitting production
- `better-auth-magic-link.md` — magic-link plugin setup
- `resend-email.md` — wiring `sendVerificationEmail` to Resend
