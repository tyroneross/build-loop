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

1. Load `~/.build-loop/memory/MEMORY.md` (global) and `.build-loop/memory/MEMORY.md` (project) if they exist. Project overrides global on key conflict.
2. Invoke `Skill("build-loop:debugging-memory")` with `intent: "list-recent"` to surface recent project incidents (one-line summary).
3. Pull recent debugger incident context via the MCP `list` tool: `mcp__plugin_build-loop-debugger__list({ filter: { project: "<current>" }, limit: 10 })`. If MCP fails, fall through to `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/fallbacks.md#bug-memory` and flag `⚠️ debugger MCP unavailable — using local grep fallback` in Review-F.

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
