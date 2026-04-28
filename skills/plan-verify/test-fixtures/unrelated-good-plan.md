# Travel Planner — Summer Camp Listings

> Clean plan from another project. False-positive control fixture for `plan_verify.py`.

## Goal

Add summer-camp listings to the existing trips dashboard. Reuse the `Trip` schema; do not introduce a new entity type. ✅ verified by reading `src/db/schema.ts` lines 14-32.

## Approach

Add a `category` enum field to `Trip` with values `vacation | camp | conference`. Default is `vacation` to preserve existing rows. ✅ verified by checking current Drizzle migration history shows no `category` column exists.

Render camps in the same TripCard component. The card already accepts an optional badge prop; we wire `category === 'camp'` to a "Camp" badge.

## Phase 1 — Schema

Add migration `0014_trip_category.sql`:

```sql
ALTER TABLE trips ADD COLUMN category text NOT NULL DEFAULT 'vacation';
```

Backfill is the default value, so the migration is idempotent.

## Phase 2 — UI

Update `TripCard.tsx` to render the badge. ✅ verified TripCard already supports a `badge` slot via prop drilling.

## Phase 3 — Tests

Add a snapshot test for the camp badge. Test data uses real category values; no mocks.

## Verification

- Migration runs cleanly on staging — ✅ verified by running `pnpm db:migrate` in CI.
- TripCard snapshot updated — ⚠️ untested, will run after merge.
- No regressions in existing trip flows — ❓ uncertain; needs a manual spot-check.

## Out of scope

- No new database table.
- No changes to auth.
- No new package dependencies.
