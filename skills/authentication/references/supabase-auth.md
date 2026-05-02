# Supabase Auth

## When to use

- **Legacy projects** already on Supabase — keep them there unless there's a compelling reason to migrate
- **Single-project apps** where Supabase's bundled Postgres + Storage + Realtime + Auth justify the lock-in
- **Edge-function-heavy** workloads where Supabase Auth's JWT verification at the edge is convenient

For new Next.js projects, the documented stack default is **Neon + Better Auth + Drizzle** instead — see `better-auth-setup.md`. Supabase Auth across multiple projects gets expensive (per-project pricing) and the SSR cookie story has been brittle (see breaking change below).

For runtime API doc lookups, use `mcp__plugin_context7_context7__query-docs` with library `/supabase/supabase`.

## When Supabase Auth vs Better Auth — decision

| Need | Pick |
|---|---|
| Brand-new project, Next.js, single repo | **Better Auth + Neon + Drizzle** (Travel Planner migrated TO this) |
| Existing project on Supabase, working fine | **Stay on Supabase** — migration cost > benefit |
| Multi-project portfolio (3+ apps) | **Better Auth** — per-project Supabase cost adds up |
| Edge functions doing JWT verification on every request | **Supabase** — built-in JWT verification at the edge |
| Need Postgres RLS policies as the auth boundary | **Supabase** (or Better Auth + manual `dbForUser` IDOR guard — see `better-auth-setup.md`) |
| Complex social provider config (Apple iOS native, etc.) | **Better Auth** — more flexible plugin surface |
| Mobile native app with offline session persistence | Either — Supabase has the JWT story; Better Auth has the Bearer plugin |

## ⚠️ SSR ≥0.3 breaking change — `getAll` / `setAll`

The single biggest gotcha. `@supabase/ssr` versions before 0.3 used per-cookie `get`/`set`/`remove`; 0.3+ requires a unified `getAll`/`setAll` interface. Code that worked in dev with an old version will fail in prod with the new version — **and the failure mode is a silent session loss**, not a thrown error.

### Old (broken on ≥0.3)

```ts
// ❌ DO NOT use on @supabase/ssr ≥0.3
const supabase = createServerClient(url, key, {
  cookies: {
    get(name) { return cookieStore.get(name)?.value },
    set(name, value, options) { cookieStore.set({ name, value, ...options }) },
    remove(name, options) { cookieStore.set({ name, value: '', ...options }) },
  },
})
```

### New (correct)

```ts
// ✅ @supabase/ssr ≥0.3
import { createServerClient } from '@supabase/ssr'
import { cookies } from 'next/headers'

export async function createClient() {
  const cookieStore = await cookies()
  return createServerClient(
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
            // Called from a Server Component — middleware will refresh sessions instead.
          }
        },
      },
    }
  )
}
```

If you see "session not persisting after sign-in", "logged out after refresh", or RSC-only auth working but route handlers losing the session — check this first.

## Browser client

```ts
// lib/supabase-browser.ts
import { createBrowserClient } from '@supabase/ssr'

export function createBrowserSupabase() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  )
}
```

## Env-var validation — warn, don't fail (super_news pattern)

Useful when Supabase is optional infrastructure and you want CI / preview envs without a real backend. From `super_news/lib/supabase.ts`:

```ts
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || 'https://placeholder.supabase.co'
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || 'placeholder_key'

const validateSupabaseConfig = () => {
  const hasValidUrl =
    process.env.NEXT_PUBLIC_SUPABASE_URL &&
    process.env.NEXT_PUBLIC_SUPABASE_URL !== 'your_supabase_project_url' &&
    process.env.NEXT_PUBLIC_SUPABASE_URL !== 'https://placeholder.supabase.co'
  const hasValidKey =
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY &&
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY !== 'your_supabase_anon_key'

  if (!hasValidUrl || !hasValidKey) {
    console.warn('Supabase configuration not properly set up. Database features will not work.')
    return false
  }
  return true
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey)

// Then in every operation:
async function getThing() {
  if (!validateSupabaseConfig()) return [] // graceful no-op, not a crash
  // ... real query ...
}
```

Trade-off: this hides config errors that should be surfaced. Prefer this only for genuinely-optional features (e.g., user-saved data when the app is also useful unauthenticated). For required auth, fail at boot — silent fallback is worse than a clear error.

## RLS as the auth boundary

Supabase's pitch is "let RLS do the auth check, pass the user JWT to Postgres, never write `dbForUser` wrappers." When it works, it's elegant:

```sql
-- Example RLS policy
create policy "users_own_camps" on camps
  for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
```

Footguns:
1. **Service-role queries bypass RLS by design.** Easy to accidentally use the service-role key in a route handler thinking you're being efficient — that's an IDOR.
2. **`anon` role policies are public.** A misconfigured `select` policy on `anon` makes the table world-readable.
3. **RLS errors look like "row not found".** `select * where id = X` returns `[]` if RLS denies, not a permission error. Confusing.
4. **Migration ordering matters.** Add RLS policies BEFORE inserting prod data; enabling RLS on a populated table without policies locks it.

If you adopt RLS, run a periodic audit: log every distinct combination of (table, role, command) the app actually uses, then assert each is policy-covered.

## Migrating away from Supabase Auth

Travel Planner migrated `@supabase/ssr` → Better Auth. Pattern:

1. Stand up Better Auth in parallel (keeps Supabase running)
2. Migrate user records via a one-time script (account, session, verification tables)
3. Switch the route handler from Supabase callbacks to `app/api/auth/[...all]/route.ts`
4. Delete Supabase-specific cookie code
5. Replace RLS-based auth with `dbForUser(userId)` IDOR wrapper (see `better-auth-setup.md`)

The biggest risk is in step 2 — schema mismatch. Better Auth's user/account/session tables don't map 1:1 to Supabase auth schema. Test on a copy of prod first.

## Verification

1. Sign up with email/password (or magic-link / social, depending on config)
2. Hard refresh the page — confirm session persists (this is the SSR cookie test)
3. Open in a new tab — confirm session persists across tabs
4. Sign out — confirm cookies cleared and protected pages redirect
5. Confirm `auth.uid()` works inside SQL via the SQL editor (`select auth.uid()`)
6. If using RLS: pick one protected table and confirm with `anon` role that it returns `[]`, with authenticated role that it returns owned rows only

## Cross-references

- Universal footgun #11 (cookie config) in `../SKILL.md`
- `better-auth-setup.md` — the recommended alternative for new projects
- For the Travel Planner migration narrative, see `lessons-travel-planner-better-auth.md`
