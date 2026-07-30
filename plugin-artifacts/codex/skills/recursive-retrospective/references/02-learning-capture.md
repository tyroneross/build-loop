# Recursive Learning Object Capture Prompt

> v2 (2026-06-18) — reframed as a PACKAGER/DEDUPER of Prompt 1's output, not a fresh re-extraction. Validated against the live run: Prompt 1 §7 already emits a full learning-object table, so re-extracting from scratch duplicated work and risked divergence. v2 ingests §7+§12 and only adds net-new objects.

## Role
You are a recursive learning and memory architect for apps, agents, plugins, and build-loop systems. Take the learning objects Prompt 1 already produced and turn them into a clean, deduplicated, copy-ready encoding package. **Not every learning object should become memory.** Decide what to encode, where, and what NOT to store.

## Primary input (required)
`[PASTE PROMPT 1 §7 LEARNING OBJECT TABLE + §12 ROADMAP HERE]` — these are the canonical learning objects, already id'd (LO-n) and ordinally prioritized.
Optional secondary input (only to fill gaps Prompt 1 missed): `[PASTE RETROSPECTIVE BODY / RCA / USER INTERACTION HISTORY / SPEC / NAVGATOR / REPO SUMMARY HERE]`

## Operating mode — dedupe first, extract second
1. **Ingest** every LO from Prompt 1 by id. Do not re-derive them; carry the id forward.
2. **Dedupe**: merge LOs that are the same object stated twice; note merges (`LO-3 ⊇ LO-7`).
3. **Reconcile with existing memory**: for each LO, check whether it is already encoded (Prompt 1's "Already-encoded?" column / a memory lookup). If already encoded, the action is "verify it fires," not "store again."
4. **Add net-new ONLY if Prompt 1 missed it.** Flag every net-new object `[NET-NEW vs Prompt 1]` with the evidence that justifies it. If you are adding many net-new objects, that is a signal Prompt 1 under-ran — say so rather than silently re-extracting.
5. **Classify and package** (below).

## Evidence rules
Per object: Explicit (stated) · Revealed (repeated behavior) · Project-specific · Cross-project · TAG:INFERRED · Do not encode (too weak/transient/sensitive/project-bound/duplicative).
**Do not store globally unless ≥1 is true:** (1) user explicitly framed it durable; (2) recurred across interactions; (3) materially affects future build-loop decisions; (4) it's a hard gate / permission / safety constraint; (5) validated reusable workflow pattern; (6) recurring failure or success mode.

## 1. Reconciled Learning Object Inventory
Table: LO-id | Learning object | Source (Prompt1 / NET-NEW) | Evidence type (Explicit/Revealed/Inferred/Failure/Success/Hard gate) | Scope (Cross-project/Project-specific/Local only) | Encoding target (Memory/Agent/Skill/Plugin/App/Eval/Preflight/Approval gate/Project note/Do not encode) | Already-encoded? (memory id or No) | Encode action (Store / Verify-fires / Needs approval / Do not encode).

## 2. Cross-Project User Preferences
Table: Preference | Evidence | Applies to (UI/Architecture/Agent behavior/Communication/Testing/Product strategy/Other) | Stability (High/Med/Low) | Recommended encoding (Memory/Agent rule/Preflight/No encode). Only preferences with explicit statements or repeated behavior.

## 3. Revealed Workflow Patterns
Carry from Prompt 1 §5 clusters; do not re-mine. Table: Workflow pattern | Evidence | Reusable? (Yes/No/Mixed) | System implication | Encoding target.

## 4. Project-Specific Learning
Table: Project-specific learning | Evidence | Why it matters | Expiration or review trigger.

## 5. Agent Instructions
Table: Agent rule | Trigger | Expected behavior | Evidence | Scope. Evidence-supported only.

## 6. Plugin and Tool Behavior Updates
Table: Tool/plugin behavior | Trigger | Expected behavior | Evidence | Priority (P0/P1/P2).

## 7. App Feature Opportunities
Table: App feature | Problem it solves | Evidence | User value | Priority (P0/P1/P2).

## 8. Skills to Create or Reuse
Table: Skill | Purpose | Trigger | Inputs | Outputs | Success criteria.

## 9. Evals and Quality Gates
Table: Eval or gate | Catches | Runs when | Pass criteria | Failure action.

## 10. Hard Gates and Approval Rules
Table: Gate | Why approval needed | Can be pre-captured? (Yes/No/Partial) | Encoding target (Memory/Preflight/Approval gate/Project note) | Build Loop behavior.

## 11. Do-Not-Encode List
Table: Finding | Why not encode? (Too weak/Project-specific/Sensitive/Temporary/Duplicative/Unverified) | Safer handling (Project note/Ignore/Ask user/Recheck later).

## 12. Final Learning Object Package
`# Recommended Learning Object Updates` with sections: Cross-project memories · Project-specific memories · Agent instructions · Skills to create or reuse · Plugin/tool behavior updates · App feature opportunities · Evals and quality gates · Preflight checks · Approval gates · Do not encode.
For each item: **LO-id** · **Learning object** · **Evidence** · **Scope** (Cross-project/Project-specific/Local only) · **Encoding target** · **Confidence** (High/Med/Low) · **Already-encoded?** (memory id or No) · **Approval needed before storing or applying?** (Yes/No).

**Approval authority (default = automatic).** Memory / project-note / skill-source / agent-instruction encodes are SAFE and reversible: the **dispatching (main) agent grants approval and they persist automatically** — confidence level alone (e.g. Med) does NOT trigger a human gate, and a medium-confidence memory note must NOT be bounced to the user. Set **Approval needed = Yes (human)** ONLY for the hard-gate taxonomy: security, privacy, cost, live/production deploy, irreversible/destructive actions, or promoting an experimental artifact into a globally runtime-active skill/agent that changes behavior for future sessions. Everything else is `encode: yes` and auto-applies under the dispatching agent's authority.
Items with encoding target = memory and "Store" action are written via the canonical `memory_writer.py`; do not hand-write memory files.

> Keep-both reconciliation (consolidation): §1-12 = v2.1 packager/deduper (RCA levers); §13 = machine-readable emit loop-closer (from feat/retro-emit-learning-objects). Neither overwrites the other.

## 13. Machine-readable emit (closes the auto-draft loop)
Also write the inventory to `.build-loop/learning-objects.json` — a JSON array, one object per §1 learning object. Exact fields (the converter skips entries that don't match):
- `title` (str) · `evidence` (list of str) · `encoding_target` (one of `skill`, `agent`, `memory`, `eval`, `gate`, `preflight`, `approval`, `project_note`, `do_not_encode`) · `scope` (`cross-project`|`project-specific`|`local`) · `confidence` (`high`|`med`|`low`) · `encode` (`yes`|`no`|`needs_approval`) · optional `trigger` (when/where it applies) and `purpose`.

Phase 6 Learn feeds this to `scripts/learning_to_draft.py`: `skill`/`agent` objects with `encode: yes` auto-draft via `self-improvement-architect`; `eval`/`gate`/`preflight`/`approval` objects become routable Prevention-Pattern enforcement specs (condition → behavior → lever → actuator → verifying artifact). This file is the contract that turns a captured finding into an action with no human re-keying.

## Acceptance Criteria
Successful only if it: ingests Prompt 1's LOs by id rather than re-extracting; dedupes and flags merges; reconciles against existing memory (Store vs Verify-fires); flags every net-new object as `[NET-NEW vs Prompt 1]` with justifying evidence; separates explicit from revealed and cross-project from project-specific; avoids encoding transient/weak findings; captures hard gates & approval rules; includes a do-not-encode list; produces a copy-ready package keyed by LO-id; routes memory writes through `memory_writer.py`; and ALSO writes the `.build-loop/learning-objects.json` emit (§13) so the auto-draft loop closes.
