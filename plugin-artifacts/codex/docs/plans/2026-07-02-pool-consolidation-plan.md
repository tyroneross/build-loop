<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Plan: Pool Consolidation — shrink + differentiate build-loop's skill/agent/script pool

<!-- checklist
Item 1 — Auth guard: N/A: no server routes; docs/skills/scripts refactor of the plugin itself.
Item 2 — External APIs: N/A: no new external API calls. Codex CLI audit uses the already-installed codex peer path (feedback_codex_peer_via_rally_not_subprocess).
Item 3 — Rate-limit criterion: N/A: no paid API calls introduced.
Item 4 — Discoverability: N/A for UI; skill discoverability IS the subject — each fold names its replacement activation path (see per-increment "activation wiring").
Item 5 — Server/client boundary: N/A: no runtime app code.
Item 6 — Concurrency: N/A per write path in app terms. Repo-level concurrency handled by the mandatory per-increment worktree + rebase-onto-moving-main protocol (§Increment Protocol).
Item 7 — Observability: N/A: no runtime side effects. Per-increment evidence = test output + grep-zero-stale output + Codex audit verdict, recorded in the increment's commit body.
Item 8 — Input validation: N/A: no new routes.
Item 9 — Stable ID traceability: U-01 → F-05 → T-05/T-06/T-08 (example chain); every [P0] feature row carries T-IDs (§Spec Object).
Item 10 — JSON spec object: present, §Spec Object (JSON).
Item 11 — Blocking-and-novel question gate: one open question (§Open Questions, blocking-test: T-09); all else resolved as [ASSUMED:] inline.
Item 12 — Low-reversibility ADRs: ADR-01 (op-routing merge), ADR-02 (bridge-fold test contract), ADR-03 (descriptions-over-renames), ADR-04 (detector-then-review cull). §ADRs.
Item 13 — Analytical lens: DSM (cross-component dependency ordering of increments) + Pugh (per-rename do/skip against blast-radius vs distinctness benefit).
Item 14 — Handoff document: docs/plans/2026-07-02-pool-consolidation-plan.handoff.md (sibling, written).
Item 15 — Synthesis dimensions: N/A: no UI surface.
Item 16 — Risk reason: N/A: none of the five canonical boundaries applies (no security/persistence/runtime-service-protocol/deployment/user-trust change). Skill-name contract changes are gated instead via modifies_api: true + scope-auditor.
Item 17 — UI input/output contract: N/A: no UI surface.
Item 19 — Env-var manifest: N/A: no new external service.
-->

```yaml
modifies_api: true            # skill names are cross-agent invocation contracts; increments 2-6 remove/rename invocable names
scope_auditor_status: pending # scope-auditor gate required at Plan→Execute boundary
dispatch_tier: frontier       # this plan's authorship; per-increment tiers declared in each chunk
```

## Goal

Shrink and differentiate build-loop's capability pool — 50 skills, 28 agents, ~370 scripts — so LLM selection accuracy stops degrading from pool size and candidate similarity (Chroma context-rot 14–85% accuracy loss; MCP tool-count studies show degradation "after just a handful" of similar tools; ~9.5% avg decline), **without losing any capability**. Falsifiable form: after increments 2–7 land, (a) the skill pool has 6 fewer entries and zero stale references (T-06), (b) every folded capability remains reachable via a named activation path (T-08), and (c) the full deterministic test suite + surface tests stay green (T-01..T-05, T-07).

Increment 1 (optimize-run ghost-command sweep) already shipped, Codex-audited. This plan covers increments 2–9.

## Locked Decisions

| Decision | Value | Reversibility |
|---|---|---|
| Analytical lens | DSM (dependency-ordered increments) + Pugh (rename decisions) | — |
| Merge style | Fold content as sections/reference files under the target — **never drop content**; progressive disclosure (short section in SKILL.md, detail in `references/`) | high (git revert) |
| Reference redirection | Every external ref to a folded skill is rewritten to the merged target in the same increment; verified by grep-zero-stale | high |
| Public entrypoints | UNCHANGED: `build-loop, debug-loop, optimize, research, knowledge, handoff` (✅ verified: `scripts/test_agent_surface_policy.py:23-30`). None of the folded skills is an entrypoint; `knowledge` is a fold *target*, kept | — |
| plugin-artifacts/codex mirror | Regenerates from source (`npm run codex:build-artifact` / commit hook). **Never hand-edited** | — |
| Execution order | Increments strictly sequential, smallest blast-radius first — validates fold mechanics cheaply before the big debugging-cluster fold | — |
| Merged debugging skill interface | Single `debugging-memory` skill with `op: search \| store \| assess` input (ADR-01) | low → ADR-01 |
| Rename policy | Descriptions-over-renames: sharpen frontmatter descriptions with explicit "NOT for X — use Y" cross-pointers instead of renaming, except where refs ≈ 0 and name-collision is structural (ADR-03) | high |
| KEEP list (do NOT touch) | security triad (scan is hook-wired), ui-design + ui-validator, `skills/architecture/*` (6) + architecture-scout, auto-finding-capture (Stop-hook), debug-loop, all agents | — |

## Scope

**In scope:** 4 skill merges (−6 pool entries), 1 rename, a description-differentiation pass, and the detector-then-review script sub-plan. All work in the build-loop repo itself (self-recursive; per-increment worktrees mandatory per CLAUDE.md §Concurrent dispatch isolation).

### Out of scope (non-goals)

- Agent-pool merges (28 agents untouched; `self-improvement-architect` and `root-cause-investigator` renames explicitly SKIPPED — §Rename Decisions).
- Any new capability, hook, or command. `commands/run.md` stays the only command.
- Renaming any `CLAUDE_PUBLIC_ENTRYPOINTS` member.
- Hand-edits to `plugin-artifacts/codex/**` or historical documents (`docs/retrospectives/`, `docs/plans/` of past runs, `refactor-history/`, `docs/_inbox/`) — history is preserved, not rewritten. KNOWN-ISSUES.md gets *appended* follow-up notes only.
- Guessing at script deadness — the cull is detector-then-review (`scripts/script_relevance.py`), never judgment-only deletion.

## Increment Protocol (applies to EVERY increment — main is a moving target)

1. `git worktree add ../build-loop.worktrees/pool-inc<N> -b pool-inc<N> origin/main` (fresh from origin/main at start time).
2. **Re-grep every old name** listed in the chunk before editing — the ref lists below were verified 2026-07-02 but a peer may have added refs since. The fresh grep output is the binding list.
3. Fold/edit + update all references + activation wiring.
4. Regenerate inventory surfaces: `architecture/model.json` + `architecture/ARCHITECTURE.md` (via `navgator:scan` if installed, else edit the affected entries) and `docs/build-loop-flow-mockup.html` (edit affected entries — ⚠️ untested whether a generator exists; check `.github/workflows/architecture-diagram.yml` first). `npm run codex:build-artifact` for the codex mirror (or let the commit hook regenerate).
5. Tests: `uv run pytest scripts/ tests/ -m "not integration" --timeout=60` (CI-equivalent deterministic suite, `.github/workflows/pytest.yml:91`) + `npm test` (bridge-preflight subset + hooks) + the chunk-specific tests named below.
6. Grep-zero-stale falsifier: `grep -rn "<old-name>" . --exclude-dir=.git --exclude-dir=plugin-artifacts --exclude-dir=node_modules` returns only the chunk's named historical-doc allowlist.
7. **Codex compatibility audit** (cross-vendor): dispatch the Codex peer to review the increment diff for skill-resolution breakage, dropped content, and codex-artifact integrity. Gating verdict PASS/VARIANCE/BLOCKED per `references/coordination-rules.md`.
8. Integrate: `git fetch && git rebase origin/main` (re-run step 2's grep after rebase — new refs may have landed), fast-forward main, push.
9. Rollback: pre-push = discard worktree branch. Post-push = `git revert` the increment's commits (each increment lands as one logical commit); codex mirror re-regenerates on the revert commit. Idempotency: every step is re-runnable (grep, regenerate, tests are pure; edits are convergent).

Increments are **strictly sequential** — shared files (CLAUDE.md, `.claude-plugin/plugin.json`, architecture inventory) are touched by several increments, so cross-increment file MECE is achieved by sequencing, not partitioning. Within an increment, one implementer owns all listed files (MECE trivially holds).

---

## Increments

### Increment 2 — Fold `verify-dispatch` into build-loop core references [F-02]

The cleanest fold (0 external refs — ✅ verified by grep: only `architecture/model.json`, `architecture/ARCHITECTURE.md`, `docs/build-loop-flow-mockup.html`, own SKILL.md). Runs first to validate the fold mechanics end-to-end at minimum risk.

- **dispatch_tier:** `sonnet` — mechanical fold with a small, fully-enumerated ref surface.
- **Fold mapping:** `skills/verify-dispatch/SKILL.md` (89 lines) → new `skills/build-loop/references/verify-dispatch.md` (content moved whole; frontmatter dropped; provenance comment added). Delete `skills/verify-dispatch/`.
- **Owned files:** `skills/verify-dispatch/` (delete), `skills/build-loop/references/verify-dispatch.md` (new), `agents/build-orchestrator.md`, `skills/build-loop/SKILL.md`, inventory surfaces.
- **References to update:** none external. Inventory surfaces only (protocol step 4).
- **Activation wiring (the real risk):** today the skill activates *reactively by description match* ("did the agent actually commit?"). A reference file has no reactive trigger — built ≠ wired. Mitigation: add an explicit pointer in `agents/build-orchestrator.md` at the post-dispatch acceptance step ("after any dispatched agent claims commits landed, walk `skills/build-loop/references/verify-dispatch.md`") and a one-line pointer in `skills/build-loop/SKILL.md`. **Falsifier:** grep shows ≥2 runtime surfaces referencing the new file; a dry read of the orchestrator's dispatch section reaches the checklist.
- **Verification:** protocol steps 5–6; chunk-specific: `scripts/test_agent_surface_policy.py`, `tests/test_capability_registry.py`.
- **Codex checkpoint:** confirm codex artifact builds and no codex-side surface referenced the skill.
- **Rollback:** revert single commit.
- **Risk flag:** `needs-review` — the reactive-trigger loss is a genuine capability-surface change; the wiring mitigation must be checked by a human/critic, not assumed.

### Increment 3 — Fold `knowledge-review` into `knowledge` as a review mode [F-03]

- **dispatch_tier:** `sonnet` — 2 external refs, bounded.
- **Fold mapping:** `skills/knowledge-review/SKILL.md` (112 lines) → `skills/knowledge/references/review-mode.md` + a short "Review mode" section in `skills/knowledge/SKILL.md` (progressive disclosure; `knowledge` is a public entrypoint and stays `user-invocable: true`). Delete `skills/knowledge-review/`.
  - **REQUIRED (W4 — capability-loss fix):** append `knowledge-review`'s review-intent trigger phrases to `skills/knowledge/SKILL.md` frontmatter `description:` (the reactive *selection signal*). The body section alone does NOT restore activation — a fold that moves content but not the description trigger silently loses reactive selectability. Additive only (keep `knowledge`'s existing triggers). This is what makes the Codex checkpoint "confirm knowledge description still routes review intent" *true*, not just checkable.
- **References to update (✅ grep-verified 2026-07-02; re-grep at execution):**
  - `skills/auto-decision-capture/SKILL.md:370` — "review surface (loaded by `build-loop:knowledge-review`…)" → point at `build-loop:knowledge` review mode.
  - `CLAUDE.md` §Claude Code Integration internal-modes list — "review-knowledge" entry → "knowledge (review mode)".
  - `scripts/test_skill_resolution.py:44-51` — historical comment names knowledge-review; append a note that the skill was folded 2026-07 (comment-only; collision suite behavior unchanged — removing a skill cannot create a collision).
  - `scripts/test_command_surface_policy.py:7` — docstring lists `review-knowledge` as an intent-reached mode; update to `knowledge (review mode)`.
  - `KNOWN-ISSUES.md:110` — append follow-up note ("skill folded into knowledge, 2026-07"); do not rewrite the historical entry.
  - Historical allowlist (leave): `docs/HANDOFF_2026-05-05_repo-episodic-memory.md:146`.
  - Also grep `skills/build-loop/**` §Routing for the intent→mode map at execution time (grep found no hit 2026-07-02, but CLAUDE.md claims the map lives there — trust the fresh grep).
- **Verification:** protocol + `scripts/test_skill_resolution.py` (zero-collision suite), `scripts/test_agent_surface_policy.py` (knowledge stays the entrypoint), capability probe: the review-needing-items procedure is fully present in the new reference file (diff-based content-preservation check: every H2 of the old file appears in the new).
- **Codex checkpoint:** artifact rebuild; confirm `knowledge` entry description still routes review intent.
- **Rollback:** revert single commit.
- **Risk flag:** `safe`.

### Increment 4 — Fold `logging-tracer-bridge` into `logging-tracer` [F-04]

- **dispatch_tier:** `sonnet` — bounded fold, but touches a test contract (see ADR-02).
- **Fold mapping:** `skills/logging-tracer-bridge/SKILL.md` (70 lines) → new §"Extended capability — Coding Debugger escalation" in `skills/logging-tracer/SKILL.md`, **including the `availablePlugins.claudeCodeDebugger` preflight and graceful-degradation contract verbatim** (the inline Tier-1 fallback helpers for Node/Python/Go/Rust move with it — content preserved, not dropped). Delete `skills/logging-tracer-bridge/`.
- **References to update (✅ grep-verified):**
  - `skills/logging-tracer/SKILL.md:165` — `Skill("build-loop:logging-tracer-bridge")` invocation → internal section reference.
  - `.claude-plugin/plugin.json:4` — description names logging-tracer-bridge → rewrite ("logging-tracer includes an optional escalation hop…").
  - `.claude-plugin/marketplace.json:15` — same description string.
  - `scripts/test_bridge_preflight.py:20-28` — **do NOT do a mechanical "5→4" edit (W3):** the current docstring list is already wrong (lists removed `debugger-bridge`+`navgator-bridge`, omits `defenseclaw-bridge`+`ibr-bridge`). Rewrite it to the ACTUAL post-fold bridge set: `prd-bridge, api-registry-bridge, defenseclaw-bridge, ibr-bridge` (logging-tracer-bridge folded away). Fixes pre-existing staleness in the same edit.
  - `docs/scripts/test_bridge_preflight.md:72` — update the narrative that cites logging-tracer-bridge as the caught example (append a "since folded" note).
  - `KNOWN-ISSUES.md:132` — append follow-up note.
  - Historical allowlist (leave): `docs/_inbox/README-proposed-2026-04-22.md:124`, `skills/build-loop/references/refactor-history/**` (3 files, past-run narrative), plugin-artifacts mirrors (regenerate).
- **Test contract change (ADR-02):** `test_bridge_preflight.py` detects bridges by the `*-bridge` name suffix; after the fold, logging-tracer's preflight would silently leave test coverage. Add a small `NON_BRIDGE_PREFLIGHT_SKILLS = {"logging-tracer"}` assertion to the existing test so the graceful-degradation contract stays tested (one named failure class: a bridge/escalation hop without preflight hard-fails when the target plugin is absent). `assertGreater(len(bridges), 0)` still passes with 4 bridges (prd, api-registry, defenseclaw, ibr).
- **Verification:** protocol + `python3 -m unittest scripts/test_bridge_preflight.py` (also in `npm test`), capability probe: preflight patterns present in logging-tracer/SKILL.md (the test now asserts this).
- **Codex checkpoint:** plugin.json/marketplace.json description changes render correctly in the codex artifact.
- **Rollback:** revert single commit.
- **Risk flag:** `needs-review` — extends a test's contract; critic should confirm the added assertion tests the real invariant, not the implementation.

### Increment 5 — Fold `debugging/{memory,store,assess}` into `debugging-memory` [F-05]

Highest conflation win, largest blast radius — runs after the mechanics are proven on increments 2–4.

- **dispatch_tier:** `opus` — cross-file reasoning across ~15 external ref sites, a script scope change, and load-bearing runtime paths; a wrong redirect breaks Review-F/Iterate at runtime.
- **Fold mapping (content preserved as reference files — progressive disclosure; folding 147+153+111 lines into the 440-line SKILL.md body would bloat it):**
  - `skills/debugging/memory/SKILL.md` (name `build-loop:debugging-memory-search`, 147 lines) → `skills/debugging-memory/references/search.md`
  - `skills/debugging/store/SKILL.md` (`build-loop:debugging-store`, 153 lines) → `skills/debugging-memory/references/store.md`
  - `skills/debugging/assess/SKILL.md` (`build-loop:debugging-assess`, 111 lines) → `skills/debugging-memory/references/assess.md`
  - `skills/debugging-memory/SKILL.md` gains an **op-routing interface** (ADR-01): input `{op: "search" | "store" | "assess", ...same fields as before}`, with a short section per op pointing at the reference file. Frontmatter description rewritten (it currently says "delegates the actual lookup to the `build-loop:debugging-memory-search` primitive"). Per-file `source:`/`source_hash:` provenance moves into an HTML comment at the top of each reference file.
  - Delete `skills/debugging/` (tree becomes empty — `debug-loop` lives at `skills/debug-loop/`, untouched).
- **References to update (✅ grep-verified 2026-07-02; the load-bearing set the brief requires preserved is marked ★):**
  - `debugging-memory-search` → `Skill("build-loop:debugging-memory") {op:"search"}`:
    - ★ `agents/api-assessor.md:43`, `agents/frontend-assessor.md:43`, `agents/database-assessor.md:89`, `agents/performance-assessor.md:44`
    - `skills/debugging-memory/SKILL.md:3,19,52,284,397,427` (become internal op references)
    - `skills/debugging-memory/references/subagent-integration.md:58,91,94`
    - `tests/test_capability_registry.py:408-410` — `_plugin_namespace` fixture uses the literal path `skills/debugging/memory/SKILL.md`; replace the fixture with another still-nested path (the `skills/architecture/scan` case at :411-413 already covers the branch — drop or swap the debugging example). **Also (finding 4):** `test_shortlist_memory_audit_preserves_relevance` (:367, asserts ≥7/8 memory-categorized) runs against the LIVE registry and is pool-composition-sensitive — removing `debugging-memory-search`/`debugging-store` from the pool can flip it. Budget a possible threshold/assertion update here, not only the fixture swap; if it flips, that's expected pool-shrink, not a regression — adjust the assertion with a comment.
  - `debugging-store` → `{op:"store"}`:
    - ★ `references/memory-systems.md:217,219` · ★ `references/phase-gate-checklist.md:230` (Review-F storage path)
    - `skills/debugging-memory/SKILL.md:54,285` (internal)
  - `debugging-assess` → `{op:"assess"}`:
    - ★ `references/iterate-protocol.md:42` · ★ `skills/build-loop/references/phase-5-iterate.md:53` (stuck-iteration fan-out)
    - `skills/debug-loop/SKILL.md:220`
    - `skills/debugging-memory/SKILL.md:268,288` (internal)
  - Path/tree references:
    - `scripts/sync_skills.py:36` — `SKILL_TREES = ("skills/architecture", "skills/debugging")` → drop `skills/debugging` (run any colocated sync-skills tests; grep `scripts/` for `test_sync` at execution).
    - `skills/sync-skills/SKILL.md` — remove the three debugging paths from the drift-detector inventory at `:122-124`, AND update the **frontmatter description at `:3` + `:12`** (they name "skills/architecture/ and skills/debugging/" — the LLM selection signal), AND fix the stale `skills/debugging/debug-loop/SKILL.md` mention at `:125`. Note provenance now lives in `skills/debugging-memory/references/*` comments (drift detection vs upstream retired for these three — "native, adapted; no canonical upstream" per their own frontmatter; say so in the commit body).
    - **Path-form refs (W1 — the name-grep is blind to these):** `scripts/sync_skills.py:8` (docstring), `scripts/capability_shortlist.py:148` (comment), `scripts/build_capability_registry.py:20` (comment), `tests/test_bridge_prose_clean.py:7`, `tests/test_capability_registry.py:341` — all cite the literal path `skills/debugging/`. Update comment-level; verify tests don't assert on the path.
    - **T-06 addition:** the grep-zero-stale falsifier for this increment MUST include the literal string `skills/debugging` (not just the three skill *names*) — none of the old names matches the path form, so a name-only grep would pass while stale path refs remain.
    - `scripts/capability_shortlist.py:164` — comment-only example path; update the comment (code is generic, no behavior change).
    - `CLAUDE.md` §Native Architecture & Debugging Skills — `skills/debugging/{memory,store,assess,debug-loop}/SKILL.md` line → rewrite to name `skills/debugging-memory/` (+ fix the pre-existing staleness: debug-loop lives at `skills/debug-loop/`).
  - Historical allowlist (leave): `docs/HANDOFF_*`, past plans/retrospectives; regenerate: architecture inventory, flow-mockup, plugin-artifacts.
- **Verification:** protocol + full deterministic pytest (this increment touches `scripts/` and `tests/`), `tests/test_capability_registry.py`, `scripts/test_agent_surface_policy.py`. Capability probes (T-08): (a) every H2 section of the three old files appears in the corresponding reference file (content-preservation diff); (b) the four ★ runtime paths each resolve — read the updated file and confirm the invocation names an existing skill + op; (c) the strict direct-apply gate and Sonnet-pin-on-assess-fan-out rules survive verbatim.
- **Codex checkpoint:** full artifact rebuild + Codex review of the op-routing interface (a cross-vendor reader must be able to follow `{op:...}` from the four ★ reference docs).
- **Rollback:** revert single commit (largest diff — keep it one logical commit precisely so revert is clean).
- **Risk flag:** `needs-review` — load-bearing runtime redirects; blocking plan-critic + scope-auditor attention here specifically.

### Increment 6 — Rename `loop-builder` → `focused-loop-builder` [F-06]

The only DO rename (§Rename Decisions for the full weighing).

- **dispatch_tier:** `sonnet` — mechanical rename, 1 external ref site.
- **Mapping:** `skills/loop-builder/` → `skills/focused-loop-builder/`; frontmatter `name: focused-loop-builder`; internal path refs `skills/loop-builder/scripts/loop_builder.py` at SKILL.md:40,79,85,91 → new dir path. Python module filename `loop_builder.py` stays (internal, not a selection surface — minimal diff).
- **References to update (✅ grep-verified):** `scripts/test_loop_builder.py:17-18` (SCRIPT/PRESETS paths). Inventory surfaces per protocol.
- **Verification:** protocol + `pytest scripts/test_loop_builder.py`.
- **Rollback:** revert single commit. **Risk flag:** `safe`.

### Increment 7 — Description-differentiation pass (the SKIPPed renames' benefit, cheaply) [F-07]

The research's mis-selection driver is candidate *similarity at selection time* — and the selection signal is the frontmatter description, not the directory name. Every SKIP in §Rename Decisions gets its distinctness benefit here at near-zero blast radius.

- **dispatch_tier:** `sonnet` — bounded rubric edits; description quality is judgment but scoped to one line each.
- **Owned files (frontmatter `description:` lines only):**
  - Remaining 4 bridges (`prd-bridge`, `api-registry-bridge`, `defenseclaw-bridge`, `ibr-bridge`): each gains a "bridges to <plugin> for <job>; NOT for <adjacent job> — use <skill>" clause.
  - `model-bakeoff` ↔ `model-tiering`: mutual cross-pointers ("NOT for choosing a tier — use model-tiering" / "NOT for benchmarking models head-to-head — use model-bakeoff").
  - `root-cause-analysis` ↔ `debug-loop` ↔ `debugging-memory` (post-Inc-5): tighten the existing disambiguation triangle (RCA's description already differentiates well; verify all three cross-point).
- **Constraints:** never remove existing trigger phrases (users' muscle memory + routing tests may depend on them); additive sharpening only. Descriptions in `plugin.json`/`marketplace.json` untouched unless they name a changed skill.
- **Verification:** protocol tests (surface policy re-reads every frontmatter; capability-registry tests exercise description-driven shortlisting) + a read-through critic pass: for each edited pair, a cold reader can pick the right skill from descriptions alone.
- **Rollback:** revert single commit. **Risk flag:** `safe`.

### Increment 8 — Script-pool classification (detector, then human gate) [F-08]

370 scripts; 0 confirmed dead; 160 lack lifecycle headers. This increment produces *evidence*, not deletions.

- **dispatch_tier:** `script` — `scripts/script_relevance.py` is the existing detector (machine-checkable output, enumerable inputs, tool exists); a `sonnet` pass then formats the review packet. Judgment stays with the human gate.
- **Steps:**
  1. Run `scripts/script_relevance.py` over `scripts/` (✅ verified the script exists; ⚠️ untested — confirm its CLI flags by reading it at execution, never guess). **Permission tier: read-only** (no writes to `scripts/`). **Validate/degrade arm (closes reads-from-dependency BLOCKER):** sanity-check the detector's output schema on a sample of rows before building the packet; if the schema can't populate a per-script row, or the detector errors partway through the 370, the affected scripts get a `detector-unknown` disposition (surfaced for human review) rather than blocking the whole packet or being silently dropped.
  2. Produce a review packet: per script — classification, referencing surfaces (hooks.json, package.json files/scripts, workflows, SKILL.md/agent bodies), lifecycle-header status. Store the packet in `build-loop-memory/projects/build-loop/` (report artifacts don't belong in the plugin repo — "repo = plugin code only").
  3. **Human-review gate (hard):** the user marks each candidate `cull | keep+header | keep-as-is`. No increment-9 action without a recorded approval line per script. **Provenance (T-09):** the approval must be **user-authored** — a quoted user message or a user-edited row in the packet — NOT a line the executor writes for itself. An auto-advancing executor may propose dispositions but MUST NOT self-approve a `cull`; absent an explicit user approval, the default disposition is `keep-as-is`.
- **Verification:** T-09 — packet exists, covers all ~370 scripts, and every headerless script (160) has a disposition row. No repo changes in this increment beyond nothing (read-only) — the packet lives in build-loop-memory.
- **Rollback:** N/A (read-only). **Risk flag:** `safe`.

### Increment 9 — Execute approved script actions [F-09]

- **dispatch_tier:** `sonnet` — mechanical batch edits against an approved list; escalate to `opus` only if a cull turns out to have live references (2-failure rule).
- **Steps:** for each *approved* cull: re-verify zero references (grep across hooks/, package.json, workflows, skills/, agents/, scripts/ imports) at execution time, then delete; for each `keep+header`: add the lifecycle header, batched by directory, one commit per batch. Expected cull count is small (0 confirmed dead today) — the primary deliverable is headers + classification durability.
- **Verification:** protocol (full pytest + `npm test` — package.json `files:` and `hooks/hooks.json` reference scripts by path; `scripts/hook_budget_lint.py`/`hook_hygiene_lint.py` run in CI) + `scripts/import_manifest_lint.py`.
- **Rollback:** revert per-batch commits. **Risk flag:** `needs-review` — only because deletions are involved; the human gate in Inc 8 is the primary control.

---

## Rename Decisions (each candidate: DO / SKIP + weighing)

| Candidate | Refs (grep-verified) | Decision | Why |
|---|---|---|---|
| `loop-builder` → `focused-loop-builder` | 1 external file (`scripts/test_loop_builder.py`) + internal paths | **DO** (Inc 6) | Structural name-reversal collision with `build-loop` itself (and `debug-loop`) — exactly the similarity class the research flags; blast radius near zero. New name comes from the skill's own body ("focused-loop specs"). |
| The 5 bridges (rename "by job") | `-bridge` suffix is load-bearing: `test_bridge_preflight.py` detects bridges BY the suffix; refs across plugin.json, CLAUDE.md, docs | **SKIP** | Bridges are already differentiated by target-plugin name (prd/api-registry/defenseclaw/ibr); renaming away from `-bridge` silently removes them from the preflight test's coverage. Benefit captured by Inc 7 descriptions instead. (logging-tracer-bridge is merged away in Inc 4, not renamed.) |
| `root-cause-analysis` | 23 occurrences / 12 files, incl. `agents/fix-critique.md`, `skills/debug-loop/SKILL.md`, `references/root-cause-analysis/` dir | **SKIP** | Moderate blast radius, and its description already carries explicit disambiguation ("Distinct from debug-loop…"). Inc 7 tightens the triangle. |
| `root-cause-investigator` (agent) | 67 occurrences / 30 files | **SKIP** | Blast radius clearly outweighs benefit; it's an agent (orchestrator-dispatched, not description-pool-selected by users). |
| `debug-loop` | Public entrypoint (`CLAUDE_PUBLIC_ENTRYPOINTS`) | **SKIP (hard)** | Renaming breaks the surface test and user muscle memory. |
| `model-bakeoff` | ~0 external (architecture inventory + 1 retrospective mention) | **SKIP rename; sharpen description (Inc 7)** | Rename is free but no better name materially reduces the real confusion axis (shared "model-" prefix with model-tiering, whose semantics already differ strongly); mutual NOT-for cross-pointers target the actual selection signal. [ASSUMED: description edits suffice; if Phase-6 telemetry later shows bakeoff/tiering mis-selection, revisit as a fold of bakeoff into model-tiering.] |
| `self-improvement-architect` (agent) | **75 occurrences / 38 files** | **SKIP** | Exactly the case the brief predicted: 20+ refs (actually far more) outweigh a speculative distinctness gain. |

## Script-cull sub-plan summary

Detector (`script_relevance.py`) → review packet in build-loop-memory → **human approval gate** → batched execution with per-script re-verification (Inc 8–9 above). Never guesswork; never a deletion without a recorded approval + a fresh zero-reference grep.

## F-Criteria (functional)

| ID | Criterion | Pass condition / falsifier | Grader |
|---|---|---|---|
| T-01 | Surface policy intact | `scripts/test_agent_surface_policy.py` green; entrypoint set unchanged | pytest |
| T-02 | Bridge preflight contract | `scripts/test_bridge_preflight.py` green with 4 bridges + new logging-tracer assertion | pytest |
| T-03 | Zero namesake collisions | `scripts/test_skill_resolution.py` green | pytest |
| T-04 | Capability registry coherent | `tests/test_capability_registry.py` green (fixture updated in Inc 5) | pytest |
| T-05 | Full deterministic suite | CI-equivalent pytest command (protocol step 5) exits 0 per increment | CI |
| T-06 | Zero stale references | Per-increment grep of every old name returns only the named historical allowlist | grep, recorded in commit body |
| T-07 | Codex artifact + audit | `npm run codex:build-artifact` succeeds; Codex audit verdict PASS per increment | Codex peer (gating) |
| T-08 | Capability preserved | Per fold: every H2 of the source file present in the target; all ★ runtime paths resolve to `debugging-memory` + valid op | content-diff + read-through |
| T-09 | Script gate honored | Review packet covers all scripts; every Inc-9 action has an approval row | human gate |

## Q-Criteria (quality)

| Criterion | Pass condition | Grader |
|---|---|---|
| Hook lints | `hook_budget_lint.py` + `hook_hygiene_lint.py` exit 0 | CI |
| Methodology drift | `methodology_drift_lint.py --strict` exit 0 (CLAUDE.md edits in Inc 3/5 must not break the four-doc invariant) | CI |
| Import manifest | `import_manifest_lint.py` exit 0 | CI |
| No history rewrites | Historical docs only ever appended to | plan-critic / reviewer |

## ADRs

**ADR-01 — Merged debugging skill uses op-routing, not aliases.** Alternatives: (a) keep 3 thin alias SKILL.md files → pool count unchanged, defeats the goal; (b) fold all text into one 800-line SKILL.md → violates progressive disclosure; (c) **chosen:** one skill, `op: search|store|assess` input, detail in `references/`. Tradeoff: 12+ call sites change shape; rollback: git revert restores the three skills byte-identical.

**ADR-02 — Extend, don't weaken, the bridge-preflight test.** Folding logging-tracer-bridge removes it from suffix-based test coverage; we add a named non-bridge preflight assertion rather than accepting silent coverage loss. Alternative (rely on prose) rejected: the test exists because prose failed once before (`docs/scripts/test_bridge_preflight.md:72`). Rollback: revert.

**ADR-03 — Descriptions-over-renames.** Renames pay their cost in refs, muscle memory, and test contracts; descriptions are the actual LLM selection signal. Renames only where refs ≈ 0 AND the collision is structural (loop-builder). Rollback: trivial.

**ADR-04 — Script cull is detector-then-review.** `script_relevance.py` classifies; a human approves; execution re-verifies. Alternative (LLM judges deadness) rejected: deterministic-first, and 0 confirmed-dead means the prior for "safe to delete" is weak. Rollback: revert per batch.

## Activation Map

Each fold's replacement activation path (`trigger:` = the reactive selection signal that fires it post-fold; `verified-live:` = the increment's falsifier). This consolidates the per-increment "activation wiring" so no folded capability ships dormant.

- **verify-dispatch → build-loop refs** · `trigger:` orchestrator post-dispatch acceptance step + a `skills/build-loop/SKILL.md` pointer · `verified-live:` grep shows ≥2 runtime surfaces referencing the new file; dry-read of the dispatch section reaches the checklist (Inc 2 falsifier).
- **knowledge-review → knowledge (review mode)** · `trigger:` review-intent phrases appended to `skills/knowledge/SKILL.md` frontmatter `description:` (W4 fix) · `verified-live:` a cold reader selects `knowledge` for a "review decisions" ask from the description alone.
- **logging-tracer-bridge → logging-tracer §Extended capability** · `trigger:` `logging-tracer`'s existing "needs more than we ship" branch now points to its own internal section · `verified-live:` `test_bridge_preflight.py` non-bridge assertion (ADR-02) holds.
- **debugging/{memory,store,assess} → debugging-memory `{op}`** · `trigger:` the four ★ assessor agents + phase docs invoke `debugging-memory {op:…}` · `verified-live:` T-08 — each ★ runtime path resolves to an existing skill+op.

## Plan-verify gate note

`plan_verify.py` (deterministic) flags 4 structured-field items after the substantive fixes. The Fable plan-critic adjudicated all four as format-fill/waivable with substance present (see critic verdicts): `activation-map-required` → satisfied by the section above; `reads-from-dependency` → substance handled by Inc 8's validate/degrade arm (`override: reads-from-dependency` — degrade path is explicit: `detector-unknown` disposition on schema/error, never silent-drop or block); `tool-without-permission-tier` → Inc 8 declares read-only permission tier for `script_relevance.py` (the only new tool run); `route-change-evidence` → each increment carries a grep-verified `file:line` reference-update list + a T-06 grep-zero-stale falsifier as the route-change evidence. These are the orchestrator's documented override, backed by the Fable critic's substance verification — not silenced defects.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| A folded skill's *reactive* activation (description-trigger) is lost even though content survives (verify-dispatch is the pure case) | Medium | Explicit activation wiring per fold + T-08 read-through; flagged `needs-review` |
| Peer lands new refs to a folded name between grep and push (main is moving) | Medium | Protocol: re-grep after rebase (step 8), grep output is binding, not this doc's list |
| Op-routing breaks a runtime path (Review-F store, Iterate assess fan-out) | Low-Med | ★-marked refs individually verified (T-08); Inc 5 at `opus`; blocking critic attention |
| Codex-side resolution differs from Claude-side | Low | Codex artifact only exposes the `build-loop` entrypoint (`test_agent_surface_policy.py:19-21`) — internal folds are invisible to Codex hosts; Codex audit per increment confirms |
| `docs/build-loop-flow-mockup.html` / architecture inventory drift (generator status ❓ uncertain) | Medium | Protocol step 4 checks for a generator first; grep-zero-stale (T-06) catches misses either way |
| Description edits (Inc 7) change routing behavior | Low | Additive-only constraint; routing/registry tests in protocol |
| Pool shrink loses a capability nobody notices until needed | Low | Content-preservation diff (every source H2 present in target) is mandatory per fold, not optional |

## Alternatives considered

1. **Big-bang consolidation in one branch** — rejected: violates per-increment rollback, and a moving main makes one giant rebase the riskiest possible shape.
2. **Aliases/stubs instead of true merges** — rejected: pool count (the measured accuracy driver) wouldn't drop; stubs are exactly the deprecation debt the CLAUDE.md KISS principle says to delete.
3. **Rename-heavy differentiation** — rejected for high-ref candidates (ADR-03); descriptions are the selection signal and cost ~nothing.
4. **Merging the root-cause trio** (root-cause-analysis + debug-loop + debugging-memory) — rejected this pass: debug-loop is a public entrypoint and the three have real behavioral differences (fix-now vs postmortem vs memory); conflation risk runs the *other* way. Revisit only with mis-selection telemetry.
5. **LLM-judged script cull** — rejected (ADR-04).

## Open Questions

- **Q-01** (blocking-test: T-09): `script_relevance.py`'s exact CLI contract and output schema were not read during planning. Inc 8 step 1 requires reading the script before running it. Non-escalating — resolvable from the repo at execution time; recorded here so the executor doesn't guess flags.

## Spec Object (JSON)

```json
{
  "needs": [
    {"id": "U-01", "priority": "P0", "statement": "Agents mis-select among build-loop's 50 skills / 28 agents / 370 scripts as pool size and candidate similarity grow; shrink and differentiate the pool without losing capability.", "evidence": "Chroma context-rot 14-85%; MCP tool-count degradation studies (~9.5% avg)."}
  ],
  "features": [
    {"id": "F-02", "priority": "P0", "name": "Fold verify-dispatch into build-loop references + orchestrator wiring", "needs": ["U-01"], "tests": ["T-01", "T-05", "T-06", "T-07", "T-08"]},
    {"id": "F-03", "priority": "P0", "name": "Fold knowledge-review into knowledge (review mode)", "needs": ["U-01"], "tests": ["T-01", "T-03", "T-05", "T-06", "T-07", "T-08"]},
    {"id": "F-04", "priority": "P0", "name": "Fold logging-tracer-bridge into logging-tracer with preflight preserved + test extension", "needs": ["U-01"], "tests": ["T-02", "T-05", "T-06", "T-07", "T-08"], "adr": "ADR-02"},
    {"id": "F-05", "priority": "P0", "name": "Fold debugging/{memory,store,assess} into debugging-memory with op-routing", "needs": ["U-01"], "tests": ["T-01", "T-04", "T-05", "T-06", "T-07", "T-08"], "adr": "ADR-01"},
    {"id": "F-06", "priority": "P1", "name": "Rename loop-builder to focused-loop-builder", "needs": ["U-01"], "tests": ["T-05", "T-06"]},
    {"id": "F-07", "priority": "P1", "name": "Description-differentiation pass (bridges, model-*, root-cause triangle)", "needs": ["U-01"], "tests": ["T-01", "T-04", "T-05"], "adr": "ADR-03"},
    {"id": "F-08", "priority": "P1", "name": "Script relevance classification + human review gate", "needs": ["U-01"], "tests": ["T-09"], "adr": "ADR-04"},
    {"id": "F-09", "priority": "P2", "name": "Execute approved script culls + lifecycle headers", "needs": ["U-01"], "tests": ["T-05", "T-09"], "depends_on": ["F-08"]}
  ],
  "tests": [
    {"id": "T-01", "check": "test_agent_surface_policy.py green; entrypoint set unchanged"},
    {"id": "T-02", "check": "test_bridge_preflight.py green with 4 bridges + logging-tracer non-bridge preflight assertion"},
    {"id": "T-03", "check": "test_skill_resolution.py zero collisions"},
    {"id": "T-04", "check": "tests/test_capability_registry.py green (fixture updated)"},
    {"id": "T-05", "check": "CI-equivalent deterministic pytest + npm test exit 0 per increment"},
    {"id": "T-06", "check": "grep of each folded/renamed name returns only the named historical allowlist"},
    {"id": "T-07", "check": "codex artifact rebuild succeeds; Codex audit verdict PASS per increment"},
    {"id": "T-08", "check": "content-preservation diff per fold (every source H2 in target) + all starred runtime refs resolve"},
    {"id": "T-09", "check": "script review packet complete; every Inc-9 action has a recorded human approval"}
  ],
  "adrs": ["ADR-01", "ADR-02", "ADR-03", "ADR-04"],
  "increments_order": ["F-02", "F-03", "F-04", "F-05", "F-06", "F-07", "F-08", "F-09"]
}
```

## Out of Scope (mirror)

Agent merges; entrypoint renames; new capabilities/hooks/commands; hand-edits to `plugin-artifacts/codex/**`; history rewrites; judgment-only script deletion; security triad / ui-design / architecture skills / auto-finding-capture / debug-loop.

---

## Closeout — 2026-07-02

**Increments 2–7: SHIPPED** to `origin/main` (`8451704..f41c22c`, 7 clean revertable commits, FF). **Skill pool 50 → 44 (−6)**, capability-preserving. Backlog of record: `build-loop-memory/projects/build-loop/backlog.md` → `bl-pool-consolidation-inc2-9` (done).

Per-fold verification held: content-preservation (every source H2 present in target), T-06 grep-zero-stale (name **and** path form), activation falsifiers (≥2 runtime surfaces per fold), and ★ runtime-path resolution for the debugging op-routing. Prerequisite baseline fix landed first (`9fcd9d6`, route-guard categorizer — the suite was red on `main` before any fold). Full suite **3571 passed**; the only red is the pre-existing `test_discovery_bridge` rally binary-drift (codex ARP lane, not introduced here). Codex T-07 cross-vendor audit requested async via rally (batch-land per user directive).

**Increments 8–9 (script cull): ASSESSED & SKIPPED.** The read-only detector `scripts/script_relevance.py` reports **0 dead / 0 attic** across 186 scripts; the 160 "review" verdicts are all "no authored capability header" on clearly-active code. The cull deliverable (Inc 9) is empty, and scripts sit at the lowest weight (1) in the capability shortlist — outranked and collapsed by skills > agents > commands — so they are not the LLM selection-accuracy driver the plan targets (that driver is the skill/agent description surface, addressed by Inc 2–7). The residual value (lifecycle-header hygiene → registry classification durability) is split to backlog item **`bl-script-lifecycle-headers`** (SAFE/M, low priority), independent and risk-free to defer.

**Q-01 resolved:** `script_relevance.py` CLI is `--workdir` / `--stale-days <N=120>` / `--json`; verdicts `keep | review | attic`.
