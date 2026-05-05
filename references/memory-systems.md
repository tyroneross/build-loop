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

Mirror of the orchestrator's executable steps. Each line is a runnable action; empty results are valid; never raise on a missing backend.

1. **MEMORY.md tiers**: `Read("~/.build-loop/memory/MEMORY.md")` (global) AND `Read("<repo>/.build-loop/memory/MEMORY.md")` (project). Project overrides global on key conflict. Empty/absent files: skip silently.
2. **Run-history priming** (added Priority 12, 2026-05-05): `Read(".build-loop/state.json")` and inspect `runs[-3:]` for prior-build outcomes/root_cause. This catches "this build follows a similar one — here's what happened" before a fresh `recall()` query is shaped.
3. **Unified recall**: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_facade.py recall --query "<goal-keywords>" --limit 10` — one read across all four backends. Returns the canonical envelope (see facade docstring). Inspect `reasons[]` for backend-unavailable signals; never block on them.
4. **Debugger incidents (priming)**: invoke `Skill("build-loop:debugging-memory")` with `intent: "list-recent"` for one-line summaries of recent project incidents. MCP failure → fall through to `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/fallbacks.md#bug-memory`; flag `⚠️ debugger MCP unavailable — using local grep fallback` in Review-F.
5. **Per-MCP fallback** (only if step 4 used the fallback): `mcp__plugin_build-loop-debugger__list({ filter: { project: "<current>" }, limit: 10 })` is the direct MCP shape used inside the skill — surfaced here for diagnostic reference.

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
