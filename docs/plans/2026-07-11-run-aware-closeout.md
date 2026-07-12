# Plan: Run-aware transactional closeout

<!-- checklist
Item 1 — Auth guard: N/A: local Git lifecycle scripts, no server route.
Item 2 — External APIs: N/A: no new external API calls.
Item 3 — Rate-limit criterion: N/A: no paid API calls.
Item 4 — Discoverability: N/A: no UI surface.
Item 5 — Server/client boundary: N/A: local Python and Bash only.
Item 6 — Concurrency: The receipt is the write-ahead authority; every state projection acquires atomic_io.LockedFile and reloads current state; branch removal rechecks registration/OID and uses Git's checked-out-aware safe deletion.
Item 7 — Observability: JSON result and schema-v1 receipt carry run_id, branch, expected_oid, bundle path/verification, safety evidence, status, reason, and timestamps.
Item 8 — Input validation: argparse and collapse() reject strict latest inference, unknown run/branch attribution, and destructive calls without explicit owner release.
Item 9 — Stable ID traceability: U-01 -> F-01 -> D-01 -> T-01; U-01 -> F-02 -> D-02 -> T-02/T-03; U-01 -> F-03 -> D-03 -> T-04.
Item 10 — JSON spec object: present in the Spec Object section; markdown renders the interlinked needs, features, data points, tests, and ADR.
Item 11 — Blocking-and-novel question gate: no open questions; code, incident evidence, and the user's no-dropped-terminals priority determine the P0 behavior.
Item 12 — Low-reversibility ADRs: ADR-01 defines receipt authority, update order, recovery, and rollback.
Item 13 — Analytical lens: DSM for lifecycle writers/callers plus failure-mode analysis for the destructive transaction.
Item 14 — Handoff document: docs/plans/2026-07-11-run-aware-closeout.handoff.md links F-01/F-02/F-03 to ADR-01 and T-01..T-14.
Item 15 — Synthesis dimensions: N/A: no UI surface.
Item 16 — Risk reason: persistence contract.
Item 17 — UI input/output contract: N/A: no UI surface.
Item 18 — Dispatch tier per work item: plan/transaction design frontier; implementation sonnet; deterministic verification script.
Item 19 — Env-var manifest: N/A: no new external service.
Item 20 — Capability gap map: present with current source, target, action, ownership, and validation.
Item 21 — Single-shot build guardrails: present with enforceable evidence.
Item 22 — Read-before-edit map: present with exact reads and edit surfaces.
-->

risk_reason: persistence contract
modifies_api: true
scope_auditor_status: scope_clear
dispatch_tier: frontier

## Goal

Prevent terminal loss by making Build Loop branch/worktree closeout explicitly authorized, attributable, recoverable, and single-owner. The production-shaped defect is a legacy run row whose `run_id` is the Build Loop ID: Stop archived its execution pointer, collapse failed to recover it, and SessionStart GC bypassed the ledger. The repair preserves direct Phase-D compatibility where safe, makes every background path report-only, and requires an explicit owner-release signal for destructive finalization.

## Architecture note

Clean-sheet, a lifecycle service would own run identity, ref ownership, owner release, transaction journal, and Rally resolution in one process. Current constraints split those responsibilities across state writers, a Phase-D script, hooks, and an external Rally CLI. This plan establishes one destructive boundary inside `collapse_run.py`, uses a versioned write-ahead receipt to bridge crashes, reduces the hook/reaper to read-only discovery, and makes the canonical `run-closeout` phase post reject missing or invalid receipts. It does not pretend that gate can intercept a direct `rally say resolve`; native Rally enforcement remains tracked follow-up work.

## Spec Object (JSON)

```json
{
  "needs": [
    {"id": "U-01", "priority": "P0", "statement": "A live or ambiguously owned terminal worktree must never be removed by background cleanup."}
  ],
  "features": [
    {"id": "F-01", "need_ids": ["U-01"], "data_ids": ["D-01"], "test_ids": ["T-01", "T-05", "T-06", "T-07", "T-08"], "statement": "Transactional branch-scoped finalizer with explicit owner release."},
    {"id": "F-02", "need_ids": ["U-01"], "data_ids": ["D-02"], "test_ids": ["T-02", "T-03", "T-09", "T-10"], "statement": "Stop persists ownership; SessionStart and reaper are non-destructive by default."},
    {"id": "F-03", "need_ids": ["U-01"], "data_ids": ["D-03"], "test_ids": ["T-04", "T-11", "T-12", "T-13", "T-14"], "statement": "Executable terminal-post gate plus an honest direct-Rally bypass limitation."}
  ],
  "data_points": [
    {"id": "D-01", "semantic": "schema-v1 branch-closeout receipt is the mutation journal authority; createdRefs is a projection."},
    {"id": "D-02", "semantic": "canonical identity is build_loop_id, else run_id, else id; Stop records open ownership but never owner release."},
    {"id": "D-03", "semantic": "terminal receipt states are closed or retained; open, prepared, deferred, and error are retryable."}
  ],
  "tests": [
    {"id": "T-01", "criterion": "production-shaped legacy run completes verified bundle, receipt, ledger, and cleanup."},
    {"id": "T-02", "criterion": "SessionStart no-candidate and candidate paths remain report-only."},
    {"id": "T-03", "criterion": "reaper cannot act without explicit owner release."},
    {"id": "T-04", "criterion": "strict CLI fails when errors remain or terminal receipt is absent."},
    {"id": "T-05", "criterion": "locked, dirty, live-CWD, unregistered, or mismatched worktrees are retained."},
    {"id": "T-06", "criterion": "moved or newly checked-out branch prevents safe deletion."},
    {"id": "T-07", "criterion": "bundle failure or pre-mutation state failure causes no Git mutation."},
    {"id": "T-08", "criterion": "prepared receipt reconciles interruptions after worktree or ref removal."},
    {"id": "T-09", "criterion": "Stop materializes open ownership before archiving execution."},
    {"id": "T-10", "criterion": "Stop never writes owner release or prepared receipt."},
    {"id": "T-11", "criterion": "branch-specific bundle contains the exact expected OID."},
    {"id": "T-12", "criterion": "SessionStart no-candidate path remains within the two-second hook budget."},
    {"id": "T-13", "criterion": "canonical run-closeout phase post rejects missing, incomplete, or invalid receipt evidence."},
    {"id": "T-14", "criterion": "tracked integration test chains inline Stop ownership through merge and strict finalization."}
  ],
  "adrs": [
    {"id": "ADR-01", "decision": "Use collapse_run.py as sole destructive authority and a schema-v1 write-ahead receipt as transaction truth.", "rollback": "Restore the verified pre-selfmod bundle and disable destructive CLI calls; report-only reaper remains safe."}
  ]
}
```

## Locked Decisions

- Analytical lens: DSM plus failure-mode analysis.
- Canonical run identity is `build_loop_id || run_id || id`.
- `collapse_run.py` is the only code allowed to bundle/remove run worktrees or delete their branches.
- Destructive API/CLI calls require an explicit `owner_released=true` / `--owner-released`. Stop is a turn boundary and never supplies that authority.
- SessionStart always invokes report-only discovery. It cannot be made destructive by an environment variable.
- CWD, Rally, dirty, Git-lock, path-root, and branch-mapping checks are vetoes only; their absence is not authorization.
- Receipt-only transaction states are `prepared`, `retained`, `deferred`, and `error`. `createdRefs.status` keeps its existing public vocabulary: `open` until terminal, then `closed`, `kept_for_review`, `surfaced_unmerged`, or retryable `error`.
- The canonical `run-closeout` phase post is executable-gated by `branch_closeout_gate.py`; missing workdir/run identity, nonterminal ledger/receipt, invalid bundle/OID, or live terminal path rejects the post.
- Direct Rally resolution remains technically bypassable. Native interception in Rally is backlog item `BUILDLOOP-COORD-001`, out of this patch.

## ADR-01: Receipt authority and recovery

The receipt path is `.build-loop/branch-closeout/<canonical-run-id>.json`, `schema_version: 1`. The receipt is the write-ahead mutation journal; `runs[].createdRefs[]` and `runs[].branch_closeout` are query projections.

State machine:

- `open`: Stop recorded branch/worktree ownership; no bundle or release is implied.
- `prepared`: explicit owner release exists, the exact branch OID was bundled and verified, and both receipt plus state projection were persisted.
- `closed`: worktree is absent and the expected branch ref passed Git's checked-out-aware safe deletion.
- `retained`: explicit human/operator disposition preserves the ref/worktree.
- `deferred` and `error`: nonterminal, retryable, and never authorize deletion.

Write order:

1. Resolve one run and exact branch; capture expected OID and safety evidence from current state.
2. Create and verify a branch-specific bundle containing that OID.
3. Write receipt `prepared`; then lock, reload, and atomically project the attempt to `branch_closeout` while keeping `createdRefs.status=open`.
4. Recheck owner release, path mapping, Git lock, dirtiness, live CWD, merge state, and OID.
5. Remove the clean worktree without force; recheck registration/OID and use Git's checked-out-aware safe deletion.
6. Write receipt terminal state first; then lock, reload, and atomically project it using the existing `createdRefs.status` vocabulary.

Recovery:

- Crash before step 3: no mutation occurred.
- Crash after receipt but before state projection: receipt is authoritative; rerun projects it before mutation.
- Crash after worktree removal: prepared receipt and branch OID allow safe continuation.
- Crash after ref deletion: verified prepared receipt plus absent path/ref reconcile to `closed`.
- Crash after terminal receipt but before state projection: rerun projects the terminal receipt.
- Existing state writers do not all honor `LockedFile`; therefore the receipt, not state, is authoritative. Each projection reloads current state under the lock and is replayable if a non-cooperating writer later overwrites the projection.

## Scope

In scope:

- Transactional, exact-branch finalization and recovery.
- Durable ownership materialization at Stop.
- Report-only SessionStart and safe reaper delegation.
- CLI/API contract, tests, performance evidence, and mirrored host documentation.
- Canonical terminal phase-post receipt enforcement and its production-shaped tests.
- Durable backlog item `BUILDLOOP-COORD-001`, the active run-worktree spec correction, direct claim-release/Stop wrapper tests, and generated architecture outputs.

### Out of scope

- Easy Terminal UI, ptyd process hosting, or multi-agent-in-one-window implementation.
- Changing Rally's CLI/server to reject direct resolution without a receipt; backlog `BUILDLOOP-COORD-001`.
- Plugin release, version bump, installation, push, or deployment.
- Automatic cleanup of unattributed/orphan folders.

## Approach Lenses

- Clean-sheet: one lifecycle daemon owns terminal process identity, ref ledger, finalization, and handoff closure.
- Current constraints: centralize destructive behavior in the existing Phase-D script and make all other callers delegates.
- Bridge/backcast: ship schema-v1 receipts and explicit owner release now; later teach Easy Terminal/Rally to issue and enforce process-lifetime release tokens.
- Recommendation: take the bridge. It removes the observed data-loss/terminal-risk path without coupling this patch to a Rally or Easy Terminal release.

## Depends-on (reads-from)

- `.build-loop/state.json.runs[]` — verified: append_run and Stop write it.
- `.build-loop/state.json.execution` — verified: run identity/provisioning writes it.
- `.build-loop/state.json.historicalExecutions[]` — verified: Stop archives execution there.
- `git worktree list --porcelain` lock/branch records — verified by Git-backed tests.
- `git status --porcelain` cleanliness — verified by Git-backed tests.
- `/proc/*/cwd` or `lsof -d cwd` liveness sensor — verified locally; veto-only.
- Rally room/session visibility — verified optional defense-in-depth; never authorization.

## Activation Map

- SessionStart reporter — trigger: SessionStart matcher in `hooks/hooks.json` invokes `hooks/session-start-worktree-gc.sh` — verified-live: yes
- Stop ownership persistence — trigger: host Stop turn boundary invokes `hooks/closeout.sh stop` and `scripts/stop_closeout.py` — verified-live: yes
- Integrator finalizer — trigger: Phase-D/manual Codex call invokes strict `collapse_run.py --owner-released` after merge — verified-live: yes
- Terminal phase gate — trigger: canonical `post(kind="phase", payload={"phase":"run-closeout"}, workdir=..., run_id=...)` calls `branch_closeout_gate.py` before any coordination write — verified-live: yes

## Five-Commit Table

| # | Commit subject | Files owned | Depends on | dispatch_tier |
|---|---|---|---|---|
| C1 | docs(plan): define run-aware closeout contract | plan, handoff, `.gitignore`, `BACKLOG.md`, `.build-loop/backlog/**` | — | frontier |
| C2 | fix(closeout): add transactional finalizer | collapse module, both collapse suites, claim-release suite | C1 | sonnet |
| C3 | fix(closeout): persist Stop ownership | Stop module, unit tests, `hooks/test_closeout.sh` | C2 | sonnet |
| C4 | fix(closeout): make reapers report/delegate only | reaper package, SessionStart hook/tests, active isolation spec | C2/C3 | sonnet |
| C5 | fix(closeout): gate terminal post and wire contract | branch-closeout gate/tests, Rally post helper, Phase-D, coordination, AGENTS mirrors, generated architecture outputs | C2-C4 | sonnet |

## Capability Gap Map

| Capability/Workflow | Current source of truth | Target behavior | Gap | Build action | Owned files/contracts | Validation |
|---|---|---|---|---|---|---|
| Legacy identity recovery | `scripts/collapse_run.py` | Archived production-shaped run resolves exactly | only `run.build_loop_id` checked | canonical identity helper + exact match | collapse + execution tests | T-01 |
| Destructive transaction | collapse writes state after Git mutation | receipt prepared before mutation; reload-and-project state; expected-OID deletion | crash, stale-state, and TOCTOU windows | schema-v1 journal + replayable projections + rechecks | collapse + atomic_io contract | T-01/T-06/T-08 |
| Owner authority | age/Rally/CWD inference | explicit owner release required | no positive authority | API/CLI flag; background never supplies it | collapse/reaper/hook | T-02/T-03/T-10 |
| Stop ownership | `stop_closeout._release_identity` | open createdRef recorded before archive | archive can lose only pointer | materialize in same LockedFile transaction | stop_closeout | T-09/T-10 |
| Reaper ownership | reaper and hook mutate Git directly | discovery delegates to collapse; unattributed stays | multiple destructive authorities | remove raw mutation and orphan fallback | reaper + hook | T-02/T-03 |
| Integrator contract | Phase-D docs | strict receipt checked before the canonical terminal phase post | final response/post could omit finalization | add read-only post gate; retain direct Rally backlog | gate + post + references + AGENTS | T-04/T-13/T-14 |
| Performance | generic `--all` bundle observed at 194 MB/~11 s | exact-branch bundle and <2 s no-candidate hook | hot-path I/O too broad | bundle only expected ref; time hook | collapse + hook | T-11/T-12 |

## Single-Shot Build Guardrails

| Guardrail | Prevents | Evidence/test |
|---|---|---|
| Explicit owner release is mandatory | deleting an idle/live terminal after Stop | T-03/T-10 |
| SessionStart is permanently report-only | automatic terminal loss | T-02 + hook source assertion |
| Receipt prepared before mutation | ledger-less branch/worktree deletion | T-01/T-07/T-08 |
| Non-force removal + immediate recheck | lock/dirty TOCTOU deletion | T-05 + source assertion |
| Expected-OID plus checked-out-aware safe delete | deleting work that advanced or became live after bundle | T-05/T-06 |
| Exact branch root + attribution | touching unrelated worktrees | T-05 |
| Receipt is authoritative projection source | non-cooperating state writers or crash leave stale projections | T-08 |
| Canonical terminal post checks live receipt evidence | declaring the run closed after omitting finalization | T-13/T-14 |
| Pre-selfmod bundle retained | unrecoverable self-modification | bundle verify + self_mod_verify |
| No release/push in this run | unapproved production mutation | git status/remote check |

## Read-Before-Edit Map

| Work item | Read first | Why it matters | Edit after |
|---|---|---|---|
| C2 finalizer | collapse module, atomic_io, both collapse test files, claim-release test, `log_decision.py`, Phase-D contract | preserve public result/status vocabulary and claim release | collapse + tests |
| C3 Stop ledger | stop_closeout, append_run writer, stop tests, `hooks/test_closeout.sh` | keep ownership projection inside the archive transaction and prove wrapper activation | stop module + tests |
| C4 reapers | reaper package/tests, SessionStart hook/tests, hooks manifest, `docs/SPEC-run-worktree-isolation.md` | remove every direct destructive path, including prune, and update the active spec | reaper/hook/tests/spec |
| C5 gate/docs | Rally post helper, Phase-D, coordination rules, both AGENTS copies, artifact generators | reject incomplete terminal posts and keep source, Codex artifact, and architecture snapshots synchronized | gate/post/tests/docs/mirrors/generated artifacts |

## Ownership and dependency order

1. C1 owns the contract and handoff.
2. C2 owns all Git mutation and receipt schema.
3. C3 owns execution-to-run ownership projection only.
4. C4 owns candidate discovery and hook activation only; it may not mutate Git directly.
5. C5 owns caller instructions and mirror parity.

parallel_skipped_reason: C2-C4 share one evolving receipt/API schema and must serialize; read-only plan criticism and final independent audit remain delegated.

## F-Criteria

| ID | Priority | Criterion | Pass condition | Grader |
|---|---|---|---|---|
| T-01 | P0 | Legacy end-to-end closeout | verified exact-OID bundle, prepared/terminal receipt, closed projection, absent ref/path, zero errors | acceptance probe |
| T-02 | P0 | SessionStart safety | candidate and no-candidate hook executions make no Git mutation | hook integration tests |
| T-03 | P0 | Positive owner authority | no explicit release means no mutation in collapse/reaper | unit/integration tests |
| T-04 | P0 | Strict result | CLI exits nonzero when errors exist or terminal receipt missing | CLI tests |
| T-05 | P0 | Worktree vetoes | dirty/locked/live/unregistered/mismatch candidates remain | Git/process tests |
| T-06 | P0 | Ref identity | moved or newly checked-out ref remains and reports the safety mismatch | Git test |
| T-07 | P0 | Prepare failure | bundle/state failure causes no Git mutation | fault-injection tests |
| T-08 | P0 | Crash reconciliation | prepared/terminal receipt repairs each interruption point | recovery tests |
| T-09 | P0 | Stop ownership | open createdRef exists before execution archive | Stop test |
| T-10 | P0 | Stop non-authority | Stop writes neither owner release nor prepared receipt | Stop test |
| T-11 | P1 | Bundle scope/performance | bundle verifies exact ref/OID and is materially smaller than all-ref fixture | Git test/measurement |
| T-12 | P1 | Hook budget | no-candidate SessionStart completes under 2 seconds | timed test |
| T-13 | P0 | Terminal post forcing function | canonical run-closeout post rejects missing/incomplete/tampered receipt and accepts verified terminal or solo-main state | gate/post tests |
| T-14 | P0 | Inline integration | `run_stop` materializes ownership, external merge lands, and strict finalizer closes receipt/ledger/ref/path | tracked Git integration test |

## Q-Criteria

| ID | Criterion | Pass condition | Grader |
|---|---|---|---|
| Q-01 | Compatibility | existing explicit merged and unmerged Phase-D tests pass with explicit owner release | pytest |
| Q-02 | Atomicity | state and receipt writes use atomic_io; every state projection reloads under `LockedFile` | source review + tests |
| Q-03 | Shell quality | hook parses under Bash 3.2-compatible syntax and shellcheck where available | bash -n + shellcheck |
| Q-04 | Self-mod safety | self_mod_verify returns pass for every commit scope | verifier JSON |
| Q-05 | Independent review | critical/high findings are zero before merge | independent auditor |

## Falsifiers and risks

The design is rejected if any test or review shows:

- owner release is absent/unknown or written by Stop, yet mutation occurs;
- a claimed Rally receipt gate can be bypassed while documentation calls it enforced;
- a canonical `run-closeout` phase post succeeds for a branchful run without verified terminal receipt evidence;
- a verified bundle omits the exact expected OID;
- an interruption after prepare, worktree removal, ref deletion, or terminal receipt cannot reconcile;
- a concurrent state writer is overwritten;
- dirtiness/lock/liveness/path mapping changes between inspection and removal are ignored;
- a path outside the approved run-worktree roots is mutated;
- explicit Phase-D unmerged behavior changes unexpectedly;
- reaper or SessionStart mutates by default;
- branch-specific bundling is not materially smaller than the observed all-ref baseline fixture;
- the SessionStart no-candidate path exceeds two seconds.

## Verification

- `python3 skills/spec-writing/scripts/check_checklist.py --plan .build-loop/plan.md --json`
- `python3 scripts/plan_verify.py .build-loop/plan.md --repo . --json`
- `python3 scripts/acceptance_probe.py classify --goal .build-loop/goal.md --json`
- `python3 scripts/acceptance_probe.py rerun --goal .build-loop/goal.md --workdir . --json`
- `python3 .build-loop/acceptance_probe_closeout.py`
- `python3 -m pytest -q scripts/test_collapse_run.py scripts/test_collapse_run_execution_worktree.py scripts/test_stop_closeout.py scripts/test_session_start_worktree_gc.py scripts/test_branch_closeout_gate.py scripts/worktree_reaper/tests/test_reaper.py`
- `python3 -m pytest -q scripts/test_collapse_run_claim_release.py scripts/test_log_decision.py scripts/test_worktree_guard.py scripts/test_append_run.py scripts/test_build_loop_id.py scripts/test_build_loop_id_worktree.py`
- `bash hooks/test_closeout.sh`
- `python3 -m scripts.worktree_reaper --workdir . --dry-run --json`
- `python3 scripts/worktree_reaper/__main__.py --workdir . --dry-run --json`
- `python3 scripts/build_codex_plugin_artifact.py --check`
- `python3 scripts/architecture_diagram/generate.py --check`
- `bash -n hooks/session-start-worktree-gc.sh`
- `python3 scripts/self_mod_verify.py --workdir "$PWD" --scope auto --changed-files <changed files> --auto-revert --json`

## Caller Audit (Scope Auditor)

Initial verdict: `scope_gap_found`; all named gaps were absorbed into C1-C5. Final read-only recheck: `scope_clear`.

- Collapse callers: both main test suites, `test_collapse_run_claim_release.py`, AGENTS source/mirror, Phase-D, coordination source/mirror, build orchestrator, reaper delegate, legacy validation script.
- Stop callers: unit tests, `hooks/closeout.sh`, `hooks/test_closeout.sh`, Claude/Codex hook manifests, append-run and identity writers.
- Reaper callers: package tests plus both supported CLI entry modes; current production activation is only the Claude SessionStart hook.
- Terminal post callers: `scripts/rally_point/post.py` rejects invalid `run-closeout` phase records through `branch_closeout_gate.py`; direct native Rally resolve remains explicitly outside this gate.
- Generated dependents: `architecture/model.json`, `architecture/ARCHITECTURE.md`, and `docs/build-loop-flow-mockup.html`.
- Scope decision: keep receipt transaction states out of `createdRefs.status`, so `log_decision.py` needs compatibility tests but no schema edit.
- Host limitation: `.codex/hooks.json` does not activate SessionStart GC; this patch does not add it because background cleanup is report-only and Codex already uses the explicit/manual finalizer contract.
