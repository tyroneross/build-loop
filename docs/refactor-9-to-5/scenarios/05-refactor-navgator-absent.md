# Scenario 5: Large refactor, NavGator absent (exercises `fallbacks.md#architecture`)

## Setup

- **Project**: Node.js + Next.js monorepo, no NavGator, no debugger, no IBR
- **Goal**: "Rename `User.email` → `User.primaryEmail` across the codebase"
- **Expected scope**: ~40 files across db models, API routes, frontend components
- **Criteria**:
  1. Tests pass
  2. Type check clean
  3. No orphan references to old field name (grep audit)

## Pre-fallback behavior

**Assess (Phase 1)**:
- `.navgator/architecture/index.json` doesn't exist
- navgator-bridge `Pre-flight`: "NO_NAVGATOR" → emit "NavGator: no architecture snapshot found" → skip
- `.build-loop/state.json.navgator` not written
- Phase 2 Plan proceeds blind: no blast-radius data, scoping based on goal text only

**Plan (Phase 2)**:
- Breaks work by grep of current field usage: finds ~40 files
- No signal about layer crossings or hotspots
- Dispatches one subagent per directory cluster

**Review-D Fact-Check** (after Execute):
- No NavGator rules check (bridge skipped silently pre-fallback)
- Other gates (fact-checker, mock-scanner) run normally
- Scorecard PASS if tests + types clean

**Risk**: the rename touches `src/db/User.ts` + `src/components/Profile.tsx` directly without going through `src/api/` — a potential `frontend-direct-db` violation is **not detected**.

## Post-fallback behavior

**Assess (Phase 1)**:
- navgator-bridge `Pre-flight`: "NO_NAVGATOR" → runs `fallbacks.md#architecture`
- Executes the grep/git commands:
  - Check 1 (changed files): ~40 files enumerated
  - Check 2 (layer classification): 12 db / 8 backend / 18 frontend / 2 test
  - Check 3 (1-hop dependents): for each changed file, grep import paths
  - Check 4 (hotspot churn): top-10 includes `src/db/User.ts` and `src/lib/auth.ts` — both touched
  - Check 5 (circular-import): defer to type check
- Risk flags:
  - ≥3 layers crossed (db + backend + frontend) → "high blast radius"
  - `src/db/User.ts` is a top-5 hotspot → "concentration risk"
  - `src/db/User.ts` imported directly from `src/components/Profile.tsx` without going through API → "possible frontend-direct-db layer violation"
- Writes to `.build-loop/state.json.architecture.standalone` with these flags

**Plan (Phase 2)**:
- Reads the standalone state. Sees blast radius + layer violation flag.
- Splits work into 3 chunks with explicit integration tests between them (normally would have been one monolithic PR).
- Adds a plan task: "Introduce API layer between Profile.tsx and User model before renaming" — the layer violation would have shipped without this.

**Review-F report**:
- Includes `⚠️ architecture analysis via static fallback — install NavGator for AST-aware dependency graph + rule enforcement`
- Flags the possible layer violation as an observed concern

## Concrete delta

| Aspect | Pre-fallback | Post-fallback |
|---|---|---|
| Assess architecture output | Nothing | Layer counts + hotspots + risk flags |
| Layer violation detection | Missed | Flagged (frontend-direct-db pattern) |
| Plan scoping | Monolithic subagent dispatch | Chunked with integration checkpoints |
| Cost if violation ships | Future bug + refactor | Caught pre-commit |
| Analysis quality | None | Directional — false positives possible, but useful |
| Install NavGator? | Silent loss | Explicit report note |

**Net**: fallback converts a silent gap into a surfaced risk. Heuristic rather than authoritative (NavGator would be exact), but 10× better than nothing.
