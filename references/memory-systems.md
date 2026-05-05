# Memory Systems — orchestrator reference

Build-loop reads/writes four memory stores. Loaded on demand at Phase 1 Assess and Phase 4 Review sub-step F.

## The four stores

| Store | Path | Purpose | Scope |
|---|---|---|---|
| Run history | `.build-loop/state.json.runs[]` | Per-build outcome + diagnostic trail. Phase 6 Learn scans this for recurring patterns. | Project-local |
| Episodic decisions | `.episodic/decisions/*.md` (legacy) OR `~/dev/git-folder/build-loop-memory/decisions/<project>/*.md` (post-cutover canonical) | MADR-style decisions. Topic-identity supersession by `primary_tag + entity`. | Project-tagged, repo-deletion-survivable |
| Semantic facts | Postgres `agent_memory.<schema>.semantic_facts` | Embeddings + structured facts for hybrid retrieval. | Project-tagged, opt-in |
| Debugger incidents | `claude-code-debugger` MCP `store`/`search`/`outcome` | Bug history with verdict-classifier feedback loop. | Cross-project; bundled MCP server |

The **memory facade** at `scripts/memory_facade.py` exposes one `recall(query, kind, project, limit)` over all four with graceful degradation. Use it instead of writing four ad-hoc reads.

## Read protocol — Phase 1 Assess

Mirrors the write-protocol's executable shape (fenced commands + return-shape table + graceful-degradation matrix). Each step is independently safe to run; empty results are valid; never raise on a missing backend. The orchestrator's Phase 1 imperative (5 numbered steps in `agents/build-orchestrator.md`) MUST stay in lock-step with this section — when call wiring changes, update both.

### 1. MEMORY.md tiers (global + project)

```bash
# Global tier (cross-project preferences and learnings)
Read("~/.build-loop/memory/MEMORY.md")
# Project tier (overrides global on key conflict)
Read("<repo>/.build-loop/memory/MEMORY.md")
```

**Return shape**: markdown text (or empty string if absent). Project keys override global. **Degradation**: missing file → empty string, no error.

### 2. Run-history priming (state.json runs[-3:])

```bash
# Slice the last 3 prior-run entries.
python3 -c "import json; s=json.load(open('.build-loop/state.json')); [print(r.get('run_id'), r.get('outcome'), r.get('root_cause','')) for r in s.get('runs',[])[-3:]]"
```

**Return shape**: zero to three lines, each `<run_id> <outcome> <root_cause>`. Catches "this build follows a similar one" context before `recall()` is queried. **Degradation**: missing/empty `runs[]` → no output, continue.

### 3. Unified recall facade (one read across four backends)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_facade.py recall \
  --query "<goal-keywords>" \
  --limit 10
```

**Return shape**: JSON envelope `{ results: [...], reasons: [...], elapsed_ms: N }`. Each result row is `{ store, score, payload }`. Inspect `reasons[]` for `db_unavailable` / `mcp_unavailable` / `path_missing` signals — those are data, not failures. **Degradation**: any backend down → that backend reports a `reason`; remaining backends still return rows.

### 4. Debugger incidents priming (recent-list)

```text
Skill("build-loop:debugging-memory") with { intent: "list-recent" }
```

**Return shape**: one-line summary `"N recent incidents in this project, top categories: [...]"`. Counts feed Phase 1's awareness of what's been failing lately. **Degradation**: MCP unreachable → fall through to `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/fallbacks.md#bug-memory` (token-extract + grep over `.build-loop/issues/` and `.build-loop/feedback.md`). Flag `⚠️ debugger MCP unavailable — using local grep fallback` in Review-F.

### 5. Per-MCP shape (diagnostic reference; use only when step 4's fallback fired)

```text
mcp__plugin_build-loop-debugger__list({ filter: { project: "<current>" }, limit: 10 })
```

**Return shape**: `{ incidents: [{ id, symptom, root_cause, fix, tags, created_at }, ...] }`. Surfaced here so a diagnostic check (e.g. "is the MCP actually returning anything?") doesn't have to traverse the skill abstraction.

### Return-shape & exit-code summary

| Step | Surface | Return shape | Empty-OK | On backend down |
|---|---|---|---|---|
| 1 | `Read()` (filesystem) | markdown text | yes | empty string |
| 2 | python slice over `state.json` | up to 3 lines | yes | no output |
| 3 | `memory_facade.py recall` | JSON envelope w/ `reasons[]` | yes | per-backend `reason`; other backends still respond |
| 4 | `Skill("build-loop:debugging-memory")` | one-line text summary | yes | grep-fallback per `fallbacks.md#bug-memory` |
| 5 | `mcp__plugin_build-loop-debugger__list` | `{ incidents: [...] }` | yes | step 4 already covered the fallback |

### Graceful-degradation matrix

| Failure mode | Step 1 | Step 2 | Step 3 | Step 4 | Step 5 |
|---|---|---|---|---|---|
| Postgres unavailable | n/a | n/a | `reason: db_unavailable` for semantic backend, others continue | n/a | n/a |
| MCP server unreachable | n/a | n/a | `reason: mcp_unavailable` | grep fallback | unusable; rely on step 4 fallback |
| `state.json` missing | n/a | no output | recall still runs other backends | n/a | n/a |
| MEMORY.md absent | empty | n/a | recall covers semantic/decision backends | n/a | n/a |
| All backends down | empty | empty | envelope w/ all `reasons` populated, `results: []` | grep fallback | n/a |

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

- **Cross-project learnings** (new tool, deployment pattern, user preference) → `~/.build-loop/memory/<type>_<slug>.md` + index in `~/.build-loop/memory/MEMORY.md`.
- **Project-specific learnings** (design decisions, internal conventions, gotchas) → `.build-loop/memory/<type>_<slug>.md` + index in `.build-loop/memory/MEMORY.md`.

Evaluate any skill authored during the build (Skill-on-Demand §SKILL.md): keep, promote, or drop. Record the decision in memory.

## Decision-store paths over time

Decisions live under TWO paths today (canonical + legacy). The orchestrator and any verification check MUST go through `scripts/memory_facade.py` (the read API), not the raw filesystem.

| Path | Status | Notes |
|---|---|---|
| `~/dev/git-folder/build-loop-memory/decisions/<project>/NNNN-YYYY-MM-DD-slug.md` | **Canonical (current)** | New writes land here. `<project>/` is resolved via `scripts/project_resolver.py` from `cwd → project tag`. |
| `<repo>/.episodic/decisions/NNNN-YYYY-MM-DD-slug.md` | Legacy (still tracked) | Pre-cutover decisions; some projects still write here during transition. The facade's read fan-out covers it. |

**Read path**: `python3 scripts/memory_facade.py recall --kind decision --query "<text>"` fans out across both paths and merges results. **Direct filesystem reads are fragile** — a verification rule that `ls`'d only the legacy path returned a phantom miss because the new canonical was authoritative. Locked by lesson `lesson-bl-decision-store-path-cutover` in `.episodic/architecture/lessons.json`.

**Write path**: `scripts/write_decision.py` writes to the canonical (new) path by default. The legacy path is only written when explicitly requested by tests fixturing pre-cutover state.

**INDEX.md**: each decision-store directory has its own `INDEX.md`. The facade reads both indexes and merges by ID. Do NOT edit `INDEX.md` by hand — `write_decision.py` regenerates it atomically as part of the memory-triad write.
