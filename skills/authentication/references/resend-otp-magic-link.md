# Resend — OTP / Magic-Link Delivery Patterns

## When to use

- Designing the user-flow side of magic-link or OTP delivery (templates, retry, bounce handling)
- Choosing between magic-link and OTP for your app
- Handling delivery failures gracefully without exposing email-existence to attackers

For the SDK + webhook signature verification side, see `resend-email.md`.
For the Better Auth plugin side (server config, expiry, single-use), see `better-auth-magic-link.md`.
For runtime API doc lookups, use `mcp__plugin_context7_context7__query-docs` with `library: "/websites/resend"` (broader) or `/resend/resend-skills` (agent patterns).

## Magic-link vs OTP — pick one

| Factor | Magic Link | OTP (numeric code) |
|---|---|---|
| UX on the device that requested sign-in | One click — best | Type 6 digits |
| UX on a different device than the request | Awful (have to forward link) | Good (read code on phone, type on laptop) |
| Mobile app sign-in | OK with deep links, but easy to misconfigure | Better — code transcription works regardless of platform |
| Phishing surface | Higher — link can be cloaked | Lower — code in body is hard to fake |
| Server complexity | Same plugin, simpler delivery | Same plugin, slightly different UX |

**Default**: magic-link for web-first apps, OTP for mobile-first or multi-device flows. Both can co-exist behind a "Send code" / "Email link instead" toggle.

## Template — magic-link (HTML)

Inline styles only — many email clients strip `<style>` blocks. Keep the styled CTA above the fold (most clients only show the first ~600px).

```ts
const html = `
<!DOCTYPE html>
<html>
<body style="margin: 0; background: #f5f5f7; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
  <div style="max-width: 480px; margin: 0 auto; padding: 32px 24px;">
    <h1 style="font-size: 22px; color: #111; margin: 0 0 12px;">Sign in to ${appName}</h1>
    <p style="color: #555; line-height: 1.5; margin: 0 0 24px;">
      Click the button below to sign in. This link expires in 10 minutes and can only be used once.
    </p>
    <a href="${url}" style="display: inline-block; padding: 14px 24px; background: #111; color: #fff; text-decoration: none; border-radius: 8px; font-weight: 500;">
      Sign in
    </a>
    <p style="color: #888; font-size: 12px; margin: 32px 0 0; line-height: 1.5;">
      If the button doesn't work, paste this into your browser:<br>
      <span style="color: #555; word-break: break-all;">${url}</span>
    </p>
    <p style="color: #aaa; font-size: 12px; margin: 16px 0 0;">
      If you didn't request this, you can ignore this email.
    </p>
  </div>
</body>
</html>
`
```

## Template — OTP (HTML)

```ts
const html = `
<!DOCTYPE html>
<html>
<body style="margin: 0; background: #f5f5f7; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
  <div style="max-width: 480px; margin: 0 auto; padding: 32px 24px;">
    <h1 style="font-size: 22px; color: #111; margin: 0 0 12px;">Your sign-in code</h1>
    <p style="color: #555; line-height: 1.5; margin: 0 0 24px;">
      Enter this code in ${appName} to sign in. It expires in 10 minutes.
    </p>
    <div style="font-size: 32px; letter-spacing: 8px; font-weight: 700; text-align: center; padding: 20px; background: #fff; border: 1px solid #e5e5e7; border-radius: 8px; color: #111; font-family: 'SF Mono', Monaco, Consolas, monospace;">
      ${code}
    </div>
    <p style="color: #aaa; font-size: 12px; margin: 24px 0 0;">
      If you didn't request this, you can ignore this email.
    </p>
  </div>
</body>
</html>
`
```

OTP-specific UX rules:
- **Don't include the OTP in the email subject line** — visible in lock-screen previews. Use a generic subject ("Your sign-in code").
- **Plain-text fallback** — many auto-readers (smart watches, accessibility tools) parse plain text only.
- **No clickable URL alongside the code** — defeats the phishing-resistance benefit.

## Retry / backoff on 429

Resend returns 429 if you exceed your rate plan. Pattern:

```ts
async function sendWithRetry({ to, subject, html, attempt = 0 }: {
  to: string; subject: string; html: string; attempt?: number
}): Promise<{ id: string }> {
  const resend = new Resend(process.env.RESEND_API_KEY!)
  const { data, error } = await resend.emails.send({
    from: process.env.AUTH_EMAIL_FROM!, to, subject, html,
  })

  if (!error) return { id: data!.id }

  // Retry on 429 with jittered exponential backoff, max 3 attempts
  if (error.name === 'rate_limit_exceeded' && attempt < 2) {
    const delay = (2 ** attempt) * 500 + Math.random() * 250
    await new Promise((r) => setTimeout(r, delay))
    return sendWithRetry({ to, subject, html, attempt: attempt + 1 })
  }

  // 4xx other than 429 are config errors — don't retry, surface up
  // 5xx are Resend-side; retry once then surface
  if (error.name === 'application_error' && attempt < 1) {
    await new Promise((r) => setTimeout(r, 1000))
    return sendWithRetry({ to, subject, html, attempt: attempt + 1 })
  }

  throw new Error(`Resend send failed: ${error.message}`)
}
```

Don't queue magic-link / OTP sends through a worker — they're sub-second user-blocking actions. If retries fail, surface "Couldn't send code, try again" rather than silently failing.

## Bounce handling — what to show the user

When Resend webhooks report `email.bounced` for a magic-link / OTP send, the user is sitting at the "check your email" screen. Options:

1. **Generic message** ("If your email is registered, you'll receive a code in a minute") — does NOT distinguish bounce from success. Best for security; protects against email-existence enumeration.
2. **Specific message after timeout** — after, say, 45s without delivery confirmation, show "We couldn't reach that email — double-check the address." Slightly leaks existence info but better UX.

**Default to (1) unless your threat model says otherwise.** Email-existence enumeration is a real attack class (drives credential-stuffing target lists).

For ops: log every `bounced` and `complained` event with context. A spike usually means either a misconfigured domain (DKIM/SPF) or an attacker enumerating addresses.

## Idempotent re-send

Users will tap "Resend code" / "Send link again" repeatedly. Behavior:

- **Within `expiresIn` window**: invalidate the previous token, send a new one. Don't accumulate active tokens.
- **Rate-limit per email**: max 3 sends per 10 minutes. After that, return generic success without sending — don't reveal the rate-limit to a flood attacker.
- **Track resend count in your DB** so abuse stands out.

## Verification

1. Send a magic-link, click it, confirm session.
2. Send a magic-link, wait 11 min, click it — confirm "expired" state.
3. Send a magic-link, then immediately request another — confirm the first is invalidated (or both work, depending on your design; pick one and document).
4. Send to a bouncing address — confirm the user sees the generic "check your email" UI, not an error, and the bounce is logged server-side.
5. Resend 4 times in a minute — confirm rate-limit kicks in, user sees identical UI.
6. Open the email on a phone — verify CTA button is finger-sized (≥44pt) and OTP code is selectable as a single block.

## Cross-references

- `resend-email.md` — SDK + webhook signature verification
- `better-auth-magic-link.md` — server-side plugin config (`expiresIn`, single-use)
- Universal footgun #10 (magic-link expiry & idempotency) in `../SKILL.md`
