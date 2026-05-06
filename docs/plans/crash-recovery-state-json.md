# Plan: state.json checkpoint that actually fires on crash

Status: **draft, not started**
Target version: **v0.11.0**
Authoring date: 2026-05-06
Authoring context: written after a 529 Overloaded crashed a Phase H+I dispatch mid-Execute. Partial work survived on disk; agent reasoning state was lost. Recovery was manual (read worktree, identify gaps, finish, commit). This plan closes that gap so a future crash can be resumed by re-dispatching the orchestrator with `--resume`.

## Constraint envelope (the user's explicit limits)

1. **No latency tax on the hot path.** Checkpoint writes must be fire-and-forget, sub-5ms, atomic temp+rename.
2. **No complex new processes.** Filesystem only. No Redis, no Postgres, no daemons.
3. **No token bloat.** Don't dump conversation transcripts. Write structured summaries only — orchestrator already has the data in memory; serializing it to disk costs zero new tokens.

## Identifiers & dispatch surface

Two cross-cutting decisions that every mitigation depends on.

### `run_id` provenance

- **Generated**: once, at the end of Phase 1 Assess, by the orchestrator.
- **Format**: `run_<UTC-timestamp>_<8-char-suffix>` (e.g. `run_20260506T221433Z_a1b2c3d4`). Suffix derives from a hash of `(timestamp, intent_md_sha, working_branch)` so it's stable for a given assess output but unique across re-dispatches.
- **Persisted**: written to `.build-loop/state.json.execution.run_id` as the *first* execution-block write, before any chunk dispatch. All subsequent M1/M2 writes carry it.
- **Today's state.json has no `run_id` field** — commit 3 (M2 schema migration) owns introducing it. Existing state.json files without a `run_id` are treated as "no in-flight run" by resume.

### `--resume` is a slash-command argument, not a CLI flag

Build-loop's orchestrator is dispatched via the `/build-loop:run` Skill, not a Python entrypoint. There is no argv parsing. The actual surface:

1. **User invokes**: `/build-loop:run --resume <run-id>` (or `/build-loop:run --resume latest` for convenience).
2. **Skill body** (in `skills/build-loop/SKILL.md`) inspects its argument string; if `--resume` is present, it:
   - Reads `.build-loop/state.json.execution` and `.build-loop/subagent-results/<run-id>/`.
   - Validates the run is resumable (schema version match, run_id exists, last phase ≠ `report`).
   - Dispatches the build-orchestrator subagent with a `RESUME_MODE` prefix in the prompt, carrying `run_id`, `completed_chunks[]`, and `remaining_chunks[]`.
3. **Build-orchestrator agent** (`agents/build-orchestrator.md`) gains a new §0 "Resume mode" section that branches: when `RESUME_MODE` is present, skip Phase 1 Assess + Phase 2 Plan, read existing `intent.md` + `plan.md`, jump to Phase 3 Execute with only `remaining_chunks[]`.

Frontmatter is **not** the parsing layer. Frontmatter only declares which model the agent runs on. The skill body is the parsing layer; the agent prompt is the wiring layer.

### `latest` resolution

`--resume latest` resolves to the most recent run_id in `.build-loop/state.json.execution` whose `phase != "report"` and whose `last_heartbeat_at` is older than the current invocation. If multiple incomplete runs exist (rare — would require a second build dispatched on top of an unfinished one), the user is asked to pick.

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
    "schema_version": 1,
    "run_id": "run_20260506T221433Z_a1b2c3d4",
    "phase": "execute | review | iterate | report",
    "iterate_attempt": 0,
    "in_flight_chunks": ["phase-h-embed-daemon", "phase-i-wiki-local"],
    "completed_chunks": [
      {"chunk_id": "phase-h-embed-daemon", "status": "fixed", "completed_at": "..."},
      {"chunk_id": "phase-i-wiki-local",  "status": "partial", "completed_at": "..."}
    ],
    "queued_chunks": ["phase-h-tests", "phase-i-tests"],
    "file_ownership": {
      "phase-h-embed-daemon": ["scripts/embed_daemon.py", "scripts/embed_backend.py"],
      "phase-i-wiki-local":   ["scripts/wiki_local.py"]
    },
    "last_heartbeat_at": "2026-05-06T22:14:35.102Z",
    "started_at": "2026-05-06T22:13:01.000Z",
    "crashed_at": null
  }
}
```

**Mechanism**: orchestrator updates state.json atomically (temp + rename via `scripts/write_run_entry.py`'s existing pattern) at these trigger points:
- **Run start** (Phase 1 Assess complete): write `run_id`, `schema_version`, `started_at`, `phase: "execute"`, populate `queued_chunks` + `file_ownership` from the plan.
- **Before dispatching each implementer**: move chunk_id from `queued_chunks` → `in_flight_chunks`.
- **After receiving each implementer's return**: move chunk_id from `in_flight_chunks` → `completed_chunks` with status; refresh `last_heartbeat_at`.
- **On phase transition**: update `phase` field.
- **On Iterate attempt**: increment `iterate_attempt` (preserves the 5x cap across resume — without this, resume could silently bypass it).
- **On clean completion** (Phase 4 Review-F success): set `phase: "report"` — this is the "no resume needed" sentinel.

**Cost**: 1 write per dispatch + 1 write per return + 1 per phase transition. Each <5ms via temp+rename. Zero token impact.

**Implementation**: extend `scripts/write_run_entry.py` with `update_execution_state(run_id, action, chunk_id, status)` helper. Add 3 callsites in `agents/build-orchestrator.md` §Phase 3 + §Phase 5. State.lock pattern already exists for safety.

### M3 — Slash-command resume surface (orchestration of M1+M2)

**Surface**: `/build-loop:run --resume <run-id>` or `/build-loop:run --resume latest`. See "Identifiers & dispatch surface" above for the parsing layer (it's the Skill body, not the agent frontmatter).

**Mechanism**: when the Skill detects `--resume`:
1. Resolve `run-id` (literal or `latest` heuristic).
2. Validate: state.json exists, `execution.run_id` matches, `execution.schema_version` is supported, `execution.phase != "report"`.
3. Read `.build-loop/subagent-results/<run-id>/*.json` for structured envelopes.
4. Compute the remaining work list = `queued_chunks` + any `in_flight_chunks` whose envelope is missing or has `status != fixed`.
5. **Concurrent-modification check**: for each chunk already in `completed_chunks` with status `fixed`, run `git status` against its `file_ownership[chunk_id]` files. If any have been hand-modified (mtime > `completed_at` in working tree, or unstaged changes), demote that chunk back into the work list with `status: concurrent_modification_detected` and surface to the user via the orchestrator prompt.
6. Skip Phase 1-2 (Assess + Plan already happened — read existing `.build-loop/intent.md` + `.build-loop/plan.md`).
7. Dispatch build-orchestrator subagent with `RESUME_MODE` prompt prefix carrying `run_id`, `remaining_chunks[]`, and `iterate_attempt`.
8. All later phases proceed normally; commits, validation, and Iterate honor the preserved `iterate_attempt` counter.

**Cost**: zero on normal builds (only fires when `--resume` is passed). Saves Opus tokens on resume by skipping work that already shipped.

**Implementation**:
- Extend the `/build-loop:run` skill body (`skills/build-loop/SKILL.md`) with `--resume` argument parsing and the validation steps above.
- Add §0 "Resume mode" to `agents/build-orchestrator.md` — a branch the agent enters when its prompt opens with `RESUME_MODE: …`. Branch reads the carried `remaining_chunks[]` directly; does not re-derive from disk.
- New helper `scripts/resume_resolver.py` (≤80 LoC) for the validation + work-list computation; testable in isolation without dispatching a real build.

### M4 — Crash detection (primary: heartbeat staleness; secondary: Stop/SubagentStop hook)

**The signal we need**: at the start of a fresh `/build-loop:run` invocation, "is there an incomplete run that should be resumed?"

**Reliability problem with hooks**: Claude Code's Stop hook fires on user-session end; SubagentStop on subagent end. A 529 mid-tool-stream may not flush either hook cleanly. Network drops, OOMs, and `kill -9` definitely don't. Hook-only crash detection is brittle.

**Primary mechanism — derive at next run start, no hook dependency**:
When `/build-loop:run` is invoked *without* `--resume`, the Skill body checks: does `.build-loop/state.json.execution` exist with `phase != "report"` AND `last_heartbeat_at` more than 5 minutes ago? If yes, surface to the user: *"An incomplete build was detected (run_id=X, last heartbeat N minutes ago, M of K chunks complete). Resume with `/build-loop:run --resume X` or start fresh? Starting fresh will not delete the incomplete state — it persists until manually cleared."* This is a pure read-side inference, fires every time, requires no hook to have run.

**Secondary mechanism — best-effort hook annotation**:
Build-loop's existing Stop hook (already fire-and-forget per `feedback_hook_design.md`) gets one extra responsibility: if `state.json.execution.phase != "report"`, set `crashed_at: <ISO timestamp>` and `crash_signal: "stop_hook"`. When this fires, it's a *cleaner* signal than heartbeat staleness; when it doesn't, the heartbeat path still works. SubagentStop is **not** wired — orchestrator-as-subagent termination is the common crash path and SubagentStop has been reported flaky in long sessions.

**Cost**: ~10ms in the Stop hook when it fires. Zero token impact. The heartbeat-staleness check runs once per fresh dispatch and is a single state.json read.

**Implementation**:
- Extend `hooks/hooks.json` Stop array with `scripts/state_finalize.py --workdir "$CLAUDE_PROJECT_DIR" --mark-incomplete-as-crashed` (best-effort).
- Add the heartbeat-staleness check to `skills/build-loop/SKILL.md` Phase 1 entry; wire it to the same user-prompt surface the `--resume latest` path uses (shared code via `scripts/resume_resolver.py`).

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

## Implementation plan — six small commits

Each commit is independently revertable. No grand-merge.

| Commit | Scope | Files | Tests | Acceptance gate |
|---|---|---|---|---|
| 1 | M1 helper script + schema | `scripts/write_subagent_result.py` (new, ≤40 LoC) | `tests/test_write_subagent_result.py` (atomic write, retry suffix, schema validation) | Helper writes valid JSON to expected path; concurrent calls don't clobber. |
| 2 | M1 wiring in orchestrator | `agents/build-orchestrator.md` (modify §Phase 3 step 8) | None — agent prompt change | Manual test: dispatch a small build, kill mid-execute, verify `.build-loop/subagent-results/<run-id>/*.json` files exist. |
| 3 | M2 schema extension + heartbeat helper | `scripts/write_run_entry.py` (extend with `update_execution_state`); migration to add `execution` field to existing state.json | `tests/test_run_entry_execution_state.py` (heartbeat trigger points, atomic write under concurrent calls) | Helper updates atomically; existing tests pass. |
| 4 | M2 wiring in orchestrator | `agents/build-orchestrator.md` (modify §Phase 3 + §Phase 5 to call helper at 3 trigger points) | Skill-level smoke test against a mock build | State.json shows correct in-flight / completed / queued state during a real build. |
| 5 | M3 — `--resume` skill surface + resolver | `skills/build-loop/SKILL.md` (parse `--resume`, validation, dispatch with `RESUME_MODE` prefix); `agents/build-orchestrator.md` (new §0 Resume mode); `scripts/resume_resolver.py` (new, ≤80 LoC, validation + work-list + concurrent-modification check) | `tests/test_resume_resolver.py` (validation, latest resolution, concurrent-modification demotion); `tests/test_resume_orchestration.py` (end-to-end with mocked subagents) | Path A acceptance gate passes — fault injection mid-execute, `--resume` finishes the build with the correct remaining chunks. |
| 6 | M4 — heartbeat-staleness detection + Stop hook annotation | `skills/build-loop/SKILL.md` (heartbeat-staleness branch on no-`--resume` dispatch); `hooks/hooks.json` (add Stop entry); `scripts/state_finalize.py` (new, ≤30 LoC) | `tests/test_state_finalize.py`; `tests/test_heartbeat_staleness.py` | Path C acceptance gate passes — re-dispatch without `--resume` after a crash surfaces the resume prompt. |

**Estimated effort**: 2-3 days of focused work. Each commit ≤250 LoC. Commit 5 is the biggest (resume_resolver + skill parsing + agent §0); the rest are scoped tightly.

## Acceptance gate (whole feature)

Reproduce the conditions of the 529 crash that motivated this plan. There is no orchestrator OS process to SIGTERM — the orchestrator runs as a Claude Code subagent. Three realistic repro paths:

**Path A — synthetic 529 injection (preferred for CI):**
1. Set env `BUILD_LOOP_INJECT_FAULT=after_chunk_2` before dispatching `/build-loop:run` against a 4-chunk test plan.
2. The orchestrator's implementer-dispatch wrapper checks this env after each chunk return; if matched, it raises a simulated 529 to terminate the subagent stream.
3. Inspect `.build-loop/state.json.execution` — must show `phase: "execute"`, 2 chunks in `completed_chunks` with status `fixed`, 2 chunks in `queued_chunks`, accurate `file_ownership`, `iterate_attempt: 0`, recent `last_heartbeat_at`.
4. Inspect `.build-loop/subagent-results/<run-id>/*.json` — must contain envelopes for the 2 returned chunks.
5. Re-dispatch with `/build-loop:run --resume <run-id>`.
6. Verify the resumed orchestrator skips Phase 1 + Phase 2, executes only the 2 remaining chunks, runs Review + Iterate normally, and reaches Phase 4 Review-F clean.
7. Confirm `state.json.execution.phase` ends as `"report"`; `subagent-results/<run-id>/` has been archived to `runs/<run-id>/`.

**Path B — user-session interrupt (manual smoke test):**
Dispatch a small build, hit `Ctrl-C` mid-Phase-3, re-dispatch with `--resume`. Validates real-world crash recovery.

**Path C — heartbeat-staleness without `--resume` (validates M4 primary signal):**
Run Path A, then re-dispatch `/build-loop:run` *without* `--resume`. Verify the Skill detects the stale heartbeat and prompts the user: "Incomplete build detected (run_id=X)… resume or start fresh?"

**Cross-cutting validation:**
- Final commit history shows ONE clean commit per chunk (or the existing single-commit-per-build pattern), no stray "fixup" or "resume" markers.
- `iterate_attempt` is preserved across resume (test by injecting a fault during Phase 5 Iterate attempt 2; resume must pick up at attempt 2, not reset to 0).
- Concurrent-modification detection: hand-edit one of the `completed_chunks`'s files between crash and resume; verify the resume orchestrator surfaces it rather than silently skipping.

## Risks

| Risk | Mitigation |
|---|---|
| State.json write contention under high parallelism (4 implementers returning simultaneously) | The existing `state.json.lock` file pattern already handles this. Verify it works under M2's increased write frequency. |
| `--resume` against a state.json from an old build-loop version (schema drift) | M3 must include schema-version check. Refuse to resume incompatible runs with a clear error message. |
| Subagent envelopes pile up in `.build-loop/subagent-results/` indefinitely | Two cleanup paths: (1) **Successful build** archives at Phase 4 Review-F into `.build-loop/runs/<run-id>/` and removes `subagent-results/<run-id>/`. (2) **Crashed build** envelopes are NOT cleaned by Review-F (it never runs). They get cleaned at the start of the *next* `/build-loop:run` invocation: when the user chooses "start fresh" instead of `--resume`, the prior run_id's directory is archived as `runs/<run-id>.abandoned/`. A manual `/build-loop:gc` command clears anything older than 30 days as a last resort. |
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

After user signoff, dispatch as `/build-loop:run` with this plan as the spec. Single feature branch `feat/state-json-resume`. Six commits per the table above. PR with all six squashed into one logical change (or kept separate if the user prefers per-commit review — both are reasonable here).
