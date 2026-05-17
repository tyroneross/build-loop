> **Historical audit — 2026-04-22.** Captured at v0.10; most P0 phase-model drift was resolved across v0.10→v0.12. Kept as a record, not a current action list.

# build-loop — performance audit and proposed fixes

## Bottom line

Build-loop's *architecture* is sound — the 5-phase model, tiered-model economics, and deploy-gating hook are real wins. The *performance gap is documentation drift*: the repo carries three different phase models (old 9-phase, old 8-phase, current 5+1-phase) simultaneously, and the README, hooks, codex-plugin manifest, and the main skill's routing section all still describe the deprecated models. Anyone following the public docs will invoke the wrong command, route to the wrong phase numbers, and hit a deploy-guard error message that references a phase that no longer exists.

Fix priority is P0 on drift (shipping-correctness), P1 on size/splitting (agent context cost), P2 on DX polish.

---

## P0 — Shipping correctness

### 1. README describes a phase model that doesn't exist

**File:** `README.md` lines 3, 121; `.codex-plugin/plugin.json` lines 4, 25, 26.

**Drift:** Claims "8-phase development loop (assess, define, plan, execute, validate, iterate, fact-check, report)". Current canonical model (per `CLAUDE.md`, `AGENTS.md`, `.claude-plugin/plugin.json`) is **5-phase + optional Learn**: Assess → Plan → Execute → Review → Iterate (+ Learn). "Define" has been folded into Assess; "Critic / Validate / Optimize / Fact-Check / Simplify / Report" are sub-steps of Review.

**Fix:** Replace the README wholesale. A corrected version is in `02-README-proposed.md`. For the Codex plugin.json, replace all three strings with: *"Orchestrated 5-phase development loop (Assess → Plan → Execute → Review → Iterate) plus optional Learn phase. Review combines critic, validate, optimize, fact-check, simplify, and report as ordered sub-steps."* (This matches `.claude-plugin/plugin.json`.)

### 2. README documents the wrong command

**File:** `README.md` line 54.

**Drift:** Shows `/build [goal description]`. Actual slash command is `/build-loop [goal description]` (`commands/build-loop.md`).

**Fix:** Corrected in proposed README. Grep the rest of the repo for `/build ` followed by a word to catch stragglers.

### 3. `commands/build-loop.md` frontmatter description is stale

**File:** `commands/build-loop.md` line 2.

**Drift:** `description: "Orchestrated development loop: assess → define → plan → execute → validate → iterate → fact-check → report"` — the old 8-phase order.

**Fix:** Change to `"Orchestrated 5-phase loop (+ optional Learn): assess → plan → execute → review → iterate"`.

### 4. Deploy-guard hook shows a stale phase number to the user

**File:** `hooks/hooks.json` line 9.

**Drift:** Error message reads `"Build loop Phase 7 (fact-check) has not completed. Run fact-check before deploying."`. Fact-check is Review sub-step D in the current model; there is no "Phase 7."

**Fix:** Change the message to `"Build loop Review fact-check (sub-step D) has not completed. Run the Review phase to fact-check before deploying."`. Also verify that `state.json` still uses the `phases.fact_check.completed` key — if the Review refactor renamed it (e.g. to `phases.review.substeps.fact_check.completed`), the hook's jq/python probe needs to update to match, or it silently fails open.

**Secondary fix on the hook:** the regex `\b(git\s+push|npm\s+publish|...)\b` won't catch multi-space or tab variants reliably across shells. Consider `[[:space:]]+` in the character class. Low severity.

### 5. Main skill (`skills/build-loop/SKILL.md`) contradicts itself

**File:** `skills/build-loop/SKILL.md`.

**Drift:**

- Line 14: *"Full 9-phase loop for implementation tasks"* — neither the current 5+1 model nor the older 8-phase model. Looks like a reference to a pre-8-phase variant that was never removed.
- Lines 36, 37, 67, 84, 473, 474, 503: references to `Phase 8 (Report)`, `Phase 8 (after Report)`, `Phase 8 scorecard`, etc.
- Line 71: `runs navgator rules diff post-change in Phase 7`.
- Lines 531, 533: "CRITIC (Phase 4.5)" and "OPTIMIZE (Phase 4.7)" — legacy sub-phase numbering from before Review consolidated.
- Meanwhile lines 42–49 carry the correct 5+1 phase-quick-reference table.

The same file therefore documents four different phase models depending on which paragraph you land on. Any subagent prompt that quotes this skill inherits the confusion.

**Fix:**

1. Replace line 14 routing with: `**Build** (default): Full 5-phase loop (+ optional Learn) for implementation tasks`.
2. Replace all `Phase 7` / `Phase 8` / `Phase 4.5` / `Phase 4.7` references with their current-model equivalents: Review sub-step D (fact-check), Review sub-step F (report), Review sub-step E (simplify, runs before Report), Review sub-step A (critic), Review sub-step C (optimize).
3. Add a brief "Phase vocabulary" callout at the top of the skill mapping old → new terms, since the refactor-history `references/` already documents the intent. That way, any residual search-hit on "Phase 7" points the reader to the authoritative translation.

### 6. Sibling skills carry the same stale numbering

**Files:**

- `skills/model-tiering/SKILL.md` lines 36, 37, 87: references to `Phase 7A` / `Phase 7B`.
- `skills/optimize/SKILL.md` line 86: `Phase 4.7 (AUTO-OPTIMIZE)`.
- `skills/build-loop/phases/fact-check.md` lines 1, 3, 32: titled `Phase 7: Fact Check & Mock Scan`, says "Loaded on demand when entering Phase 7."
- `skills/build-loop/fallbacks.md` lines 224, 341: `Phase 8 report` references.

**Fix:** Global search-and-replace against a phase-name translation table (see #5). Since `fact-check.md` is a phase-detail file, rename to `review-substep-d-fact-check.md` or move to `references/review/` — its current path implies it's a top-level phase document, which it isn't anymore.

### 7. Add a pre-commit / CI check to prevent future drift

The `references/refactor-history/STANDALONE_TEST_RUN.md` file (line 119) already notes this as a lesson: *"Add a pre-commit check that greps for '8-phase' | 'eight phase' whenever .build-loop/goal.md references phase counts, so canonical phase-count drift surfaces before commit."*

**Fix:** Ship the check. Minimal `.github/workflows/phase-lint.yml` plus a `scripts/dev-tools/phase_lint.py` that greps for the deprecated tokens across `*.md`, `*.json`, `*.mjs`, and `commands/` and fails the build on any match outside `references/refactor-history/**`. Regex: `(8|9)-phase|\bPhase [789]\b|Phase 4\.[0-9]`.

---

## P1 — Efficiency / context cost

### 8. Main skill is 551 lines — too large to load cheaply into every subagent

Anthropic's skill guidance recommends concise SKILL.md files (often ≤ 200 lines) that point to `references/*.md` for depth. The current `skills/build-loop/SKILL.md` bundles:

- Routing (keep in SKILL.md — decision-critical)
- Capability-routing table (~20 rows, many long) — **move to `references/capability-routing.md`**
- Trigger conditions (~50 lines of pyramid/etc. triggers) — **move to `references/trigger-conditions.md`**
- Phase quick reference — keep
- Memory / handoff / post-build sections — consider moving detailed specs to `references/memory-system.md`
- ASCII process flow with old phase numbering — **delete, replace with a terse 5-phase diagram**

Target: SKILL.md ≤ 200 lines, with clear `See references/<name>.md for …` pointers. The autoloaded file the orchestrator sees every run shrinks ~3×, and references load only when the triggering capability fires.

### 9. Duplicate phase documentation across README, CLAUDE.md, AGENTS.md, SKILL.md

Same phase table exists (in four slightly different forms) in four files. Every refactor pays a 4× doc-maintenance tax. This is already what caused the drift above.

**Fix:** Make one canonical source — suggest `docs/phases.md` or `AGENTS.md` (since it's the portable one). Have README, CLAUDE.md, and SKILL.md each link to it and include only the one-line summary plus the quick-reference table — not the full phase details. The bodies of Phase 1–6 detail should live in exactly one place.

### 10. Agent frontmatter: `build-orchestrator` carries an enormous tool allowlist

Every agent spawn pays a per-tool schema cost. If some of those tools (e.g. `TaskCreate`/`TaskUpdate`/`TaskList`) are only used in specific branches, consider tool-subset scoping at dispatch time. Not a correctness issue, but a cost lever given this plugin is explicitly about cost-tiered execution.

---

## P2 — DX polish

### 11. No `CHANGELOG.md` or release notes

The 9 → 5 phase refactor is substantial and user-facing. A short `CHANGELOG.md` with version ↔ phase-model mapping prevents the kind of drift we just audited, and lets consumers discover what changed between `0.2.x` and `0.3.0`.

### 12. README has no prominent "When NOT to use" callout

The "skip the loop for single-file edits" guidance is buried in the Usage section. For a tool that charges Opus tokens per run, this belongs above the fold. Proposed README places it in the opening block.

### 13. README's "Codex" section is placed after License

Move above License. License should be last.

### 14. `plugin.json` (Claude) has no `keywords` overlap with `.codex-plugin/plugin.json`

Not blocking, but worth aligning so search surfaces behave consistently across the two install surfaces.

### 15. `feedback.md` vs `evals/` vs `runs[]` — redundant but useful

Three places hold post-build state. Worth a short `docs/state-schema.md` explaining the separation (feedback = human-readable lessons, evals = per-run scorecards, runs[] = machine-readable series for Phase 6). Otherwise each new contributor will re-ask.

---

## Proposed fix sequence (90-minute shop-floor pass)

1. **Phase-drift sweep** (~30 min) — Items 1–6, single commit, driven by the P0 grep list.
2. **Hook + commands/ frontmatter patch** (~10 min) — Items 3, 4.
3. **Add `scripts/dev-tools/phase_lint.py` + CI** (~15 min) — Item 7.
4. **Split SKILL.md into SKILL + references/** (~30 min) — Item 8.
5. **Add CHANGELOG.md, move License to bottom, tighten README** (~5 min) — Items 11, 13.

Defer: Items 9 (single source of truth), 10 (tool-scope), 14–15 (docs) to a follow-up commit. Each is worth ≤ 20 min on its own.

## What's working and should not be touched

- Five-phase model with Review consolidation is a real improvement over the old 9-phase flow; trace-comparison.md already validates parity.
- Tier assignment philosophy (Opus at boundaries, Sonnet inside, Haiku for pattern-match) is correctly documented in CLAUDE.md and plugin.json.
- Deploy-gating hook is the right shape; only the user-facing string and phase-key name need updating.
- Companion-bridge cherry-pick pattern with standalone fallbacks is a strong architectural choice — don't consolidate into embedded dependencies.
- `.build-loop/` project-local state separation is clean.
