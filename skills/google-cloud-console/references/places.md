# Places API (New) v1 — 2026

The legacy Places API can no longer be enabled on new projects. Use **Places API (New)** with the v1 REST endpoints and **field masks** on every call.

## Core Endpoints

| Endpoint | Method | Use |
|----------|--------|-----|
| `POST https://places.googleapis.com/v1/places:searchText` | POST | Text Search — "summer camps near me" |
| `POST https://places.googleapis.com/v1/places:searchNearby` | POST | Nearby Search — points within radius |
| `POST https://places.googleapis.com/v1/places:autocomplete` | POST | Autocomplete — as user types |
| `GET  https://places.googleapis.com/v1/places/{PLACE_ID}` | GET | Place Details — after picking a result |

## Field Masks (MANDATORY — affects pricing)

Every Places API (New) request requires an `X-Goog-FieldMask` header. Google **charges per field category requested**. Don't request what you won't use.

```
X-Goog-FieldMask: places.id,places.displayName,places.formattedAddress,places.location
```

Categories (as of April 2026):

- **IDs Only** (cheapest): `places.id`, `places.name`
- **Essentials**: `places.displayName`, `places.formattedAddress`, `places.location`, `places.shortFormattedAddress`
- **Pro**: adds `photos`, `priceLevel`, `rating`, `userRatingCount`, `businessStatus`, `types`
- **Enterprise**: adds `reviews`, `editorialSummary`, `openingHours`, `websiteUri`, etc.

The finer the mask, the cheaper the call. Start with Essentials and add fields only when the UI needs them.

## Text Search Example

```ts
// Server-side only — use the API key that's restricted to your server's IP,
// NOT the browser-exposed Maps JS key.
async function searchPlaces(query: string, bias?: { lat: number; lng: number; radius: number }) {
  const res = await fetch('https://places.googleapis.com/v1/places:searchText', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Goog-Api-Key': process.env.GOOGLE_PLACES_API_KEY!,
      'X-Goog-FieldMask': [
        'places.id',
        'places.displayName',
        'places.formattedAddress',
        'places.location',
        'places.types',
        'places.primaryType',
        'places.primaryTypeDisplayName',
      ].join(','),
    },
    body: JSON.stringify({
      textQuery: query,
      ...(bias && {
        locationBias: {
          circle: {
            center: { latitude: bias.lat, longitude: bias.lng },
            radius: bias.radius,
          },
        },
      }),
      maxResultCount: 10,
    }),
  })

  if (!res.ok) throw new Error(`Places API ${res.status}: ${await res.text()}`)
  return res.json() as Promise<{
    places: Array<{
      id: string
      displayName: { text: string; languageCode: string }
      formattedAddress: string
      location: { latitude: number; longitude: number }
      types?: string[]
      primaryType?: string
      primaryTypeDisplayName?: { text: string }
    }>
  }>
}
```

**Key Places API (New) note**: Use `X-Goog-Api-Key` header, NOT `?key=` query param. The query-param style is legacy.

## Autocomplete + Session Tokens

Session tokens bundle a series of Autocomplete requests with a single final Place Details request into one session → Google bills it as one request instead of N+1. Use a fresh UUID per autocomplete "session" (one user typing → one selection).

```ts
import { randomUUID } from 'crypto'

// Start a session when the user focuses the input
const sessionToken = randomUUID()

// Each keystroke
async function autocomplete(input: string) {
  return fetch('https://places.googleapis.com/v1/places:autocomplete', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Goog-Api-Key': process.env.GOOGLE_PLACES_API_KEY!,
    },
    body: JSON.stringify({
      input,
      sessionToken,  // same for every keystroke in this session
      locationBias: { /* optional */ },
    }),
  }).then(r => r.json())
}

// User picks a result → fetch details with the same token
async function getDetails(placeId: string) {
  return fetch(`https://places.googleapis.com/v1/places/${placeId}?sessionToken=${sessionToken}`, {
    headers: {
      'X-Goog-Api-Key': process.env.GOOGLE_PLACES_API_KEY!,
      'X-Goog-FieldMask': 'id,displayName,formattedAddress,location,websiteUri,internationalPhoneNumber',
    },
  }).then(r => r.json())
}
```

After the Place Details call, the session is consumed. Start a fresh token for the next query.

## The "Camp at a Parent Location" Problem

The Trip Planner / Summer Camps use case has a wrinkle: a camp is an _activity_ that runs at a _venue_ that has its own Google Place. E.g. "Kids Coding Camp" at "YMCA Brooklyn". You want to store both.

**Data model approach:**

```
camps
  id
  name              -- "Kids Coding Camp"
  provider          -- "Code Ninjas" (who runs the camp)
  provider_place_id -- Google Place ID for Code Ninjas HQ (optional)
  venue_place_id    -- Google Place ID for YMCA Brooklyn (where it actually meets)
  venue_name        -- cached displayName
  venue_address     -- cached formattedAddress
  venue_lat, venue_lng
  start_date, end_date
  daily_start, daily_end  -- e.g. 09:00 - 15:00
  ages_min, ages_max
  cost
  url
  notes
```

The key insight: **`venue_place_id` and `provider_place_id` are different Google Places**, and you should store and display both. Users care about where to drop off (venue) AND who to contact (provider).

When searching Places for a camp venue, query for the venue name directly — don't try to match the camp name to a Google Place, it won't exist as one.

## Caching + Terms of Service

Google's Places API ToS allow you to cache Place IDs **indefinitely** (they're considered stable identifiers), but most other Place data is capped at **30 days** before you must refetch. In practice:

- Store `place_id`, `displayName`, `formattedAddress`, `location` in your DB
- Refresh from Google if the cached row is > 30 days old
- Never display cached data older than 30 days without refetching

## Common Failures

- **HTTP 400 "Field mask is required"** — add `X-Goog-FieldMask` header
- **HTTP 400 "Invalid field mask"** — you can't request `places.name` alongside other `places.*` fields without the correct prefix. Check the [field-mask docs](https://developers.google.com/maps/documentation/places/web-service/text-search#fieldmask)
- **HTTP 403 "API has not been used"** — enable Places API (New), not legacy
- **HTTP 429** — rate limited. Default quota is generous but enforce client-side debounce on autocomplete (200-400ms)
- **Empty results but no error** — check `languageCode` and `regionCode` in the request body

## Sources

- [Places API New overview](https://developers.google.com/maps/documentation/places/web-service/overview)
- [Text Search (New)](https://developers.google.com/maps/documentation/places/web-service/text-search)
- [Place Details (New)](https://developers.google.com/maps/documentation/places/web-service/place-details)
- [Migrate to New from Legacy](https://developers.google.com/maps/documentation/places/web-service/legacy/migrate-text)
