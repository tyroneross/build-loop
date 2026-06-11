<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Memory Systems — orchestrator reference

Build-loop reads/writes four memory stores. Loaded on demand at Phase 1 Assess and Phase 4 Review sub-step F.

## The four stores

| Store | Path | Purpose | Scope |
|---|---|---|---|
| Run history | `.build-loop/state.json.runs[]` | Per-build outcome + diagnostic trail. Phase 6 Learn scans this for recurring patterns. | Project-local |
| Episodic decisions | `<memory-root>/projects/<project>/decisions/*.md` (canonical); legacy paths only when `BUILD_LOOP_MEMORY_MIGRATION_MODE=1` | MADR-style decisions. Topic-identity supersession by `primary_tag + entity`. | Project-tagged, repo-deletion-survivable |
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
- `canonical_memory`: root and project `MEMORY.md` / `constitution.md` from `<memory-root>`, plus `scripts/memory_facade.py recall()` results over canonical project files and local runs. If the root `constitution.md` is missing, `scripts/context_bootstrap.py` seeds it once from `templates/memory/constitution.md.template` before reading; existing files and project-specific constitutions are never overwritten. Semantic/Postgres reads are opt-in via `--include-postgres` so the default Phase 1 pass stays file-backed and fast.
- Ephemeral project plans must be archived before cleanup removes them. Use `scripts/archive_project_plan.py <plan> --workdir "$PWD"` to copy them into `build-loop-memory/projects/<slug>/archive/plans/<YYYY-MM-DD>/`; pass `--remove-source` only when the local file should be deleted after the archive write succeeds.
- `repo_local`: `.build-loop/feedback.md`, `.build-loop/state.json` summary including `runs[-3:]` and backend health when present, plus current `.build-loop/intent.md`, `.build-loop/goal.md`, and `.build-loop/plan.md`.
- `codex_memory`: `~/.codex/memories/MEMORY.md` registry hits and bounded excerpts from linked `rollout_summaries/*` files.
- `rally`: best-effort `coordination_status.py` result when coordination context exists.

**Degradation**: every source carries `reasons[]`. Missing Codex memory, absent repo-local files, skipped or down Postgres, unavailable optional Coding Debugger, or Rally errors are context-quality signals, not blockers. Surface high-impact gaps in the Assess brief.

### 1b. Re-read cadence — long/autonomous mode only (WP-G1)

Short runs read once at Phase 1 (above). In **LONG / AUTONOMOUS mode ONLY**, re-read
memory at each iterate-loop entry and each phase boundary, **gated by
`scripts/memory_staleness_check.py`** so it is a no-op when nothing changed:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_staleness_check.py --workdir "$PWD" --json
# stale=true → re-run the §1 bootstrap; stale=false → skip (cheap milestone-vs-HEAD read)
```

The staleness check is a single cheap file read (latest milestone `commit` sha vs
commits-since count); it costs almost nothing when clean. The re-read catches two
things: parallel-session writes landing in canonical memory mid-run, and the run's
OWN accumulating decisions (see incremental writes, G2). Classic short runs skip
this entirely — the once-at-Phase-1 read is sufficient when the run is brief.

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

Apply the canonical recall-optimized write rule in
`build-loop-memory/references/2026-06-11-memory-discipline-prompt.md`
(`version: 2026-06-11.1`) before every durable memory write: recall first,
write only future-recallable facts, avoid duplicates, classify by indexed
lane, stamp provenance through the writer in use, and verify reachability from
the relevant recall surface.

The **run entry + milestone** (the structured summary of the whole run) is written
once on the final Review pass — that aggregate is correctly batch-at-Review-G.

### Incremental durable writes — at discovery time (WP-G2, crash-resilience)

Durable **lessons / decisions / falsifiers** are written INCREMENTALLY at discovery
time, NOT batched to Review-G. The same total volume, written earlier:

- When a lesson is learned, a decision is made, or a falsifier is named mid-run,
  append it to canonical memory on the spot via `scripts/write_decision/__main__.py`
  (decisions) or `scripts/memory_writer.py` (lessons/reusable memories) — the
  append-immediately contract.
- Review-G then does a final **dedup sweep** over what accumulated (it no longer
  originates the writes, it reconciles them).

Why: the batch-at-Review-G model loses every lesson when a run crashes before close
(the resume / 529 / OOM scenario; documented closeout-never-fires-on-crashed-work
class). Incremental append means a crash at iterate-3 still leaves iterate-1/2's
lessons durable. Pairs with the G1 re-read: the run's own incremental writes are
exactly what the staleness-gated re-read picks back up.

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

- **Cross-project learnings** (new tool, deployment pattern, user preference) → `<memory-root>/lessons/<type>_<slug>.md` via `scripts/memory_writer.py --scope top-level write ...`.
- **Project-specific learnings** (design decisions, internal conventions, gotchas) → `<memory-root>/projects/<project>/lessons/<type>_<slug>.md` via `scripts/memory_writer.py --scope project --project <project> write ...`.

Do not hand-write project decision markdown. Use the paired decision writer. It
writes `projects/<slug>/decisions/`, regenerates that lane's `INDEX.md`, and
records `indexes/updates.jsonl`. Generated master-index reachability is still
incomplete for project decisions: `rebuild_memory_indexes.py` does not
content-scan `decisions/`, so verify through the `memory_facade` read API or the
decision lane/index until the scanner/map split is reconciled.

Reference capture has a lane mismatch today: `memory_writer.py` has `research`
as a project sublane but not `references`; `reference_capture` writes to
`projects/<slug>/research/`, while
`build-loop-memory/scripts/rebuild_memory_indexes.py` scans `references/` and
not `research/`. For generated-index recall, write `type: reference` content
under `projects/<slug>/lessons/references/`, or update both writer and indexer
to agree on `references` or `research`.

Evaluate any skill authored during the build (Skill-on-Demand §SKILL.md): keep, promote, or drop. Record the decision in memory.

## Decision-store paths over time

Decisions live under TWO paths today (canonical + legacy). The orchestrator and any verification check MUST go through the `scripts.memory_facade` read API, not raw filesystem assumptions.

| Path | Status | Notes |
|---|---|---|
| `<memory-root>/projects/<project>/decisions/NNNN-YYYY-MM-DD-slug.md` | **Canonical (current)** | New writes land here. `<project>/` is resolved via `scripts/project_resolver.py` from `cwd → project tag`. |
| `<repo>/.episodic/decisions/NNNN-YYYY-MM-DD-slug.md` | Legacy migration/archive input | Pre-cutover decisions. Active reads include it only when `BUILD_LOOP_MEMORY_MIGRATION_MODE=1`. |

**Read path**: `scripts.memory_facade.recall(..., kind="decision", ...)` reads canonical indexes/files and, only in migration mode, legacy paths. **Direct filesystem reads are fragile** — a verification rule that `ls`'d only the legacy path returned a phantom miss because the new canonical was authoritative. Locked by lesson `lesson-bl-decision-store-path-cutover`; consume it through the facade instead of hard-coding the lesson-file path.

**Write path**: `scripts/write_decision/__main__.py` writes to the canonical (new) path by default. The legacy path is only written when explicitly requested by tests fixturing pre-cutover state.

**INDEX.md**: each decision-store directory has its own `INDEX.md`. The facade reads both indexes and merges by ID. Do NOT edit `INDEX.md` by hand — `write_decision.py` regenerates it atomically as part of the memory-triad write.
