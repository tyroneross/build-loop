---
name: build-loop:navgator-bridge
description: Read NavGator architecture data during Phase 1 (ASSESS) and Phase 7 (FACT CHECK) to compute blast radius before changes and detect new violations after. Filesystem-only — consumes NavGator's JSON outputs, does not require running NavGator CLI mid-build.
version: 0.1.0
user-invocable: false
---

# NavGator Bridge

Lets build-loop consume NavGator's architecture graph without tight coupling. NavGator writes to `.navgator/architecture/`; this skill reads from there. If NavGator has never run on the project, this skill no-ops with a one-line note.

**Use:**
- Phase 1 ASSESS — compute blast radius before planning
- Phase 7 FACT CHECK — detect new violations after implementation
- Never writes. Never invokes NavGator CLI. Purely a reader.

## Pre-flight

Before either phase's logic runs, check:

```bash
[ -f ".navgator/architecture/index.json" ] && echo "HAVE_NAVGATOR" || echo "NO_NAVGATOR"
```

If `NO_NAVGATOR`, emit exactly one line to the report and skip:

```
NavGator: no architecture snapshot found (run /navgator:scan to enable blast-radius analysis).
```

Do not error, do not block the build. NavGator is optional.

## Phase 1 — Blast-Radius Read

**Purpose**: narrow the plan. Before Phase 3 PLAN writes a dependency graph, tell it which components the changes will touch so subagent scoping is accurate.

### Inputs

- `.navgator/architecture/file_map.json` — path → COMP_id lookup
- `.navgator/architecture/graph.json` — full connection graph
- `.navgator/architecture/SUMMARY.md` — hotspots and layer health

### Steps

1. **Identify candidate files**. Extract from the goal text any file paths, directories, or glob patterns mentioned. If none, use `git diff --name-only origin/main..HEAD` to capture any staged/in-progress changes.

2. **Map files to components**:
   ```bash
   # Pseudocode — read file_map.json, for each candidate file return COMP_id
   jq -r --arg f "$FILE" '.files[$f] // "UNMAPPED"' .navgator/architecture/file_map.json
   ```
   Unmapped files (not scanned yet) are logged but not treated as blockers.

3. **Classify by layer**. Each COMP_id has a type in `index.json`: `frontend`, `backend`, `database`, `queue`, `service`, `npm`, `external`, `infra`. Count how many of each are touched.

4. **Compute blast radius**. For each touched component, read `graph.json` and enumerate:
   - **1-hop dependents** — components that import/call this one (these will definitely see the change)
   - **2-hop dependents** — one level further out (may need regression checks)
   Cap 2-hop at 50 components; if exceeded, flag "high blast radius" and include only top 10 by fan-in.

5. **Risk flags**. Read `SUMMARY.md` for hotspots. If any touched component is listed as a hotspot (high fan-in), or appears in circular-dependency chains, or spans a layer violation — add a `⚠️ risk` flag to the Phase 1 output.

6. **Emit compact summary** (≤15 lines) into `.build-loop/state.json.navgator.phase1`:

   ```json
   {
     "timestamp": "ISO-8601",
     "changedFiles": N,
     "mapped": N,
     "unmapped": N,
     "layersTouched": ["frontend", "backend", "database"],
     "componentsTouched": ["COMP_auth_ts", "COMP_users_model"],
     "oneHopDependents": 12,
     "twoHopDependents": 34,
     "risks": [
       { "component": "COMP_auth_ts", "reason": "hotspot (24 dependents)" },
       { "component": "COMP_users_model", "reason": "crosses frontend→database layer" }
     ]
   }
   ```

7. **Log to Phase 1 output** one line:

   ```
   Blast radius: touches [N components] across [layers], [risks_count] risk flags. See state.json.navgator.phase1 for details.
   ```

### When to Escalate

If `risks.length >= 3` or `twoHopDependents > 50`: note this in state.json and the Phase 1 summary. Phase 3 PLAN should consider splitting the work into smaller chunks with explicit integration tests between them.

## Phase 7 — Post-Change Violation Check

**Purpose**: detect architectural regressions introduced during Phase 4. NavGator runs `rules` internally; this skill invokes it and diffs against the Phase 1 baseline.

### Inputs

- The Phase 1 baseline in `.build-loop/state.json.navgator.phase1`
- A fresh run of `navgator rules --json` (executed by this skill)

### Steps

1. **Run violation check**:
   ```bash
   navgator rules --json > .build-loop/state.json.navgator.phase7_raw
   ```
   If the command fails (NavGator CLI not installed or scan stale), skip and emit:
   ```
   NavGator violation check skipped (CLI not available or scan stale).
   ```

2. **Parse violations**. Each entry has shape:
   ```json
   { "rule": "circular-dependency", "severity": "error", "components": ["A", "B"], "message": "..." }
   ```
   NavGator's built-in rules: `orphan`, `database-isolation`, `frontend-direct-db`, `circular-dependency`, `hotspot`, `high-fan-out`, `layer-violation`.

3. **Diff against baseline**. Compare Phase 7 violations with Phase 1's pre-change state (optional — only meaningful if Phase 1 captured a full rules snapshot; for now treat all violations as candidates for blocking).

4. **Route findings**:
   - `severity: "error"` on `circular-dependency`, `layer-violation`, `database-isolation`, `frontend-direct-db` → **BLOCKING**. Route back to Phase 6 with the violation as a new failed criterion.
   - `severity: "warn"` on `hotspot`, `high-fan-out`, `orphan` → **WARNING**. Include in Phase 8 report, do not block.

5. **Lessons matching**. Read `.navgator/lessons/lessons.json` if present. If any new violation matches a known recurring pattern (same `rule` + same `component`), flag it as a "recurrence" in the report — the user should see "this violation type has appeared N times in this project" for context.

6. **Emit summary** (≤10 lines) into `.build-loop/state.json.navgator.phase7`:

   ```json
   {
     "timestamp": "ISO-8601",
     "violationsFound": N,
     "blocking": [{"rule": "circular-dependency", "components": ["A", "B"]}],
     "warnings": [{"rule": "hotspot", "component": "auth.ts", "fanIn": 28}],
     "recurrences": [{"rule": "frontend-direct-db", "seenBefore": 3}]
   }
   ```

## Integration with Orchestrator

The build-orchestrator dispatches this skill via the `Skill` tool during its Phase 1 and Phase 7 coordination. Pass the goal text and current phase as context. The skill reads/writes under `.build-loop/state.json.navgator.*` for later phase consumption.

## What This Skill Does NOT Do

- Does not run `navgator scan` automatically — user owns scan freshness
- Does not modify `.navgator/` outputs
- Does not reinterpret NavGator's component classifications
- Does not block a build when NavGator is not installed

## Model

Haiku sufficient for reads + JSON diff. Sonnet only if a Phase 7 violation description requires nuanced re-writing for the user report. Default: inline, no model — pure filesystem ops.
