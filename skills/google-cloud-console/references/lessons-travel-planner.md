# Lessons from Travel Planner (2025 Build)

Real failures from shipping a Next.js + Supabase + Google OAuth app. Each one cost at least half a day. Read this when something breaks — there's a decent chance it's listed here.

## 1. "Works in dev, fails in prod" Auth — Supabase SSR Cookie API

**Symptom**: Sign-in works locally. In production, after Google consent the user lands back on `/signin`. No error in the UI. Vercel logs show:

```
TypeError: Cannot read properties of undefined (reading 'get')
  at <supabase ssr internals>
```

**Root cause**: Supabase SSR ≥ 0.3 moved from `{ get, set, remove }` cookie methods to `{ getAll, setAll }`. Next.js 15 dev had fallback compat that masked the bug. Prod enforced the new contract.

**Fix**: Use `await cookies()` and `getAll`/`setAll` everywhere. Full recipe in `oauth-setup.md`.

**Prevention**: Test the prod build locally (`npm run build && npm start`) before every deploy. Dev-only bugs hide in the hot-reload fallback paths.

## 2. Route-Group Conflict → Vercel `ENOENT` manifest failure

**Symptom**: Local build works. Vercel build fails during "Collecting build traces":

```
Error: ENOENT: no such file or directory,
lstat '/vercel/path0/.next/server/app/(app)/page_client-reference-manifest.js'
```

**Root cause**: `app/page.tsx` AND `app/(app)/page.tsx` both exist. Next.js generates the manifest at `.next/server/app/page_client-reference-manifest.js`, but the tracer expects it at `.next/server/app/(app)/page_client-reference-manifest.js`. The app won't deploy until one of the pages is removed.

**Fix**:
```bash
rm app/page.tsx  # if using app/(app)/page.tsx as the real root
npm run build
ls .next/server/app/(app)/*manifest*  # verify manifest now exists
```

**Prevention**: Pick one root-level routing strategy up front — either a root `page.tsx` or a route group for authenticated views. Not both.

## 3. "100% Complete" Claims Without End-to-End Testing

**Symptom**: Status doc said "Stage 1: OAuth Authentication — 100% Complete". Actual state: sign-in was broken. This wasn't a technical bug — it was a process bug. Completion was claimed on "implementation written", not "flow works".

**Fix**: Never mark auth "done" until:
1. `npm run build` succeeds
2. You can sign in via Google in the prod build
3. Session survives hard refresh
4. Session survives browser close/reopen
5. Sign-out actually clears cookies
6. A protected API route returns 401 when unauthenticated

All six. Every time. See the verification checklist in `oauth-setup.md`.

## 4. Production Trace Logs Are Essential

**Observation**: Local debugging couldn't reproduce the Supabase SSR bug (#1). Only Vercel's production traces showed the exact error. Without them, debugging would have been blind guesses.

**Prevention**:
- Enable Vercel Log Drains or Sentry from day one
- Add correlation IDs to auth callback logs (`callback_id=abc` so you can trace one user's flow)
- Log the auth step at each stage: `auth.redirect`, `auth.callback_received`, `auth.tokens_exchanged`, `auth.session_set`

Don't wait for production issues to add observability.

## 5. `redirect_uri_mismatch` Chasing

**Symptom**: After clicking "Sign in with Google", lands on a Google error page: `Error 400: redirect_uri_mismatch`.

**Root cause — any of**:
- Trailing slash difference (`http://localhost:3000` vs `http://localhost:3000/`)
- Protocol difference (`http` vs `https`)
- Wrong port
- Using app URL as Google redirect when Supabase is in the middle (Supabase uses its own callback)
- Stale entry in Google Cloud Console that doesn't match current env var

**Fix**: The redirect URI you register in Google Cloud Console MUST match the one your app passes in the OAuth request **character-for-character**.

For Supabase:
```
https://<project-ref>.supabase.co/auth/v1/callback
```

For direct OAuth:
```
https://your-domain.com/api/auth/callback/google
http://localhost:3000/api/auth/callback/google
```

**Prevention**: Add a boot-time check that logs the exact redirect URI your app will send, and compare it manually with the Cloud Console list during setup.

## 6. Credential Rotation Is Painful — Plan For It

**Observation**: When a secret leaked, rotating the Google client secret required:
1. Generate new secret in Google Cloud Console
2. Update Supabase Dashboard → Providers → Google
3. Update Vercel env var (`GOOGLE_CLIENT_SECRET`)
4. Trigger redeploy
5. Test sign-in flow
6. After verifying, delete old secret in Cloud Console

Steps 1-3 can be done with the old secret still active (Google supports two live secrets during rotation). If you skip the "both live" window, users in mid-session get `invalid_client`.

**Prevention**: Document the rotation procedure BEFORE you need it. Store credentials in a secret manager (Vercel Env, Doppler, AWS Secrets Manager) so rotation is one place to change.

## 7. "Demo" / "Placeholder" Values Shipped to Prod

**Symptom**: `.env.example` had `GOOGLE_CLIENT_ID=your_client_id_here`. Someone copied it to `.env.local`, it got committed, CI pushed it to prod. Sign-in broke silently.

**Fix**: Boot-time env validator that REFUSES to start in prod if any env var matches `your_|demo_|development|placeholder`:

```ts
const bad = Object.entries(requiredVars).filter(([, v]) =>
  !v || /^(your_|demo_|development|placeholder)/i.test(v)
)
if (bad.length && process.env.NODE_ENV === 'production') {
  throw new Error(`Missing env vars: ${bad.map(([k]) => k).join(', ')}`)
}
```

## 8. CSRF Defense — State Parameter

**Observation**: Without a state parameter, nothing stops a malicious site from forging an OAuth callback. Supabase's flow handles this internally; direct OAuth flows must generate + verify state themselves.

**Pattern**:
1. Generate a random state token, store in httpOnly cookie with 10-min TTL
2. Pass state in the authorize URL
3. On callback, compare returned state to the cookie
4. Mismatch → reject, redirect to signin with `error=state_mismatch`

See the code in `oauth-setup.md` section 6.

## 9. Session Refresh Needs a Threshold

**Symptom**: User's session expires mid-action, they lose unsaved work.

**Fix**: Check expiry proactively. Refresh if `expires_at - now < 15 minutes`:

```ts
const timeUntilExpiry = session.expires_at - Math.floor(Date.now() / 1000)
if (timeUntilExpiry < 15 * 60) {
  await supabase.auth.refreshSession()
}
```

Run this check on a 1-hour interval + on visibility change (user returns to tab).

## 10. Open Redirect via `returnTo`

**Symptom**: Code review flagged `?returnTo=http://evil.com` — sign-in flow would redirect to attacker's site.

**Fix**: Allow-list validation on the redirect URL:

```ts
function isValidRedirect(url: string): boolean {
  try {
    const parsed = new URL(url, process.env.NEXT_PUBLIC_APP_URL)
    const allowed = ['localhost', '127.0.0.1', new URL(process.env.NEXT_PUBLIC_APP_URL!).hostname]
    return allowed.includes(parsed.hostname)
  } catch {
    return false
  }
}
```

Never trust a query param as a navigation target without validating its host.

## Process Lessons (Not Technical)

These aren't bugs, they're habits that would have prevented half the bugs:

1. **Ship a sign-in E2E test before any other feature**. It's your canary. If sign-in breaks, everything else does.
2. **Separate dev and prod Google Cloud projects**, not just credentials. Quota and consent-screen state are project-scoped.
3. **Publish the OAuth consent screen early** so you hit Google verification before you have a deadline.
4. **Use certainty markers** in status docs (✅ verified / ⚠️ untested / ❓ unknown) — not "100% complete".
5. **Document rotation, scope changes, and env-var lifecycle** in a `docs/AUTH.md` — not buried in commits.
