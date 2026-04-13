# Google Calendar API v3 — Sync Patterns

Syncing events between your app and a user's Google Calendar. Covers scopes, incremental sync via `syncToken`, refresh-token storage, and the 410-recovery dance.

## Scopes — Pick the Narrowest

| Scope | What you can do | Sensitive? |
|-------|-----------------|------------|
| `calendar.readonly` | List events, list calendars | Yes (verification required) |
| `calendar.events.readonly` | List events (any calendar you specify) | Yes |
| `calendar.events` | Create/update/delete events | Yes |
| `calendar.app.created` | Only events your app created | **No** (unverified OK) |
| `calendar` | Full access (including settings) | Yes |

**Use `calendar.app.created` if possible** — it only lets your app touch events it created, requires no Google verification, and is the least privacy-invasive. The catch: you can't read events the user created manually. For two-way sync of user events you need `calendar.events`.

## Adding Calendar Scope to Existing OAuth

```
Google Cloud Console → APIs & Services → OAuth consent screen → EDIT APP
  → Scopes → ADD OR REMOVE SCOPES
  → Search for "calendar" → select scope → UPDATE → SAVE
```

If your app was already published, adding a sensitive scope triggers re-verification. Plan for ~1 week.

## Store the Refresh Token (Critical)

Google only returns a `refresh_token` on the **first** consent, unless you pass `prompt=consent&access_type=offline`. Always request both:

```
https://accounts.google.com/o/oauth2/v2/auth?
  client_id=...&
  redirect_uri=...&
  response_type=code&
  scope=openid%20email%20profile%20https%3A//www.googleapis.com/auth/calendar.events&
  access_type=offline&   ← required for refresh_token
  prompt=consent&        ← force refresh_token even on re-consent
  state=...
```

The refresh_token is **long-lived** (months). The access_token expires in 1 hour. Store both:

```sql
CREATE TABLE user_calendars (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) NOT NULL,
  google_email VARCHAR(255) NOT NULL,
  google_calendar_id VARCHAR(255) NOT NULL,  -- 'primary' or specific ID
  access_token TEXT NOT NULL,                 -- expires in ~1h
  refresh_token TEXT NOT NULL,                -- long-lived
  token_expires_at TIMESTAMPTZ NOT NULL,
  sync_token TEXT,                            -- Google's nextSyncToken for incremental sync
  last_synced_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (user_id, google_calendar_id)
);
```

**Encrypt refresh_token at rest.** Use column-level encryption (pgcrypto) or your hosting provider's secret manager.

## Refresh an Access Token

```ts
async function refreshAccessToken(refreshToken: string) {
  const res = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      client_id: process.env.GOOGLE_CLIENT_ID!,
      client_secret: process.env.GOOGLE_CLIENT_SECRET!,
      refresh_token: refreshToken,
      grant_type: 'refresh_token',
    }),
  })

  if (!res.ok) {
    const err = await res.json()
    if (err.error === 'invalid_grant') {
      // User revoked access or refresh_token is dead
      throw new CalendarReauthRequiredError()
    }
    throw new Error(`Token refresh failed: ${JSON.stringify(err)}`)
  }

  return res.json() as Promise<{ access_token: string; expires_in: number }>
}
```

**`invalid_grant` handling**: Mark the user_calendar row as needing re-auth and show a UI prompt. Don't retry silently — they've revoked access.

## Incremental Sync with syncToken

This is the whole point — don't fetch all events every time.

### First sync (full)

```ts
async function fullSync(calendarId: string, accessToken: string) {
  let pageToken: string | undefined
  let nextSyncToken: string | undefined
  const events: any[] = []

  do {
    const url = new URL(`https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events`)
    url.searchParams.set('singleEvents', 'true')        // expand recurring
    url.searchParams.set('showDeleted', 'false')
    url.searchParams.set('maxResults', '250')
    if (pageToken) url.searchParams.set('pageToken', pageToken)

    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })

    if (!res.ok) throw new Error(`Calendar list failed: ${res.status}`)
    const data = await res.json()

    events.push(...(data.items ?? []))
    pageToken = data.nextPageToken
    nextSyncToken = data.nextSyncToken  // only on the LAST page
  } while (pageToken)

  // Store nextSyncToken for the next incremental sync
  return { events, nextSyncToken }
}
```

### Incremental sync (subsequent runs)

```ts
async function incrementalSync(calendarId: string, accessToken: string, syncToken: string) {
  let pageToken: string | undefined
  let newSyncToken: string | undefined
  const changes: any[] = []

  do {
    const url = new URL(`https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events`)
    url.searchParams.set('syncToken', syncToken)
    if (pageToken) url.searchParams.set('pageToken', pageToken)

    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })

    // 410 GONE — syncToken expired (happens after ~7 days of inactivity, or if Google invalidates it)
    if (res.status === 410) {
      // Drop stored syncToken and do a full sync
      await db.query('UPDATE user_calendars SET sync_token = NULL WHERE ...')
      return fullSync(calendarId, accessToken)
    }

    if (!res.ok) throw new Error(`Calendar sync failed: ${res.status}`)
    const data = await res.json()

    // Events returned here include deletions (status: 'cancelled')
    changes.push(...(data.items ?? []))
    pageToken = data.nextPageToken
    newSyncToken = data.nextSyncToken
  } while (pageToken)

  return { changes, newSyncToken }
}
```

**Critical**: `nextSyncToken` is only on the LAST page of results. If you stop paging early, you lose the token and have to full-sync next time.

**Do NOT combine `syncToken` with other filters** like `timeMin`, `timeMax`, `q`. Google errors out. Sync tokens return ALL events that changed — filter client-side after.

## Create an Event

```ts
async function createEvent(calendarId: string, accessToken: string, event: {
  summary: string
  description?: string
  location?: string
  start: { dateTime: string; timeZone: string }
  end: { dateTime: string; timeZone: string }
}) {
  const res = await fetch(
    `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(event),
    }
  )

  if (!res.ok) throw new Error(`Create event failed: ${res.status} ${await res.text()}`)
  return res.json() as Promise<{ id: string; htmlLink: string }>
}
```

Store the returned `id` as `google_event_id` on your local row so you can update/delete it later.

## Push Notifications (Watch Channels)

For near-real-time sync, subscribe to a channel:

```ts
await fetch(`https://www.googleapis.com/calendar/v3/calendars/primary/events/watch`, {
  method: 'POST',
  headers: {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    id: randomUUID(),
    type: 'web_hook',
    address: 'https://your-domain.com/api/calendar/webhook',
    token: 'your-verification-secret',
    expiration: String(Date.now() + 7 * 24 * 60 * 60 * 1000),  // 7 days max
  }),
})
```

Google POSTs to your webhook when the calendar changes. The webhook doesn't contain the change — it's a signal to trigger an incremental sync.

**Your webhook must be HTTPS with a valid public cert.** ngrok works for local testing.

Channels expire after 7 days. Refresh them on a cron.

## Sync Job Pattern

```ts
// Cron or queued job — runs per user_calendar row
async function syncUserCalendar(userCalendarId: string) {
  const row = await db.queryOne(/* user_calendars by id */)
  if (!row) return

  // Refresh token if needed
  if (row.token_expires_at < new Date(Date.now() + 5 * 60_000)) {
    try {
      const { access_token, expires_in } = await refreshAccessToken(row.refresh_token)
      await db.query(/* update access_token, token_expires_at */)
      row.access_token = access_token
    } catch (e) {
      if (e instanceof CalendarReauthRequiredError) {
        await db.query(/* mark needs_reauth = true */)
        return
      }
      throw e
    }
  }

  // Sync
  let result
  if (row.sync_token) {
    result = await incrementalSync(row.google_calendar_id, row.access_token, row.sync_token)
  } else {
    result = await fullSync(row.google_calendar_id, row.access_token)
  }

  // Apply changes to local DB
  for (const ev of result.changes ?? result.events) {
    if (ev.status === 'cancelled') {
      await db.query('DELETE FROM events WHERE google_event_id = $1', [ev.id])
    } else {
      await upsertEvent(ev)
    }
  }

  // Persist new sync token
  await db.query('UPDATE user_calendars SET sync_token = $1, last_synced_at = NOW() WHERE id = $2',
    [result.nextSyncToken ?? result.newSyncToken, userCalendarId])
}
```

## Sources

- [Sync resources efficiently](https://developers.google.com/workspace/calendar/api/guides/sync)
- [Choose Calendar API scopes](https://developers.google.com/workspace/calendar/api/auth)
- [Events: list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list)
- [Push notifications](https://developers.google.com/workspace/calendar/api/guides/push)
