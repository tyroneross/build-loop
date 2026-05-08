# Implementer Envelope Schema — Canonical Reference

Version: 1.1 (C5, exp/synth-decisions-mixed — adds status:blocked + novel_decisions halt-and-ask)

All build-loop implementers MUST return a response conforming to this schema. The orchestrator and scope-auditor parse against this contract. Missing required fields cause the orchestrator to mark the commit as malformed and either request a revision or quarantine the diff.

---

## Top-level required fields

| Field | Type | Description |
|---|---|---|
| `branch` | string | Git branch the implementer worked on. Must match the brief's branch. |
| `commit_sha` | string | SHA of the commit once created. Use `"pending"` if the implementer does not commit (Mode A — orchestrator commits). Never omit. |
| `files_changed` | array of strings | Absolute paths of all files the implementer modified or created. The orchestrator uses this list to stage the commit. |
| `loc_added` | integer | Lines added (from `git diff --stat` or equivalent). |
| `loc_removed` | integer | Lines removed. |
| `f_criteria` | object | Map of F-criterion ID → `"pass"` or `"fail"`. Every criterion from the brief must be present. |
| `synthesis_attestation` | object | Attestation for each synthesis dimension named in the plan (see below). **Empty object `{}` is valid when the plan has no `synthesis_dimensions` block.** |
| `novel_decisions` | array of objects | Any synthesis-class decision the implementer made that was NOT enumerated in the plan. Each entry: `{"decision": "...", "reasoning": "..."}`. Empty array `[]` is valid but the field MUST be present. |
| `notes` | string | Free-text. Judgment calls, surprises, deferred concerns. Max 200 words. |
| `wall_clock_seconds` | number | Elapsed real time from receiving the brief to returning the envelope. |

**Rule**: every top-level field above must appear in every envelope. Use empty values (`{}`, `[]`, `""`, `0`) for absent data. Do NOT omit keys.

---

## `status` field — valid values

| Value | Meaning | Commit? |
|---|---|---|
| `completed` | All enumerated synthesis dimensions attested; `novel_decisions` is empty. Proceed to Phase 4.5a attestation lint. | Yes |
| `blocked` | Implementer encountered at least one synthesis-class decision NOT enumerated in the plan's `synthesis_dimensions` block. `novel_decisions` MUST be non-empty when status is `blocked`. | No — orchestrator routes to resolution loop |

**When to use `blocked`**: any time the implementer faces a synthesis-class decision that was not listed in the plan's `synthesis_dimensions` block. The threshold is permissive: any such decision triggers `blocked`, not only ones judged "material." This permissive threshold is intentional — it enables the experiment to measure the silent-decision rate without implementer-side filtering.

**Resolution-resume flow (from implementer perspective)**:

1. Implementer returns `status: blocked` with `novel_decisions` populated.
2. Orchestrator dispatches each novel decision to `tier: thinking` for resolution.
3. Orchestrator re-dispatches implementer with original brief PLUS a `## Novel Decision Resolutions` appended section.
4. Implementer treats resolved decisions as authoritative; does NOT re-add them to `novel_decisions`.
5. Loop repeats until `status: completed` OR hard-fail counter reaches N=3. On hard-fail, orchestrator escalates to user.

**Extended-object form for `synthesis_attestation`** (accepted by C3 lint): each dimension may be expressed as a string shorthand (`"applied"` / `"deviated"` / `"n/a"`) OR as an object with `status` and `claim_text` fields:

```json
"synthesis_attestation": {
  "thinking_tier_resolution_format": {
    "status": "applied",
    "claim_text": "tier:thinking responses are plain-text answers stored in novelDecisionResolutions[].resolution"
  }
}
```

Both forms are accepted. Object form is preferred when `claim_text` provides verifiable evidence for lint.

---

## `synthesis_attestation` contract

When the plan includes a `synthesis_dimensions:` block, the implementer must attest to each named dimension:

```yaml
synthesis_attestation:
  <dimension_id>: "applied" | "deviated" | "n/a"
  # if "deviated", include:
  <dimension_id>_deviation_reason: "<explanation>"
```

- `applied` — the implementer followed the synthesis guidance as specified.
- `deviated` — the implementer diverged. The `<id>_deviation_reason` field is required.
- `n/a` — the dimension was not applicable to this implementer's files-owned slice.
- `{}` (empty object) — the plan has no `synthesis_dimensions` block; no attestation required.

---

## `novel_decisions` contract

If the implementer encountered a synthesis-class decision that was NOT listed in the plan's `synthesis_dimensions` block, they MUST:

1. Add an entry to `novel_decisions` describing what was decided and why.
2. Set `status: blocked` — do NOT commit, do NOT proceed. Return early.
3. NOT decide silently — the orchestrator cannot audit what it cannot see.

When `status: blocked` and `novel_decisions` is non-empty, the orchestrator halts the commit pipeline and routes each decision to `tier: thinking` for resolution. The implementer is then re-dispatched with resolutions appended. Hard-fail counter is N=3 (see `agents/build-orchestrator.md` Phase 3 blocked-envelope branch). Resolved decisions are stored in `state.json.novelDecisionResolutions[]`.

```json
"novel_decisions": [
  {
    "decision": "Chose X over Y for Z reason",
    "reasoning": "The plan did not address this tradeoff; X is safer because..."
  }
]
```

---

## Examples

### Minimal envelope (non-UI commit, no synthesis dimensions, all criteria pass)

```yaml
envelope:
  branch: "exp/synth-decisions-mixed"
  commit_sha: "pending"
  files_changed:
    - "/abs/path/to/project/references/implementer-envelope-schema.md"
  loc_added: 95
  loc_removed: 0
  f_criteria:
    F1: pass
    F2: pass
    F3: pass
  synthesis_attestation: {}
  novel_decisions: []
  notes: "Straightforward doc creation. No judgment calls."
  wall_clock_seconds: 42
```

### Rich envelope (UI commit, one deviation, one novel decision)

```yaml
envelope:
  branch: "feat/dashboard-refresh"
  commit_sha: "a3f9c12"
  files_changed:
    - "/abs/path/app/components/DashboardCard.tsx"
    - "/abs/path/app/components/StatusBadge.tsx"
  loc_added: 87
  loc_removed: 34
  f_criteria:
    F1: pass
    F2: pass
    F3: fail
    F4: pass
  synthesis_attestation:
    calm_precision_signal_rule: deviated
    calm_precision_signal_rule_deviation_reason: "StatusBadge uses a background pill for error state because the design-system token `status-error-bg` is already locked in and removing it would require a breaking token rename outside files-owned."
    gestalt_grouping: applied
  novel_decisions:
    - decision: "Extracted shared DateFormatter util into lib/date.ts"
      reasoning: "Both DashboardCard and StatusBadge independently formatted dates with identical logic. Plan did not enumerate a shared-util decision; adding novel_decisions entry rather than deciding silently."
  notes: "F3 fails because the icon-only fallback label is missing; this requires a copy decision the plan did not resolve. Logged as a follow-up for the orchestrator."
  wall_clock_seconds: 310
```

---

## Consumer parsing contract

The orchestrator and scope-auditor MUST:

- Reject envelopes missing any top-level field (treat as malformed).
- Treat `f_criteria` entries with value `"fail"` as blocking the commit if the criterion is marked required-for-ship in the brief.
- Treat `synthesis_attestation` entries with value `"deviated"` as requiring orchestrator review before marking the phase complete.
- Surface all `novel_decisions` entries in the phase report for the user.
- When `status == "blocked"` AND `novel_decisions` is non-empty: halt the commit pipeline and enter the C5 resolution loop (see `agents/build-orchestrator.md` Phase 3 blocked-envelope branch). Do NOT commit. Do NOT proceed to Phase 4.5a.
- When `status == "blocked"` AND `novel_decisions` is empty: treat as malformed (contradictory state). Request a revision from the implementer.
- After resolution loop resolves all novel decisions and implementer returns `status: completed`, proceed normally to Phase 4.5a attestation lint.
