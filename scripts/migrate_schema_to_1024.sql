-- migrate_schema_to_1024.sql
--
-- Migrate build_loop_memory embedding columns from VECTOR(768) (nomic-embed-text)
-- to VECTOR(1024) (mxbai-embed-large / mlx-community/mxbai-embed-large-v1).
--
-- Embedding values cannot be cast across dimensionalities; this migration
-- NULLs the embedding column on every affected row and drops the HNSW
-- indexes. Re-populate via:
--
--   python3 scripts/sync_db_from_files.py --workdir <repo> --rebuild
--
-- which embeds each canonical .episodic/decisions/*.md via the new
-- embed_backend (MLX default, Ollama fallback) and re-INSERTs the rows.
--
-- The HNSW indexes are recreated AFTER repopulation by re-running
-- init_agent_memory_schema.sql (idempotent), or you can let `--rebuild`
-- complete and then `psql -d agent_memory -f scripts/init_agent_memory_schema.sql`
-- to rebuild the indexes alongside the dimension fix.
--
-- Idempotent: re-running on an already-1024 schema is a no-op for the
-- ALTER COLUMN statements (Postgres reports "is already of type vector(1024)")
-- and `DROP INDEX IF EXISTS` is always safe.
--
-- Run with:
--   psql -d agent_memory -f scripts/migrate_schema_to_1024.sql

\set ON_ERROR_STOP on

-- 1. Drop dependent objects: HNSW indexes + the active_facts view (which
--    SELECT *'s from semantic_facts and so depends on the embedding column).
DROP INDEX IF EXISTS build_loop_memory.episode_events_embedding_hnsw;
DROP INDEX IF EXISTS build_loop_memory.semantic_facts_embedding_hnsw;
DROP INDEX IF EXISTS build_loop_memory.procedures_embedding_hnsw;
DROP VIEW  IF EXISTS build_loop_memory.active_facts;

-- 2. Migrate the embedding columns. USING NULL preserves all rows but
--    requires `sync_db_from_files.py --rebuild` to repopulate.
ALTER TABLE build_loop_memory.episode_events
  ALTER COLUMN embedding TYPE VECTOR(1024) USING NULL;

ALTER TABLE build_loop_memory.semantic_facts
  ALTER COLUMN embedding TYPE VECTOR(1024) USING NULL;

ALTER TABLE build_loop_memory.procedures
  ALTER COLUMN embedding TYPE VECTOR(1024) USING NULL;

-- 3. Recreate HNSW indexes at the new dim. (Empty / NULL data is fine;
--    pgvector builds the index lazily as rows get embeddings.)
CREATE INDEX IF NOT EXISTS episode_events_embedding_hnsw
  ON build_loop_memory.episode_events USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS semantic_facts_embedding_hnsw
  ON build_loop_memory.semantic_facts USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS procedures_embedding_hnsw
  ON build_loop_memory.procedures USING hnsw (embedding vector_cosine_ops);

-- 4. Recreate the active_facts helper view (mirrors init_agent_memory_schema.sql).
CREATE OR REPLACE VIEW build_loop_memory.active_facts AS
  SELECT * FROM build_loop_memory.semantic_facts WHERE status = 'active';

SELECT 'migrate_schema_to_1024: ok (run sync_db_from_files.py --rebuild to repopulate embeddings)' AS status;
