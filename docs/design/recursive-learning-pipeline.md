# Recursive-learning capture→action pipeline — design + merge brief

**Status:** draft, in-flight across several branches. **Author context:** built across a
single interactive session (2026-06-17/18). **Audience:** the next agent or human who
merges this — possibly reconciling parallel work by other agents. Read §"Seams &
overlaps" and §"Branch map" before merging anything.

## Why this exists
Goal: pattern-matching → auto-update agents/skills → eventually automate workflows.
Driving evidence (this session): a filed lesson does **not** get read — the Learn
phase's lesson retrieval is goal-keyword-ranked, and for a feature run it returned
**zero** relevant lessons (measured). So a learning must become an **action** (a draft,
or an enforced check), not a memo that depends on an agent recalling it.

## The pipeline (capture → route → produce)
1. **Capture** — the `recursive-retrospective` skill emits `.build-loop/learning-objects.json`:
   a list, each object tagged `encoding_target` (skill | agent | memory | eval | gate |
   preflight | approval | project_note | do_not_encode) + `encode` (yes|no|needs_approval).
2. **Route** — `scripts/learning_to_draft.py`:
   - `skill`/`agent` + `encode:yes` → drafter pattern-proposals.
   - `eval`/`gate`/`preflight`/`approval` + `encode:yes` → **Prevention-Pattern enforcement
     specs** (condition → required behavior → lever → actuator → verifying artifact).
   - `memory`/`project_note`/`do_not_encode` → routed elsewhere / skipped (reported).
3. **Produce**
   - skill/agent → `self-improvement-architect` drafts → `experimental/` → human promote. **(EXISTING.)**
   - gate/eval → `scripts/gate_builder.py` scaffolds a DRAFT check + test into a pending
     area → human approve. **(WIRED — move C.)** The Learn protocol now pipes
     `learning_to_draft.py` output into `gate_builder.py` automatically
     (`references/learn-protocol.md` step 2 source-(c)); live gate activation stays human-gated.

## Design decisions + rationale
- **Prevention Pattern is the contract** between route and produce. It carries everything a
  check needs (condition, behavior, lever, actuator, verifying artifact), and it's
  machine-routable. Keep its field names stable across `learning_to_draft.py` and
  `gate_builder.py`.
- **The gate-builder SCAFFOLDS; it does NOT auto-generate check logic.** Rationale: turning a
  natural-language spec into correct executable assertions is unreliable, and a *wrong* gate
  (false positive) blocks all work — strictly worse than no gate. So it produces the gate
  shape + the spec + a test harness + an approval requirement, and leaves the actual
  assertion body as an explicit `NotImplementedError` for a human (or a follow-up drafting
  agent) to fill. The check is **inert until its body is written and it's approved**.
- **Human-approve to activate (pending → active).** Matches the `extensions` pending/approve
  model. An autonomous path that writes *enforcing* gates is old power via a new path —
  it must stay gated. Also aligns with the standing rule "deterministic gates only for
  evidenced risks": the scaffold exists, but a human confirms the risk is real and the gate
  is correct before it can block anything.
- **Verification-first activation.** A scaffolded gate's regression test MUST demonstrably
  fail on the prior behavior before the gate is allowed to activate (the "old fails / new
  passes" rule). This is encoded as a TODO in the test stub, enforced at approval time.
- **Nothing in this pipeline auto-enforces or auto-installs.** Capture and route are
  automatic; both producers land in a pending/experimental area that a human promotes.

## Boundaries (what this explicitly does NOT do)
- Does not write working check logic automatically (only the shape + spec + stub).
- Does not wire any gate into the live verify step.
- Does not auto-approve, auto-activate, or auto-install.
- Does not replace `self-improvement-architect` (that's the skill/agent producer; this is its
  gate sibling).

## Seams & overlaps a merge agent MUST reconcile
- **`self-improvement-architect`** (on main) — drafts skills/agents. `gate_builder.py` is its
  sibling for gate/eval targets. The converter's `encoding_target` decides which producer
  runs. Do not let both draft the same finding.
- **`enforce_retro_signals.py` + `.build-loop/proposals/enforce-from-retro/`** (on main) — a
  pre-existing retro→detector→drafter path that is **recurrence-gated (≥2 run-ids)** and not
  encoding-target-aware. `learning_to_draft.py` is the **encoding-target-aware,
  non-recurrence-gated** path. Both feed the Learn drafter. **Merge action:** keep both, but
  dedupe — the converter names drafts `experimental-<kebab-title>`; ensure the detector's
  dedupe step (`learn-protocol.md` step 3) also dedupes converter output so one finding
  doesn't draft twice.
- **`extensions` pending/approve** (branch `feat/extensions-p1`) — the safe install pipeline
  for learned skills/agents. **Gates should reuse the SAME pending/approve mechanism**, not a
  second approval system. This draft writes gates to `.build-loop/gates/experimental/`; a
  merge agent should align that with the extensions pending layout if extensions lands first.
- **`recursive-retrospective` skill** (branch `feat/recursive-retrospective`) — the capture
  step. The emit (branch `feat/retro-emit-learning-objects`) folds onto it.

## Branch map + suggested merge order
| Branch | Commit | Base | Contents |
|---|---|---|---|
| `feat/recursive-retrospective` | (owner's) | a1c8823 | the capture skill (3 prompts) + a dogfood doc |
| `feat/retro-emit-learning-objects` | 3f223d4 | feat/recursive-retrospective | emit `.build-loop/learning-objects.json` |
| `feat/learning-to-draft` | 60f0dbb | a1c8823 | `learning_to_draft.py` (route) + `learn-protocol.md` wiring |
| `feat/gate-builder` | (this) | feat/learning-to-draft | `gate_builder.py` (scaffold) + this doc |

**Suggested order onto current main (`ab05669`):** recursive-retrospective → retro-emit →
learning-to-draft → gate-builder. Files are largely disjoint. The only shared edited file is
`references/learn-protocol.md` (touched only by `feat/learning-to-draft`). `gate_builder.py`
imports nothing from `learning_to_draft.py` (it consumes its *output* shape), so they can
merge independently as long as the Prevention-Pattern field names stay aligned.

## Open questions for the merge agent
1. Should `.build-loop/gates/experimental/` be folded into the `extensions` pending layout?
   (Recommend yes if extensions lands — one approval system, not two.)
2. Who fills the check body — a new `gate-architect` agent (LLM) or a human? This draft
   leaves it to a human/follow-up; an agent producer is a reasonable next step but was
   deliberately not auto-built (correctness risk).
3. Dedupe policy between `enforce_retro_signals` output and `learning_to_draft` output.
4. Activation wiring: where an approved gate actually hooks into the verify step (Review-B).
   Not wired here on purpose.

## What is verified vs not
- `gate_builder.py` scaffolding behavior: unit-tested (`scripts/test_gate_builder.py`).
- End-to-end route→scaffold: spot-checked (a Prevention-Pattern spec scaffolds a draft gate).
- NOT verified: that any scaffolded gate's *body* is correct (there is none yet — by design).
