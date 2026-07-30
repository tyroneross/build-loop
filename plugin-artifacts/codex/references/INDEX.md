<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# References Index

Navigation map for `references/`. These are **on-demand orchestrator/agent reference docs** — load the one the current phase needs, not the whole directory. Each row: doc → what it answers → who loads it. Find your phase or concern below, then open only that file.

This index is descriptive, not authoritative: it points at canonical docs, it does not restate their rules. When a doc and this index disagree, the doc wins.

## By build phase

| Doc | Answers | Primary caller |
|---|---|---|
| [phase-gate-checklist.md](phase-gate-checklist.md) | Phase 1 Assess full protocol + Phase 4 Review sub-steps A–G routing checklist | `agents/build-orchestrator.md` |
| [trigger-rules.md](trigger-rules.md) | How to set sub-routers (`uiTarget`, `platform`, `migrationSource`) + triggers in Phase 1 | `agents/build-orchestrator.md` |
| [memory-systems.md](memory-systems.md) | Memory read protocol (Phase 1 Assess) + write protocol (Phase 4 Review-G); backend degradation | `agents/build-orchestrator.md`, `phase-1-assess.md` |
| [research-trigger-policy.md](research-trigger-policy.md) | When Research plugin work is required; t-shirt depth lower bounds; final-claim citation gate | `agents/build-orchestrator.md`, `phase-1-assess.md`, `phase-2-plan.md` |
| [task-capture-policy.md](task-capture-policy.md) | Canonical active task view from existing state/queue/backlog surfaces; no new ledger by default | `agents/build-orchestrator.md`, `phase-1-assess.md` |
| [capability-routing.md](capability-routing.md) | Which capability/skill a phase routes to; trigger-driven routing | `agents/build-orchestrator.md`, `SKILL.md` |
| [plan-template-ids.md](plan-template-ids.md) | The `T-N` task-ID convention plans use | `agents/implementer.md` |
| [implementer-brief-template.md](implementer-brief-template.md) | Structure of the Phase 3 implementer dispatch brief (pre-Execute checklist) | `agents/build-orchestrator.md` |
| [implementer-envelope-schema.md](implementer-envelope-schema.md) | Canonical implementer return-envelope contract; `status` enum; `decision_ledger`; `novel_decisions[]` | `agents/implementer.md`, `agents/synthesis-critic.md` |
| [single-writer-commit-protocol.md](single-writer-commit-protocol.md) | Phase 3 commit step — orchestrator owns `.git/` as single writer | `agents/build-orchestrator.md` |
| [dogfood-reload-checkpoint.md](dogfood-reload-checkpoint.md) | Stop/reload/resume checkpoint for self-recursive runtime-changing stages | `agents/build-orchestrator.md`, `phase-3-execute.md` |
| [ui-spotcheck-protocol.md](ui-spotcheck-protocol.md) | Phase 3 chunk-close UI spot-check (`uiTouched` routing) | `skills/.../ui-guidance-sources.md` |
| [runtime-smoke-triggers.md](runtime-smoke-triggers.md) | When the Phase 4 Review-B runtime smoke gate fires | `phase-4-review.md` |
| [autonomy-config.md](autonomy-config.md) | Autonomy gate config consumed by Phase 4 Auto-Resolve | `phase-4-review.md` |
| [iterate-protocol.md](iterate-protocol.md) | Phase 5 Iterate — diagnosis cascade, work list, fan-out, autonomous loop | `agents/build-orchestrator.md` |
| [learn-protocol.md](learn-protocol.md) | Phase 6 Learn — pattern detection, experiment drafting, promotion signoff | `agents/build-orchestrator.md` |
| [push-readiness-checklist.md](push-readiness-checklist.md) | Advisory push recommendation checklist: policy, Rally state, dirt, validation, accuracy, architecture, efficiency | Closeout/reporting agents |
| [npm-package-publishing.md](npm-package-publishing.md) | npmjs Trusted Publisher/OIDC provenance standard, pack/publish verification, GitHub Packages separation | Package/release implementers |

## Decision & escalation handling

| Doc | Answers | Primary caller |
|---|---|---|
| [do-branch-surface-policy.md](do-branch-surface-policy.md) | SAFE / RISKY / DECISION / PRODUCTION classification; do/branch/surface mechanics; the six always-escalate exceptions | `agents/build-orchestrator.md` |
| [halt-and-ask-protocol.md](halt-and-ask-protocol.md) | Mode-aware decision handler (auto-pick in long-mode, surface trade-offs in normal-mode); C5 architectural-decision backstop; UI spot-check routing | `agents/build-orchestrator.md`, `agents/design-contract-specialist.md` |
| [agent-role-taxonomy.md](agent-role-taxonomy.md) | Lead vs peer vs coder/implementer vs assessor/reviewer vs skill responsibilities; when to add a new agent | `agents/build-orchestrator.md`, `SKILL.md` |
| [model-tier-mapping.md](model-tier-mapping.md) | Multi-provider tier substitution table + swap recipes; dual-mode A/B test design. Tier defaults live in the repo `CLAUDE.md` Model Tiering table; this file is the substitution detail | `CLAUDE.md` |
| [m-series-protocol.md](m-series-protocol.md) | M1 envelope persist, M2 heartbeat, M3 cost-ledger; crash-recovery snapshots | `agents/build-orchestrator.md` |
| [resume-protocol.md](resume-protocol.md) | §0 crash-recovery flow when a build is re-dispatched mid-Execute | `agents/build-orchestrator.md` |

## Agent/domain guidance

| Doc | Answers | Primary caller |
|---|---|---|
| [database-agent-constitution.md](database-agent-constitution.md) | SQLite-style database/vector/retrieval agent guidance: invariants, shared primitives, durability, failure modes, and memory-promotion path | `agents/database-assessor.md`, database/retrieval implementer briefs |

## Multi-session coordination

Three docs with distinct audiences — they are NOT duplicates. The split is registered in `scripts/check_cache_sync.py` (`COORDINATION_EXACT_REFS`) and `scripts/rally_point/plugin_boundary.json`, and the integration-map rationale lives in `skills/build-loop/references/coordination.md`.

| Doc | Answers | Audience |
|---|---|---|
| [coordination-rules.md](coordination-rules.md) | **Binding constitution** — verdict gating, `post()` mandate, MECE packets, trust model, peer-liveness, release-surface verification, Phase D closeout | Any participant: Claude, Codex, CI, peer session |
| [rally-point-protocol.md](rally-point-protocol.md) | Presence/phase **write protocol** — when the orchestrator writes, channel/slug resolution, reading & surfacing the checkpoint envelope, script-first checks | Anyone writing to the channel |
| [multi-session-coordination.md](multi-session-coordination.md) | **Orchestrator integration points** — Rally Point presence steps at each phase + M5 memory-index trigger family | build-loop orchestrator |
| [coordination-file-template.md](coordination-file-template.md) | Canonical starting **shape** for a per-run `.build-loop/coordination/<topic>.md` (placeholders, mandatory sections, parser-compatible verdict headings) | Coord-file bootstrap (`scripts/coordination_bootstrap.py`) |

## When to add a doc here

Add a row when you add a `references/*.md` file. Keep rows one line; put detail in the target doc, not here. Per the repo KISS+DRY principle, prefer extending an existing reference doc over adding a new one.
