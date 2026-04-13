# Geocoding + Directions + Distance Matrix

Non-interactive, server-side APIs. Get an API key restricted to your server's IP (or unrestricted + used only via server routes).

## Geocoding — address ↔ lat/lng

```ts
async function geocode(address: string) {
  const url = new URL('https://maps.googleapis.com/maps/api/geocode/json')
  url.searchParams.set('address', address)
  url.searchParams.set('key', process.env.GOOGLE_GEOCODING_API_KEY!)

  const res = await fetch(url)
  const json = await res.json()

  if (json.status !== 'OK') {
    throw new Error(`Geocode failed: ${json.status} ${json.error_message ?? ''}`)
  }

  const first = json.results[0]
  return {
    formattedAddress: first.formatted_address,
    location: first.geometry.location, // { lat, lng }
    placeId: first.place_id,
    types: first.types,
  }
}
```

**Status codes to handle:**
- `OK` — results found
- `ZERO_RESULTS` — valid request, no match (show "address not found")
- `OVER_QUERY_LIMIT` — rate limited, back off
- `REQUEST_DENIED` — key issue, log loudly
- `INVALID_REQUEST` — bad params

**Reverse geocoding** uses the same endpoint with `latlng=` instead of `address=`.

**Cache aggressively.** A given address's lat/lng doesn't change. Store results permanently in your DB keyed by normalized address.

## Directions

```ts
async function directions(origin: string, destination: string, mode: 'driving' | 'walking' | 'bicycling' | 'transit' = 'driving') {
  const url = new URL('https://maps.googleapis.com/maps/api/directions/json')
  url.searchParams.set('origin', origin)
  url.searchParams.set('destination', destination)
  url.searchParams.set('mode', mode)
  url.searchParams.set('key', process.env.GOOGLE_DIRECTIONS_API_KEY!)

  const res = await fetch(url)
  const json = await res.json()
  return json.routes[0]  // legs, overview_polyline, duration, distance
}
```

Directions returns an **encoded polyline** (`overview_polyline.points`) — decode client-side with `google.maps.geometry.encoding.decodePath()` (import the `geometry` library) to draw on a map.

## Distance Matrix — many-to-many

When you need "what's the drive time from home to each of these 5 camps":

```ts
async function distanceMatrix(origins: string[], destinations: string[]) {
  const url = new URL('https://maps.googleapis.com/maps/api/distancematrix/json')
  url.searchParams.set('origins', origins.join('|'))
  url.searchParams.set('destinations', destinations.join('|'))
  url.searchParams.set('mode', 'driving')
  url.searchParams.set('key', process.env.GOOGLE_DIRECTIONS_API_KEY!)

  const res = await fetch(url)
  return res.json() // rows[i].elements[j] = { distance, duration, status }
}
```

**Billing**: Distance Matrix is charged per element (origins × destinations). 3 origins × 5 destinations = 15 elements. Cap batch sizes to avoid surprise bills.

## Rate Limiting + Caching Strategy

Both Geocoding and Directions have generous free tiers but billing ramps fast. Build a simple cache layer:

```ts
// lib/google-cache.ts
import { createHash } from 'crypto'

type CacheRow = { key: string; value: unknown; cached_at: Date }

async function cachedFetch<T>(
  bucket: 'geocode' | 'directions',
  keyParts: string[],
  fetcher: () => Promise<T>,
  ttlDays: number,
): Promise<T> {
  const key = createHash('sha256').update(`${bucket}:${keyParts.join('|')}`).digest('hex')

  const row = await db.query<CacheRow>(
    'SELECT value, cached_at FROM google_cache WHERE key = $1',
    [key],
  )

  if (row && daysSince(row.cached_at) < ttlDays) {
    return row.value as T
  }

  const value = await fetcher()
  await db.query(
    'INSERT INTO google_cache (key, value, cached_at) VALUES ($1, $2, NOW()) ON CONFLICT (key) DO UPDATE SET value = $2, cached_at = NOW()',
    [key, value],
  )
  return value
}
```

TTL suggestions:
- Geocode (address → lat/lng): **permanent** (addresses don't move)
- Reverse geocode: **90 days** (neighborhoods/places do change)
- Directions: **1 day** (traffic + road changes + route updates)
- Distance Matrix: **1 day**

## Sources

- [Geocoding API](https://developers.google.com/maps/documentation/geocoding/overview)
- [Directions API](https://developers.google.com/maps/documentation/directions/overview)
- [Distance Matrix API](https://developers.google.com/maps/documentation/distance-matrix/overview)
