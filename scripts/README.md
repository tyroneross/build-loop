# scripts/

Build-loop's deterministic scripts. Each script:

- Has an explicit exit-code contract (0 success / 1 validation / 2 fs).
- Logs to stderr; success output goes to stdout.
- Targets Python 3.11+ stdlib unless otherwise noted below.
- Has a sibling `test_<name>.py` with deterministic tests.

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
| `recall.py` | Hybrid retrieval (cosine + pg_trgm + ts_rank). Embeds query via local Ollama, returns ~500–1500 token summary. |
| `sync_db_from_files.py` | Rebuild Postgres state from canonical markdown. Idempotent. `--rebuild` truncates first. |
| `test_init_schema.py`, `test_recall.py`, `test_sync_db_from_files.py` | Phase 2 tests. Each uses a temporary test schema (`test_schema_*`) so they don't touch production data. |

### Phase 3 — live integration tests

Phase 3 (2026-05-04) replaced the prior `--mock-llm-output` test
fixtures with real ollama integration tests, plus added two new test
scripts:

| Script | Purpose |
|---|---|
| `test_dedup_path.py` | Live cosine-similarity dedup test. Seeds a known fact via psycopg, asserts a paraphrase classifies as duplicate (≥ 0.85) and an unrelated string does not. Uses `nomic-embed-text` over HTTP. |
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

### Embedding model

`nomic-embed-text` (768-dim) via local Ollama. The schema's `VECTOR(768)`
column matches this dimension. To swap to `mxbai-embed-large`
(1024-dim), the schema would need to change.

The `scan_transcript_for_decisions.py` extraction step uses
`qwen3:8b-q4_K_M` via the ollama HTTP API (`POST /api/generate` with
`stream: false`, `think: false`). The HTTP path is preferred over
`ollama run <model>` because the CLI emits TTY-aware streaming output
(cursor-back / erase-line escape codes) that corrupt JSON spans even
when stdout is piped.

### Required runtime services

All Phase 2 / Phase 3 tests require:

1. **Postgres 15+** with `vector` and `pg_trgm` extensions, and the
   `agent_memory.build_loop_memory` schema initialized from
   `scripts/init_agent_memory_schema.sql`.
2. **Ollama daemon** (`ollama serve`) running on
   `http://127.0.0.1:11434` with both models pulled:
   ```bash
   ollama pull qwen3:8b-q4_K_M       # decision extraction
   ollama pull nomic-embed-text      # embedding model (768-dim)
   ```
3. **`psycopg[binary]`** installed (`uv pip install -r requirements.txt`).

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
