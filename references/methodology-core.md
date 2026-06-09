<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
# Methodology Core — canonical single-source

This file is the **single source of truth** for the small set of methodology
facts that previously drifted across `CLAUDE.md`, `AGENTS.md`,
`skills/build-loop/SKILL.md`, and `agents/build-orchestrator.md`. Each of those
four files restates these facts for its own audience and format; this file
defines what they must agree on.

`scripts/methodology_drift_lint.py` (wired into `.github/workflows/pytest.yml`)
checks that every satellite states these invariants identically. It does NOT
require byte-identical prose — the four files keep their own framing — it
requires the **canonical phrasings below to appear verbatim** in each satellite
that covers that fact. Update a fact HERE, then run the lint; it names every
satellite that still carries the old phrasing.

Why a phrase-presence lint and not a generated include block: the four files
have genuinely different formats (a plugin README, an open-standard spec, a
skill router, an agent prompt). A generated block would force one format on all
four. The drift that actually bit us was semantic — the Review sub-step
sequence disagreed across files (one dropped Auto-Resolve, one dropped Optimize)
— so the guard targets the load-bearing phrasings, the cheapest mechanism that
catches the observed failure (KISS).

## Scope of the lint (KISS — guard the evidenced drift, not every fact)

The 2026-06-09 audit found exactly ONE semantic disagreement across the four
files: the Review sub-step sequence. CLAUDE.md and build-orchestrator.md dropped
Auto-Resolve; SKILL.md's expanded form dropped Optimize. That is the failure the
lint enforces (INV-REVIEW-SUBSTEPS).

The other methodology facts below are documented here for single-source
reference, but are NOT phrase-linted: they legitimately appear in different
formats per file (AGENTS.md renders the phases as a table; SKILL.md as lowercase
prose), and they did not drift semantically. A phrase-presence lint over them
would be rigidity masquerading as safety — it would fail correct, intentional
format variation. If one of them drifts in a future audit, promote it to an
enforced invariant then (add a `Canonical phrase` blockquote under its heading).

## Canonical invariants

### INV-REVIEW-SUBSTEPS  (ENFORCED)
Review (Phase 4) has seven ordered sub-steps with a single exit point. The
canonical sequence is:

```
Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Auto-Resolve → Report
```

Lettered A–G in the orchestrator: A=Critic, B=Validate, C=Optimize, D=Fact-Check,
E=Simplify, F=Auto-Resolve, G=Report.

Canonical phrase (must appear verbatim in every satellite that lists the sub-steps):

> Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Auto-Resolve → Report

## Documented (not phrase-linted) facts

- **Phase sequence**: `Assess → Plan → Execute → Review → Iterate`, plus a
  mandatory Phase 6 Learn that always runs after Review-G.
- **Phase count headline**: "5-phase development loop with a mandatory Phase 6
  Learn" — five-plus-mandatory-sixth, never "6-phase" as the headline count.
- **Iterate cap**: loops back to Review on failure, capped at 5 (classic) / 25
  (autonomous); overflow → `.build-loop/followup/`.

## Maintenance
1. Change a fact here first.
2. Run `python3 scripts/methodology_drift_lint.py --json` (or just `--strict` in CI).
3. The lint prints every satellite + line that still carries a stale phrasing.
4. Fix each named satellite. Re-run until clean.

A NEW invariant is added here ONLY when a real drift is observed across the four
files (KISS+DRY: the guard exists for evidenced drift, not hypothetical drift).
