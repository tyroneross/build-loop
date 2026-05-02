# Better Auth — Magic Link / OTP

## When to use

- Email-only auth (no password, no social) — atomize-ai and ProductPilot both use this
- Adding magic-link as an alternative sign-in method on top of social providers
- OTP / verification-code flows (uses the same plugin with a different delivery template)

For the broader Better Auth setup (database, providers, routes), start at `better-auth-setup.md`.
For Resend wiring on the email delivery side, see `resend-email.md` (transactional) or `resend-otp-magic-link.md` (delivery + retry patterns).

## Server config (atomize-ai pattern)

```ts
// lib/auth.ts
import { betterAuth } from 'better-auth'
import { prismaAdapter } from 'better-auth/adapters/prisma'
import { magicLink } from 'better-auth/plugins'
import { prisma } from '@/lib/prisma'
import { sendMagicLinkEmail } from '@/lib/email/send-magic-link'

export const auth = betterAuth({
  database: prismaAdapter(prisma, { provider: 'postgresql' }),
  baseURL: process.env.BETTER_AUTH_URL || 'http://localhost:3150',
  secret: process.env.BETTER_AUTH_SECRET,
  emailAndPassword: { enabled: false },
  plugins: [
    magicLink({
      // 10-minute expiry. Single-use is the plugin default — do not weaken.
      expiresIn: 60 * 10,
      sendMagicLink: async ({ email, url }) => {
        await sendMagicLinkEmail({ email, url })
      },
    }),
  ],
})
```

For Drizzle, swap `prismaAdapter(prisma, { provider: 'postgresql' })` with `drizzleAdapter(db, { provider: 'pg' })` (Travel Planner / ProductPilot use Drizzle).

## Client (with magic-link plugin)

```ts
// lib/auth-client.ts
'use client'
import { createAuthClient } from 'better-auth/react'
import { magicLinkClient } from 'better-auth/client/plugins'

export const authClient = createAuthClient({
  baseURL: process.env.NEXT_PUBLIC_BETTER_AUTH_URL || 'http://localhost:3150',
  plugins: [magicLinkClient()],
})

export const { signIn, signOut, useSession } = authClient

// Usage:
// await authClient.signIn.magicLink({ email, callbackURL: '/dashboard' })
```

## Email delivery handoff (dev-friendly, prod-strict)

atomize-ai's `lib/email/send-magic-link.ts` shows a useful pattern: log to console in dev, throw in prod, with the actual Resend block ready to uncomment.

```ts
interface SendMagicLinkParams { email: string; url: string }

export async function sendMagicLinkEmail({ email, url }: SendMagicLinkParams): Promise<void> {
  const hasEmailProvider = Boolean(process.env.RESEND_API_KEY)

  if (!hasEmailProvider) {
    if (process.env.NODE_ENV === 'production') {
      throw new Error(
        '[auth] sendMagicLinkEmail: no email provider configured. ' +
          'Set RESEND_API_KEY or wire up a different provider.'
      )
    }
    // Dev mode: print the link to the terminal so you can click it directly.
    console.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    console.log('  📧 MAGIC LINK (dev mode — email not sent)')
    console.log(`  To:   ${email}`)
    console.log(`  Link: ${url}`)
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')
    return
  }

  const { Resend } = await import('resend')
  const resend = new Resend(process.env.RESEND_API_KEY)

  const { error } = await resend.emails.send({
    from: process.env.AUTH_EMAIL_FROM || 'YourApp <noreply@example.com>',
    to: email,
    subject: 'Sign in to YourApp',
    html: `
      <div style="font-family: -apple-system, system-ui, sans-serif; max-width: 480px; margin: 0 auto;">
        <h1 style="font-size: 20px; color: #111;">Sign in to YourApp</h1>
        <p style="color: #555; line-height: 1.5;">Click the button below to sign in. This link expires in 10 minutes.</p>
        <a href="${url}" style="display: inline-block; padding: 12px 20px; background: #111; color: #fff; text-decoration: none; border-radius: 6px; font-weight: 500;">Sign in</a>
        <p style="color: #888; font-size: 12px; margin-top: 24px;">If you didn't request this, you can safely ignore this email.</p>
      </div>
    `,
  })

  if (error) throw new Error(`[auth] Resend send failed: ${error.message}`)
}
```

Key behaviors:
- **Dev never blocks on missing API key** — link goes to terminal
- **Prod hard-fails** when key is absent (no silent skip)
- **Dynamic import** of `resend` keeps it out of the cold-start bundle if unused

## Footguns specific to magic-link

1. **Don't lengthen `expiresIn`.** 10 minutes is the right default. Longer windows widen the attack surface for email-account compromise.
2. **Rate-limit by email.** A loop submitting `/sign-in/magic-link` for `victim@x.com` is an email-flood vector. Use a per-email rate limiter (Upstash, Vercel KV, or a simple in-memory LRU for low traffic).
3. **Log delivery failures, not delivery successes.** Bouncebacks from Resend (`email.bounced`) are NOT the user's fault — log them and surface to ops, not to the user.
4. **Single-use is built-in.** Don't extend `magicLink({ ... })` to allow re-use; that defeats the security model.
5. **Idempotent click handlers.** If a user double-clicks the magic-link, the second hit should land them in their session, not 401. Better Auth's default does this; verify if you customize.

## Verification

1. Trigger a magic-link send. Confirm the magicLink record in `verification` (Better Auth) / equivalent table.
2. Click the link. Confirm session created, link record marked consumed.
3. Click the same link again — should fail with `INVALID_TOKEN` or redirect to a "link already used" state.
4. Wait 11 minutes, retry — should fail with `EXPIRED_TOKEN`.
5. In prod with `RESEND_API_KEY` unset, sign-in should hard-fail with the error message above (not silently succeed).

## Cross-references

- Universal footgun #10 (magic-link expiry & idempotency) in `../SKILL.md`
- `resend-email.md` for transactional + webhook integration
- `resend-otp-magic-link.md` for delivery patterns
- For OTP (numeric code) instead of clickable link, query Context7: `library: "/better-auth/better-auth"`, `topic: "OTP plugin"` — same plugin, different delivery template.
