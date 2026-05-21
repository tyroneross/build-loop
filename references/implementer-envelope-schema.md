# Implementer Envelope Schema — Canonical Contract

This document defines the **canonical return envelope** every build-loop implementer (Mode A fan-out subagent or Mode B inline self-call) MUST populate. The orchestrator and `scope-auditor` parse implementer output against this schema; missing required fields cause the orchestrator to mark the commit as **malformed** and either request a revision or quarantine the diff.

Per-brief envelope shapes (round-1 through round-3) are deprecated. New briefs reference this schema by path; legacy shapes are accepted by the orchestrator only when the brief was written before this schema landed.

## Required top-level fields

| Field | Type | Description |
|---|---|---|
| `branch` | string | Git branch the implementer worked on. |
| `commit_sha` | string | SHA of the implementer's commit, OR the literal `"pending"` if the implementer didn't commit (canonical case under Hard rule 4 — orchestrator commits). |
| `files_changed` | array of paths | Authoritative list of paths the orchestrator should stage and commit. Absolute or repo-relative; be consistent. |
| `loc_added` | integer | Lines added across `files_changed`. `0` when none. |
| `loc_removed` | integer | Lines removed across `files_changed`. `0` when none. |
| `f_criteria` | object | Map of F-criterion ID → `"pass"` or `"fail"`. Every F-criterion named in the brief MUST appear. |
| `synthesis_attestation` | object | For each dimension named in the plan's `synthesis_dimensions` block, value is `"applied"`, `"deviated"`, or `"n/a"`. If `"deviated"`, the value MUST be an object `{"status": "deviated", "deviation_reason": "<why>"}`. **Empty object `{}` is allowed when the plan has no `synthesis_dimensions` block** (e.g. methodology commits, infra-only commits). |
| `novel_decisions` | array | Array of decision objects (schema below). Empty array `[]` is OK, **but the field MUST be present**. Implementers add an entry whenever they make a synthesis-class decision NOT enumerated in the plan's `synthesis_dimensions`. When the novel decision is **architectural-class** (where a phase lives, defensive contract shape, error-propagation policy, persistence boundary, etc.), the implementer MUST halt and set `status: "blocked"` (see below) rather than guess. **Each entry MUST include `recommended_default` and `confidence`** so the orchestrator can auto-pick in long-mode and surface trade-offs to the operator in normal-mode. See "novel_decisions[] entry schema" below. |
| `decision_ledger` | array | **Required when the plan has a `synthesis_dimensions` block.** Empty array `[]` is permitted only when the plan has no `synthesis_dimensions` block. Each entry MUST contain all six fields: `dimension` (string — matches a key in `synthesis_attestation`), `owner` (`"plan"` or `"implementer"`), `locked_value` (string — concrete value chosen), `alternatives_rejected` (array of strings, or `["none considered"]`), `evidence_file` (repo-relative path, or `null` when `owner == "implementer"` AND the decision is non-code), `on_new_decision` (enum: `"block" \| "flag" \| "absorb"`; default `"block"` for `risk_reason`-tagged chunks, `"flag"` otherwise). See `## decision_ledger in detail` below. |
| `notes` | string | Free-text. ≤200 words. Judgment calls, surprises, deferred concerns. |
| `wall_clock_seconds` | number | End-to-end implementer wall-clock duration. Orchestrator uses this for tier-mix telemetry. |
| `task_id` | string | Per-dispatch unique identifier echoed from the orchestrator's `[TASK_ID: <id>]` prompt prefix. Format: `t-<8-hex-chars>`. The orchestrator generates this before dispatch and writes one row per dispatch to `~/.bookmark/cost-ledger.jsonl` via `scripts/write_cost_ledger_row.py`. The implementer MUST echo it back unmodified so the row's completed-at update can be correlated against its dispatched-at row. Missing field = malformed envelope. If the brief omits the `[TASK_ID: …]` prefix (legacy briefs), return `task_id: "unknown"`. |
| `status` | string | Optional for routine Phase 3 Execute commits (legacy). REQUIRED when the implementer halts on an architectural-class novel decision: set `status: "blocked"` and return early without committing. See "status enum" below. |
| `capabilities_used` | array of strings | **Additive (Step 7 / audit §5.E).** IDs of `available_capabilities[]` entries from the brief that the implementer actually invoked during this chunk. Empty array `[]` when none were used. The orchestrator joins this against the cost-ledger row and surfaces credit-assignment telemetry in Phase 4 Report. Backward-compat: legacy envelopes that omit the field are accepted; the parser treats absence as `[]`. |
| `capabilities_rejected` | array of objects | **Additive (Step 7 / audit §5.E).** Capabilities from `available_capabilities[]` the implementer considered but did NOT use, with a short reason. Shape per entry: `{"id": "<capability_id>", "reason": "<one-sentence why-not>"}`. Empty array `[]` when none were considered-and-rejected. Powers the self-improvement-architect's Phase 6 credit-assignment pattern detector: a capability rejected with the same reason across N chunks is a signal the registry entry needs revision. |
| `downstream_iterate_outcome` | string or null | **Additive (Step 7 / audit §5.E).** Set by the orchestrator AFTER this commit's downstream Phase 5 Iterate cycle closes (the implementer leaves this `null`; the orchestrator backfills via `write_run_entry.py`). Enum: `"clean"` (no Iterate needed), `"resolved-on-pass-1"`, `"resolved-on-pass-2-or-later"`, `"overflow-to-followup"`, `"abandoned"`. Used by Phase 6 Learn to attribute commit-time decisions to their downstream verification outcomes. Backward-compat: legacy envelopes omit this; the parser treats absence as `null`. |

**Contract:** missing required fields = malformed envelope. Use empty/null sentinels (`""`, `0`, `[]`, `{}`) for absent data; **do not omit keys**. The orchestrator's parser distinguishes "field absent" (malformed) from "field present but empty" (legitimate).

## Optional fields (legacy compatibility)

The following round-1/round-3 fields remain accepted but are no longer required. When the orchestrator commits on the implementer's behalf, it populates these fields itself:

- `commit_subject`, `commit_body` — used by Mode A orchestrator-commits flow.
- `verifications` — `{typecheck, lint, adjacent_tests, re_grep}` map; recommended but not required.
- `intentional_non_fixes` — list of in-scope items the implementer chose not to fix; recommended for v2-pattern briefs.
- `status` — enum: `fixed | partial | blocked | scope_breach | deferred_architecture | plan_malformed | evidence_stale | needs_dependency | failed | concurrent_modification_detected | completed`. Required for Phase 5 Iterate fix-plan implementers and for the **halt-and-ask** Phase 3 path (see "status enum" below). Optional for routine Phase 3 Execute commits (the orchestrator infers `completed` from a clean envelope with no `blocked` signal).

## status enum — full contract

| Value | When | Implementer behavior | Orchestrator routing |
|---|---|---|---|
| `completed` / `fixed` | Routine success — all `f_criteria` either pass or are honestly marked fail | Modify working tree, return envelope with `commit_subject` + `commit_body`. Do NOT commit. | Orchestrator commits per Phase 3 commit step. |
| `partial` | Fixed M of N evidence lines; remainder needs human judgment | Same as `fixed`; document remainder in `notes`. | Commit and route remainder to Iterate. |
| `blocked` | **Halt-and-ask: encountered an architectural-class synthesis decision NOT in plan's `synthesis_dimensions`** | Add the decision(s) to `novel_decisions[]` with full reasoning. **Do NOT commit. Do NOT make the decision.** Return early with `status: "blocked"`. `commit_sha: ""`, `files_changed: []` (or partial set if work was done before the block was hit — orchestrator will reset). | Orchestrator dispatches each `novel_decisions[]` entry to the configured **Thinking-tier** resolver (per `references/model-tier-mapping.md`). Resolutions stored in `state.json.novelDecisionResolutions[]`; implementer is re-dispatched with resolutions appended to its brief. Hard-fail counter N=3 per chunk. |
| `scope_breach` | Fix needs a file outside `files_touched` | Return with `needed_file` + `why`. | Orchestrator decides whether to extend scope. |
| `deferred_architecture` | Plan's `architecture_impact: true` flag set | Refuse implementation; return immediately. | Routes to user confirmation in Review-F. |
| `plan_malformed` / `evidence_stale` / `needs_dependency` / `failed` | See `agents/implementer.md` "Failure modes" table | Return with the diagnostic field(s) named there. | Re-plan, retry, or escalate per protocol. |
| `concurrent_modification_detected` | A `files_touched` file was modified by something other than this implementer | Return immediately. | Indicates MECE-partition bug; orchestrator investigates. |

**`blocked` is distinct from `scope_breach`.** Scope breach is a file-system question ("the fix needs a file I don't own"). Blocked is a synthesis question ("the fix needs an architectural decision the plan didn't make"). Both halt the implementer; they route to different resolvers.

### When `blocked` is the right call (vs. logging a `novel_decisions` entry and proceeding)

The C3 attestation lint catches deterministic synthesis drift; the C4 synthesis-critic catches subjective UI-tone drift. Together they cover most synthesis-class decisions. **Architectural-class decisions** fall outside both — `attestation_lint.py` has nothing to grep for, and `synthesis-critic` only fires on UI files. C5 catches those.

Block when the missing decision is one of:

- **Where a phase or responsibility lives** (orchestrator vs. implementer; client vs. server; sync vs. async). E.g. "the plan says route the resolved decisions back to the implementer, but doesn't say whether the orchestrator stores resolutions before or after the re-dispatch."
- **Defensive contract shape.** Whether to fail-closed or fail-open on a backend outage; whether a missing field is a malformed envelope or a legitimate empty.
- **Error-propagation policy.** Re-throw vs. swallow vs. wrap in a domain error; route to Iterate vs. mark as ❓ Unfixed.
- **Persistence boundary.** Where new state lives (state.json vs. a new file vs. memory-only); whether it survives a process restart.
- **Hard-fail counter or retry limit.** When to give up.

Do NOT block on routine synthesis decisions the lint/critic already cover (placement, cta_tier, visual_weight, copy_tone, empty_state) — log them in `novel_decisions[]` and proceed; the orchestrator + scope-auditor will route them appropriately at commit time.

When in doubt: block. A wasted Thinking-tier resolution is cheaper than a wrong architectural decision shipped to the diff.

## synthesis_attestation in detail

The `synthesis_dimensions` block was added in C1 (`feat(spec-writing): synthesis_dimensions checklist item`). Plans now enumerate the synthesis-class decisions an implementer is expected to apply (e.g. "uses repo's existing pagination convention", "reuses Toast component for error states", "matches existing API error shape"). The implementer attests against each named dimension:

- `"applied"` — implementer made the synthesis decision exactly as the plan named it.
- `"deviated"` — implementer deviated from the named synthesis call, with a reason. Use the object form `{"status": "deviated", "deviation_reason": "<one sentence>"}`.
- `"n/a"` — the dimension wasn't reachable from this commit's scope (e.g. a UI-only commit attesting to a backend pagination dimension).

If the implementer finds itself making a synthesis-class decision that the plan didn't name, the implementer MUST halt that decision path and add the decision to `novel_decisions` instead of attesting silently. The orchestrator and `scope-auditor` then decide whether to extend the plan's `synthesis_dimensions` (route to plan-revise) or accept the novel decision (route to commit).

## decision_ledger in detail

The `decision_ledger` array was added in C5 (`feat(envelope): require decision_ledger when synthesis_dimensions present`). While `synthesis_attestation` records *what the implementer claimed* about each dimension, `decision_ledger` records *why each value was chosen* — creating an audit trail that survives the commit log.

**Required when:** the originating plan contains a `synthesis_dimensions:` block. Every dimension listed in `synthesis_attestation` must have a corresponding `decision_ledger` entry (matched by the `dimension` field).

**Permitted absent:** when the plan has no `synthesis_dimensions:` block. In that case, omit the field entirely OR provide an empty array `[]`. Both are valid.

**Per-entry fields (all six required):**

| Field | Type | Rule |
|---|---|---|
| `dimension` | string | Must match a key in `synthesis_attestation` for this envelope. |
| `owner` | `"plan"` or `"implementer"` | `"plan"` = the value was prescribed by the plan's `synthesis_dimensions` block. `"implementer"` = discovered or chosen during execution. |
| `locked_value` | string | The concrete value chosen (e.g. `"secondary"`, `"after '<SummaryRow>' in path/to/file"`). Never a status word like `"applied"`. |
| `alternatives_rejected` | array of strings | At least one alternative considered. Use `["none considered"]` only when there was genuinely a single viable option and documenting that is itself informative. |
| `evidence_file` | string or `null` | Repo-relative path to the file where the decision manifests in code. `null` is allowed only when `owner == "implementer"` AND the decision is non-code (e.g. a copy-tone choice with no diff file). For `owner == "plan"` decisions, `null` triggers a WARN from the attestation lint. |
| `on_new_decision` | enum string | One of `"block" \| "flag" \| "absorb"`. Controls what happens if a *new* undeclared decision of the same class is encountered during a future re-dispatch. Default: `"block"` for chunks tagged `risk_reason`; `"flag"` otherwise. |

**Lint behavior (`--check-ledger`):** the attestation lint enforces that (a) every `synthesis_attestation` dimension has a ledger entry, (b) all six fields are present and non-empty (except `evidence_file` per the `null` rule above), and (c) `on_new_decision` is one of the three enum values. See `scripts/attestation_lint.py` `--check-ledger` flag.

## novel_decisions[] entry schema

Each entry in `novel_decisions[]` is a decision the implementer surfaced to the orchestrator. The orchestrator routes the decision per `references/halt-and-ask-protocol.md` (mode-aware: auto-pick in long-mode, surface trade-offs in normal-mode).

**Required fields per entry:**

| Field | Type | Rule |
|---|---|---|
| `decision_id` | string | Stable identifier within the chunk (e.g. `"d1"`, `"cache_strategy"`). Used as the dedupe key in `state.json.runs[].autonomousDefaults[]`. |
| `decision` | string | One-sentence statement of the question. Plain language. |
| `options` | array of objects | At least one option. Each option has the option-object schema below. |
| `recommended_default` | string | The `id` of the option the implementer recommends. Must be present and match one of the `options[].id` values. Cannot be omitted — if the implementer cannot recommend, set `confidence: "low"` and pick its best guess; the orchestrator will escalate. |
| `confidence` | `"high" \| "med" \| "low"` | How sure the implementer is about `recommended_default`. **`low` always escalates**, even in long-mode — the implementer is saying "I cannot pick well." |
| `reasoning` | string | Why the implementer recommends `recommended_default`. Cite plan rubric IDs or constitution rules where applicable. |

**Option-object schema (each entry in `options[]`):**

| Field | Type | Rule |
|---|---|---|
| `id` | string | Short identifier (e.g. `"A"`, `"cache"`, `"fetch"`). Used as the value of `recommended_default` and `state.json.runs[].autonomousDefaults[].chosen`. |
| `summary` | string | One-line description of what this option does. |
| `user_impact` | string | What end users see if this option ships. Cannot be `""` or `"n/a"`. If the option has no user-visible impact, write `"none — internal-only change"`. |
| `performance` | string | Quantitative or qualitative perf delta (e.g. `"p95 60ms vs 220ms"`, `"~2× faster on warm cache"`, `"no measurable change"`). |
| `speed` | string | Time-to-ship estimate (e.g. `"~30 min"`, `"~half a day if migration succeeds"`, `"unknown — depends on schema migration"`). |
| `cost` | string | Dollar/quota impact at expected volume (e.g. `"$0"`, `"~$12/mo at 10k req/day"`, `"unknown — depends on caching hit rate"`). |

**Why user-visible-impact fields are required.** The trade-off table is what the operator sees in normal-mode prompts. If the fields are missing or filled with `"n/a"`, the operator can't make an informed choice — they see a list of opaque options. Schema validation rejects entries where `user_impact` is empty or `"n/a"` (use `"none — internal-only change"` for legitimately invisible work).

**Example entry:**

```yaml
novel_decisions:
  - decision_id: "classification_provider"
    decision: "Which LLM provider for article classification?"
    options:
      - id: "A"
        summary: "OpenAI gpt-4o-mini for classification"
        user_impact: "Higher classification accuracy; users see fewer mis-categorized articles in feed"
        performance: "p95 ~800ms per classify call"
        speed: "~20 min to wire — existing OpenAI client"
        cost: "~$3/mo at 10k articles/day"
      - id: "B"
        summary: "Groq llama-3-70b for classification"
        user_impact: "Comparable accuracy; faster feed refresh — users see new articles ~3× sooner"
        performance: "p95 ~250ms per classify call"
        speed: "~45 min to wire — new SDK"
        cost: "~$0.50/mo at 10k articles/day"
      - id: "C"
        summary: "Local classifier rules (no LLM)"
        user_impact: "Lower accuracy; users see more 'Other' bucketing"
        performance: "p95 ~5ms"
        speed: "~3 hours to build + tune"
        cost: "$0"
    recommended_default: "B"
    confidence: "med"
    reasoning: "Plan rubric r2 prioritizes feed-refresh latency; Groq's 3× speed advantage matters more than accuracy delta. Confidence med because we haven't benchmarked Groq's accuracy on this taxonomy."
```

**Routing summary** (see `references/halt-and-ask-protocol.md` for full protocol):

| Confidence | Long-mode (budget ≥4h or `--long` or `overnight` keyword) | Normal-mode |
|---|---|---|
| `high` | Auto-pick `recommended_default`, log to `autonomousDefaults[]` | Surface trade-off table, wait for operator |
| `med` | Auto-pick, log, flag `confidence: "med"` for judge review | Surface trade-off table, wait |
| `low` | **Escalate** — surface trade-off table even in long-mode | Surface trade-off table, wait |

## Examples

### Example 1 — Minimal (non-UI commit, no synthesis_dimensions)

A C2 methodology commit with no UI surface and no `synthesis_dimensions` block in the plan. No ledger required; `decision_ledger` is omitted (equivalent to `[]`).

```yaml
envelope:
  branch: "exp/synth-decisions-opus-only"
  commit_sha: "pending"
  files_changed:
    - "references/implementer-envelope-schema.md"
    - "agents/implementer.md"
    - "references/implementer-brief-template.md"
  loc_added: 142
  loc_removed: 6
  f_criteria:
    F1: pass
    F2: pass
    F3: pass
    F4: pass
    F5: pass
  synthesis_attestation: {}
  decision_ledger: []
  novel_decisions: []
  notes: "All three files updated in one commit. Schema doc landed under references/ alongside implementer-brief-template.md per existing convention."
  wall_clock_seconds: 184
```

### Example 2 — Rich (UI commit with one deviation + one novel decision)

A C5 commit adding a settings panel. Plan named four synthesis dimensions: `error_state_pattern`, `loading_state_pattern`, `save_on_change_pattern`, `form_field_layout`. Implementer applied three; deviated on one; surfaced one novel decision the plan hadn't enumerated.

```yaml
envelope:
  branch: "feat/news-preferences-panel"
  commit_sha: "pending"
  files_changed:
    - "components/settings/NewsPreferencesPanel.tsx"
    - "components/settings/NewsPreferencesPanel.test.tsx"
    - "app/settings/news/page.tsx"
  loc_added: 287
  loc_removed: 14
  f_criteria:
    F1: pass
    F2: pass
    F3: pass
    F4: fail
    F5: pass
  synthesis_attestation:
    error_state_pattern: applied
    loading_state_pattern: applied
    save_on_change_pattern: applied
    form_field_layout:
      status: deviated
      deviation_reason: "Plan specified two-column grid; existing settings pages all use single-column stacked. Followed in-repo precedent over plan."
  novel_decisions:
    - decision: "Debounced save-on-change at 400ms instead of immediate."
      reasoning: "Toggling 5+ topic checkboxes triggered 5 sequential POSTs in tests; debounce coalesces. Plan didn't address rate-of-change."
  notes: "F4 fails because the optimistic-UI test is flaky against the local API mock; tracking as known-flake. Real backend confirms shape is correct."
  wall_clock_seconds: 612
```

### Example 3 — Populated ledger (UI commit with placement + cta_tier dimensions)

A UI commit adding a MetricCard to the dashboard. The plan named two synthesis dimensions; both are attested and each has a full ledger entry.

```yaml
envelope:
  branch: "feat/dashboard-metric-card"
  commit_sha: "pending"
  files_changed:
    - "components/dashboard/MetricCard.tsx"
    - "components/dashboard/MetricCard.test.tsx"
  loc_added: 84
  loc_removed: 12
  f_criteria:
    F1: pass
    F2: pass
  synthesis_attestation:
    placement_MetricCard: applied
    cta_tier_export_button: applied
  decision_ledger:
    - dimension: "placement_MetricCard"
      owner: "plan"
      locked_value: "after `<SummaryRow>` in components/dashboard/MetricCard.tsx"
      alternatives_rejected:
        - "before `<SummaryRow>` — plan specified after; reversing would change visual grouping"
        - "inside `<DashboardGrid>` — would require grid-slot refactor outside this chunk's scope"
      evidence_file: "components/dashboard/MetricCard.tsx"
      on_new_decision: "flag"
    - dimension: "cta_tier_export_button"
      owner: "plan"
      locked_value: "secondary"
      alternatives_rejected:
        - "primary — too visually dominant for a utility data-export action"
        - "tertiary — insufficient affordance for a trigger users must discover"
      evidence_file: "components/dashboard/MetricCard.tsx"
      on_new_decision: "flag"
  novel_decisions: []
  notes: "Both dimensions applied as specified. Ledger entries document why alternatives were rejected."
  wall_clock_seconds: 142
```

## Brief-construction: input_filter pattern (Step 7 / audit §5.E)

The orchestrator constructs each implementer brief from a superset of context (intent, plan, MECE packet, architecture slice, capability shortlist, memory recall, prior chunk envelopes). Without scoping, this can balloon — particularly when many prior chunks have closed.

OpenAI Agents SDK ships an `input_filter` primitive on its `handoff(...)` call: a function that receives the full `HandoffInputData` (with `input_history`, `pre_handoff_items`, `new_items`) and returns a filtered subset for the downstream agent. Build-loop's structure is **agent-as-tools** (not handoff per Microsoft's distinction — control returns to the orchestrator after each dispatch), so build-loop does not adopt the OpenAI runtime. It DOES adopt the `input_filter` pattern at the brief-construction layer.

**Pattern adoption (internal, build-loop-native):**

1. Each Phase 3 dispatch site in `agents/build-orchestrator.md` builds a candidate brief from the full context superset.
2. Before dispatching, the orchestrator MAY apply a per-chunk `filter` callable to the brief's context blocks (architecture slice, memory recall, prior envelopes). The callable returns a scoped subset — e.g. only memory entries whose `domain` matches `files_owned`, or only prior envelopes from chunks the current chunk has a dependency edge to.
3. The implementer brief carries an `applied_filter` annotation (one line: `applied_filter: <name>; dropped: <N entries>`) so commit-auditor knows what was suppressed.
4. Default behavior is the identity filter (no scoping); filters are opt-in per-chunk in the plan via `chunk[*].brief_filter:` field. The orchestrator's catalogue of built-in filters lives in `references/brief-filters.md` (TBD; not required for this step).

**Why this matters for the envelope schema**: `capabilities_used[]` / `capabilities_rejected[]` are the implementer's view of brief utility. `applied_filter` is the orchestrator's view. Together they let Phase 6 Learn answer "did we send the right context?" without the implementer having to enumerate everything it ignored.

**Sources** (full citations in `~/dev/research/topics/agentic-systems/agentic-systems.build-loop-agent-audit-2026-05-20.md` §5):
- OpenAI Agents SDK — `handoff(input_type, on_handoff, input_filter)` primitive (Bucket 1 §5.A core)
- Microsoft Agent Framework — handoff vs agent-as-tools distinction (§5.C)

## Parser behavior

The orchestrator parses envelopes via `scripts/parse_implementer_envelope.py` (TBD; not yet landed). Until then, the build-orchestrator agent reads the envelope inline. Either way, the parsing rules are:

1. Strict required-field check. Missing key → malformed.
2. `synthesis_attestation` cross-check against the plan's `synthesis_dimensions` block — every named dimension must have an attestation entry; extra entries are accepted (forward-compat).
3. `novel_decisions` non-empty + plan had `synthesis_dimensions` block + `status != "blocked"` → orchestrator routes the diff to `scope-auditor` for synthesis-scope review before committing.
4. **`status: "blocked"` + `novel_decisions` non-empty** → orchestrator does NOT commit. Each `novel_decisions[]` entry is dispatched to the configured Thinking-tier resolver (see `agents/build-orchestrator.md` §"Phase 3 halt-and-ask branch"). Resolutions are stored in `state.json.novelDecisionResolutions[]`, then the implementer is re-dispatched with resolutions appended to its brief. Loop until `status: "completed"` (or equivalent success) or the hard-fail counter (N=3) is exhausted.
5. `status: "blocked"` + `novel_decisions: []` → malformed. The block has no payload to resolve. Orchestrator treats as `failed` and routes to Iterate.
6. Any `f_criteria` value of `"fail"` → orchestrator routes to Iterate (Phase 5) with the failing F-criterion as the entry point.
7. **`novel_decisions[i]` schema check** (do/branch/surface policy): each entry must include `decision_id`, `options` (non-empty), `recommended_default` (matching one of `options[].id`), and `confidence` (`high|med|low`). Each option must include non-empty `user_impact`, `performance`, `speed`, `cost` fields. Missing or `"n/a"`-valued trade-off fields → orchestrator routes to Iterate with the implementer asked to fill them in. Once the schema is clean, `classify_action.py` returns `DECISION` and the orchestrator routes per `references/halt-and-ask-protocol.md`.
