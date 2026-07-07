<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Modular Systems Pack

Use this pack on every non-trivial build. It makes modular, scalable structure the default while allowing a simpler or more integrated approach when that better serves the use case.

## Default

Build-loop should prefer systems that are:

- **Modular**: each module hides one important design decision behind a stable interface.
- **Scalable**: the design can grow in data volume, user volume, feature count, or team ownership without immediate redesign.
- **MECE**: task groups, file ownership, and agent scopes are mutually exclusive and collectively exhaustive.
- **Pyramid-structured**: plans, handoffs, reports, and repo organization lead with the governing thought, then supporting groups, then details.

This is a default, not dogma. The goal is durable user value, not extra architecture.

## Exception Rule

Do not add modularity for its own sake. Choose a simpler or more integrated approach when:

- The change is a one-off script, short-lived migration, or isolated fix.
- A new boundary would add indirection without reducing real complexity.
- A performance hot path needs a tightly integrated implementation.
- The repo is small and the added module structure would obscure the core workflow.
- The product need is intentionally limited and extra optionality would confuse users.

When taking an exception, record:

```text
MODULARITY EXCEPTION: <why simpler/integrated is better for this use case>
```

## Module Shape Is a Cost Lever, Not an Accuracy Gate

Module-shape guidance — **narrow public interface + small, well-named internal files + a testable boundary per capability** — is a **cost** lever, not a correctness gate. Apply it proportionally; **never make it build-blocking.**

A controlled minimal-pair study (matched repos differing only in cleanliness) measured the payoff of cleaner, better-shaped code as roughly **−34% agent file-revisitation/thrash and −7–8% tokens**, with **~0 change in task pass-rate** (91.3% clean vs 92.1% messy). So better module shape makes an agent navigate a codebase *cheaper*, not *more correct*. The "deep module" tension is also illusory: deep constrains *interface width* (fewer symbols the model must hold in context) while "small files" constrains *token load per read* — orthogonal, and the best-supported synthesis is a deep module implemented across small internal files, which modern sub-agent tooling (external refs + exploration summaries) already delivers without fat single files.

Because the win is cost and the accuracy delta is unproven, treat this as guidance the Critic can *surface*, not a gate that can *block*. Do not mandate refactors for module depth; flag avoidable thrash, but ship on correctness. Source: `build-loop-memory/research/2026-07-06-ai-coding-fundamentals-and-harness-claims.md` (Claims 1 & 2).

## MECE File And Agent Partition

Phase 2 Plan must partition work so every changed file has exactly one owner and every required responsibility has an owner.

For each task group, use this packet:

```md
Group: <name>
Dimension: <domain | layer | workflow | bounded context | adapter | test surface>
Owns files: <paths>
Does not own: <paths handled elsewhere>
Interface contract: <exports/events/API/schema it may change>
Integration checkpoint: <command or review step>
Intent link: <north-star or user-value rule this group supports>
```

MECE checks:

- No overlap: a file or interface is not owned by two agents unless the plan defines a handoff point.
- No gaps: every required behavior, state, migration, test, and user-facing surface has an owner.
- One dimension per level: avoid mixing domains, layers, and workflows in the same grouping level.
- Stable interfaces: agents can change internals, but cross-group contracts are explicit.
- Integration checkpoint: every boundary has a test, build, visual check, schema check, or reviewer step.

## Modular Design Heuristics

- Hide volatile decisions behind small interfaces: data shape, provider choice, algorithm, storage, rendering strategy, or external API.
- Prefer high cohesion: code that changes for the same reason lives together.
- Prefer loose coupling: callers depend on published interfaces, not internal data structures or side effects.
- Design around business/domain capabilities when the system is large enough for domains to matter.
- Keep boundaries small enough to understand and large enough to own a useful capability.
- Separate deploy/runtime config from code when values vary by environment.
- Preserve directness when extra layers make the core workflow harder to read, test, or operate.

## Pyramid Structure

Use pyramid structure for plans, reports, repo notes, and agent handoffs:

1. Governing thought: the one decision, result, or recommendation.
2. MECE key lines: 3-5 non-overlapping supporting claims or work groups.
3. Evidence/details: commands, files, risks, interfaces, and validation.

For repo structure, this means names should communicate purpose, folders should group by one clear dimension, and cross-cutting utilities should stay genuinely shared rather than becoming a junk drawer.

## Review Gates

Critic and final review should flag:

- Avoidable tight coupling or weak cohesion.
- Hidden cross-file ownership overlap between agents.
- Missing owner for a required behavior, state, migration, test, or user-facing surface.
- Abstraction added without user, scalability, testability, security, or maintainability benefit.
- Simplification that collapses a boundary needed for accuracy, security, scale, testability, or future optionality.
- Missing `MODULARITY EXCEPTION` when the plan intentionally chooses an integrated shortcut.

## Source Basis

- Parnas, "On the Criteria to Be Used in Decomposing Systems into Modules" (CACM, 1972): https://cacm.acm.org/research/on-the-criteria-to-be-used-in-decomposing-systems-into-modules/
- AWS Well-Architected REL04-BP02, "Implement loosely coupled dependencies": https://docs.aws.amazon.com/wellarchitected/2024-06-27/framework/rel_prevent_interaction_failure_loosely_coupled_system.html
- Microsoft Azure Architecture Center, domain analysis for microservices: https://learn.microsoft.com/en-us/azure/architecture/microservices/model/domain-analysis
- Microsoft Azure Architecture Center, design principles for Azure applications: https://learn.microsoft.com/en-gb/azure/architecture/guide/design-principles/
- Twelve-Factor App config guidance: https://12factor.net/config
- MECE framework overview: https://www.casestar.io/guides/mece
