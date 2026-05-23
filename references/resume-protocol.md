<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Resume Protocol — Crash Recovery via state.json

Build-loop's crash-recovery surface. Loaded on demand by the build-orchestrator agent when its incoming prompt opens with `RESUME_MODE:` or when Phase 1 needs to act on a stale-heartbeat detection. Detail intentionally lives here, not in the orchestrator skeleton, to keep the skeleton ≤200 lines.

Authoring context: written after a 529 Overloaded crashed a Phase H+I dispatch mid-Execute. Partial work survived on disk; agent reasoning state was lost. Recovery was manual. This protocol closes the gap so a future crash can be resumed by re-dispatching the orchestrator with `--resume`.

## §0 Resume Mode flow (agent-side)

When your incoming prompt opens with `RESUME_MODE:` you have been re-dispatched to finish a build that crashed mid-Execute. The Skill body has already validated the resume request and run the concurrent-modification check. The prompt prefix carries everything you need:

```
RESUME_MODE: run_id=<id>; remaining_chunks=<json-array>; iterate_attempt=<n>; concurrent_modifications=<json-array>
```

When you see this prefix:

1. **Skip Phase 1 Assess and Phase 2 Plan entirely.** Read `.build-loop/intent.md` and `.build-loop/plan.md` for context; do not re-derive.
2. **Restore the in-memory chunk pointer** from `remaining_chunks`. Each entry has `{chunk_id, files, prior_status, reason}`. The `reason` field tells you why it's in the list:
   - `queued` — never dispatched
   - `in_flight_no_clean_return` — dispatched-but-crashed (or returned with non-fixed status)
   - `completed_then_hand_modified` — M3 concurrent-modification demotion
3. **For each `concurrent_modifications` entry**, surface to the user before re-dispatching: "Chunk `<id>` was previously marked complete, but `<files>` have been hand-edited since. Redo the chunk (default) or keep the hand-edits?" Use `AskUserQuestion`. Default is redo.
4. **Set `iterate_attempt` in your in-memory state** to the carried value — do NOT reset to 0. The 5x cap is preserved across resume.
5. **Jump directly to Phase 3 Execute** dispatching only the `remaining_chunks`. All later phases (Review, Iterate, Report) proceed normally and follow the same M1/M2 heartbeat rules — every implementer return goes through `write_subagent_result.py`, every dispatch/return updates `state.json.execution`.
6. **At Review-F**, the run completes normally with `update_execution_state(state_path, 'complete')`. There is no "resume completion" sentinel separate from a clean build; the M2 schema does not distinguish "completed-from-resume" from "completed-from-fresh-start."

## Path A test injection (synthetic 529)

When `BUILD_LOOP_INJECT_FAULT` is set in the environment, the implementer-dispatch wrapper checks the value AFTER each chunk return. Recognized values: `after_chunk_<n>` raises a simulated 529 after the n-th chunk (1-indexed) returns. This is the M3 acceptance gate's primary mechanism — see `tests/test_resume_orchestration.py` for the canonical Path A flow. Zero cost when unset; the env-flag check is a single dict lookup.

The fault-injection helper lives in `tests/test_resume_orchestration.py:_maybe_inject_fault`. Real orchestrator dispatches mirror the same check inline (read env var; raise if matched).

## Heartbeat-staleness path (crash-resume staleness signal)

> **Naming note.** This crash-recovery staleness signal previously shared the
> "M4" label with the (now-removed) concurrent-collision mechanism. They were
> always separate concerns sharing zero code — this path reads
> `state.json.execution` heartbeat via `scripts/resume_resolver.py`; it has
> nothing to do with concurrent-presence collision, which is now owned solely
> by Rally Point presence (`scripts/rally_point/presence.py` — see
> `references/multi-session-coordination.md` and `KNOWN-ISSUES.md` §M4). The
> label is disambiguated below; the behavior is unchanged.

When `/build-loop:run` is invoked WITHOUT `--resume`, the Skill body runs the resume resolver with `--resume-arg ""`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/resume_resolver.py \
    --workdir "$PWD" \
    --resume-arg "" \
    --staleness-minutes 5
```

If the resolver returns `decision: "prompt_user"`, the Skill body surfaces to the user verbatim (using the `reason` field from the resolver):

> "Incomplete build detected (run_id=X, last heartbeat N min ago). Resume with `/build-loop:run --resume X` or start fresh? Starting fresh will not delete the incomplete state — it persists until manually cleared."

This fires every fresh dispatch, regardless of whether the Stop hook ran. It is the **crash-resume staleness signal** (the primary crash-recovery signal). The Stop hook annotation (crash-resume secondary annotation) is best-effort; when it fires, `state.json.execution.crash_signal` is set to `"stop_hook"` for forensic visibility, but the prompt path does not depend on it.

## Resolver decision matrix

| `--resume-arg` value | Pre-state               | Decision     | Action                                                                  |
|----------------------|-------------------------|--------------|-------------------------------------------------------------------------|
| `""` (no flag)       | no state.json            | `fresh`      | proceed normally                                                         |
| `""` (no flag)       | execution.phase=report   | `fresh`      | proceed normally (clean prior exit)                                      |
| `""` (no flag)       | heartbeat fresh          | `fresh`      | proceed normally                                                         |
| `""` (no flag)       | heartbeat stale          | `prompt_user`| skill surfaces resume-or-fresh prompt                                    |
| `"<run-id>"`         | run_id mismatch          | `abort`      | refuse with reason; user picks correct id or starts fresh                |
| `"<run-id>"`         | schema_version mismatch  | `abort`      | refuse with reason; user upgrades or starts fresh                         |
| `"<run-id>"`         | phase=report             | `abort`      | refuse — already complete                                                |
| `"<run-id>"`         | match + incomplete       | `resume`     | dispatch orchestrator with RESUME_MODE prefix                             |
| `"latest"`           | no incomplete run         | `abort`      | refuse — nothing to resume                                                |
| `"latest"`           | one stale incomplete run | `resume`     | resolve to that run_id, then dispatch                                     |

## Cleanup behavior

- **Successful build**: at Phase 4 Review-F, the orchestrator archives `.build-loop/subagent-results/<run-id>/` into `.build-loop/runs/<run-id>/` and removes the original directory. (Implementation pending — referenced in plan §Risks; subagent-results pile-up not a blocker for v0.11.)
- **Crashed build envelopes**: NOT cleaned by Review-F (it never ran). They get cleaned at the start of the next `/build-loop:run` invocation when the user chooses "start fresh" instead of `--resume`. The prior run_id's directory is then archived as `.build-loop/runs/<run-id>.abandoned/`.
- **Manual gc**: a `/build-loop:gc` command (future) clears anything older than 30 days as a last resort.

## Out-of-scope

- LangGraph-style cross-machine resume (single-machine local-first tool by design)
- UI for inspecting incomplete builds (CLI tools are enough)
- Auto-retry on 529 specifically (Anthropic SDK already does this; once their retry budget exhausts, we surface)
- Cross-build resume (each build is its own run_id)
