---
name: build-loop:navgator-bridge
description: Read NavGator architecture data in Assess and Review-D Fact-Check to compute blast radius and detect new violations. Filesystem-only; no NavGator CLI required mid-build.
version: 0.1.0
user-invocable: false
---

# NavGator Bridge

Lets build-loop consume NavGator's architecture graph without tight coupling. NavGator writes to `.navgator/architecture/`; this skill reads from there. If NavGator has never run on the project, this skill no-ops with a one-line note.

**Use:**
- Assess — compute blast radius before planning
- Review-D Fact-Check — detect new violations after implementation
- Report (Review-F) — orphan scan after build completes

## Cherry-pick principle

**NavGator remains an independent tool and repository.** This bridge does not embed or duplicate NavGator's functionality — it only consumes the relevant outputs:

- Reads `.navgator/architecture/{index.json, file_map.json, graph.json, SUMMARY.md, lessons/lessons.json}` — filesystem only
- Invokes `navgator impact`, `navgator rules`, `navgator llm-map`, `navgator dead` — CLI delegation only
- Writes to `.build-loop/state.json.navgator.*` — bridge's own namespace in build-loop's state

What this bridge does NOT do:
- Reimplement any NavGator rule logic, scanning, or graph construction
- Cache or shadow NavGator's outputs (always reads live)
- Modify NavGator's config, lessons, or architecture files
- Couple to NavGator's internal schema beyond the documented stable fields (`schema_version: "1.0.0"`)

If NavGator is absent, this bridge skips — it does not provide a fallback implementation.

## Pre-flight

Before either phase's logic runs, check:

```bash
[ -f ".navgator/architecture/index.json" ] && echo "HAVE_NAVGATOR" || echo "NO_NAVGATOR"
```

If `HAVE_NAVGATOR`, run the steps in this skill against the NavGator outputs.

If `NO_NAVGATOR`, **run the standalone fallback** instead of skipping silently. Build-loop carries degraded-but-useful architecture knowledge when NavGator isn't installed:

- **Load**: `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/fallbacks.md` §`architecture` — executable grep/git commands that approximate Assess blast-radius
- **Write output to**: `.build-loop/state.json.architecture.standalone` (distinct namespace from the NavGator-fed `.navgator` key so downstream phases can tell which source produced the data)
- **Flag in Review-F report**: `⚠️ architecture analysis via static fallback — install NavGator for AST-aware dependency graph + rule enforcement`

The fallback covers: layer classification by directory convention, 1-hop import grep, git-churn hotspot detection, risk flags for cross-layer/high-fan-out changes. It does NOT cover: transitive (2-hop+) tracing, LLM prompt mapping, or architectural rule enforcement — those require NavGator.

Do not error, do not block the build. Standalone is the worst case; NavGator is better.

## Assess — Blast-Radius Read

**Purpose**: narrow the plan. Before Plan writes a dependency graph, tell it which components the changes will touch so subagent scoping is accurate.

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

5. **Risk flags**. Read `SUMMARY.md` for hotspots. If any touched component is listed as a hotspot (high fan-in), or appears in circular-dependency chains, or spans a layer violation — add a `⚠️ risk` flag to the Assess output.

5a. **Per-file impact for highest-risk components**. For each component flagged as `risk` in step 5, call `navgator impact <component> --json` (not just read graph.json) for authoritative downstream enumeration. This is slower than JSON reads but accurate — use only for flagged components, not all touched components. Cap at 5 impact calls per Assess (if > 5 risks, take the top 5 by fan-in).

   ```bash
   navgator impact "$COMP" --depth 2 --json > /tmp/navgator_impact_$$_$COMP.json
   ```

   Merge into the risk entry: `{component, reason, impactedFiles: [...], impactedLines: N, blastCategory: "contained|spreading|critical"}`.

5b. **Prompts in scope** (when `triggers.promptAuthoring` or `triggers.promptEditingExisting` is true): call `navgator llm-map --json` to enumerate all LLM prompts in the project and which components they live in. Intersect with `componentsTouched`. If the build will edit an in-scope prompt, surface it explicitly:

   ```json
   "promptsInScope": [
     { "file": "src/agents/researcher/system.ts", "component": "COMP_researcher", "provider": "anthropic", "calls_per_day_estimate": "high" }
   ]
   ```

   Execute consults this so the implementer knows *which* prompts are load-bearing vs incidental before editing them.

6. **Emit compact summary** (≤20 lines) into `.build-loop/state.json.navgator.phase1`:

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
       { "component": "COMP_auth_ts", "reason": "hotspot (24 dependents)", "impactedFiles": ["a.ts", "b.ts"], "blastCategory": "spreading" },
       { "component": "COMP_users_model", "reason": "crosses frontend→database layer", "impactedFiles": [...], "blastCategory": "critical" }
     ],
     "promptsInScope": [
       { "file": "src/agents/researcher/system.ts", "component": "COMP_researcher", "provider": "anthropic" }
     ]
   }
   ```

7. **Log to Assess output** one line:

   ```
   Blast radius: touches [N components] across [layers], [risks_count] risk flags. See state.json.navgator.phase1 for details.
   ```

### When to Escalate

If `risks.length >= 3` or `twoHopDependents > 50`: note this in state.json and the Assess summary. Plan should consider splitting the work into smaller chunks with explicit integration tests between them.

## Review-D — Post-Change Violation Check

**Purpose**: detect architectural regressions introduced during Execute. NavGator runs `rules` internally; this skill invokes it and diffs against the Assess baseline.

### Inputs

- The Assess baseline in `.build-loop/state.json.navgator.phase1`
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

3. **Diff against baseline**. Compare Review-D violations with Assess pre-change state (optional — only meaningful if Assess captured a full rules snapshot; for now treat all violations as candidates for blocking).

4. **Route findings**:
   - `severity: "error"` on `circular-dependency`, `layer-violation`, `database-isolation`, `frontend-direct-db` → **BLOCKING**. Route back to Iterate with the violation as a new failed criterion.
   - `severity: "warn"` on `hotspot`, `high-fan-out`, `orphan` → **WARNING**. Include in Review-F report, do not block.

5. **Lessons matching**. Read `.navgator/lessons/lessons.json` if present. If any new violation matches a known recurring pattern (same `rule` + same `component`), flag it as a "recurrence" in the report — the user should see "this violation type has appeared N times in this project" for context.

6. **Emit summary** (≤15 lines) into `.build-loop/state.json.navgator.phase7`:

   ```json
   {
     "timestamp": "ISO-8601",
     "violationsFound": N,
     "blocking": [{"rule": "circular-dependency", "components": ["A", "B"]}],
     "warnings": [{"rule": "hotspot", "component": "auth.ts", "fanIn": 28}],
     "recurrences": [{"rule": "frontend-direct-db", "seenBefore": 3}]
   }
   ```

## Review-F — Orphan Scan (informational)

After the scorecard is written, run a quick orphan detection to surface dead code introduced or exposed by this build:

```bash
navgator dead --json > /tmp/navgator_dead_phase8.json
```

If the CLI is unavailable or the scan is stale, skip silently. Otherwise, diff against the Assess snapshot:

- **New orphans** (code added during the build that ended up with zero imports/callers): include in Review-F report as "⚠️ potentially dead code" with file paths. These are common when a feature is half-wired — e.g. a helper written but never called.
- **Resolved orphans** (previously orphaned components now connected): include as "✅ resolved orphans" in the report — credit where due; indicates earlier dead code was wired in.
- **Persistent orphans** (orphaned before and still orphaned): do not report every build. Only surface if `persistent_count > 10` with a pointer to `/navgator:dead` for the user to act.

Write summary to `.build-loop/state.json.navgator.phase8`:

```json
{
  "newOrphans": [{"component": "COMP_helper_util", "file": "src/utils/new-helper.ts"}],
  "resolvedOrphans": [{"component": "COMP_legacy_formatter"}],
  "persistentOrphansCount": 14
}
```

This gate never blocks — orphan detection is noisy and sometimes wrong (dynamic imports, string-based routing, test fixtures). Informational only.

## Integration with Orchestrator

The build-orchestrator dispatches this skill via the `Skill` tool during its Assess and Review-D coordination. Pass the goal text and current phase as context. The skill reads/writes under `.build-loop/state.json.navgator.*` for later phase consumption.

## What This Skill Does NOT Do

- Does not run `navgator scan` automatically — user owns scan freshness
- Does not modify `.navgator/` outputs
- Does not reinterpret NavGator's component classifications
- Does not block a build when NavGator is not installed — routes to `fallbacks.md#architecture` for a standalone (degraded-but-useful) analysis instead of skipping silently

## Model

Haiku sufficient for reads + JSON diff. Sonnet only if a Review-D violation description requires nuanced re-writing for the user report. Default: inline, no model — pure filesystem ops.
