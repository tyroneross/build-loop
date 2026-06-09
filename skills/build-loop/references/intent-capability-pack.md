<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Intent Capability Pack

Use this pack on every build. It keeps decentralized subagent work aligned to the app's purpose, the user's actual job, and the update's intent.

## North Star

Every build starts by capturing:

- **App/repo purpose**: what this product is for and who it serves.
- **Primary users**: the people or roles affected by this change.
- **Core jobs**: the tasks users perform most often or rely on most.
- **Update intent**: why this change matters now.
- **User value**: how the change makes the product faster, clearer, more accurate, more trustworthy, more useful, or easier to navigate.
- **Non-goals**: what this build should not add, expose, or complicate.

Write the result to `.build-loop/intent.md` and mirror the compact version into `.build-loop/state.json.intent`.

### Commander's-intent posture (WP-F, all OPTIONAL — LLM-inferred, confirm-on-ambiguity)

North Star captures who/what; **posture** captures the tradeoff stance that drives
autonomous forks when the agent loses comms (the Marine-Corps commander's-intent
analog: purpose + key tasks + end state, so a subordinate who can't ask still
chooses correctly). A walkie-talkie app for generals-on-ops, for kids, and for
traders share North Star *fields* but differ entirely in *what to optimize when
forced to choose*. All fields are OPTIONAL and LLM-inferred from the ask; confirm
only on genuine ambiguity (reuse the restatement protocol — never `AskUserQuestion`,
never a gate). Mirror into `state.json.intent.posture`.

- **audience** + **stakes** — one line each (who the change serves; what a failure
  costs). `stakes` ∈ {low, medium, high}.
- **priority_order** — the ranked tie-breaker the agent applies when two viable
  paths conflict. FIXED VOCAB (ordered subset of
  `security / reliability / speed / cost / simplicity / polish`) plus a free-text
  `notes` escape for expressiveness. The fixed vocab aids weak-LLM recall; `notes`
  carries anything the vocab can't.
- **acceptable_tradeoffs** — what is OK to cut under pressure.
- **non_goals** — what is never cut. At `stakes: high`, a `non_goals` entry that
  names a REAL risk is a candidate to **graduate to a constitution invariant** — but
  only once it is **promoted into the project constitution** (`projects/<slug>/constitution.md`),
  where the LLM enforces it as a hard line. Until promoted, it stays advisory like the
  rest of the posture. There is no separate deterministic gate that reads `non_goals`
  directly (`grep non_goals scripts/` is intentionally empty); enforcement rides the
  constitution, not a parallel mechanism. See the tiered charter below.

`priority_order` wires into the `alignment-checker` as the Phase-2-fork and
Phase-5-queue-drain tie-breaker: not just "matches intent?" but "which viable path
does THIS user's priority order prefer?" — advisory data the LLM weighs, never a gate.

### Tiered intent — `stakes` is the depth dial (WP-F/F2)

- **Per-run intent** (ephemeral, `.build-loop/intent.md`): restated ask + this
  change's posture. Unchanged lifecycle.
- **Project charter** (persistent): stable North Star + posture + invariants + key
  architecture decisions. ACCRETES via promotion — a fact promotes to durable when
  user-confirmed OR stable/unchallenged across N runs; stays `inferred` until then;
  carries the falsifier that would unseat it (doctrine rule 8). Storage + sync:
  `scripts/charter.py` (canonical `build-loop-memory/projects/<slug>/charter.md`;
  repo mirror `.build-loop/charter.md` with a `canonical:` pointer + content hash;
  one writer = the run, from canonical; user hand-edit of the mirror promotes to
  canonical `authored_by: user` on next run via hash-mismatch detection).
- **Depth scales by `stakes`**: low → intent line only (skills/agents/toys — do NOT
  force a charter, that's the anti-pattern); medium → thin charter (web/mobile);
  high → full charter, and risk-naming `non_goals` become candidates to promote into
  the constitution as invariants (where the LLM enforces them). The promotion is the
  enforcement; no `non_goals`-specific gate exists outside the constitution.
- **PRD stance**: opt-in upfront via `start-prd`; accretion is the default; never
  required. A PRD, when present, PREFILLS the charter richer — input, not a gate.

Enforcement philosophy (binding): all advisory. The one stronger-than-advisory path
is **constitution promotion** — a risk-naming `non_goals` at `stakes: high` graduates
to a hard invariant only when it is written into `projects/<slug>/constitution.md`,
which the LLM treats as a binding line. Per `feedback_deterministic_only_for_known_risks` —
posture/charter depth is the dial; the LLM weighs, never a gate. The constitution, not
a `non_goals`-specific script, carries any graduated invariant (no dormant determinism
claim — there is no `non_goals` enforcement code to wire up).

## Intent restatement protocol (always-on)

Run this protocol on every build, judged by the orchestrator LLM — never a regex, never a detector script, never a binary gate. Depth scales with ambiguity, not a threshold. The behavior is intrinsic to Phase 1; no separate skill, script, or routing step gates it.

### Step A — One-line concrete restatement (always)

Read the user's goal text and the surrounding context. Write a single sentence restating the most likely concrete interpretation to `.build-loop/intent.md` under a `## Restated intent` heading. For a concrete unambiguous goal, this is the entire protocol — write the line and move on. No options, no assumption-tagging, no exploration detour. The auto-execute fast path is unaffected.

Heuristics the LLM uses (judgment, not a checklist):

- Does the goal name a file path, function, schema field, route, command, or other concrete deliverable? → restate once and proceed.
- Could two reasonable readers infer materially different work? → continue to Step B.
- Does the goal use investigative or hedging language ("explore", "look into", "something like", "brainstorm", "design from scratch") without a concrete target? → continue to Step B.

The judgment is one pass, fast, and does not block. When in doubt about whether ambiguity is "real," do the lighter version (Step A only) and tag assumptions inline so downstream work can correct.

### Step B — Options + tradeoffs when ambiguity is genuine

When the LLM judges genuine ambiguity (Step A heuristics fail), extend `.build-loop/intent.md` with:

```md
## Approach options
1. **<short label>** — <≤2 sentences on what + tradeoff>
2. **<short label>** — <≤2 sentences on what + tradeoff>
3. **<short label>** — (optional third — stop at 3)

## Recommended path
<one sentence naming option 1/2/3 and the reason>

## Scope cuts considered
- <thing the orchestrator believes can be cut without losing user value>
- <second if present>
```

Lead with the recommended option. Avoid speculative "we could also" lists. Keep to 1–3 options — the goal is to narrow, not to enumerate.

The reference file `skills/build-loop/references/intent-exploration-prompts.md` carries four template patterns (vague-verb, branching-or, creative-open, hedge-phrase) the LLM can consult when shaping options for common ambiguity shapes. Load on demand only when the goal matches one of those shapes.

### Step C — Tagged assumptions (always when Step B fires; optional in Step A)

For every leap the restatement made that isn't grounded in the repo or the user's prompt, append a `TAG:ASSUMED` line under a `## Open assumptions (TAG:ASSUMED)` heading naming the assumption + the evidence that would close it. Examples:

- `TAG:ASSUMED — user wants the smallest concrete restatement; would close by user pinning a specific deliverable.`
- `TAG:ASSUMED — "explore" means "survey + recommend"; would close by repo showing prior similar work pattern.`

Tagged assumptions are the audit trail. The user reads them in the run report and can override on the next dispatch.

### Step D — Mirror compact summary to state.json

Mirror the result to `.build-loop/state.json.intent`:

```json
{
  "restated_intent": "<one sentence>",
  "approach_options": ["<label>", "<label>"],   // optional; empty when Step A alone fired
  "assumptions": ["<line>", "<line>"],          // optional; empty when no leaps were made
  "confidence": "high" | "medium" | "low"       // LLM judgment, not a script
}
```

### Hard guarantees (non-negotiable)

- **Never `AskUserQuestion`.** Intent capture is autonomous. The user reads the restatement + assumptions in the run report and can correct on the next dispatch.
- **Never `## Held`.** Advisory output only. Phase 2 Plan proceeds with whatever Step A or A+B produced.
- **Never blocks Phase 1.** A goal that is too ambiguous to restate concretely still gets restated as the best-effort interpretation + assumptions tagged. The flow proceeds.
- **Fail-safe.** Any error in this protocol (file write fails, intent.md missing) is logged as one line and the build continues. No exit-non-zero path exists here.
- **Auto-execute fast path preserved.** A concrete unambiguous goal produces the one-line restatement only. Zero added cost for Step B/C. No skill dispatch, no script call, no detection layer.
- **Fork-on-uncertainty consumes the output.** When Step B fired AND `confidence == "medium"|"low"` AND Phase 2 surfaces 2+ viable approaches differing only on implementation tradeoffs, the orchestrator's existing fork-on-uncertainty rule fans out worktrees per approach. The protocol provides the options; the existing rule consumes them.

### Why intrinsic, not gated

The prior shape used a regex script to decide whether to run an exploration skill. That regex false-fired on ordinary prose ("auth fails or times out", "returns 200 or 404") and forced exploration detours on concrete goals — violating the no-friction fast-path contract. The LLM judges ambiguity better than a regex can. This protocol is the application of the "host agent is the LLM" principle to intent capture.

## Intent Packet

Every subagent prompt must include this packet:

```md
North star: <one sentence>
Update intent: <one sentence>
Primary user/workflow: <who does what>
This task fits by: <how this subtask advances the build>
User-value rule: <speed | accuracy | trust | navigation | scalability | reduced choice burden | other>
Decision constraints:
- No fake data or mock responses in production/user decision paths.
- No dead controls, dead navigation, decorative options, or UI promises without working behavior.
- Prefer the simplest approach that preserves user value and long-term scalability.
- Use a more complex approach only when the simpler approach harms user experience, correctness, extensibility, or performance.
Evidence required: <tests, build, visual check, data trace, performance check, etc.>
```

## Decision Rules

- **Real value beats apparent progress**. A UI that looks complete but hides mock data is worse than an honest incomplete state.
- **Basics must be excellent**. Core flows, data accuracy, loading, empty states, error states, navigation, and primary actions matter more than secondary features.
- **Every visible element needs intent**. Each button, label, option, nav item, chart, and message must help the user act, understand, decide, or recover.
- **One clear primary action by default**. Multiple hero or primary buttons need a strong reason. If choices create confusion, reduce them.
- **No non-working promises**. Do not ship listed options, nav items, filters, actions, charts, or integrations that do nothing or return placeholders.
- **Simplicity is not shortcutting**. Prefer the smallest durable solution. Choose additional complexity only when it materially improves user value, reliability, scalability, or future optionality.
- **End-to-end data integrity matters**. If users make decisions from search, charts, metrics, recommendations, or summaries, trace those outputs to real sources.

## UI Standard: Beauty in the Basics

For UI work, the baseline is intentional, useful, and polished:

- **Hierarchy**: the screen makes the next best action obvious.
- **Copy**: text is specific, truthful, and necessary. Remove generic filler.
- **Controls**: controls have working behavior, appropriate affordance, and accessible labels.
- **Navigation**: navigation reflects real destinations and common workflows.
- **Choices**: option count is constrained to what users can meaningfully use.
- **States**: loading, empty, error, success, disabled, and permission states are designed, not incidental.
- **Data displays**: charts, tables, search results, and metrics show real data or a clear unavailable state.
- **Performance**: avoid visual or data-flow choices that make common tasks slower without clear value.
- **Scalability**: layouts and data models should tolerate realistic growth without immediate redesign.

## User-Impact Issue Rule

When build-loop discovers a bug or issue while working:

1. Ask whether it impacts users by checking:
   - Does it make the app slower or faster?
   - Does it make information less or more accurate?
   - Does it affect trust, data integrity, security, or recovery from failure?
   - Does it make core workflows easier or harder to navigate?
   - Does it add unnecessary choices or remove useful optionality?
   - Does it create short-term code that blocks scalable future work?
2. If yes and the fix is local to the current build, add it to the plan and fix it automatically.
3. If yes but the fix is too large or risky, log it to `.build-loop/issues/` with user impact, proposed fix, and why it was deferred.
4. If no, log only when it is likely to affect future maintenance.

## Review Gates

Review must check:

- **Intent fidelity**: the implementation advances the north star and update intent.
- **User value**: the result improves at least one declared user-value rule.
- **UI intentionality**: visible elements are meaningful, working, and not excessive.
- **Data integrity**: production/user decision paths do not use fake, random, or placeholder data.
- **Simplicity and scalability**: the solution is the simplest durable approach that protects user experience.

## Source Basis

This pack operationalizes human-centered design and usability principles from:

- [ISO 9241-210:2019](https://www.iso.org/standard/77520.html): human-centered design across the interactive-system life cycle.
- [NIST summary of ISO human-centered design](https://www.nist.gov/itl/iad/visualization-and-usability-group/human-factors-human-centered-design): explicit users/tasks/environments, iterative evaluation, whole user experience, and multidisciplinary perspective.
- [GOV.UK Service Manual: understand users and their needs](https://www.gov.uk/service-manual/service-standard/point-1-understand-user-needs): understand full context, validate assumptions, and avoid building the wrong thing.
- [GOV.UK Service Manual: learning about users and their needs](https://www.gov.uk/service-manual/user-centred-design/user-needs): design around real user needs and keep needs traceable to user stories.
- [W3C WCAG 2.2 Understanding](https://www.w3.org/WAI/WCAG22/understanding/): accessible interfaces should be perceivable, operable, understandable, and robust.
- [Apple Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/): hierarchy, harmony, consistency, accessibility, platform patterns, and common components.
- [Nielsen Norman Group usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/): visibility, match to real world, user control, consistency, error prevention, recognition, flexibility, minimalist design, recovery, and help.
