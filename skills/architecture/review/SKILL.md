---
name: build-loop:architecture-review
description: Full architectural integrity review — system flow, component connections, documentation drift, and lessons. Build-loop's native review, copied from NavGator's code-review skill. Heavy weight; use for Phase 4 Review when a build crosses 2+ layers.
version: 0.1.0
user-invocable: false
source: NavGator/skills/code-review/SKILL.md
source_hash: 61d49f3329da9dbf059fe54b24b3fe6537050c2d159fcac8d00fadb292dd5d4c
---

# Architectural Integrity Review

Orchestrates impact analysis, data-flow tracing, the rules engine, and the lessons system into a 5-phase architectural review. Native to build-loop — content adapted from `NavGator/skills/code-review/SKILL.md`. This skill is an architectural integrity reviewer — not a linter, not a bug hunter.

## What This Skill IS vs. IS NOT

**IS:**
- System flow — how data moves from user input through the system to output
- Component communication — APIs, data formats, connection patterns between layers
- API contract validation — interface changes and whether consumers are updated
- LLM architecture — provider routing, prompt patterns, model selection logic
- Documentation drift — whether docs reflect what the code actually does
- Lessons learned — patterns that caused issues, tracked and matched over time
- Freshness validation — periodic research to avoid stale architectural knowledge

**IS NOT:**
- Code linter or style checker
- Individual function bug hunter — local logic errors, off-by-one mistakes
- Security vulnerability scanner — use `security-reviewer` agent or dedicated tools
- Test coverage auditor
- TypeScript type error detector — the compiler handles that
- Performance optimizer — out of scope

## When to Activate

- Build-orchestrator Phase 4 Review when the build touches 2+ layers (cross-layer change)
- User asks "review architecture", "is this safe to merge", "what did I break"
- After a large refactor

## Scope Resolution

| Invocation | Scope |
|------------|-------|
| Default (no flags) | `git diff origin/main..HEAD` — changed files since branch diverged |
| `--all` | Full architecture review across all components |
| `<component>` | Focused review on one component and its direct connections |
| `--validate` | Run Phase 5 freshness validation regardless of age |
| `learn "..."` | Record a manual lesson, skip full review |

When scope is ambiguous, default to `git diff origin/main..HEAD`. If the branch has no divergence from main, ask the user what to review.

## Prerequisites

1. `.navgator/architecture/index.json` must exist. If not, run `build-loop:architecture-scan` first.
2. If `generated_at` is >24 hours old, warn before proceeding.
3. Load `.navgator/architecture/file_map.json` for file-to-component resolution.
4. Load `.navgator/architecture/graph.json` for connection traversal.
5. If `.navgator/lessons/lessons.json` is missing, create:
   ```json
   { "schema_version": "1.0.0", "lessons": [] }
   ```

Do not proceed without architecture data. Stale data is worse than pausing.

---

## Phase 1 — Structural Changes

**Goal:** Identify which components and layers were touched.

1. Run `git diff [scope] --stat` to get changed files
2. For each changed file, look it up in `file_map.json` → parent component ID
3. Look up component ID in `index.json` → type and layer
4. Classify each change: new component / modified connection / config change / documentation
5. Identify which layers were touched: frontend, backend, database, infra, external
6. Flag any cross-layer change as **higher risk**
7. Note any new files that don't resolve to any tracked component

```
PHASE 1: STRUCTURAL CHANGES
  N components touched across N layers
  Cross-layer: [ComponentA (frontend→backend), ComponentB (backend→database)]
  New components: [unconnected — needs scan to track]
  Layers: frontend(N) | backend(N) | database(N) | infra(N)
```

---

## Phase 2 — Connection & Flow Integrity

**Goal:** Verify connections valid, data flows intact.

For each component from Phase 1:

1. Call `build-loop:architecture-impact` (uses `mcp__plugin_navgator__impact`)
2. Call `build-loop:architecture-trace` direction=both
3. Call `mcp__plugin_navgator__connections` direction=both

| Issue | Severity | Detection |
|-------|----------|-----------|
| Orphaned component | Important | New component with 0 incoming AND 0 outgoing connections |
| Broken reference | Critical | Connection points to a component not in `graph.json` |
| Layer violation | Critical | Frontend connects directly to database, bypassing backend |
| High fan-out | Important | Component has >8 outgoing dependencies |
| Import cycle | Critical | Component A → B → A |
| API contract mismatch | Critical | Interface changed, consumers not updated |
| Self-referencing connection | Important | Component listed as its own dependency |

```
PHASE 2: CONNECTION INTEGRITY
  Rules: N violations (N critical, N important, N minor)
  [CRITICAL] Layer violation: ComponentA (frontend) → ComponentB (database)
    File: src/pages/users.tsx:45
    Why: Frontend bypasses API layer, creating tight coupling.
```

If no violations: report "No connection integrity issues found" — do not omit.

---

## Phase 2.5 — LLM Purpose Classification

**Goal:** Classify what each LLM call does, not just who it calls.

1. Run `mcp__plugin_navgator__llm_map` with `--classify`
2. For each uncategorized use case: read the primary file, determine purpose (summarization, extraction, search/ranking, generation, embedding, classification, translation, agent/tool-use, validation, analysis, synthesis)
3. Note system effect (search results, UI charts, DB writes, queue processing, API responses)
4. Record classifications as lessons in `.navgator/lessons/lessons.json` with category `'llm-architecture'`

---

## Phase 3 — Documentation Drift

**Goal:** Verify docs reflect what code does.

1. Read README.md — for each CLI command/flag in implementation, verify it appears in CLI Reference. Run `--help` and compare.
2. Read CLAUDE.md — verify command table is complete (every `/build-loop:*` slash command listed)
3. List all directories under `skills/` — for each capability, verify a skill file exists
4. Read `plugin.json` — verify all referenced directories and entry points exist on disk
5. For each new/modified capability from Phase 1, check that it appears in README, CLAUDE.md, AND a skill file

An **agent-invisible feature** is one in code but absent from agent-readable files (CLAUDE.md or skill files). Highest-priority gap — silently degrades agent capability.

```
PHASE 3: DOCUMENTATION DRIFT
  [AGENT-INVISIBLE] --validate added but not in CLAUDE.md command table
  [STALE] README references `navgator check` (renamed to `/navgator:check`)
  [UNDOCUMENTED] navgator coverage --typespec — no skill file
```

If no drift: report "Documentation matches implementation".

---

## Phase 4 — Lessons Check

**Goal:** Match findings against known patterns; record new ones.

### Matching Known Lessons

1. Read `.navgator/lessons/lessons.json`
2. For each lesson, check whether any changed file/component/finding matches `signature` patterns
3. If a match: flag with recurrence context. Do not silently skip.

### Recording New Lessons

For each NEW finding (not already in `lessons.json`), append:

```json
{
  "id": "<sha256(category+pattern) truncated to 8 chars>",
  "category": "layer-violation|orphaned-component|api-contract|doc-drift|import-cycle|triplicated-logic|other",
  "pattern": "human-readable description",
  "signature": ["regex or code fragment to match recurrence"],
  "severity": "critical|important|minor",
  "context": {
    "first_seen": "ISO 8601",
    "last_seen": "ISO 8601",
    "occurrences": 1,
    "files_affected": ["paths"],
    "resolution": "specific fix"
  },
  "example": { "bad": "...", "good": "...", "why": "..." },
  "validation": { "last_validated": "ISO 8601", "source": "agent", "status": "unvalidated" }
}
```

If a lesson already exists for the pattern (matched by `id` or `signature`), update `last_seen`, increment `occurrences`, merge `files_affected`. Don't duplicate.

---

## Phase 5 — Freshness Validation

**Goal:** Lessons referencing external APIs or libraries still reflect current best practice.

Trigger only when:
- User passed `--validate`
- Lesson references an external API/library/version-specific behavior
- More than 30 days since `validation.last_validated` on any matched lesson

For each lesson needing validation:
1. WebSearch the referenced API/library/pattern
2. Verify still recommended approach
3. Update `validation.last_validated`, `validation.status` (`current`/`stale`), `validation.source` (`web-search`)
4. If stale, add `validation.note` explaining what changed

---

## What to Ignore

- Individual function logic — use a debugger
- Code style/formatting — use a linter
- Test coverage — use coverage tools
- Performance — use profiling
- Security vulnerabilities — use `security-reviewer`
- TypeScript type errors — compiler handles those
- Spelling/grammar in comments

If a finding falls into one of these, note briefly and redirect: "This is a linter issue — outside architectural review scope." Then move on.

## Sibling Skills

- `build-loop:architecture-scan` — refresh data first
- `build-loop:architecture-impact`, `build-loop:architecture-trace`, `build-loop:architecture-rules`, `build-loop:architecture-dead` — sub-steps of this review

*Source: copied verbatim from NavGator and rewritten for build-loop. Drift-checked by `build-loop:sync-skills`.*
