-- init_agent_memory_schema.sql
--
-- Initialize a per-project schema in the agent_memory database.
--
-- Idempotent (uses CREATE … IF NOT EXISTS / CREATE OR REPLACE).
--
-- Schema is parameterized via the psql -v variable `schema`. Pass it on
-- the command line:
--
--   psql -d agent_memory -v schema=personal_memory -f scripts/init_agent_memory_schema.sql
--   psql -d agent_memory -v schema=tmp_test_schema  -f scripts/init_agent_memory_schema.sql
--
-- Default when -v is not provided: `personal_memory` (the global schema
-- introduced in the Phase B cutover).
--
-- Source design: see the project's repo-episodic-memory-framework
-- design doc, §13.

\set ON_ERROR_STOP on

-- Default the `schema` psql variable when the caller didn't pass -v schema=...
\if :{?schema}
\echo Using schema: :schema
\else
\set schema personal_memory
\echo Using default schema: :schema
\endif

-- Required extensions.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Per-project schema.
CREATE SCHEMA IF NOT EXISTS :"schema";
SET search_path TO :"schema", public;

-- ----------------------------------------------------------------------
-- sessions
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     TEXT NOT NULL,
  started_at  TIMESTAMPTZ DEFAULT now(),
  ended_at    TIMESTAMPTZ,
  channel     TEXT,                       -- 'chat', 'code', 'api', 'orchestrator'
  summary     TEXT
);

-- ----------------------------------------------------------------------
-- episode_events  (raw episodic stream)
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS episode_events (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id      UUID REFERENCES sessions(id) ON DELETE SET NULL,
  user_id         TEXT NOT NULL,
  seq_num         INTEGER NOT NULL,
  occurred_at     TIMESTAMPTZ DEFAULT now(),
  actor           TEXT NOT NULL,          -- 'user', 'agent', 'tool', 'system'
  verb            TEXT NOT NULL,          -- 'said', 'called', 'returned', 'decided'
  object          TEXT,
  raw_content     TEXT,                   -- verbatim, never trimmed
  summary         TEXT,
  embedding       VECTOR(1024),           -- mxbai-embed-large / mlx-community/mxbai-embed-large-v1
  metadata        JSONB,
  -- v2 metadata (design §15, added 2026-05-04)
  project         TEXT,
  tool            TEXT,
  model           TEXT,
  task_category   TEXT,
  author          TEXT,
  last_validated  TIMESTAMPTZ,
  last_accessed   TIMESTAMPTZ,
  closing_commit  TEXT,
  files_touched   TEXT[],
  -- v3 metadata (design §16, added 2026-05-04)
  confidence_source        TEXT,
  confirmation_count       INTEGER DEFAULT 0,
  valid_until              TIMESTAMPTZ,
  causal_parent_id         TEXT,
  embedding_model_version  TEXT,
  domain                   TEXT,
  goal                     TEXT
);

-- ----------------------------------------------------------------------
-- semantic_facts  (current truth; derived from episodic via extraction pipeline)
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS semantic_facts (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subject            TEXT NOT NULL,
  predicate          TEXT NOT NULL,
  object             TEXT NOT NULL,
  confidence         FLOAT DEFAULT 1.0,
  source_episode_id  UUID REFERENCES episode_events(id) ON DELETE SET NULL,
  status             TEXT DEFAULT 'active',  -- 'proposed', 'active', 'superseded', 'retracted'
  valid_from         TIMESTAMPTZ DEFAULT now(),
  valid_to           TIMESTAMPTZ,
  embedding          VECTOR(1024),
  metadata           JSONB,
  -- v2 metadata (design §15, added 2026-05-04)
  project            TEXT,
  tool               TEXT,
  model              TEXT,
  task_category      TEXT,
  author             TEXT,
  last_validated     TIMESTAMPTZ,
  last_accessed      TIMESTAMPTZ,
  closing_commit     TEXT,
  files_touched      TEXT[],
  -- v3 metadata (design §16, added 2026-05-04)
  confidence_source        TEXT,
  confirmation_count       INTEGER DEFAULT 0,
  valid_until              TIMESTAMPTZ,
  causal_parent_id         TEXT,
  embedding_model_version  TEXT,
  domain                   TEXT,
  goal                     TEXT,
  -- Phase D (Anthropic Contextual Retrieval, added 2026-05-06).
  -- Dense ~80-token "this decision comes from {context}" summary
  -- prepended to the embedding text on write. Migration script for
  -- existing installs: scripts/migrate_add_chunk_context_column.py.
  chunk_context            TEXT
);

-- ----------------------------------------------------------------------
-- fact_conflicts  (surfaced by extraction pipeline, never auto-resolved)
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_conflicts (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fact_id_a           UUID REFERENCES semantic_facts(id) ON DELETE CASCADE,
  fact_id_b           UUID REFERENCES semantic_facts(id) ON DELETE CASCADE,
  conflict_type       TEXT,
  resolved            BOOLEAN DEFAULT FALSE,
  resolution_fact_id  UUID REFERENCES semantic_facts(id) ON DELETE SET NULL,
  detected_at         TIMESTAMPTZ DEFAULT now(),
  resolved_at         TIMESTAMPTZ
);

-- ----------------------------------------------------------------------
-- procedures  (procedural memory; versioned)
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS procedures (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name                TEXT NOT NULL,
  trigger_pattern     TEXT,
  steps               JSONB NOT NULL,
  source_episodes     UUID[],
  version             INTEGER DEFAULT 1,
  status              TEXT DEFAULT 'active',
  created_at          TIMESTAMPTZ DEFAULT now(),
  last_validated_at   TIMESTAMPTZ,
  embedding           VECTOR(1024)
);

-- ----------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------
-- HNSW vector indexes (cosine ops). pgvector >= 0.5 supports HNSW.
-- DO blocks cannot read psql variables, so we resolve schemaname dynamically
-- via current_schema() (we set search_path above to the target schema).
DO $$
DECLARE
  s text := current_schema();
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = s AND indexname = 'episode_events_embedding_hnsw'
  ) THEN
    EXECUTE 'CREATE INDEX episode_events_embedding_hnsw ON episode_events
             USING hnsw (embedding vector_cosine_ops)';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = s AND indexname = 'semantic_facts_embedding_hnsw'
  ) THEN
    EXECUTE 'CREATE INDEX semantic_facts_embedding_hnsw ON semantic_facts
             USING hnsw (embedding vector_cosine_ops)';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = s AND indexname = 'procedures_embedding_hnsw'
  ) THEN
    EXECUTE 'CREATE INDEX procedures_embedding_hnsw ON procedures
             USING hnsw (embedding vector_cosine_ops)';
  END IF;
END $$;

-- ----------------------------------------------------------------------
-- v2 metadata column backfill (idempotent — runs on already-existing tables)
-- design §15. Mirror this block in `migrate_schema_v2.py` for cross-tool runs.
-- ----------------------------------------------------------------------
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS project        TEXT;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS tool           TEXT;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS model          TEXT;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS task_category  TEXT;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS author         TEXT;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS last_validated TIMESTAMPTZ;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS last_accessed  TIMESTAMPTZ;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS closing_commit TEXT;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS files_touched  TEXT[];

ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS project        TEXT;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS tool           TEXT;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS model          TEXT;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS task_category  TEXT;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS author         TEXT;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS last_validated TIMESTAMPTZ;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS last_accessed  TIMESTAMPTZ;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS closing_commit TEXT;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS files_touched  TEXT[];

-- ----------------------------------------------------------------------
-- v3 metadata column backfill (idempotent, design §16, added 2026-05-04)
-- Mirror this block in `migrate_schema_v3.py`.
-- ----------------------------------------------------------------------
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS confidence_source        TEXT;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS confirmation_count       INTEGER DEFAULT 0;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS valid_until              TIMESTAMPTZ;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS causal_parent_id         TEXT;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS embedding_model_version  TEXT;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS domain                   TEXT;
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS goal                     TEXT;
-- Phase D (added 2026-05-06).
ALTER TABLE semantic_facts ADD COLUMN IF NOT EXISTS chunk_context            TEXT;

ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS confidence_source        TEXT;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS confirmation_count       INTEGER DEFAULT 0;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS valid_until              TIMESTAMPTZ;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS causal_parent_id         TEXT;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS embedding_model_version  TEXT;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS domain                   TEXT;
ALTER TABLE episode_events ADD COLUMN IF NOT EXISTS goal                     TEXT;

-- B-tree indexes for time-series + lookup
CREATE INDEX IF NOT EXISTS episode_events_user_time_idx
  ON episode_events (user_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS semantic_facts_subject_predicate_status_idx
  ON semantic_facts (subject, predicate, status);

-- v2 metadata-filter indexes (design §15)
CREATE INDEX IF NOT EXISTS semantic_facts_project_task_category_idx
  ON semantic_facts (project, task_category);

CREATE INDEX IF NOT EXISTS semantic_facts_last_accessed_idx
  ON semantic_facts (last_accessed DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS episode_events_project_task_category_idx
  ON episode_events (project, task_category);

CREATE INDEX IF NOT EXISTS episode_events_last_accessed_idx
  ON episode_events (last_accessed DESC NULLS LAST);

-- v3 metadata-filter indexes (design §16)
CREATE INDEX IF NOT EXISTS semantic_facts_domain_goal_idx
  ON semantic_facts (domain, goal);

CREATE INDEX IF NOT EXISTS semantic_facts_causal_parent_id_idx
  ON semantic_facts (causal_parent_id) WHERE causal_parent_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS episode_events_domain_goal_idx
  ON episode_events (domain, goal);

-- GIN for hybrid full-text search on raw_content
CREATE INDEX IF NOT EXISTS episode_events_raw_content_fts_idx
  ON episode_events USING gin (to_tsvector('english', raw_content));

-- pg_trgm GIN for fuzzy/BM25-ish substring search on facts
CREATE INDEX IF NOT EXISTS semantic_facts_object_trgm_idx
  ON semantic_facts USING gin (object gin_trgm_ops);

CREATE INDEX IF NOT EXISTS semantic_facts_subject_trgm_idx
  ON semantic_facts USING gin (subject gin_trgm_ops);

-- ----------------------------------------------------------------------
-- Helper view: active semantic facts only
-- ----------------------------------------------------------------------
CREATE OR REPLACE VIEW active_facts AS
  SELECT * FROM semantic_facts WHERE status = 'active';

-- Done.
SELECT 'init_agent_memory_schema: ok in schema ' || current_schema() AS status;
