# Plan: Orchestrator Line-Budget Refactor

<!-- checklist
Item 1 — Auth guard: N/A: doc refactor, no runtime auth surface touched
Item 2 — External APIs: N/A: no external API calls added or removed
Item 3 — Rate-limit criterion: N/A: no paid APIs in scope
Item 4 — Discoverability: N/A: doc-only change, no user-facing UI
Item 5 — Server/client boundary: N/A: no server/client modules touched
Item 6 — Concurrency: N/A: no shared-state writes
Item 7 — Observability: N/A: doc refactor, structuredLog calls unchanged
Item 8 — Input validation: N/A: no user input handling added
Item 9 — Stable ID traceability: U-01 → F-01 → D-01 → T-01 chain locked in §Spec Object (JSON)
Item 10 — JSON spec object: ## Spec Object (JSON) section present below
Item 11 — Blocking-and-novel question gate: 2 open questions, both non-blocking → captured as Assumptions in §Open Questions
Item 12 — Low-reversibility ADRs: N/A: this is a low-stakes doc refactor; 2 reversible decisions captured as inline notes (no separate ADRs)
Item 13 — Analytical lens: DSM — section-by-section dependency map between agent file and references/* targets, used to choose split boundaries
Item 14 — Handoff document: 2026-05-09-orchestrator-line-budget-refactor.handoff.md generated alongside this plan
Item 15 — Synthesis dimensions: N/A: no UI files in scope (agent definition + reference markdown only)
Item 16 — Risk reason: N/A: no high-consequence boundary; reversible doc edits with structural test as the safety net
-->

## Goal

Bring `agents/build-orchestrator.md` from 376 lines back under the 200-line budget enforced by `tests/test_orchestrator_skeleton.py::test_orchestrator_under_line_budget`, while preserving every structural invariant the test suite already locks (6 phase headers, ≥5 architecture-scout dispatches, 6 required reference links, plan-verify wiring, capability-registry wiring, IBR mention, deployment-policy block).

## Locked Decisions

Analytical lens: DSM — produced by mapping each agent-file section to its current line count and existing-or-needed `references/*.md` target. Largest dependency-free blocks lift first.

| Decision | Type | Note |
|----------|------|------|
| Move "Phase 3 halt-and-ask branch" (59 lines) to `references/halt-and-ask-protocol.md` | reversible | Largest single block, fully self-contained procedure |
| Move "Phase 3 commit step" (31 lines) to `references/single-writer-commit-protocol.md` | reversible | Already named ("single-writer git contract") — natural reference |
| Compress "Phase 1 Assess" body to bullet list, push 18-step detail into existing `references/phase-gate-checklist.md` | reversible | Reference file already exists and is the canonical detail home |
| Compress "Model Tiering & Escalation" to a 5-line summary linking `references/model-tier-mapping.md` | reversible | Reference already exists; agent body duplicates table |
| Compress "Deployment Policy" body, link `references/runtime-smoke-triggers.md` and policy doc | reversible | Detail belongs in references; agent keeps the gate signal |
| Keep §0 Resume Mode + §0a Per-commit dispatch as inline pointers (already short) | — | Each is ≤4 lines pointing at protocol docs |

Trace: U-01 (CI guardrail violation: 376 > 200 line cap) → F-01 (compressed orchestrator + 2 new reference files) → D-01 (agent file ≤200 lines, references/halt-and-ask-protocol.md, references/single-writer-commit-protocol.md, references/phase-gate-checklist.md updated) → T-01 (`uv run pytest tests/test_orchestrator_skeleton.py -q` exits 0)

## Scope

In scope:
- `agents/build-orchestrator.md` — compress to ≤200 lines
- `references/halt-and-ask-protocol.md` — NEW, lifts Phase 3 halt-and-ask body verbatim
- `references/single-writer-commit-protocol.md` — NEW, lifts Phase 3 commit step body verbatim
- `references/phase-gate-checklist.md` — EXTEND with the Phase 1 Assess 18-step detail (currently in agent inline)
- `tests/test_orchestrator_skeleton.py` — verify all existing invariants still pass; add 2 new `REQUIRED_REFERENCES` entries for the new reference files
- `docs/plans/2026-05-09-orchestrator-line-budget-refactor.handoff.md` — sibling handoff per item 14

### Out of scope

- Behavior changes to any phase
- Renaming any phase, section, or wiring point
- Touching `skills/build-loop/SKILL.md`, `scripts/`, or any consumer project
- The 3 environment/flaky perf tests (`test_recall_skip_postgres`, `test_rerank_daemon`, `test_wiki_local`) — separate concern

## Spec Object (JSON)

```json
{
  "needs": [
    {"id": "U-01", "description": "CI line-budget gate failing on agent file at 376 lines vs 200 cap", "priority": "P1"}
  ],
  "features": [
    {"id": "F-01", "need_ids": ["U-01"], "description": "Compress orchestrator to ≤200 lines by lifting two large self-contained protocol blocks into new references/* files and compressing two existing-reference duplications"}
  ],
  "data_points": [
    {"id": "D-01", "feature_ids": ["F-01"], "description": "agents/build-orchestrator.md line count, content of new references/halt-and-ask-protocol.md and references/single-writer-commit-protocol.md, extension of references/phase-gate-checklist.md"}
  ],
  "tests": [
    {"id": "T-01", "feature_ids": ["F-01"], "description": "tests/test_orchestrator_skeleton.py exits 0 — line budget + all 6 phase headers + ≥5 architecture-scout dispatches + 8 required references linked + plan-verify/capability-registry/IBR/deployment-policy wiring all preserved"}
  ],
  "adrs": [
    {"id": "A-01", "decision": "Lift halt-and-ask + single-writer-commit to new references; compress existing-reference duplication", "alternatives": ["inline summary tables only", "split agent into two agents (orchestrator + executor)"], "rollback": "git revert single commit; reference files are additive"}
  ]
}
```

## Six-Commit Table

| # | Commit subject | Files owned | Depends on |
|---|----------------|-------------|------------|
| 1 | refs(orchestrator): extract Phase 3 halt-and-ask + single-writer-commit protocols | `references/halt-and-ask-protocol.md` (new), `references/single-writer-commit-protocol.md` (new) | — |
| 2 | refs(orchestrator): extend phase-gate-checklist with Phase 1 Assess detail | `references/phase-gate-checklist.md` | 1 |
| 3 | refactor(orchestrator): compress to ≤200 lines, link extracted references | `agents/build-orchestrator.md` | 1, 2 |
| 4 | test(orchestrator): add new references to REQUIRED_REFERENCES, verify line budget | `tests/test_orchestrator_skeleton.py` | 3 |

(Four commits, not six — naming the table by convention.)

## ADR-001: Lift halt-and-ask + single-writer-commit to references vs. inline summary

Context: Two Phase 3 sub-protocols (halt-and-ask at 59 lines, single-writer commit at 31 lines) account for 90 of the 176 lines we need to remove. Both are self-contained procedures with their own decision-tree logic; users following them rarely need to context-switch back to the agent file mid-procedure.

Alternatives:
- **Inline summary tables only**: keeps agent in one place, but loses the decision-tree richness that makes both protocols correct under fan-out conditions. Past fan-out commit-race incidents (memory `feedback_buildloop_parallel_commit_race.md`) suggest the detail is load-bearing.
- **Split agent into two agents (orchestrator + executor)**: bigger change, breaks Mode B inline dispatch from `Agent(subagent_type="build-loop:build-orchestrator")` calls.

Decision: lift both into `references/*.md` files. Agent body keeps the trigger condition + a 1-line link, full procedure lives in the reference. Both are reversible (cat/git mv). Rollback: `git revert` the extraction commit; both reference files become orphans, agent reverts inline.

## F-Criteria (functional)

| Criterion | Pass condition | Grader |
|-----------|---------------|--------|
| Line budget | `tests/test_orchestrator_skeleton.py::test_orchestrator_under_line_budget` exits 0 | pytest |
| Phase headers preserved | `test_all_six_phase_headers_present` exits 0 | pytest |
| Architecture-scout dispatches preserved | `test_at_least_5_architecture_scout_dispatches` exits 0 (≥5) | pytest |
| All required references linked from agent | `test_orchestrator_references_each_file` (×8 entries after extending list) exits 0 | pytest |
| New reference files non-empty | `test_reference_file_exists` (×8) exits 0 (each ≥200 chars) | pytest |
| plan-verify + plan-critic + capability-registry + IBR + deployment-policy wiring preserved | `test_plan_verify_gate_present`, `test_capability_registry_wired`, `test_ibr_quickpass_present`, `test_deployment_policy_block_present` exit 0 | pytest |

## Q-Criteria (quality)

| Criterion | Pass condition | Grader |
|-----------|---------------|--------|
| Full pytest suite | No NEW failures vs. baseline (838-ish passed, 4 known pre-existing flaky/perf) | pytest |
| Plan-verify | `python3 scripts/plan_verify.py docs/plans/2026-05-09-orchestrator-line-budget-refactor.md --json` returns 0 BLOCKER | plan_verify.py |
| No prose drift in extracted procedures | Lifted text in references/* matches the previous inline text byte-for-byte except for added title + 1-line back-pointer | manual diff |

## Risks

- **Line-budget margin too tight after refactor.** If compression lands at 198 lines, future small additions reopen the failure. Mitigation: target 180-line headroom, not exactly 200.
- **Concurrent-session interference.** Per `~/.claude/projects/-Users-tyroneross/memory/feedback_buildloop_parallel_commit_race.md`, parallel work on this repo can absorb edits silently. Mitigation: run on a feature branch, push promptly, watch for divergence in `git log` before each commit.
- **Reference-file proliferation.** Adding 2 more reference files brings the count to 14 total. If split granularity is wrong, future readers context-switch more often. Mitigation: 2 new files chosen because each is a single self-contained protocol with a distinct trigger condition; not arbitrary subsections.

## Open Questions

1. **Should the line budget be raised to e.g. 220 instead of refactoring?** *Assumption*: no — the budget exists because the agent file's job is to be a contract surface, not a textbook. If two more protocols emerge in the future they should also extract.
2. **Should `references/halt-and-ask-protocol.md` and `references/single-writer-commit-protocol.md` use the same naming convention as existing references (`<topic>-protocol.md` vs `<topic>.md`)?** *Assumption*: yes — `iterate-protocol.md`, `resume-protocol.md`, `learn-protocol.md` are the precedent. Both new files follow `<topic>-protocol.md`.

Both questions are non-blocking — proceeding with the assumptions documented.

## Out of Scope

Behavior changes, agent renames, plugin SKILL.md edits, consumer-project edits, perf-test fixes, line-budget threshold tuning.
