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
| `novel_decisions` | array | Array of `{"decision": "<one-sentence>", "reasoning": "<why>"}` objects. Empty array `[]` is OK, **but the field MUST be present**. Implementers add an entry whenever they make a synthesis-class decision NOT enumerated in the plan's `synthesis_dimensions`. |
| `notes` | string | Free-text. ≤200 words. Judgment calls, surprises, deferred concerns. |
| `wall_clock_seconds` | number | End-to-end implementer wall-clock duration. Orchestrator uses this for tier-mix telemetry. |

**Contract:** missing required fields = malformed envelope. Use empty/null sentinels (`""`, `0`, `[]`, `{}`) for absent data; **do not omit keys**. The orchestrator's parser distinguishes "field absent" (malformed) from "field present but empty" (legitimate).

## Optional fields (legacy compatibility)

The following round-1/round-3 fields remain accepted but are no longer required. When the orchestrator commits on the implementer's behalf, it populates these fields itself:

- `commit_subject`, `commit_body` — used by Mode A orchestrator-commits flow.
- `verifications` — `{typecheck, lint, adjacent_tests, re_grep}` map; recommended but not required.
- `intentional_non_fixes` — list of in-scope items the implementer chose not to fix; recommended for v2-pattern briefs.
- `status` — round-1 enum (`fixed | partial | scope_breach | deferred_architecture | plan_malformed | evidence_stale | needs_dependency | failed | concurrent_modification_detected`). Still required for Phase 5 Iterate fix-plan implementers; not required for Phase 3 Execute implementers.

## synthesis_attestation in detail

The `synthesis_dimensions` block was added in C1 (`feat(spec-writing): synthesis_dimensions checklist item`). Plans now enumerate the synthesis-class decisions an implementer is expected to apply (e.g. "uses repo's existing pagination convention", "reuses Toast component for error states", "matches existing API error shape"). The implementer attests against each named dimension:

- `"applied"` — implementer made the synthesis decision exactly as the plan named it.
- `"deviated"` — implementer deviated from the named synthesis call, with a reason. Use the object form `{"status": "deviated", "deviation_reason": "<one sentence>"}`.
- `"n/a"` — the dimension wasn't reachable from this commit's scope (e.g. a UI-only commit attesting to a backend pagination dimension).

If the implementer finds itself making a synthesis-class decision that the plan didn't name, the implementer MUST halt that decision path and add the decision to `novel_decisions` instead of attesting silently. The orchestrator and `scope-auditor` then decide whether to extend the plan's `synthesis_dimensions` (route to plan-revise) or accept the novel decision (route to commit).

## Examples

### Example 1 — Minimal (non-UI commit, all-applied)

A C2 methodology commit with no UI surface. The plan named two synthesis dimensions: `attestation_field_naming` and `schema_doc_location`. Both applied as written. No novel decisions.

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
  synthesis_attestation:
    attestation_field_naming: applied
    schema_doc_location: applied
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

## Parser behavior

The orchestrator parses envelopes via `scripts/parse_implementer_envelope.py` (TBD; not yet landed). Until then, the build-orchestrator agent reads the envelope inline. Either way, the parsing rules are:

1. Strict required-field check. Missing key → malformed.
2. `synthesis_attestation` cross-check against the plan's `synthesis_dimensions` block — every named dimension must have an attestation entry; extra entries are accepted (forward-compat).
3. `novel_decisions` non-empty + plan had `synthesis_dimensions` block → orchestrator routes the diff to `scope-auditor` for synthesis-scope review before committing.
4. Any `f_criteria` value of `"fail"` → orchestrator routes to Iterate (Phase 5) with the failing F-criterion as the entry point.
