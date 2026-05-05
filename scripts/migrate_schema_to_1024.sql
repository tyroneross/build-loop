-- migrate_schema_to_1024.sql
--
-- Migrate per-project schema's embedding columns from VECTOR(768)
-- (nomic-embed-text) to VECTOR(1024) (mxbai-embed-large /
-- mlx-community/mxbai-embed-large-v1).
--
-- Schema is parameterized via the psql -v variable `schema`. Pass it on
-- the command line:
--
--   psql -d agent_memory -v schema=personal_memory   -f scripts/migrate_schema_to_1024.sql
--   psql -d agent_memory -v schema=build_loop_memory -f scripts/migrate_schema_to_1024.sql
--
-- Default when -v is not provided: `personal_memory`.
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
-- complete and then `psql -d agent_memory -v schema=<name> -f
-- scripts/init_agent_memory_schema.sql` to rebuild the indexes alongside
-- the dimension fix.
--
-- Idempotent: re-running on an already-1024 schema is a no-op for the
-- ALTER COLUMN statements (Postgres reports "is already of type vector(1024)")
-- and `DROP INDEX IF EXISTS` is always safe.

\set ON_ERROR_STOP on

\if :{?schema}
\echo Migrating schema: :schema
\else
\set schema personal_memory
\echo Defaulting to schema: :schema
\endif

-- 1. Drop dependent objects: HNSW indexes + the active_facts view (which
--    SELECT *'s from semantic_facts and so depends on the embedding column).
DROP INDEX IF EXISTS :"schema".episode_events_embedding_hnsw;
DROP INDEX IF EXISTS :"schema".semantic_facts_embedding_hnsw;
DROP INDEX IF EXISTS :"schema".procedures_embedding_hnsw;
DROP VIEW  IF EXISTS :"schema".active_facts;

-- 2. Migrate the embedding columns. USING NULL preserves all rows but
--    requires `sync_db_from_files.py --rebuild` to repopulate.
ALTER TABLE :"schema".episode_events
  ALTER COLUMN embedding TYPE VECTOR(1024) USING NULL;

ALTER TABLE :"schema".semantic_facts
  ALTER COLUMN embedding TYPE VECTOR(1024) USING NULL;

ALTER TABLE :"schema".procedures
  ALTER COLUMN embedding TYPE VECTOR(1024) USING NULL;

-- 3. Recreate HNSW indexes at the new dim. (Empty / NULL data is fine;
--    pgvector builds the index lazily as rows get embeddings.)
CREATE INDEX IF NOT EXISTS episode_events_embedding_hnsw
  ON :"schema".episode_events USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS semantic_facts_embedding_hnsw
  ON :"schema".semantic_facts USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS procedures_embedding_hnsw
  ON :"schema".procedures USING hnsw (embedding vector_cosine_ops);

-- 4. Recreate the active_facts helper view (mirrors init_agent_memory_schema.sql).
CREATE OR REPLACE VIEW :"schema".active_facts AS
  SELECT * FROM :"schema".semantic_facts WHERE status = 'active';

SELECT 'migrate_schema_to_1024: ok for schema ' || :'schema'
       || ' (run sync_db_from_files.py --rebuild to repopulate embeddings)' AS status;
