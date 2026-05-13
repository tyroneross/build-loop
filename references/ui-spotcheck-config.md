# UI spot-check configuration

Project-level configuration the orchestrator reads at Phase 1 Assess to drive the `ui-validator` agent at Phase 3 chunk-close and Phase 4 Review-B. See `agents/ui-validator.md` for the agent contract and `agents/build-orchestrator.md` §"Phase 3 UI spot-check" for the trigger conditions.

## Schema

Add a `uiSpotcheck` block to `.build-loop/config.json` (alongside `deploymentPolicy`, `autonomy`, etc.):

```json
{
  "uiSpotcheck": {
    "enabled": true,
    "baseUrl": "http://localhost:3000",
    "signInForm": {
      "url": "/api/auth/sign-in/email",
      "email": "fixture@example.test",
      "password": "fixture-pw-2026"
    },
    "baselineDir": ".build-loop/ui-baselines",
    "phase3RouteCap": 8,
    "phase4RouteCap": 12,
    "ssimThreshold": 0.98
  }
}
```

All fields are optional. Defaults below.

## Fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `enabled` | boolean | `true` if `.tsx` files exist under `app/` or `components/` | Master kill-switch. Library-only and API-only projects auto-default to `false`. |
| `baseUrl` | string | auto-detected via `scripts/detect_runtime_server.py` | Dev server URL the agent scans against. |
| `signInForm.url` | string | none | Path of the sign-in endpoint (relative to `baseUrl`). When absent, the agent attempts `/api/auth/guest`; if that fails, it returns `status: skipped (auth-gap)`. |
| `signInForm.email` | string | none | Fixture email — never a real account. |
| `signInForm.password` | string | none | Fixture password — never a real credential. |
| `baselineDir` | string | `.build-loop/ui-baselines` | Where the agent stores baseline screenshots/JSON for cross-build visual regression. Set to `null` to disable visual diffs. |
| `phase3RouteCap` | integer | `8` | Max routes scanned per chunk-close. Higher = more coverage per chunk, longer wall-clock. |
| `phase4RouteCap` | integer | `12` | Max routes scanned at Review-B. Higher than Phase 3 because Review-B is the final gate. |
| `ssimThreshold` | number | `0.98` | Visual regression cutoff (0–1, higher = stricter). Visual diffs below threshold WARN in Review-G, never block. |

## Auth fixture — security stance

The `signInForm` fixture is the project's responsibility. Build-loop never invents credentials. Two recommended patterns:

1. **Dev-only seeded user.** Project's dev migrations seed `fixture@example.test` with a known password. The fixture lives in `.build-loop/config.json` (gitignored or committed-but-clearly-dev).
2. **Guest fallback only.** Omit `signInForm`. The agent uses `/api/auth/guest` (if the project has one). Coverage is reduced to what guest users see, but no credential lives on disk.

Never put a real-user password in `signInForm`. Production audits run from CI with separately-scoped fixtures, not from this config.

## Baseline directory layout

When `baselineDir` is set, the agent maintains:

```
.build-loop/ui-baselines/
├── <run_id>/                                # per-build snapshot tree
│   ├── _app_library.json                    # ScanResult JSON, route as slug
│   ├── _app_library.png                     # baseline screenshot
│   ├── _app_chat.json
│   └── _app_chat.png
└── _latest -> <run_id>/                     # symlink updated on green builds
```

The agent reads `_latest/` for diffs and writes the current build's snapshot to a new `<run_id>/`. After Review-G passes, the orchestrator updates the `_latest` symlink. Failed builds leave their snapshot in place under `<run_id>/` for diagnosis but do not advance `_latest`.

**First build for a project**: there is no `_latest/`. The agent emits `visual_regression: []` and writes the initial snapshot. From the second build forward, diffs are computed.

**Snapshot pruning**: keep the latest 10 `<run_id>/` directories. Phase 6 Learn handles pruning (`scripts/prune_ui_baselines.py`, follow-on PR). Until that ships, the dir grows unboundedly — disable visual diffs (`baselineDir: null`) for projects where that's a problem.

## Skip rules

`ui-validator` returns `status: skipped` (not `fail`) and the orchestrator continues when any of:

| Reason | Returned reason string | When |
|---|---|---|
| Config disabled | `disabled-by-config` | `config.uiSpotcheck.enabled: false` |
| No dev server | `no-dev-server` | `detect_runtime_server.py` returns `runtimeServer: false` |
| Library-only | `no-routes-implicated` | Chunk touched no `app/**/page.tsx`, no layout, no theme |
| Auth gap | `auth-gap` | `signInForm` absent AND `/api/auth/guest` also fails AND any changed route is under `/app/*` |
| Dev server unreachable | `server-unreachable` | TCP connect or first GET fails after 3s |

Skipped builds get a `⚠️ ui-spotcheck skipped — <reason>` marker in Review-G but do not block.

## Defaults summary

A project with **no** `uiSpotcheck` block:

- `enabled` derived from file inventory (true for typical Next.js projects, false for library/API projects)
- `baseUrl` auto-detected
- `signInForm` absent → guest fallback or `auth-gap` skip
- `baselineDir` defaults to `.build-loop/ui-baselines`
- Route caps and threshold use the table above

You only need a `uiSpotcheck` block when overriding one of these defaults — most commonly, to provide a `signInForm` fixture for protected-route coverage.

## Cross-reference

- Agent: `agents/ui-validator.md`
- Orchestrator wiring: `agents/build-orchestrator.md` §"Phase 3 UI spot-check"
- RFC (this repo): `docs/rfcs/2026-05-ui-validator-agent.md`
- RFC (IBR-core library): `tyroneross/interface-built-right#5`
