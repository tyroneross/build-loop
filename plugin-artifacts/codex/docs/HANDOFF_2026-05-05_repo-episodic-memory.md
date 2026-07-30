# Handoff — Repo-local episodic memory framework

**Date:** 2026-05-05
**Branch:** `feat/repo-episodic-memory` (10 commits, all pushed to `origin`)
**PR-ready URL:** https://github.com/tyroneross/build-loop/pull/new/feat/repo-episodic-memory
**Status:** Feature-complete, schema v3, tested live against real Postgres + real MLX + real Ollama. Two open dogfood gaps documented below.

---

## TL;DR

A repo-local memory framework that captures decisions / events / knowledge into per-project `.episodic/`, `.semantic/`, and `.procedural/` directories, indexed by Postgres + pgvector with MLX-default embedding (Ollama fallback). Auto-capture skill + Stop hook + explicit subagent invocation cover three capture contexts. v3 metadata schema supports cohort queries (`tool`, `model`, `project`, `task_category`, `domain`, `goal`) and lifecycle tracking (`last_validated`, `confirmation_count`, `valid_until`, `causal_parent_id`). Built dogfood-style: the framework was used to capture decisions during its own construction (decision `0006-cosine-0.85-dedup-threshold` is from the Phase 4 build).

---

## Commit chain (in order, all on `feat/repo-episodic-memory`)

```
5c2a030  fix(memory): close subagent capture gap — explicit invocation + agent-style signals
cd2c693  fix(memory): sync_db_from_files.py writes v3 typed columns
cc0a7c0  feat(memory): metadata schema v3 — confidence_source … domain, goal
4613a08  feat(memory): metadata schema v2 — tool, model, project, task_category, author + lifecycle
208326b  fix(memory): background Stop hook so session-end is non-blocking
228b5da  feat(memory): Phase 4 — consolidation, review surface, procedural governance
5499226  fix(memory): harden Stop hook against session disruption
8c9ed1c  feat(memory): MLX-default embedding backend with Ollama fallback
ddf5036  feat(memory): repo-local episodic memory framework Phases 1+2+3
```

---

## What works (verified live)

| Component | Status | Evidence |
|---|---|---|
| `.episodic/decisions/` MADR-format file storage | ✅ | 6 decisions on disk, atomic writes via `fcntl + os.replace` |
| `.episodic/events.jsonl` append-only event log | ✅ | 6 events, dedup via `dedup_key` |
| `.semantic/{MEMORY,intent,goal,TAXONOMY}.md` | ✅ | Seeded; controlled vocabulary at TAXONOMY.md |
| `.procedural/` (YAML frontmatter procedures) | ✅ | 1 migrated procedure (`ios-notification-alarm`) |
| Postgres `agent_memory.build_loop_memory` schema | ✅ | 4 tables, HNSW + GIN indexes, all 7 v3 columns + indexes verified live |
| `embed_backend.py` MLX-default with Ollama fallback | ✅ | `mxbai-embed-large-v1` (MLX) primary; live tested 9.4ms p50 |
| `recall.py` hybrid retrieval (cosine + BM25 + filters) | ✅ | All v2 + v3 metadata filters working live |
| `write_decision.py` atomic writer (file + DB + embedding) | ✅ | 16 v2 tests + 25 v3 tests pass |
| `scan_transcript_for_decisions.py` (Stop hook + explicit) | ✅ | 10 unit tests pass; live qwen3:8b extraction works |
| `migrate_schema_v2.py` + `migrate_schema_v3.py` | ✅ | Idempotent; verified 6 decisions migrated through both |
| Stop hook backgrounded (`nohup ... & exit 0`) | ✅ | Returns in 28ms; zero terminal output; integration tests pass |
| `auto-decision-capture` skill (signal-based, agent-tuned) | ✅ | Tier 1 + 2 patterns documented; agent-style language now in detection |
| `consolidate_memory.py` (Phase 4) | ✅ | 3 live tests pass; conflict surfacing for review |
| `detect_decision_rot.py` (Phase 4) | ✅ | Age-based stale detection, JSON output |
| `procedural_governance.py` (Phase 4 three-phase governance) | ✅ | 6 tests pass; `--mode validate-symbols`, `--mode detect-patterns`, `--mode auto-draft` |
| `/knowledge:review` slash command | ✅ | Aggregates 4 sections (review queue, rot, conflicts, stale procedures) |
| `supersede_decision.py` + `revoke_decision.py` | ✅ | Idempotent; integration tested |
| Auto-decision-capture skill mid-conversation (interactive) | ⚠️ Partially proven | Phase 4 live-captured decision 0006 (cosine threshold); skill is reactive — Claude must self-invoke |

---

## What's NOT verified live (open dogfood gaps)

| Gap | Severity | Mitigation |
|---|---|---|
| **Stop hook in subagent contexts** — does Claude Code fire the Stop hook at end of build-orchestrator subagent runs? Phase 4's session log shows zero "real-transcript" entries during its run. | Medium | Closed structurally by commit `5c2a030` — build-orchestrator now invokes `scan_transcript_for_decisions.py` explicitly in Phase 5 Report. No longer depends on Stop hook firing in subagent context. |
| **Live qwen3:8b extraction quality on agent-style language** — Prompt C now has agent-style examples and detection rules, but only validated with mock LLM in tests. | Low | Will be exercised on next user-session Stop hook OR next build-orchestrator run. Mock-based unit tests verify prompt/parsing path; live LLM call is uninstrumented but the change is incremental over the existing prompt. |
| **`auto-decision-capture` skill firing rate in interactive sessions** | Low | Skill loaded; signal patterns expanded; user's next conversation will be a real test |
| **`closing_commit` field auto-population via post-commit hook** | None today | Field exists; population is a future enhancement not in scope for v3 |

The above are *unverified*, not known-broken. Worst case is silent under-capture, which is the same shape as the system not existing — no regression.

---

## Three capture mechanisms — three contexts

This was the architecturally important design. Decision capture happens through three complementary mechanisms, each covering a different invocation context. Dedup (cosine ≥ 0.85 on `primary_tag + entity` match) handles overlap.

| Mechanism | Where it lives | Trigger | Context covered |
|---|---|---|---|
| **`auto-decision-capture` skill** (live tier 1+2) | `skills/auto-decision-capture/SKILL.md` | Claude self-invokes when detecting a signal mid-conversation | Interactive user-Claude sessions |
| **Stop hook (backgrounded)** | `hooks/hooks.json` Stop entry | Claude Code session-end event | User's interactive session-end batch sweep |
| **Explicit Phase 5 Report scan** | `agents/build-orchestrator.md` Phase 5 sub-step F | Build-orchestrator subagent runs the script before completing | Every build-loop / build-orchestrator subagent run |

The third mechanism (added in `5c2a030`) was the structural fix for the dogfood gap surfaced over the v2/v3 builds: subagents don't reliably self-invoke the skill, so capture is now mandated by the agent definition.

---

## File map (key paths)

### Per-repo memory (created on first capture)
```
.episodic/                   # immutable history
├── decisions/
│   ├── 0001-…md → 0006-…md  # MADR per decision
│   ├── _history/            # superseded versions (recoverable)
│   ├── _review/             # tier-3 (inferred/assumed) quarantine
│   ├── INDEX.md             # auto-generated TOC
│   └── .gitignore           # exclude transcript-summaries/ if added
├── events.jsonl             # append-only multi-source event stream
├── issues/                  # file-per-issue (Phase 4)
├── transcript-summaries/    # per-session compact summaries
└── .no-capture              # opt-out flag (when present)

.semantic/                   # current truth (mutable)
├── MEMORY.md                # consolidated knowledge
├── intent.md
├── goal.md
├── TAXONOMY.md              # controlled vocabulary
├── _candidates.jsonl        # pre-consolidation buffer
└── derived/
    ├── libraries.json
    └── architecture.json

.procedural/                 # how-to (Phase 4 governance)
├── _index.yaml
└── <name>/
    ├── procedure.md         # YAML frontmatter + body
    └── incidents.jsonl
```

### Code
```
scripts/
├── write_decision.py            # atomic writer (file + DB + embedding)
├── recall.py                    # hybrid retrieval entry point
├── scan_transcript_for_decisions.py  # Stop hook + explicit-invocation entry
├── consolidate_memory.py        # Phase 4 dedup + conflict detection
├── detect_decision_rot.py       # Phase 4 staleness scoring
├── procedural_governance.py     # Phase 4 three-phase learning
├── knowledge_review.py          # /knowledge:review backend
├── supersede_decision.py
├── revoke_decision.py
├── embed_backend.py             # MLX/Ollama swap layer
├── db.py                        # Postgres connection helper
├── sync_db_from_files.py        # rebuild DB from canonical files
├── validate_knowledge.py        # frontmatter validator
├── regenerate_knowledge_index.py
├── migrate_feedback_to_decisions.py    # one-shot v0 → v1
├── migrate_playbooks_to_procedural.py  # one-shot v0 → v1
├── migrate_schema_v2.py         # v1 → v2 (tool/model/project/etc.)
├── migrate_schema_v3.py         # v2 → v3 (confidence_source/domain/goal/etc.)
├── init_agent_memory_schema.sql # DDL (idempotent ADD COLUMN IF NOT EXISTS)
└── test_*.py                    # 25 test files; 31 pass, 1 unrelated pre-existing failure
```

### Skills + agents
```
skills/auto-decision-capture/SKILL.md  # signal taxonomy, confidence ladder, agent-style patterns
skills/knowledge/SKILL.md              # entry point describing the namespace
skills/knowledge-review/SKILL.md       # review surface
skills/knowledge/templates/madr-minimal.md
agents/build-orchestrator.md           # Phase 5 Report mandates explicit scan invocation
```

### External infra
```
~/.config/agent-memory/connection.env  # DATABASE_URL=postgresql://tyroneross@localhost:5432/agent_memory
~/.local/state/build-loop/scan.log     # Stop hook log (rotated at 10MB → 1MB tail)
/tmp/build-loop-scan.lock              # fcntl single-flight lock
~/dev/research/topics/repo-episodic-memory-framework/  # canonical design entry
```

---

## Common operations

### Write a decision manually
```bash
python3 scripts/write_decision.py \
  --title "Use Postgres + pgvector for repo memory" \
  --decision "..." \
  --tags "architecture,tooling" --primary-tag "architecture" \
  --entity "build-loop:storage" \
  --confidence explicit --source manual \
  --tool claude-code --model claude-opus-4-7 \
  --project build-loop --task-category config \
  --domain meta --goal maintainability
```

### Search memory
```bash
python3 scripts/recall.py --query "Postgres extension" --limit 5
# With filters:
python3 scripts/recall.py --query "..." --domain search --goal reliability
```

### Review what needs attention
```bash
# Either via the slash command (if reload-plugins picked it up):
/knowledge:review

# Or directly:
python3 scripts/knowledge_review.py
```

### Disable capture for a session (sensitive content)
```bash
touch .episodic/.no-capture
# work the session
rm .episodic/.no-capture
```

### Rebuild DB from canonical files (recovery)
```bash
python3 scripts/sync_db_from_files.py --rebuild
```

### Run all tests
```bash
for t in scripts/test_*.py; do python3 "$t" || echo "FAIL: $t"; done
# Expected: all pass except test_bridge_preflight (pre-existing unrelated)
```

---

## Required local services

| Service | How to start | Verify |
|---|---|---|
| Postgres 15 + pgvector 0.8.2 + pg_trgm | `brew services start postgresql@15` | `psql -d agent_memory -c "\dx"` |
| Ollama 0.21+ (with MLX backend on Apple Silicon) | macOS `.app` autostart | `ollama list` |
| Models: `mxbai-embed-large`, `nomic-embed-text`, `qwen3:8b-q4_K_M` | `ollama pull <name>` | `ollama list \| grep <name>` |
| Python deps: `psycopg[binary]`, `mlx-embeddings; sys_platform == 'darwin'` | `uv pip install --system --break-system-packages -r requirements.txt` | `python3 -c "import psycopg, mlx_embeddings; print('ok')"` |

`agent_memory` DB and `build_loop_memory` schema are created on first run of `init_agent_memory_schema.sql`.

---

## Open items / roadmap (in priority order)

| # | Item | Why | Effort |
|---|---|---|---|
| 1 | **Validate that next user-session Stop hook produces meaningful captures** — this is the primary unproven mechanism | Confirms hypotheses on signal-detection sensitivity | 0 effort, just observe |
| 2 | **Investigate `auto-decision-capture` skill firing rate** in interactive sessions over a few days | Tells us whether the skill is reactive-but-firing OR reactive-and-silent | Passive observation |
| 3 | **Auto-population for `closing_commit`** via git post-commit hook | Closes the decision → commit linkage automatically | Small (~50 LOC + 1 hook entry) |
| 4 | **`task_category` and `domain` auto-inference** from conversational context (currently most captures default to `unknown`) | Better cohort queries; enables filter-driven retrieval | Medium (~150 LOC + LLM-driven classifier) |
| 5 | **Cross-project promotion via `/research:save` integration** (when `tags: [..., promote-global]`) | Decisions in example-app relevant to example-web-app surface in both | Medium |
| 6 | **`recall.py` query-rewriting using local LLM** when initial cosine retrieval has low confidence | Better recall on vocabulary-mismatched queries | Medium |
| 7 | **Auto-memory frontmatter expansion** (`~/.claude/projects/.../memory/` files currently have only `name, description, type` — could match v3 schema for unified categorization) | Cross-system consistency | Small but separate system |
| 8 | **Procedural memory auto-draft** (Phase 3 of the three-phase governance, gated at ≥5 hand-authored procedures) — currently 1 procedure exists, gate not yet open | Will fire automatically when threshold met | 0 effort until threshold met |

---

## Known limitations (by design, not bugs)

1. **No sensitivity filter** — by user direction. The system has no automated guard against capturing sensitive content (legal threats, compensation, pre-announcements). User uses `.episodic/.no-capture` for opt-out per session.
2. **Memory is per-cwd, not per-repo** — auto-memory at `~/.claude/projects/<encoded-cwd>/memory/` is keyed by the cwd Claude was launched from, not by the repo being edited. The new `.knowledge/`-style framework is the FIRST per-repo memory layer; auto-memory is separate.
3. **Single-machine `fcntl.flock`** — works for the user's single-machine workflow; would need replacement for NFS/network mounts.
4. **GPL-v3 dep (mlx-embeddings)** — build-loop is also GPL-3 so this is fine, but flag if license posture changes.
5. **Subagent Stop hook firing is unverified** — the explicit-invocation mitigation in `5c2a030` makes this irrelevant.

---

## Key design decisions (audit trail)

These are captured as numbered decisions in `.episodic/decisions/` (file-on-disk + DB row). Listing the load-bearing ones for handoff:

| ID | Decision | Why |
|---|---|---|
| 0005 | Postgres + pgvector for canonical retrieval | More mature than sqlite-vec for hybrid search at scale; user already had postgres@15 installed |
| 0006 | Cosine 0.85 threshold for dedup MERGE/IGNORE | Mem0/Zep production consensus; tighter than 0.75 (incidental similarity) and looser than 0.95 (exact-quotation) |
| (file only) | MLX-default with Ollama fallback | 1.8× per-call faster, 8× faster batched; Apple-native; cross-backend cosine 0.97 confirms equivalence |
| (file only) | mxbai-embed-large for embeddings (1024-dim) | Available in both Ollama AND MLX (`mlx-community/mxbai-embed-large-v1`); enables fair backend comparison |
| (file only) | Stop hook backgrounded via `nohup ... & exit 0` | User-direction: hooks must not interrupt session-end. Hook returns in 28ms; scan runs detached. |
| (file only) | Three-mechanism capture (skill + Stop hook + explicit Phase-5 scan) | Subagent contexts don't reliably fire skill or Stop hook; explicit invocation closes the gap. |

The full decision file frontmatter (post-v3) includes `confidence_source`, `domain`, `goal`, etc. for filtering at retrieval time.

---

## What the next session/contributor should know

1. **The framework is functional and committed.** No work-in-progress in flight. Two unrelated working-tree files (`.codex-plugin/plugin.json`, `scripts/test_plugin_manifest.py`) were the user's in-flight work that was deliberately preserved untouched across all 10 commits — those need to be addressed separately.

2. **`/reload-plugins` may be needed** in the user's interactive Claude Code session if they want the latest hook config (`208326b` backgrounding fix) active before this session ends. The plugins were loaded at session start before that commit. (Built-in CLI command; only the user can invoke it.)

3. **Reading the design entry first is recommended.** `~/dev/research/topics/repo-episodic-memory-framework/repo-episodic-memory-framework.md` — 16 numbered sections covering taxonomy, hierarchy, capture rules, four-memory-types, Postgres schema, procedural memory, v2 + v3 metadata schemas. Source-tier annotated. Not committed in build-loop repo (lives in the research KB).

4. **The dogfood test is ongoing.** Three captures so far: decision 0006 (Phase 4 build), and two passes (v2/v3 builds) where the auto-skill didn't fire. Decision 0006 is the one substantive auto-capture proof. The next user-session Stop hook firing is the single most informative test outstanding.

5. **Memory feedback entries** (relevant to building on this work):
   - `~/.claude/projects/-Users-tyroneross/memory/feedback_hooks_decision_framework.md`
   - `~/.claude/projects/-Users-tyroneross/memory/feedback_verify_before_briefing.md`
   - `~/.claude/projects/-Users-tyroneross/memory/feedback_separate_first_run_from_steady_state.md`
   - `~/.claude/projects/-Users-tyroneross/memory/feedback_benchmark_methodology.md`
   - `~/.claude/projects/-Users-tyroneross/memory/feedback_options_with_tradeoffs.md`

   These were derived from this thread's dogfood failures; reading them prevents the same mistakes on follow-up work.

6. **Don't add more hooks without strong justification.** Per `feedback_hooks_decision_framework.md`: production teams run 2–4 hooks max; we're at 3. Adding more is the smell.

---

## How to verify the system works (smoke test for handoff)

Run these in order. Expected: every step passes.

```bash
# 1. State is clean and pushed
cd ~/dev/git-folder/build-loop
git status --short                                       # only .codex-plugin + test_plugin_manifest (user's)
git log --oneline -3                                     # 5c2a030 head; pushed

# 2. Postgres is up with v3 schema
psql -d agent_memory -c "\d build_loop_memory.semantic_facts" | grep -c "domain\|goal\|confidence_source"
# expect: 3

# 3. Ollama + MLX backends both work
python3 -c "from scripts.embed_backend import embed; v = embed('test'); print(len(v))"
# expect: 1024

# 4. Recall works with v3 filters
python3 scripts/recall.py --query "Postgres" --domain meta --confidence-floor inferred --limit 1
# expect: decision 0005 (Postgres acceptance) returns

# 5. Auto-capture pattern recognizes agent-style language
echo '[{"decision":"Test","evidence":"I test","confidence":"explicit","primary_tag":"tooling","entity":"test:smoke","tags":["tooling"],"context":"smoke","alternatives":"","rationale":"smoke"}]' > /tmp/mock.json
TMP=$(mktemp -d); mkdir -p "$TMP/.episodic/decisions/_review" "$TMP/.semantic"; cp .semantic/TAXONOMY.md "$TMP/.semantic/"
ln -s "$(pwd)/scripts" "$TMP/scripts"
echo '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"x"}]},"timestamp":"2026-05-05T20:00:00Z"}' > "$TMP/transcript.jsonl"
python3 scripts/scan_transcript_for_decisions.py --transcript "$TMP/transcript.jsonl" --workdir "$TMP" --mock-llm-output /tmp/mock.json --no-db --lock-file "$TMP/lock" 2>&1 | head -2
ls "$TMP/.episodic/decisions/" | grep -v INDEX | grep -v _
# expect: 0001-...md exists

# 6. Stop hook returns in <500ms
START=$(python3 -c "import time; print(time.perf_counter_ns())")
CLAUDE_PROJECT_DIR=$(pwd) CLAUDE_TRANSCRIPT_PATH=/dev/null /bin/sh -c "$(python3 -c 'import json; print(json.load(open("hooks/hooks.json"))["hooks"]["Stop"][0]["hooks"][1]["command"])')" >/dev/null 2>&1
END=$(python3 -c "import time; print(time.perf_counter_ns())")
python3 -c "print(f'hook elapsed: {(${END} - ${START})/1e6:.1f}ms')"
# expect: <100ms (was 28ms in our tests)
```

If all six steps pass, the handoff is clean.
