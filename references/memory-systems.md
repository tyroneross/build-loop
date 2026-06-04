<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Memory Systems — orchestrator reference

Build-loop reads/writes four memory stores. Loaded on demand at Phase 1 Assess and Phase 4 Review sub-step F.

## The four stores

| Store | Path | Purpose | Scope |
|---|---|---|---|
| Run history | `.build-loop/state.json.runs[]` | Per-build outcome + diagnostic trail. Phase 6 Learn scans this for recurring patterns. | Project-local |
| Episodic decisions | `~/dev/git-folder/build-loop-memory/projects/<project>/decisions/*.md` (canonical); legacy paths only when `BUILD_LOOP_MEMORY_MIGRATION_MODE=1` | MADR-style decisions. Topic-identity supersession by `primary_tag + entity`. | Project-tagged, repo-deletion-survivable |
| Semantic facts | Postgres `agent_memory.<schema>.semantic_facts` | Embeddings + structured facts for hybrid retrieval. | Project-tagged, opt-in |
| Debugger incidents | `.build-loop/issues/*.md` plus optional standalone Coding Debugger MCP | Bug history with local recall; optional verdict-classifier feedback loop when Coding Debugger is installed. | Project-local by default; optional cross-project |

The **memory facade** at `scripts/memory_facade.py` exposes one `recall(query, kind, project, limit)` over all four with graceful degradation. Use it instead of writing four ad-hoc reads.

## Read protocol — Phase 1 Assess

Mirrors the write-protocol's executable shape (fenced commands + return-shape table + graceful-degradation matrix). The first operation is the automatic context bootstrap; empty results are valid; never raise on a missing backend. The orchestrator's Phase 1 imperative in `agents/build-orchestrator.md` MUST stay in lock-step with this section — when call wiring changes, update both.

### 1. Automatic context bootstrap

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/context_bootstrap.py \
  --workdir "$PWD" \
  --query "<goal-keywords>" \
  --output "$PWD/.build-loop/context-bootstrap.json" \
  --json
```

**Return shape**: JSON packet with `{ generated_at, workdir, project, query, terms, sources, agent_brief }`.

`sources` includes:
- `canonical_memory`: root and project `MEMORY.md` / `constitution.md` from `~/dev/git-folder/build-loop-memory`, plus `scripts/memory_facade.py recall()` results over canonical project files and local runs. Semantic/Postgres reads are opt-in via `--include-postgres` so the default Phase 1 pass stays file-backed and fast.
- `repo_local`: `.build-loop/feedback.md`, `.build-loop/state.json` summary including `runs[-3:]` and backend health when present, plus current `.build-loop/intent.md`, `.build-loop/goal.md`, and `.build-loop/plan.md`.
- `codex_memory`: `~/.codex/memories/MEMORY.md` registry hits and bounded excerpts from linked `rollout_summaries/*` files.
- `rally`: best-effort `coordination_status.py` result when coordination context exists.

**Degradation**: every source carries `reasons[]`. Missing Codex memory, absent repo-local files, skipped or down Postgres, unavailable optional Coding Debugger, or Rally errors are context-quality signals, not blockers. Surface high-impact gaps in the Assess brief.

### 1a. Live context snapshots (handoff/resume, not durable memory)

After bootstrap, Build Loop keeps the current handoff state fresh through:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/context_snapshot.py \
  --workdir "$PWD" \
  --trigger "<manual | interval | phase_transition | agent_dispatch | agent_return | pre_commit | post_commit>" \
  --phase "<phase>" \
  --run-id "$RUN_ID" \
  --message "<one-line current state>" \
  --if-changed \
  --json
```

**Return shape**: `{ ok, action: "written" | "skipped", snapshot_id, snapshot_path?, current_path }`.

**Writes**: `.build-loop/context/current.md`, `.build-loop/context/snapshots/*.json`, and trigger-specific JSONL sidecars for agent and commit boundaries. This is session/runtime context like Bookmark's useful handoff layer, but non-blocking and repo-local. Do NOT promote every snapshot into durable memory. Only Review-G or explicit decisions write reusable facts to `build-loop-memory`.

### 2. Unified recall facade (diagnostic/reference)

The bootstrap calls the facade directly. Use the standalone command when debugging the canonical memory layer itself:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_facade.py recall \
  --query "<goal-keywords>" \
  --limit 10
```

**Return shape**: JSON envelope `{ results_by_kind: {...}, merged: [...], reasons: [...], telemetry_correlation_id: "..." }`. Inspect `reasons[]` for `db_unavailable` / `mcp_unavailable` / `path_missing` signals — those are data, not failures. **Degradation**: any backend down -> that backend reports a `reason`; remaining backends still return rows.

### 3. Debugger incidents priming (recent-list)

The bootstrap includes build-loop incident results through the facade. If the build is debugging-heavy, also call:

```text
Skill("build-loop:debugging-memory") with { intent: "list-recent" }
```

**Return shape**: one-line summary `"N recent incidents in this project, top categories: [...]"`. Counts feed Phase 1's awareness of what's been failing lately. **Degradation**: optional Coding Debugger unavailable -> fall through to `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/fallbacks.md#bug-memory` (token-extract + grep over `.build-loop/issues/` and `.build-loop/feedback.md`). Flag debugger fallback in Review-F only when cross-project recall was requested and unavailable.

### 4. Optional Coding Debugger MCP shape (diagnostic reference; use only when installed)

```text
mcp__plugin_coding_debugger__list({ filter: { project: "<current>" }, limit: 10 })
```

**Return shape**: `{ incidents: [{ id, symptom, root_cause, fix, tags, created_at }, ...] }`. Surfaced here so a diagnostic check of the standalone debugger doesn't have to traverse the skill abstraction.

### 5. Backend health check (Priority 17)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/backend_health.py --workdir "$PWD"
```

**Why this exists**: `recall()` (step 2) gracefully degrades on Postgres-down or MCP-down — the orchestrator never visibly logs which backends responded. The health-check surface makes that explicit so the Phase 1 Assess brief can tell the user whether memory is at full or partial capacity.

**Return shape**: stdout one-liner `runs: OK N entries | decisions: OK N entries | semantic: DOWN postgres_unavailable | debugger: DOWN mcp_unreachable`. Full JSON envelope is written to `state.json.architecture.backendHealth` (`{ runs: {ok, count}, decisions: {ok, count}, semantic: {ok, reason?}, debugger: {ok, reason?}, summary, generated_at, total_duration_ms }`).

**Budget**: 5s per backend, 30s total. Exit 0 even when all backends are down — graceful degradation is the contract.

**Surface in the Phase 1 Assess brief**: the orchestrator must echo the one-liner so the user can see backend availability before any work begins.

### Return-shape & exit-code summary

| Step | Surface | Return shape | Empty-OK | On backend down |
|---|---|---|---|---|
| 1 | `context_bootstrap.py` | JSON packet w/ `sources.*.reasons[]` + `agent_brief` | yes | per-source `reason`; other sources still respond |
| 2 | `memory_facade.py recall` | JSON envelope w/ `reasons[]` | yes | per-backend `reason`; other backends still respond |
| 3 | `Skill("build-loop:debugging-memory")` | one-line text summary | yes | grep-fallback per `fallbacks.md#bug-memory` |
| 4 | optional `mcp__plugin_coding_debugger__list` | `{ incidents: [...] }` | yes | step 3 already covered the fallback |
| 5 | `scripts/backend_health.py` | one-liner + JSON envelope written to `state.json.architecture.backendHealth` | n/a | per-backend `ok: false` + `reason`; other backends still probable |

### Graceful-degradation matrix

| Failure mode | Step 1 | Step 2 | Step 3 | Step 4 | Step 5 |
|---|---|---|---|---|---|
| Postgres unavailable | `canonical_memory.reasons[]` records skip/down | `reason: db_unavailable` for semantic backend, others continue | n/a | n/a | `semantic.ok: false` |
| Optional Coding Debugger unavailable | `canonical_memory.reasons[]` records debugger unavailable when requested | `reason: debugger_unavailable` | grep fallback | unusable; rely on step 3 fallback | `debugger.ok: false` |
| `state.json` missing | `repo_local.reasons[]` records missing file | recall still runs other backends | n/a | n/a | runs may still report down |
| Codex MEMORY.md absent | `codex_memory.reasons[]` records missing registry | n/a | n/a | n/a | n/a |
| All backends down | packet still emits with populated `reasons[]` | envelope w/ all `reasons` populated, `results: []` | grep fallback | n/a | all relevant backends `ok: false` |

## Write protocol — Phase 4 Review sub-step F

Runs only on the final Review pass.

### Run entry — delegate to the deterministic writer

Do NOT hand-write JSON. Schema source-of-truth lives in `scripts/write_run_entry.py`.

```bash
RUN_ID=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/write_run_entry.py" \
  --workdir "$PWD" \
  --goal "$GOAL_SUMMARY" \
  --outcome pass \
  --phases-json '{"assess":{"status":"pass","duration_s":12},"plan":{...}}' \
  --files-touched-from-git \
  --diagnostic-commands "$(printf 'cmd1\ncmd2\n')" \
  --manual-interventions-json '[]' \
  --active-experimental-artifacts "skill-a,skill-b" \
  --security-findings-json .build-loop/issues/security-findings.json)
```

Capture `RUN_ID` from stdout and cite it in the scorecard. Always pass `--security-findings-json` even when `triggers.riskSurfaceChange` was false — the script silently treats a missing file as no findings. Exit codes: `0` ok, `1` validation error, `2` filesystem error.

### Resolved debugger incidents

Use the native `Skill("build-loop:debugging-store")`. Procedure also in `Skill("build-loop:debugging-memory")` §"Review-F outcome feedback":

- For each newly resolved Review-B/Iterate failure: invoke `build-loop:debugging-store` with `{symptom, root_cause, fix, tags: ["build-loop", project, layer], files}`.
- For each Review-B memory gate where a prior `KNOWN_FIX` or `LIKELY_MATCH` was applied: invoke `outcome` MCP tool with `{incident_id, result: "worked"|"failed"|"modified", notes}`. This trains the verdict classifier.

Both steps are required to close the memory-first gate's feedback loop. Skipping `outcome` means the verdict classifier never improves from this build's signal.

### Memory tier

Write new memory entries to the correct tier:

- **Cross-project learnings** (new tool, deployment pattern, user preference) → `~/dev/git-folder/build-loop-memory/lessons/<type>_<slug>.md` via `scripts/memory_writer.py --scope top-level write ...`.
- **Project-specific learnings** (design decisions, internal conventions, gotchas) → `~/dev/git-folder/build-loop-memory/projects/<project>/lessons/<type>_<slug>.md` via `scripts/memory_writer.py --scope project --project <project> write ...`.

Evaluate any skill authored during the build (Skill-on-Demand §SKILL.md): keep, promote, or drop. Record the decision in memory.

## Decision-store paths over time

Decisions live under TWO paths today (canonical + legacy). The orchestrator and any verification check MUST go through `scripts/memory_facade.py` (the read API), not the raw filesystem.

| Path | Status | Notes |
|---|---|---|
| `~/dev/git-folder/build-loop-memory/projects/<project>/decisions/NNNN-YYYY-MM-DD-slug.md` | **Canonical (current)** | New writes land here. `<project>/` is resolved via `scripts/project_resolver.py` from `cwd → project tag`. |
| `<repo>/.episodic/decisions/NNNN-YYYY-MM-DD-slug.md` | Legacy migration/archive input | Pre-cutover decisions. Active reads include it only when `BUILD_LOOP_MEMORY_MIGRATION_MODE=1`. |

**Read path**: `python3 scripts/memory_facade.py recall --kind decision --query "<text>"` reads canonical indexes/files and, only in migration mode, legacy paths. **Direct filesystem reads are fragile** — a verification rule that `ls`'d only the legacy path returned a phantom miss because the new canonical was authoritative. Locked by lesson `lesson-bl-decision-store-path-cutover`; consume it through the facade instead of hard-coding the lesson-file path.

**Write path**: `scripts/write_decision.py` writes to the canonical (new) path by default. The legacy path is only written when explicitly requested by tests fixturing pre-cutover state.

**INDEX.md**: each decision-store directory has its own `INDEX.md`. The facade reads both indexes and merges by ID. Do NOT edit `INDEX.md` by hand — `write_decision.py` regenerates it atomically as part of the memory-triad write.
