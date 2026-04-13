# Google Maps JavaScript API — 2026 Modern Pattern

## Enable and Restrict

```
Google Cloud Console → APIs & Services → Library
  Enable: Maps JavaScript API
  Enable: Places API (New)        ← for autocomplete/search
  Enable: Geocoding API            ← for address ↔ lat/lng
  Enable: Directions API           ← only if you need routing
```

Then:
```
APIs & Services → Credentials → + CREATE CREDENTIALS → API key
```

**Immediately restrict it** — unrestricted keys are scraped and billed within hours.

- **Application restrictions**: HTTP referrers (web sites)
  - `https://your-domain.com/*`
  - `http://localhost:3000/*` (for dev)
- **API restrictions**: Restrict key → select only the APIs you enabled above

Create **one key per environment** (dev, staging, prod). Use the restriction to prevent a leaked dev key from calling prod APIs.

Maps API keys are **public by design** — the browser sees them. Restriction, not secrecy, is what protects them.

## Get a Map ID (Required for Advanced Markers)

```
Google Cloud Console → Google Maps Platform → Map Management → CREATE MAP ID
  Map type: JavaScript
  Raster or Vector: Vector (for cloud-based styling + Advanced Markers)
```

Save the Map ID — without it, `AdvancedMarkerElement` silently refuses to render.

## Modern Async Loader Pattern

Don't use `<script src="https://maps.googleapis.com/maps/api/js?key=...">` anymore. Use the inline bootstrap loader with `importLibrary()`.

### Option A: Inline bootstrap (recommended — no npm dep)

```html
<script>
(g=>{var h,a,k,p="The Google Maps JavaScript API",c="google",l="importLibrary",q="__ib__",m=document,b=window;b=b[c]||(b[c]={});var d=b.maps||(b.maps={}),r=new Set,e=new URLSearchParams,u=()=>h||(h=new Promise(async(f,n)=>{await (a=m.createElement("script"));e.set("libraries",[...r]+"");for(k in g)e.set(k.replace(/[A-Z]/g,t=>"_"+t[0].toLowerCase()),g[k]);e.set("callback",c+".maps."+q);a.src=`https://maps.${c}apis.com/maps/api/js?`+e;d[q]=f;a.onerror=()=>h=n(Error(p+" could not load."));a.nonce=m.querySelector("script[nonce]")?.nonce||"";m.head.append(a)}));d[l]?console.warn(p+" only loads once. Ignoring:",g):d[l]=(f,...n)=>r.add(f)&&u().then(()=>d[l](f,...n))})
({key: "YOUR_API_KEY", v: "weekly"});
</script>
```

Paste that once in your root layout. It exposes `google.maps.importLibrary()` globally without actually fetching the API until you call it.

### Option B: `@googlemaps/js-api-loader` (npm, typed)

```ts
import { Loader } from '@googlemaps/js-api-loader'

const loader = new Loader({
  apiKey: process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY!,
  version: 'weekly',
  libraries: ['maps', 'marker', 'places'],
})
```

Per this repo's user preference (minimal deps, build from scratch), prefer **Option A** unless you already have the package for other reasons.

## Render a Map with Advanced Markers

```tsx
'use client'
import { useEffect, useRef } from 'react'

declare global {
  interface Window {
    google: typeof google
  }
}

export function CampMap({ camps }: { camps: { id: string; lat: number; lng: number; name: string }[] }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    async function init() {
      if (!ref.current) return
      const { Map } = await google.maps.importLibrary('maps') as google.maps.MapsLibrary
      const { AdvancedMarkerElement, PinElement } = await google.maps.importLibrary('marker') as google.maps.MarkerLibrary

      if (cancelled) return

      const map = new Map(ref.current, {
        center: { lat: 40.7128, lng: -74.006 },
        zoom: 11,
        mapId: process.env.NEXT_PUBLIC_GOOGLE_MAPS_MAP_ID!,  // REQUIRED
        disableDefaultUI: false,
      })

      for (const camp of camps) {
        const pin = new PinElement({
          background: '#4F46E5',
          borderColor: '#312E81',
          glyphColor: '#FFF',
        })
        new AdvancedMarkerElement({
          map,
          position: { lat: camp.lat, lng: camp.lng },
          title: camp.name,
          content: pin.element,
        })
      }
    }
    init()
    return () => { cancelled = true }
  }, [camps])

  return <div ref={ref} className="h-[500px] w-full rounded-lg" />
}
```

## Deprecated — DO NOT USE

- `new google.maps.Marker(...)` — deprecated Feb 2024. Use `AdvancedMarkerElement`.
- `google.maps.event.addListener(marker, 'click', ...)` on old Markers — use DOM events on the advanced marker's `content` element or its `.addListener('gmp-click', ...)`.
- Synchronous script tag with `&callback=initMap` — still works but no IntelliSense/types and no lazy loading.

## Types

Install `@types/google.maps` (tiny, just ambient types):

```bash
npm i -D @types/google.maps
```

Or hand-roll the needed types to avoid the dep. The ambient types are not pulled into the bundle.

## Common Failures

- **"Google Maps JavaScript API error: InvalidKeyMapError"** — key doesn't exist, isn't enabled on this project, or the Maps JS API isn't enabled.
- **"Google Maps JavaScript API error: ApiNotActivatedMapError"** — enable Maps JavaScript API on this project.
- **"Google Maps JavaScript API error: RefererNotAllowedMapError"** — current origin not in the key's HTTP referrer allow-list. Common during local dev if you only whitelisted prod.
- **Map renders but markers don't** — `mapId` missing, or you used `google.maps.Marker` without importing the `marker` library.
- **Blank map / grey square** — container has height 0. `h-[500px]` or equivalent must resolve to pixels.
- **"For development purposes only" watermark** — billing not enabled on the project. Google requires a billing account even for free-tier usage.

## Cost Control

Maps loads are $7/1000 after the free tier. Places API (New) Text Search is $32/1000 (without field mask). Aggressive field masks and client-side caching are critical.

- Cache place details in your DB (with TTL per Google's usage terms — 30 days for most fields)
- Use `fields` / `X-Goog-FieldMask` on every Places call
- Prefer Autocomplete (cheaper) over Text Search when the user is typing
- Session tokens on Autocomplete make a sequence of requests + one Place Details count as a single session (much cheaper)

## Sources

- [Load Maps JS API](https://developers.google.com/maps/documentation/javascript/load-maps-js-api)
- [Advanced Markers migration](https://developers.google.com/maps/documentation/javascript/advanced-markers/migration)
- [Advanced Markers reference](https://developers.google.com/maps/documentation/javascript/reference/advanced-markers)
- [Libraries](https://developers.google.com/maps/documentation/javascript/libraries)
