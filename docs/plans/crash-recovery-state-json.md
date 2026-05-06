# Plan: state.json checkpoint that actually fires on crash

Status: **draft, not started**
Target version: **v0.11.0**
Authoring date: 2026-05-06
Authoring context: written after a 529 Overloaded crashed a Phase H+I dispatch mid-Execute. Partial work survived on disk; agent reasoning state was lost. Recovery was manual (read worktree, identify gaps, finish, commit). This plan closes that gap so a future crash can be resumed by re-dispatching the orchestrator with `--resume`.

## Constraint envelope (the user's explicit limits)

1. **No latency tax on the hot path.** Checkpoint writes must be fire-and-forget, sub-5ms, atomic temp+rename.
2. **No complex new processes.** Filesystem only. No Redis, no Postgres, no daemons.
3. **No token bloat.** Don't dump conversation transcripts. Write structured summaries only — orchestrator already has the data in memory; serializing it to disk costs zero new tokens.

## Lost-data inventory, ranked by recovery cost × context value

Recovery cost = effort to recreate from scratch. Context value = degree to which the missing data drives correct routing on resume. Both 1-5.

| # | Lost item | Recovery cost | Context value | Composite | Notes |
|---|---|---|---|---|---|
| 1 | **Implementer subagent return envelopes** (status, files_changed, verifications, notes per chunk) | 5 | 5 | **25** | Re-running an implementer to recompute its outcome costs full Sonnet+Opus tokens AND time. Without these, orchestrator can't tell which chunks already shipped vs which still need work. |
| 2 | **Active chunk pointer** ("currently executing chunk 3 of 6, chunks 1-2 returned `fixed`") | 4 | 5 | **20** | The plan file (Phase 2 output) survives on disk, but the dynamic in-flight pointer doesn't. Without it, resumer doesn't know where to pick up. |
| 3 | **MECE chunk → files mapping** | 3 | 4 | **12** | Plan markdown captures intent but the dynamic file-ownership graph the orchestrator built lives in context. Reconstructible from the plan but with effort. |
| 4 | **Test run outcomes** ("Phase 4 sub-step B last result: pass on chunks 1-2, fail on chunk 3 with `evidence_stale`") | 2 | 4 | **8** | Cheap to re-run tests, but knowing the outcome lets resumer skip to Phase 5 Iterate without burning Phase 4 again. |
| 5 | **Git commit intent** ("chunks 1-2 ready to commit; chunk 3 still pending") | 2 | 3 | **6** | Cheap to re-derive from `git status` + plan, but explicit intent disambiguates. |
| 6 | **Loaded MLX model in subagent process** | 1 | 1 | **1** | Already mitigated by Phase H daemon. No action. |

Items 1-3 are the load-bearing losses. Item 4 is a nice-to-have. Items 5-6 don't need explicit mitigation.

## Mitigations within the constraint envelope

### M1 — Persist subagent return envelopes immediately on receipt (closes #1)

**File**: `.build-loop/subagent-results/<run-id>/<chunk-id>.json`

**Mechanism**: when the orchestrator receives an implementer subagent's return value, immediately atomic-write it to disk before doing anything else with it. One file per chunk. Append-only naming so retries get suffixed (`<chunk-id>.attempt-2.json`).

**Schema** (already exists in `agents/build-orchestrator.md` §Phase 5; just add the disk write):
```json
{
  "chunk_id": "phase-h-embed-daemon",
  "status": "fixed | partial | scope_breach | deferred_architecture | evidence_stale | plan_malformed | needs_dependency | failed | concurrent_modification_detected",
  "files_changed": ["scripts/embed_daemon.py", "scripts/embed_backend.py"],
  "verifications": ["pytest tests/test_embed_daemon.py: 25/25 passed"],
  "notes": "...",
  "received_at": "2026-05-06T22:14:33.241Z",
  "attempt": 1
}
```

**Cost**: 1 file write per implementer return (~500 bytes JSON). Sub-1ms. Zero token impact (data is already in orchestrator memory).

**Implementation**: extend `agents/build-orchestrator.md` §Phase 3 step 8 ("Coordination checkpoints") with: *"After each implementer subagent returns, atomic-write its envelope to `.build-loop/subagent-results/<run-id>/<chunk-id>.attempt-<n>.json` before any further routing decision."* Add a tiny helper script `scripts/write_subagent_result.py` (≤40 LoC) that handles the temp+rename.

### M2 — Heartbeat the orchestrator's chunk pointer to state.json on every dispatch + return (closes #2)

**File**: `.build-loop/state.json` — extend the existing schema, do NOT create a new file.

**New fields**:
```json
{
  "execution": {
    "run_id": "run_20260506T221433Z_a1b2c3d4",
    "phase": "execute | review | iterate",
    "in_flight_chunks": ["phase-h-embed-daemon", "phase-i-wiki-local"],
    "completed_chunks": [
      {"chunk_id": "phase-h-embed-daemon", "status": "fixed", "completed_at": "..."},
      {"chunk_id": "phase-i-wiki-local",  "status": "partial", "completed_at": "..."}
    ],
    "queued_chunks": ["phase-h-tests", "phase-i-tests"],
    "last_heartbeat_at": "2026-05-06T22:14:35.102Z"
  }
}
```

**Mechanism**: orchestrator updates state.json atomically (temp + rename via `scripts/write_run_entry.py`'s existing pattern) at three trigger points:
- Before dispatching each implementer (move chunk_id from `queued_chunks` → `in_flight_chunks`)
- After receiving each implementer's return (move chunk_id from `in_flight_chunks` → `completed_chunks` with status)
- On Phase transition (update `phase` field)

**Cost**: 1 write per dispatch + 1 write per return + 1 per phase transition. Each <5ms via temp+rename. Zero token impact.

**Implementation**: extend `scripts/write_run_entry.py` with `update_execution_state(run_id, action, chunk_id, status)` helper. Add 3 callsites in `agents/build-orchestrator.md` §Phase 3 + §Phase 5. State.lock pattern already exists for safety.

### M3 — Add `--resume <run-id>` flag to the orchestrator (orchestration of M1+M2)

**Mechanism**: when invoked with `--resume <run-id>`:
1. Read `.build-loop/state.json.execution.completed_chunks[]`
2. Read `.build-loop/subagent-results/<run-id>/*.json` for the structured envelopes
3. Compute the remaining work list = `queued_chunks` + any `in_flight_chunks` whose envelope is missing or has `status != fixed`
4. Skip Phase 1-2 (Assess + Plan already happened — read existing `.build-loop/intent.md` + `.build-loop/plan.md`)
5. Resume at Phase 3 Execute with only the remaining chunks
6. All later phases proceed normally

**Cost**: zero on normal builds (only fires when `--resume` is passed). Saves Opus tokens on resume by skipping work that already shipped.

**Implementation**: extend the build-orchestrator agent's frontmatter / dispatch pattern to recognize `--resume`. Add a new section to `agents/build-orchestrator.md` §Phase 1 that branches on resume mode.

### M4 — Stop-hook safety net: write a final state snapshot if the orchestrator process is dying (closes #4)

**File**: `.build-loop/state.json.execution.last_test_run` field.

**Mechanism**: Claude Code's Stop hook fires when an agent terminates (gracefully or otherwise). Build-loop already has Stop hooks for transcript scanning and decision capture. Add one more: if `.build-loop/state.json.execution` exists and `phase != "report"`, write a `crashed_at: <timestamp>` field. This is the heuristic flag for "previous run didn't finish cleanly — resume is available."

**Cost**: ~10ms in the Stop hook (already a fire-and-forget context). Zero token impact.

**Implementation**: extend `hooks/hooks.json` Stop array with a new entry that calls `scripts/state_finalize.py --workdir "$CLAUDE_PROJECT_DIR" --mark-incomplete-as-crashed`.

## Mitigations OUTSIDE the constraint envelope (mention but don't recommend)

### X1 — Per-chunk git commits on success
- **Constraint violation**: changes the commit-discipline workflow (build-loop's clean-history default). Optional config flag could expose it (`autoCommitPerChunk: true`).
- **When it'd help**: very long builds where git is the easiest signal of "what's done."
- **Why it's marked "outside"**: the user (in CLAUDE.md feedback) values clean PR history; per-chunk commits make squashing more error-prone.

### X2 — Stream-replay logging (full conversation transcript)
- **Constraint violation**: token bloat, complexity. Even compressed transcripts of an Opus orchestrator run are 20K+ tokens.
- **When it'd help**: forensic debugging of what the orchestrator was thinking when it crashed.
- **Why it's marked "outside"**: transcripts already live in `~/.claude/projects/<dir>/<id>.jsonl` — bookmark plugin handles this. Don't duplicate.

### X3 — Distributed agent state (Redis / Postgres checkpointer, LangGraph-style)
- **Constraint violation**: massive complexity, new dependency, new failure surface. The user explicitly said "no complex new processes."
- **When it'd help**: multi-machine builds, very long-running flows (days), human-in-the-loop with approval gates.
- **Why it's marked "outside"**: build-loop is a single-machine local-first tool. Filesystem persistence is sufficient.

### X4 — Idempotent retries via deterministic chunk_ids + content-hash dedup
- **Constraint violation**: would need every implementer subagent to be deterministic, which they're not (LLM non-determinism).
- **When it'd help**: pure code-generation workloads where the same prompt always produces the same output.
- **Why it's marked "outside"**: doesn't apply to LLM agent work.

### X5 — Subagent process supervisor (separate Python process holds subagent state)
- **Constraint violation**: new process, new failure surface, introduces IPC complexity.
- **When it'd help**: if we wanted in-flight subagents to survive orchestrator crashes (we don't — subagents are short-lived per chunk).
- **Why it's marked "outside"**: solves a problem we don't have.

## Implementation plan — five small commits

Each commit is independently revertable. No grand-merge.

| Commit | Scope | Files | Tests | Acceptance gate |
|---|---|---|---|---|
| 1 | M1 helper script + schema | `scripts/write_subagent_result.py` (new, ≤40 LoC) | `tests/test_write_subagent_result.py` (atomic write, retry suffix, schema validation) | Helper writes valid JSON to expected path; concurrent calls don't clobber. |
| 2 | M1 wiring in orchestrator | `agents/build-orchestrator.md` (modify §Phase 3 step 8) | None — agent prompt change | Manual test: dispatch a small build, kill mid-execute, verify `.build-loop/subagent-results/<run-id>/*.json` files exist. |
| 3 | M2 schema extension + heartbeat helper | `scripts/write_run_entry.py` (extend with `update_execution_state`); migration to add `execution` field to existing state.json | `tests/test_run_entry_execution_state.py` (heartbeat trigger points, atomic write under concurrent calls) | Helper updates atomically; existing tests pass. |
| 4 | M2 wiring in orchestrator | `agents/build-orchestrator.md` (modify §Phase 3 + §Phase 5 to call helper at 3 trigger points) | Skill-level smoke test against a mock build | State.json shows correct in-flight / completed / queued state during a real build. |
| 5 | M3 + M4 — `--resume` flag + Stop-hook crash detection | `agents/build-orchestrator.md` (new §Resume mode); `hooks/hooks.json` (add Stop entry); `scripts/state_finalize.py` (new, ≤30 LoC) | `tests/test_state_finalize.py`; `tests/test_resume_orchestration.py` (mock incomplete state, verify resume picks up at correct chunk) | Crash a real build mid-Phase-3, re-dispatch with `--resume <run-id>`, verify only remaining chunks execute. |

**Estimated effort**: 1-2 days of focused work. Each commit ≤200 LoC.

## Acceptance gate (whole feature)

Reproduce the conditions of the 529 crash that motivated this plan:
1. Dispatch a 2-phase build via `/build-loop:run`.
2. Mid-Phase-3, kill the orchestrator process (SIGTERM).
3. Inspect `.build-loop/state.json` — must show `execution.phase: "execute"`, accurate in-flight / completed / queued lists, and `crashed_at` timestamp from the Stop hook.
4. Inspect `.build-loop/subagent-results/<run-id>/*.json` — must contain envelopes for every chunk that returned before the crash.
5. Re-dispatch with `/build-loop:run --resume <run-id>`.
6. Verify the resumed orchestrator skips Assess + Plan + the already-completed chunks, picks up at the first incomplete chunk, and runs the build to clean completion.
7. Final commit history shows ONE clean commit per chunk (or the existing single-commit-per-build pattern, depending on chunk granularity), no stray "fixup" or "resume" markers.

## Risks

| Risk | Mitigation |
|---|---|
| State.json write contention under high parallelism (4 implementers returning simultaneously) | The existing `state.json.lock` file pattern already handles this. Verify it works under M2's increased write frequency. |
| `--resume` against a state.json from an old build-loop version (schema drift) | M3 must include schema-version check. Refuse to resume incompatible runs with a clear error message. |
| Subagent envelopes pile up in `.build-loop/subagent-results/` indefinitely | Add a Phase 4 Review-F cleanup step: after a successful build, archive the run's envelopes into `.build-loop/runs/<run-id>/` and delete the subagent-results subdirectory. Failed/incomplete runs keep their envelopes for the next resume. |
| Stop hook fires on normal end-of-conversation (not just crash) | The existing `state.json.execution.phase` field disambiguates: if `phase == "report"`, it's a clean exit, not a crash. Stop hook's `--mark-incomplete-as-crashed` only fires when the phase isn't `report`. |
| "Crashed mid-Phase-2 Plan" leaves an unparsable state.json | M2 only writes execution state during Phase 3 onward. Phase 1-2 crashes mean the build never started executing chunks; nothing to resume. The user re-dispatches normally. |

## Out of scope

- LangGraph-style thread_id resume across machines
- Anthropic Background Tasks integration (separate API surface)
- Auto-retry on 529 specifically (Anthropic SDK already does this; once their retry budget exhausts, we surface)
- Cross-build resume (each build is its own run_id)
- UI for inspecting incomplete builds (CLI tools are enough)

## Sources consulted

- LangGraph Persistence docs (PostgresSaver, MemorySaver, thread_id resume pattern) — https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph TypeScript Checkpointing Guide — https://langgraphjs.guide/persistence/
- Eunomia: Checkpoint/Restore Systems (Evolution, Techniques, Applications in AI Agents, 2025) — https://eunomia.dev/blog/2025/05/11/checkpointrestore-systems-evolution-techniques-and-applications-in-ai-agents/
- Anthropic claude-code issue #25413 (Background Task agent output retrieval ~40% failure in multi-agent session) — https://github.com/anthropics/claude-code/issues/25413
- Mager.co: LangGraph Build Stateful Multi-Agent Systems That Don't Crash — https://www.mager.co/blog/2026-03-12-langgraph-deep-dive/
- arXiv 2601.13671: The Orchestration of Multi-Agent Systems — https://arxiv.org/html/2601.13671v1
- IntuitionLabs: Agentic AI Workflows with Temporal Orchestration — https://intuitionlabs.ai/articles/agentic-ai-temporal-orchestration

## Confidence

**High** on the plan structure: the lost-data ranking is grounded in the actual 529 crash this session, not hypothetical. The mitigation pattern (persist after every step, resume by reading state) is what every production agent framework converges on (LangGraph, Temporal, Couchbase checkpointer).

**Medium** on the parameter values: the schema additions (`execution.in_flight_chunks`, `execution.completed_chunks`, etc.) are first-cut. Likely needs one tuning pass after the first real resume run.

**Low** on whether `--resume` will fire often enough to justify itself: 529s and other mid-build crashes are uncommon (this session is the first I've seen). The feature pays for itself by being there when needed, not by being used often.

## Next action

After user signoff, dispatch as `/build-loop:run` with this plan as the spec. Single feature branch `feat/state-json-resume`. Five commits per the table above. PR with all five squashed into one logical change.
