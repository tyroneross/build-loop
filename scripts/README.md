# scripts/

Build-loop's deterministic scripts. Each script:

- Has an explicit exit-code contract (0 success / 1 validation / 2 fs).
- Logs to stderr; success output goes to stdout.
- Targets Python 3.11+ stdlib unless otherwise noted below.
- Has a sibling `test_<name>.py` with deterministic tests.

## Lifecycle & discovery (G5)

The orchestrator routes work by selecting from a **capability registry**
(`build_capability_registry.py` → `.build-loop/capability-registry.json`),
narrowed per dispatch by `capability_shortlist.py` to ≤8 candidates. A
registry polluted with completed one-shots, orphans, and mis-classified
scripts routes agents to dead code — a coordination failure, not adjacent
hygiene. The lifecycle below keeps the registry honest.

### The capability header (authored, not heuristic)

Every `scripts/*.py` should declare a `# capability:` header — a four-line
comment block immediately after the shebang. The registry reads it instead
of guessing the category from the filename.

```python
#!/usr/bin/env python3
# capability:
#   purpose: One sentence — what this script does.
#   application: coordination
#   status: active
"""Module docstring follows as normal."""
```

| Field | Meaning |
|---|---|
| `purpose` | One-sentence description. Shown in the registry entry. |
| `application` | The coarse namespace (taxonomy below). Overrides the filename heuristic. |
| `status` | Lifecycle state — one of the four values below. |

**`status` values:**

| Status | Meaning | Registry / relevance effect |
|---|---|---|
| `active` | In live use. | Routable. `keep` verdict if referenced. |
| `experimental` | New, not yet load-bearing. | Routable; flagged experimental. |
| `deprecated` | Superseded; kept temporarily. | `attic` candidate — move to `_attic/`. |
| `oneshot-complete` | A migration/backfill that has run. | `attic` candidate. |
| _absent_ | No header authored. | `status: unknown` — `review` verdict. |

### The `application` namespace taxonomy

`application` is the coarse routing label. Today's namespaces:
`coordination`, `meta`, `architecture`, `debugging`, `validation`,
`planning`, `execution`, `observability`, `memory`, `testing`,
`deployment`, `ux-ui`, `optimization`.

When `scripts/` grows enough that a flat directory obscures purpose,
namespaces become physical subdirectories (`scripts/coordination/`,
`scripts/memory/`, …), migrated **opportunistically** — a script moves the
next time a build touches it, not in a big-bang reshuffle.

### Lifecycle: add → find → move → deprecate

**Add.** Create the script. Author the `# capability:` header in the same
commit — an absent header surfaces as `status: unknown` and a `review`
verdict in `script_relevance.py`.

**Find.** Agents do not browse `scripts/`. They route via the registry +
shortlist. After adding or renaming a script, the registry rebuilds on the
next Phase 1 Assess; rebuild manually with
`python3 scripts/build_capability_registry.py --workdir "$PWD"`.

**Move.** When a build touches a script and its `application` warrants a
subdirectory, move it then, update every importer (`git grep <name>.py`),
and rebuild the registry.

**Deprecate.** When a script is superseded or a one-shot has run:

1. Set its header `status` to `deprecated` or `oneshot-complete`.
2. Run `python3 scripts/script_relevance.py --workdir "$PWD"` to confirm it
   is not still referenced.
3. Once the detector lists it under `attic_candidates`, move it to
   `scripts/_attic/`. The registry crawler excludes `_attic/`, so the
   script disappears from routing without being deleted.

### The relevance detector

`script_relevance.py` cross-references three signals per script — the
authored `status`, git last-touched age (`--stale-days`, default 120), and
whether any other tracked file references the script — and emits a JSON
report with a `keep` / `review` / `attic` verdict each. It is **read-only**:
it never moves a file. Run it before a deprecation sweep, or as a periodic
hygiene check as `scripts/` scales.

## Phase A: orchestrator + plan tooling (pre-existing)

| Script | Purpose |
|---|---|
| `write_run_entry.py` | Atomic Review-F writer for `state.json.runs[]` |
| `plan_verify.py` | Deterministic plan grep-rules verifier |
| `version_advisor.py` | Suggest semver bumps based on `release-pending.md` |
| `ux_triage.py`, `ibr_quickpass.py` | Sub-step D Gates 7/8 |
| `optimize_loop.py`, `optimize_doe.py`, `metric_runner.py` | Optimization runner + DOE |
| `sync_skills.py`, `check_cache_sync.py` | Native-skill drift detector + cache hygiene |
| `transcript-pattern-miner.py` | Read-only transcript miner |
| `coordination_status.py` | Cheap multi-agent coordination sensor: Rally Point peers, overlaps, latest verdicts, dirty files → `clear | warn | blocked` JSON |
| `coordination_rally.py` | Lightweight Rally Point rally publisher: presence + handoff without creating a coordination file |
| `coordination_watch.py` | Active-coding watcher; polls `coordination_status.py` and prints only state transitions, including direct/broadcast inbox unread count |
| `rally_point/inbox.py` | Inbox API at `apps/<slug>/inbox/<tool>.jsonl` plus common `inbox/all.jsonl`, with optional `changes.jsonl` mirror |

## Phase B: repo-local episodic memory (added 2026-05-04)

The four-memory-types framework. Files canonical (markdown + JSONL +
YAML), Postgres + pgvector as the index/retrieval sidecar.

### Phase 1 — file foundation (stdlib only, no DB)

| Script | Purpose |
|---|---|
| `write_decision.py` | Atomic MADR writer; mirrors `write_run_entry.py` atomicity contract. Writes file + INDEX + events.jsonl as a unit. |
| `regenerate_knowledge_index.py` | Frontmatter-rollup INDEX for `.episodic/decisions/` and `.episodic/issues/`. |
| `validate_knowledge.py` | Frontmatter shape, controlled-vocab, supersession-link validation. |
| `migrate_feedback_to_decisions.py` | One-shot: `.build-loop/feedback.md` → `.episodic/decisions/*.md` (confidence: confirmed, source: migration). |
| `migrate_playbooks_to_procedural.py` | One-shot: `skills/debugging-memory/references/*-playbook.md` → `.procedural/<slug>/procedure.md`. |
| `test_write_decision.py`, `test_regenerate_knowledge_index.py`, `test_validate_knowledge.py` | Phase 1 tests. |

### Phase 2 — Postgres + pgvector + retrieval

DB-side scripts use `psycopg[binary]` (added 2026-05-04 during Phase 3
hardening) for a persistent connection per script invocation. The
helper module `scripts/db.py` wraps `psycopg.connect()` and caches the
connection at module scope; `atexit` closes it on process termination.

| Script | Purpose |
|---|---|
| `db.py` | psycopg helper. `get_connection()`, `execute()`, `execute_many()`, `query()`, `query_one()`, `vector_literal()`. Handles `~/.config/agent-memory/connection.env` resolution and `DATABASE_URL` env override. |
| `init_agent_memory_schema.sql` | Apply with `psql -d agent_memory -f scripts/init_agent_memory_schema.sql`. Idempotent. Creates `build_loop_memory` schema. Per-project copy this file and rename schema. |
| `recall.py` | Hybrid retrieval (cosine + pg_trgm + ts_rank). Embeds query via `embed_backend.embed`, returns ~500–1500 token summary. |
| `sync_db_from_files.py` | Rebuild Postgres state from canonical markdown. Idempotent. `--rebuild` truncates first. |
| `embed_backend.py` | Embedding abstraction. MLX `mxbai-embed-large-v1` default, Ollama `mxbai-embed-large` fallback, both 1024-dim. See "Embedding backend" below. |
| `migrate_schema_to_1024.sql` | One-shot schema migration from VECTOR(768) → VECTOR(1024). Idempotent. Run once per project, then `sync_db_from_files.py --rebuild` to repopulate. |
| `test_init_schema.py`, `test_recall.py`, `test_sync_db_from_files.py`, `test_embed_backend.py` | Phase 2 tests. Each uses a temporary test schema (`test_schema_*`) so they don't touch production data. |

### Phase 3 — live integration tests

Phase 3 (2026-05-04) replaced the prior `--mock-llm-output` test
fixtures with real ollama integration tests, plus added two new test
scripts:

| Script | Purpose |
|---|---|
| `test_dedup_path.py` | Live cosine-similarity dedup test. Seeds a known fact via psycopg, asserts a paraphrase classifies as duplicate (≥ 0.85) and an unrelated string does not. Uses `embed_backend.embed` (MLX default, Ollama fallback, 1024-dim). |
| `test_stop_hook_integration.py` | End-to-end test of the Stop hook. Synthesizes a transcript, invokes the exact command from `hooks/hooks.json` via `/bin/sh`, verifies the live `qwen3:8b-q4_K_M` extraction produces ≥ 1 captured artifact. |
| `test_scan_transcript_for_decisions.py` | Rewritten to call live `qwen3:8b-q4_K_M` via the ollama HTTP API rather than the legacy `--mock-llm-output` fixture. The script's `--mock-llm-output` flag remains for developer convenience but the test suite no longer uses it. |

### Database driver decision

**Choice (2026-05-04): `psycopg[binary]` with persistent connection.**

Rationale:

- Per-query latency: psycopg `SELECT 1` ≈ 0.2ms vs psql subprocess fork ≈ 20ms (measured on this host). Stop-hook tier-3 batches can write 10–30 decisions per session; the 100x speedup matters at scale.
- `db.py` exposes parameterized queries (`%s` placeholders) end-to-end. No more SQL-string interpolation of user-provided values.
- Native vector handling via `vector_literal()` + explicit `::vector` cast. No extra `pgvector-python` dep needed.
- Single dep adds 5 MB; install path is well-documented (uv or pip with PEP 668 override on Homebrew Python).

Connection config: `~/.config/agent-memory/connection.env` exports
`DATABASE_URL=postgresql://tyroneross@localhost:5432/agent_memory`.

#### Install

```bash
# Preferred: uv (matches the user's uv-by-default policy)
uv pip install --system -r requirements.txt

# Or pip with PEP 668 override (Homebrew Python)
pip3 install --break-system-packages -r requirements.txt
```

### Embedding backend

Embeddings flow through `scripts/embed_backend.py`, which exposes a
single `embed(text)` function (single-string or list-of-strings) and
returns 1024-dim Python `list[float]`. Two backends are supported, both
producing the **same dimension and same base weights** so cross-backend
cosine similarity stays high (measured 0.9664–0.9697 on identical
text):

| Backend | Default model | Per-call (warm) | Batch=10 amortized |
|---|---|---|---|
| `mlx` (default) | `mlx-community/mxbai-embed-large-v1` | ~10ms | ~2ms |
| `ollama` (fallback) | `mxbai-embed-large` | ~15ms | ~15ms (no native batch) |

Selection: `$EMBED_BACKEND` ∈ {`mlx`, `ollama`}, default `mlx`. Override
the model with `$EMBED_MODEL`. On Linux (or wherever
`mlx-embeddings` cannot be imported), the module logs a warning and
falls through to Ollama for the rest of the process. Once fallen
through, MLX is not retried — keeps stop-hook latency predictable.

First-run model download (`mlx-community/mxbai-embed-large-v1`,
~9 files, ~150 MB) adds about 17 seconds one-time. Cold start with
warm cache is ~220 ms per process; production scripts amortize this
across many calls within one process.

The schema column is `VECTOR(1024)` for `episode_events.embedding`,
`semantic_facts.embedding`, and `procedures.embedding`. To migrate an
existing 768-dim project, run `migrate_schema_to_1024.sql` then
`sync_db_from_files.py --rebuild` to repopulate.

Why same-base-weights matters: if you embed query text with MLX while
the seeded rows were embedded with Ollama (or vice versa), cosine
distances stay in the same numerical regime. You can switch backends
mid-flight without re-embedding the whole DB. The 0.9664 baseline is
"different code paths producing the same embedding modulo bf16
quantization on the MLX side" — not "different models that happen to
agree."

The `scan_transcript_for_decisions.py` extraction step uses
`qwen3:8b-q4_K_M` via the ollama HTTP API (`POST /api/generate` with
`stream: false`, `think: false`). The HTTP path is preferred over
`ollama run <model>` because the CLI emits TTY-aware streaming output
(cursor-back / erase-line escape codes) that corrupt JSON spans even
when stdout is piped.

#### Stop-hook hardening contract

The Stop hook can never disrupt a coding session. The script enforces:

- **Wall-clock budget** — default 25s (override via env `SCAN_BUDGET_S`).
  Checked before the LLM call and between writes; on overrun the script
  logs `budget exceeded` and exits 0 with whatever was already written.
  The Claude Code hook timeout (60s) is a backstop, not the primary control.
- **Single-flight lock** — Python `fcntl.flock` on
  `/tmp/build-loop-scan.lock` (override via `--lock-file`). A second
  concurrent scan exits 0 immediately with a log line. This is portable
  on macOS (which doesn't ship `flock(1)`).
- **Output suppression** — the hook command in `hooks/hooks.json`
  redirects both stdout and stderr to `/dev/null`. The durable record is
  the log file at `${XDG_STATE_HOME:-$HOME/.local/state}/build-loop/scan.log`
  (override via `--log-file`). The log auto-rotates when it exceeds
  10 MB (last 1 MB kept).
- **Per-session opt-out** — `touch .episodic/.no-capture` in the project
  root to skip the auto-capture sweep for the current session. Remove
  the file to re-enable.

### Required runtime services

All Phase 2 / Phase 3 tests require:

1. **Postgres 15+** with `vector` and `pg_trgm` extensions, and the
   `agent_memory.build_loop_memory` schema initialized from
   `scripts/init_agent_memory_schema.sql`.
2. **Ollama daemon** (`ollama serve`) running on
   `http://127.0.0.1:11434` with these models pulled:
   ```bash
   ollama pull qwen3:8b-q4_K_M       # decision extraction
   ollama pull mxbai-embed-large     # embedding fallback (1024-dim)
   ```
3. **`psycopg[binary]`** installed (`uv pip install -r requirements.txt`).
4. **`mlx-embeddings`** (macOS only; default embedding backend):
   ```bash
   uv pip install --system mlx-embeddings
   # First call lazy-downloads mlx-community/mxbai-embed-large-v1 (~150 MB).
   ```

The tests do not skip if these are missing — they fail loud, because
silent skips hide real config problems in CI.

### Running everything

```bash
# Phase 1 tests (no DB needed)
for t in scripts/test_write_decision.py scripts/test_regenerate_knowledge_index.py scripts/test_validate_knowledge.py; do
  python3 "$t"
done

# Phase 2 tests (require Postgres + Ollama)
for t in scripts/test_init_schema.py scripts/test_recall.py scripts/test_sync_db_from_files.py; do
  python3 "$t"
done

# Phase 3 tests (require Postgres + Ollama with qwen3:8b-q4_K_M and nomic-embed-text)
for t in scripts/test_scan_transcript_for_decisions.py scripts/test_dedup_path.py scripts/test_stop_hook_integration.py; do
  python3 "$t"
done
```
