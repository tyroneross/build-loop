-- init_agent_memory_schema.sql
--
-- Initialize the build_loop_memory schema in the agent_memory database.
--
-- Idempotent (uses CREATE … IF NOT EXISTS / CREATE OR REPLACE).
-- Run with:  psql -d agent_memory -f scripts/init_agent_memory_schema.sql
--
-- Per-project schema strategy: each consumer project gets its own schema
-- inside the single agent_memory DB. This file initializes
-- `build_loop_memory` for the build-loop project. To bootstrap another
-- project, copy this file and replace the schema name.
--
-- Source design: ~/dev/research/topics/repo-episodic-memory-framework/
-- repo-episodic-memory-framework.md §13.

\set ON_ERROR_STOP on

-- Required extensions.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Per-project schema (parameterizable; default build_loop_memory).
CREATE SCHEMA IF NOT EXISTS build_loop_memory;
SET search_path TO build_loop_memory, public;

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
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id    UUID REFERENCES sessions(id) ON DELETE SET NULL,
  user_id       TEXT NOT NULL,
  seq_num       INTEGER NOT NULL,
  occurred_at   TIMESTAMPTZ DEFAULT now(),
  actor         TEXT NOT NULL,            -- 'user', 'agent', 'tool', 'system'
  verb          TEXT NOT NULL,            -- 'said', 'called', 'returned', 'decided'
  object        TEXT,
  raw_content   TEXT,                     -- verbatim, never trimmed
  summary       TEXT,
  embedding     VECTOR(768),              -- nomic-embed-text dimension
  metadata      JSONB
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
  embedding          VECTOR(768),
  metadata           JSONB
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
  embedding           VECTOR(768)
);

-- ----------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------
-- HNSW vector indexes (cosine ops). pgvector >= 0.5 supports HNSW.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'build_loop_memory' AND indexname = 'episode_events_embedding_hnsw'
  ) THEN
    EXECUTE 'CREATE INDEX episode_events_embedding_hnsw ON episode_events
             USING hnsw (embedding vector_cosine_ops)';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'build_loop_memory' AND indexname = 'semantic_facts_embedding_hnsw'
  ) THEN
    EXECUTE 'CREATE INDEX semantic_facts_embedding_hnsw ON semantic_facts
             USING hnsw (embedding vector_cosine_ops)';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'build_loop_memory' AND indexname = 'procedures_embedding_hnsw'
  ) THEN
    EXECUTE 'CREATE INDEX procedures_embedding_hnsw ON procedures
             USING hnsw (embedding vector_cosine_ops)';
  END IF;
END $$;

-- B-tree indexes for time-series + lookup
CREATE INDEX IF NOT EXISTS episode_events_user_time_idx
  ON episode_events (user_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS semantic_facts_subject_predicate_status_idx
  ON semantic_facts (subject, predicate, status);

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
SELECT 'init_agent_memory_schema: ok' AS status;
