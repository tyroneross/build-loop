# Resend — Transactional Email + Webhooks

## When to use

- Sending transactional email from a Better Auth or custom backend
- Wiring `Better Auth`'s `sendVerificationEmail` or `magicLink.sendMagicLink` to Resend
- Verifying Resend webhook signatures (so bounces/complaints update your DB safely)
- Tracking delivery status (sent → delivered → bounced) per message

For magic-link / OTP delivery patterns specifically (templates, retry, bounce handling at the user-flow level), see `resend-otp-magic-link.md`.

For runtime API doc lookups, use `mcp__plugin_context7_context7__query-docs` with library `/websites/resend` (broader SDK surface) or `/resend/resend-skills` (better tuned for agent-flavored questions).

## Sending — basic transactional

```ts
// lib/email/resend.ts
import { Resend } from 'resend'

const resend = new Resend(process.env.RESEND_API_KEY!)

export async function sendTransactional({
  to,
  subject,
  html,
  tags,
}: {
  to: string | string[]
  subject: string
  html: string
  tags?: Array<{ name: string; value: string }>
}) {
  const { data, error } = await resend.emails.send({
    from: process.env.AUTH_EMAIL_FROM || 'YourApp <noreply@example.com>',
    to,
    subject,
    html,
    tags, // showed up in webhook payloads — useful for routing webhooks to the right tracker
  })
  if (error) throw new Error(`Resend: ${error.message}`)
  return data!.id // store this; webhook events reference it
}
```

## Wiring Better Auth's `sendVerificationEmail` (or `sendMagicLink`)

Pattern from atomize-ai. The handler is small because Better Auth gives you the URL and email; Resend just delivers:

```ts
// lib/email/send-magic-link.ts
import { Resend } from 'resend'

export async function sendMagicLinkEmail({ email, url }: { email: string; url: string }) {
  if (!process.env.RESEND_API_KEY) {
    if (process.env.NODE_ENV === 'production') {
      throw new Error('[auth] sendMagicLinkEmail: RESEND_API_KEY missing')
    }
    // Dev: log to console (see better-auth-magic-link.md for the full pattern)
    console.log(`[dev] magic link → ${email}: ${url}`)
    return
  }

  const resend = new Resend(process.env.RESEND_API_KEY)
  const { error } = await resend.emails.send({
    from: process.env.AUTH_EMAIL_FROM || 'YourApp <noreply@example.com>',
    to: email,
    subject: 'Sign in to YourApp',
    html: `<a href="${url}">Sign in</a>`, // see better-auth-magic-link.md for a styled template
  })
  if (error) throw new Error(`[auth] Resend send failed: ${error.message}`)
}
```

Then in `lib/auth.ts`:

```ts
import { magicLink } from 'better-auth/plugins'
import { sendMagicLinkEmail } from '@/lib/email/send-magic-link'

magicLink({
  expiresIn: 60 * 10,
  sendMagicLink: async ({ email, url }) => sendMagicLinkEmail({ email, url }),
})
```

For email-verification on email/password sign-up, swap `magicLink` for `emailVerification` and pass `sendVerificationEmail` instead — same handoff shape.

## Webhook signature verification

**Critical:** without signature verification, an attacker can POST fake `email.bounced` events at your webhook to mark legit emails as bounced (potentially blocking real users from receiving mail, depending on your bounce-handling policy).

Pattern from `Travel Planner/app/api/webhooks/resend/route.ts`:

```ts
// app/api/webhooks/resend/route.ts
import { NextRequest, NextResponse } from 'next/server'
import { headers } from 'next/headers'
import { createHash, timingSafeEqual } from 'crypto'

interface ResendWebhookEvent {
  type: 'email.sent' | 'email.delivered' | 'email.bounced' | 'email.complained' | 'email.delivery_delayed'
  created_at: string
  data: {
    id: string
    to: string[]
    from: string
    subject: string
    created_at: string
    tags?: Array<{ name: string; value: string }>
  }
}

function verifyWebhookSignature(body: string, signature: string, secret: string): boolean {
  try {
    const expected = createHash('sha256').update(body).update(secret).digest('hex')
    const provided = signature.replace('sha256=', '')
    return timingSafeEqual(Buffer.from(expected, 'hex'), Buffer.from(provided, 'hex'))
  } catch (err) {
    console.error('webhook signature verify failed:', err)
    return false
  }
}

export async function POST(request: NextRequest) {
  const headersList = await headers()
  const signature = headersList.get('resend-signature')
  const webhookSecret = process.env.RESEND_WEBHOOK_SECRET

  if (!webhookSecret) {
    // Decision call: warn vs hard-fail. Travel Planner warns. For a stricter
    // posture, return 401 here when the secret isn't configured in prod.
    console.warn('RESEND_WEBHOOK_SECRET not configured — verification disabled')
  }

  const body = await request.text() // MUST read raw text BEFORE JSON.parse — signature is over the raw bytes
  if (webhookSecret && signature) {
    if (!verifyWebhookSignature(body, signature, webhookSecret)) {
      return NextResponse.json({ error: 'Invalid signature' }, { status: 401 })
    }
  }

  let event: ResendWebhookEvent
  try {
    event = JSON.parse(body)
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  // Idempotency: events can deliver more than once. Use event.data.id + event.type
  // as the dedupe key against your delivery-status table.

  // Map and persist:
  const status = (
    {
      'email.sent': 'sent',
      'email.delivered': 'delivered',
      'email.bounced': 'bounced',
      'email.complained': 'complained',
      'email.delivery_delayed': 'sent', // keep as sent, log the delay separately
    } as const
  )[event.type]

  if (!status) {
    return NextResponse.json({ received: true })
  }

  await updateEmailDeliveryStatus(event.data.id, status, {
    webhook_type: event.type,
    webhook_received_at: new Date().toISOString(),
    resend_created_at: event.created_at,
    recipients: event.data.to,
    tags: event.data.tags,
  })

  return NextResponse.json({ received: true, messageId: event.data.id, status })
}

export async function GET() {
  // Health check — useful for verifying the webhook URL is wired before pointing Resend at it
  return NextResponse.json({
    status: 'ok',
    configured: {
      webhook_secret: !!process.env.RESEND_WEBHOOK_SECRET,
      resend_api_key: !!process.env.RESEND_API_KEY,
    },
  })
}
```

### Why the specific crypto choices

- **`timingSafeEqual`** (not `===`) — prevents timing-based signature forgery. `===` short-circuits on first mismatched byte and leaks signature length info.
- **`createHash('sha256').update(body).update(secret)`** — the order matches what Resend signs. If you swap them, signatures won't verify.
- **Read raw text BEFORE `JSON.parse`** — Next.js will not give you the unparsed body if you call `request.json()` first; the bytes for signature verification have to be the exact wire bytes.

## Idempotency — same event twice

Resend webhooks are at-least-once. If your `updateEmailDeliveryStatus` is non-idempotent, a redelivered event flips `bounced` → `sent` → `bounced`. Two patterns:

1. **State-machine guard** — `delivered` is terminal; once a message is `delivered`, ignore subsequent non-`bounced` events. `bounced`/`complained` always wins.
2. **Event-id dedupe** — store `(message_id, event_type, received_at)` as a unique tuple; if it's a duplicate, no-op.

Pattern 1 is simpler; pattern 2 is correct under arbitrary event ordering. For most apps, pattern 1 is fine.

## Required env vars

```env
RESEND_API_KEY=re_...
RESEND_WEBHOOK_SECRET=whsec_...   # configure in Resend dashboard, then paste here
AUTH_EMAIL_FROM="YourApp <noreply@yourdomain.com>"   # MUST match a verified domain
```

## Verification

1. Send a test email via the SDK. Confirm Resend dashboard shows it.
2. Check the webhook health endpoint: `GET /api/webhooks/resend` should return `configured: { webhook_secret: true, resend_api_key: true }`.
3. From the Resend dashboard, send a test webhook. Confirm 200 response with valid signature; tamper with the body and confirm 401.
4. Send to a bouncing address (`bounce@simulator.amazonses.com` if Resend is on SES) and confirm the `email.bounced` event lands and updates your tracker.
5. Confirm the `from` address matches a verified domain — sending from an unverified domain returns a permission error from the SDK, not a webhook event.

## Cross-references

- `resend-otp-magic-link.md` — delivery / retry / bounce-handling patterns at the user-flow level
- `better-auth-magic-link.md` — caller-side handoff pattern
- For domain verification, DKIM, and SPF setup: query Context7 with `library: "/websites/resend"`, `topic: "domain verification"`
