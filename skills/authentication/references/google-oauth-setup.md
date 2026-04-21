# OAuth Setup — Console + App Wiring

End-to-end recipe for "Sign in with Google" on a web app. Includes Supabase flow (recommended for Next.js) and direct-OAuth flow (for when you're not using an auth provider).

## 1. Create the Google Cloud Project

```
console.cloud.google.com → project selector → NEW PROJECT
```

Use **separate projects for dev and prod**. Not just separate credentials — separate projects. This isolates quotas, billing, and consent-screen state.

## 2. Configure OAuth Consent Screen (DO THIS FIRST)

You can't create an OAuth client until the consent screen exists.

```
APIs & Services → OAuth consent screen
```

- **User type**: External (unless you're inside a Workspace org and only need internal users)
- **App name**: Your app's display name (shown on consent dialog — users see this)
- **User support email**: Must be reachable
- **App domain / privacy policy / terms of service**: Required before you can move out of "Testing" mode
- **Authorized domains**: Root of your app URL (e.g. `your-domain.com` — no protocol)
- **Developer contact**: Your email

**Scopes**: Add only what you need. For basic sign-in:
- `openid`
- `https://www.googleapis.com/auth/userinfo.email`
- `https://www.googleapis.com/auth/userinfo.profile`

For Calendar read:
- `https://www.googleapis.com/auth/calendar.readonly`

For Calendar read+write:
- `https://www.googleapis.com/auth/calendar.events`  (recommended — per-event access, narrower than `calendar`)

**Test users**: While in Testing mode, add every email that needs to sign in. Testing mode is fine for dev; publish before prod.

**Verification**: If you request any "sensitive" or "restricted" scopes (Calendar is sensitive), publishing the app triggers Google verification. Budget a week for first-time verification. Non-sensitive scopes (profile, email) don't need it.

## 3. Create the OAuth Web Client

```
APIs & Services → Credentials → + CREATE CREDENTIALS → OAuth client ID
```

- **Application type**: Web application
- **Name**: `<app> Web Client (prod)` — name it so you can tell dev from prod later
- **Authorized JavaScript origins**:
  - `https://your-domain.com`
  - Local dev: `http://localhost:3000` (use the actual port)
- **Authorized redirect URIs** — EXACT MATCH, no trailing slash:
  - **If using Supabase**: `https://<your-supabase-project>.supabase.co/auth/v1/callback` (NOT your app URL — Supabase handles the exchange)
  - **If direct OAuth**: `https://your-domain.com/api/auth/callback/google`
  - Local dev: `http://localhost:3000/api/auth/callback/google`

Copy the **Client ID** and **Client Secret** immediately. The secret is visible only until you navigate away; you can re-download later but it's easier to store now. Treat the secret like a password.

## 4. Wire Up Supabase (Recommended for Next.js Apps)

```
Supabase Dashboard → Authentication → Providers → Google → ON
```

- Paste **Client ID** and **Client Secret**
- Save
- Then: `Authentication → URL Configuration`
  - **Site URL**: `https://your-domain.com` (must match `NEXT_PUBLIC_APP_URL` in your app)
  - **Redirect URLs**: `https://your-domain.com/**` and `http://localhost:3000/**`

Supabase handles the token exchange, session cookies, and refresh-token storage. You don't write any OAuth code — you call `supabase.auth.signInWithOAuth({ provider: 'google' })`.

## 5. Next.js 16 Cookie API (CRITICAL)

This is the #1 production auth bug. Next.js 15+ made `cookies()` async; Supabase SSR ≥ 0.3 requires the `getAll`/`setAll` shape, not the old `get`/`set`/`remove`.

### ❌ OLD — DO NOT USE
```ts
// This fails in production on Next.js 15+
const cookieStore = cookies()
createServerClient(url, key, {
  cookies: {
    get: cookieStore.get,
    set: cookieStore.set,
    remove: cookieStore.remove,
  },
})
```

### ✅ NEW — REQUIRED
```ts
// lib/supabase/server.ts
import { createServerClient } from '@supabase/ssr'
import { cookies } from 'next/headers'
import type { Database } from '@/types/database'

export async function createServerSupabase() {
  const cookieStore = await cookies()  // NOTE: await
  return createServerClient<Database>(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll()
        },
        setAll(cookiesToSet) {
          try {
            cookiesToSet.forEach(({ name, value, options }) =>
              cookieStore.set(name, value, options)
            )
          } catch {
            // Called from a Server Component — set via middleware instead
          }
        },
      },
    }
  )
}
```

### Why the try/catch?

Supabase SSR tries to `set` cookies during Server Component rendering, but Next.js disallows that (Server Components can't set cookies). The try/catch lets it fail silently; the middleware refresh below handles the actual cookie writes.

### Middleware session refresh
```ts
// middleware.ts
import { createServerClient } from '@supabase/ssr'
import { NextResponse, type NextRequest } from 'next/server'

export async function middleware(request: NextRequest) {
  let response = NextResponse.next({ request })

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() { return request.cookies.getAll() },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value))
          response = NextResponse.next({ request })
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options)
          )
        },
      },
    }
  )

  // Must call getUser to refresh the session
  await supabase.auth.getUser()
  return response
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)'],
}
```

## 6. Direct OAuth Flow (No Supabase)

Use this only if you're not using Supabase/Auth.js/Clerk. Most of the time, one of those is the right call.

```ts
// app/api/auth/google/route.ts
import { NextResponse } from 'next/server'
import { randomBytes } from 'crypto'
import { cookies } from 'next/headers'

export async function GET() {
  const state = randomBytes(32).toString('hex')
  const cookieStore = await cookies()

  // Store state in httpOnly cookie for CSRF validation
  cookieStore.set('oauth_state', state, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 600, // 10 min
    path: '/',
  })

  const params = new URLSearchParams({
    client_id: process.env.GOOGLE_CLIENT_ID!,
    redirect_uri: `${process.env.NEXT_PUBLIC_APP_URL}/api/auth/callback/google`,
    response_type: 'code',
    scope: 'openid email profile',
    state,
    access_type: 'offline',   // get refresh token
    prompt: 'consent',        // force refresh token on every consent
  })

  return NextResponse.redirect(
    `https://accounts.google.com/o/oauth2/v2/auth?${params}`
  )
}
```

```ts
// app/api/auth/callback/google/route.ts
import { NextResponse, type NextRequest } from 'next/server'
import { cookies } from 'next/headers'

export async function GET(request: NextRequest) {
  const url = new URL(request.url)
  const code = url.searchParams.get('code')
  const state = url.searchParams.get('state')
  const cookieStore = await cookies()
  const savedState = cookieStore.get('oauth_state')?.value

  // CSRF check
  if (!code) return NextResponse.redirect('/signin?error=missing_code')
  if (!state || !savedState) return NextResponse.redirect('/signin?error=state_missing')
  if (state !== savedState) return NextResponse.redirect('/signin?error=state_mismatch')

  // Exchange code for tokens
  const tokenRes = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      code,
      client_id: process.env.GOOGLE_CLIENT_ID!,
      client_secret: process.env.GOOGLE_CLIENT_SECRET!,
      redirect_uri: `${process.env.NEXT_PUBLIC_APP_URL}/api/auth/callback/google`,
      grant_type: 'authorization_code',
    }),
  })

  if (!tokenRes.ok) return NextResponse.redirect('/signin?error=oauth_exchange_error')

  const tokens = await tokenRes.json()
  // tokens: { access_token, refresh_token, expires_in, id_token, scope, token_type }

  // Store refresh_token securely server-side (e.g. encrypted DB column)
  // Store a session cookie for the user

  cookieStore.delete('oauth_state')
  return NextResponse.redirect('/dashboard')
}
```

## 7. Env Validation

Refuse to run with missing or placeholder values. Add this to `instrumentation.ts` so it runs at boot.

```ts
// instrumentation.ts
export function register() {
  const required = {
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL,
    NEXT_PUBLIC_SUPABASE_ANON_KEY: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    GOOGLE_CLIENT_ID: process.env.GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET: process.env.GOOGLE_CLIENT_SECRET,
    NEXT_PUBLIC_APP_URL: process.env.NEXT_PUBLIC_APP_URL,
  }

  const bad = Object.entries(required).filter(([, v]) =>
    !v || /^(your_|demo_|development|placeholder)/i.test(v)
  )

  if (bad.length > 0) {
    const names = bad.map(([k]) => k).join(', ')
    if (process.env.NODE_ENV === 'production') {
      throw new Error(`Missing/placeholder env vars: ${names}`)
    }
    console.warn(`⚠️  Auth config incomplete: ${names}`)
  }

  // Prod sanity
  if (process.env.NODE_ENV === 'production') {
    if (!process.env.NEXT_PUBLIC_APP_URL?.startsWith('https://')) {
      throw new Error('NEXT_PUBLIC_APP_URL must use HTTPS in production')
    }
    if (process.env.NEXT_PUBLIC_APP_URL !== process.env.NEXT_PUBLIC_SITE_URL) {
      throw new Error('APP_URL and SITE_URL must match in production')
    }
  }
}
```

## 8. Redirect URL Validation

Never blindly trust a `returnTo` query param — it's the classic open-redirect vector.

```ts
export function isValidRedirectUrl(url: string | null): boolean {
  if (!url) return false
  try {
    const parsed = new URL(url, process.env.NEXT_PUBLIC_APP_URL)
    const allowed = [
      'localhost',
      '127.0.0.1',
      new URL(process.env.NEXT_PUBLIC_APP_URL!).hostname,
    ]
    return allowed.includes(parsed.hostname)
  } catch {
    return false
  }
}
```

## 9. Error Code Mapping

Give users actionable messages. Don't expose raw Google/Supabase errors.

```ts
export function oauthErrorMessage(code: string): string {
  const map: Record<string, string> = {
    missing_code: 'Authorization failed. Please try signing in again.',
    state_missing: 'Security validation failed. Please try again.',
    state_mismatch: 'Security validation failed. Please try again.',
    session_failed: 'Failed to create session. Please try again.',
    oauth_exchange_error: 'OAuth token exchange failed. Please try again.',
    access_denied: 'You denied access. Please grant permission to sign in.',
    invalid_grant: 'Authorization expired. Please sign in again.',
    invalid_client: 'Configuration error. Please contact support.',
    browser_extension_conflict: 'Browser extension is blocking sign-in. Disable ad blockers and retry.',
    nextjs_cookie_error: 'Cookies are disabled. Enable cookies and retry.',
  }
  return map[code] ?? 'Authentication failed. Please try again.'
}
```

## 10. Verification Checklist

Don't mark auth "done" until all of these pass:

- [ ] `npm run build` succeeds
- [ ] Sign-in button navigates to Google consent screen
- [ ] Consent grants → redirect to callback → lands on dashboard
- [ ] Hard refresh → still signed in
- [ ] Close browser, reopen → still signed in
- [ ] Sign-out clears cookies and redirects to `/signin`
- [ ] Protected route redirects unauthenticated users
- [ ] Protected API route returns 401 for unauthenticated requests
- [ ] Works in prod build locally (`npm run build && npm start`)
- [ ] Works in actual production deploy (NOT just dev)
