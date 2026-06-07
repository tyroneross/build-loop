<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 3: Execute — full protocol (parallel)

Extracted from `agents/build-orchestrator.md` §"Phase 3: Execute (parallel)". The agent body keeps a tight summary + a pointer here. Load before running Phase 3.

## Pre-dispatch scope-audit gate (mandatory for `modifies_api: true`)

For each chunk, if `modifies_api: true` AND `state.json.scopeAuditorStatus.<chunk_id>` is not `"passed"`, halt dispatch. Run `Agent(subagent_type="build-loop:scope-auditor", ...)` against owned files + plan's caller-audit table. `verdict: scope_clean` → write `passed`, proceed. `verdict: scope_gap_found` → absorb missing callers OR record acceptance in `state.json.scopeGapAccepted[]`. Doc-only commits skip. See `agents/scope-auditor.md`.

## Per-chunk dispatch sequence

- Identify independent tasks from the plan's dependency graph; dispatch one subagent per task.
- **Parallel dispatch record**: when the dependency graph allows fan-out, record `parallel_batch:` with the chunk IDs dispatched together. When work appears parallelizable but you intentionally serialize, record `parallel_skipped_reason:` with the blocking dependency or tool limitation. Review-G lint treats this as required evidence, not prose.
- Each agent gets: task description, file paths, integration contract, fallback snippets, intent packet from `.build-loop/intent.md`, MECE ownership packet (`owns`, `does not own`, `interface contract`, `integration checkpoint`), `architecture_context:` block read verbatim from `.build-loop/architecture/scout-cache/chunk-<N>.json`, and `available_capabilities:` block from `state.json.activeCapabilities["3"][-1].results[:8]` (fall back to `["2"]`). Implementers MUST flag any change that exits the architecture slice in their return envelope. Do NOT re-dispatch the scout in Phase 3 and do NOT re-run `capability_shortlist.py`.

### MECE-packet lint (advisory) before peer-handoff dispatch

For every `Agent(subagent_type=..., ...)` call that includes a peer-handoff brief (Phase 3 implementer dispatch, cross-session worktree-isolated dispatch, Codex slice handoff), write the brief to a tmpfile and run `python3 scripts/brief_mece_validator.py --brief-file <tmpfile> --json` BEFORE the Agent call. Exit 0 → proceed silently. Exit 1 → log a `[warn]` line citing the missing fields (one of `owns / does-not-own / interface-contract / integration-checkpoint`); dispatch ALSO proceeds (C-FLOW pattern — non-blocking lint, never halts execution). Surface lint findings in the run report's `## Done` section as `[warn] MECE lint: chunk <id> missing <fields>`. Skip the lint ONLY for pure-read handoffs ("go look at this and tell me what you find"). Memory citation: `feedback_handoffs_require_mece_packets`. Constitution: `references/coordination-rules.md` §"MECE Packets".

### Brief-discipline guardrail (the [ME] backstop)

Before dispatching any `Agent(subagent_type=..., ...)`, the brief MUST pass two checks. Failure to honor either is the proximate cause of the session-2026-06-04 visual-verify miss; BL-1's scanner is the safety-net, this is the prevention.

1. **Tool reachability** — every tool, MCP, or scanner the brief names as a means of verification MUST appear in the dispatched subagent's `tools:` frontmatter (e.g. the default `implementer` toolset is `["Read", "Write", "Edit", "Bash", "Glob", "Grep"]` — no `Agent`, no IBR MCP, no `scan_macos` direct call). If the brief needs an unreachable tool (IBR `scan_macos`, NavGator MCP, native AX driver), either (a) dispatch the agent that owns that tool, (b) call the tool from the orchestrator and pass the result into the brief as `evidence_paths`/`verification`, or (c) drop the tool reference. Never name it speculatively.
2. **No pre-authorized symbol-only fallback for UI verification** — the brief MUST NOT bless `nm`, `strings`, `otool`, `git grep`-over-binaries, "compiles cleanly", "identifier present", or any other symbol/string-only signal as a substitute for visual/AX verification when the chunk touches a UI file and `uiTarget != null`. Phrasing like "if X is unreachable, fall back to nm/strings" is forbidden; the correct fallback is `status: blocked` naming the unreachable verifier — the chunk routes back to Iterate with a clear failure mode, not forward with stale evidence. BL-1's gate (`scanners/require-visual-evidence.mjs`, exit 2) is the enforcement; this bullet is the upstream prevention.

### Implementer brief template + UI rules

Structure each brief per `references/implementer-brief-template.md`. Pre-Execute checklist: schema pre-grepped, reference patterns verified, LoC target computed, test cap math shown, scope-auditor caller-audit accepted. If any can't be populated, return to Phase 2.

For UI work, every visible control/nav item/option/message/chart must have working behavior, clear user purpose, matching contract entry. Prefer one primary action. UI briefs must include contract section + `templates/ui-subagent-prompt.md`.

At coordination checkpoints, verify outputs align before continuing.

Consult `model-router` per dispatch — see `references/capability-routing.md` §"Phase 3 routing".

## M1/M2/M3 — Crash-recovery + context snapshots + cost-ledger

At every dispatch + return, write subagent envelopes atomically (M1), heartbeat the chunk pointer + working-state (M2), write non-blocking `.build-loop/context/` snapshots at dispatch/return/phase boundaries, and emit cost-ledger rows (M3). Full procedure in `references/m-series-protocol.md` (six M2 trigger points: run_id provenance + run start, dispatch_chunk, return_chunk, phase_transition, iterate_attempt, complete).

### Step 9 — Per-agent invocation telemetry (cost-ledger extension)

Closes OPEN-ITEMS #4. Wrap every `Agent(subagent_type=..., ...)` call site with TWO `scripts/write_cost_ledger_row.py` invocations sharing the same `--task-id` (format: `t-<8-hex>`, generated before dispatch with `scripts/dispatch_identity.py --plain`):

1. **Dispatch row** (before `Agent(...)` returns): `--status dispatched --called true --started-at <iso> --elapsed-seconds null`. If the call site decided NOT to dispatch (gate untripped, trivial bypass, prior-pass cached), emit instead with `--called false --skipped-reason "<why>" --status dispatched`.
2. **Return row** (after `Agent(...)` returns): `--status <terminal value from envelope> --called true --failed <bool> --issue-found <bool> --elapsed-seconds <float> --completed-at <iso>`. The orchestrator backfills `--downstream-iterate-outcome <enum>` once Phase 5 closes (one of `clean | resolved-on-pass-1 | resolved-on-pass-2-or-later | overflow-to-followup | abandoned`).

Consumers join the two rows on `task_id`. The `agent` field carries the `subagent_type`. Together this provides: which agents were dispatched (vs skipped); how long each took; whether they found issues; and what the downstream verification did with their output. All new fields are additive + nullable — existing cost-ledger readers ignore them. Storage stays at `~/.bookmark/cost-ledger.jsonl`.

## Phase 3 commit step (single-writer git contract)

Full protocol in `references/single-writer-commit-protocol.md`. Implementers no longer call `git add` or `git commit` (Hard rule 4); the orchestrator owns `.git/` as a single-writer resource. After each parallel batch returns, sequentially per envelope with `status: fixed | partial | completed`: the commit step executes unconditionally — no operator confirmation is required, even in interactive mode (the autonomy gate classifies `git commit` as `auto`). Sequence: context-snapshot pre_commit → verify-no-staged-residue → verify-scope → stage → commit (pre-commit hook runs HERE; no `--no-verify`) → verify-landed → context-snapshot post_commit → attestation-lint → synthesis-critic (UI files only) → independent-auditor advisory (with trivial bypass). For `status: blocked`, see `references/halt-and-ask-protocol.md` (C5 architectural-decision backstop, N=3 cap, Thinking-tier resolver).

## Dogfood reload checkpoint (self-recursive runtime changes)

After a validated stage/commit touches build-loop runtime surfaces, run
`python3 scripts/dogfood_reload_checkpoint.py detect` on the changed files. If
`runtime_change_required: true`, create a checkpoint and do not dispatch the
next stage until `dogfood_reload_checkpoint.py status` returns `ready: true`.
Read `references/dogfood-reload-checkpoint.md` for the create/ACK/fallback
commands and host-specific reload boundaries. A stale or unmanaged peer is not
a reason to wait silently: record `fallback` with `reassign`, `defer`, or
`continue_solo`, then post the decision to Rally.

## Phase 3 UI spot-check (between chunks)

After each chunk's commit step closes and before the next chunk dispatches, fire `ui-validator` whenever `uiTouched: true`. Full protocol — `uiTouched` signal table, dispatch brief, routing on return (`pass`/`fail`/`skipped`), iteration budget, and render-path fallback — in `references/halt-and-ask-protocol.md` §"Phase 3 UI spot-check (between chunks)".

## Phase 3 design-contract reconciliation (between chunks)

After UI spot-check returns AND whenever `uiTouched: true OR dataChanges: true` for the just-closed chunk, dispatch `Agent(subagent_type="build-loop:design-contract-specialist", prompt='trigger_point: phase3-chunk-close, chunk_id: <id>')` with: ui-validator's envelope `design_doc_delta` (when present), architecture-scout's `schema_delta` from `task: schema-map` (when `dataChanges: true`), the chunk's `files_changed`, the app slug, and `state_path`. The specialist integrates both deltas, writes `.build-loop/app-contract/*`, and may surface `novel_decisions[]` for the halt-and-ask resolver per `references/halt-and-ask-protocol.md`. Specialist auto-commits on `status: completed`; routes `novel_decisions[]` to the Thinking-tier resolver otherwise.
