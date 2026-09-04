-- Read-only PostgreSQL diagnostic set for build-loop:database-practice.
-- Run inside BEGIN READ ONLY with a statement timeout. Requires pg_stat_statements
-- for sections 2-3; every other section works on a stock instance.
--
-- psql "$DATABASE_URL" -X -A -F $'\t' -v ON_ERROR_STOP=1 \
--   -c 'set statement_timeout=30000' -f diagnostic-queries.sql

-- 1. Counter window. Everything below is "since" these timestamps.
--    stats_reset NULL means never reset; use postmaster start as the floor.
select pg_postmaster_start_time()                       as counters_since,
       now() - pg_postmaster_start_time()               as window_length,
       (select stats_reset from pg_stat_database
         where datname = current_database())            as stats_reset;

-- 2. Where the time actually goes. Rank every proposal against this.
select round((total_exec_time / 3600000)::numeric, 2)                          as hours,
       round((100.0 * total_exec_time
              / sum(total_exec_time) over ())::numeric, 2)                     as pct_of_db_time,
       calls,
       round(mean_exec_time::numeric, 1)                                       as mean_ms,
       round((rows::numeric / nullif(calls, 0)), 2)                            as rows_per_call,
       temp_blks_written,
       left(regexp_replace(query, '\s+', ' ', 'g'), 160)                       as statement
  from pg_stat_statements
 where dbid = (select oid from pg_database where datname = current_database())
 order by total_exec_time desc
 limit 25;

-- 3. Single-row-insert fingerprint: rows_per_call = 1.00 on a high-call INSERT.
select calls, round(mean_exec_time::numeric, 1) as mean_ms,
       round((total_exec_time / 3600000)::numeric, 2) as hours,
       left(regexp_replace(query, '\s+', ' ', 'g'), 100) as statement
  from pg_stat_statements
 where query ~* '^\s*insert' and rows = calls and calls > 1000
 order by total_exec_time desc
 limit 15;

-- 4. Per-table liveness. idx_scan > 0 means an application issued a filtered
--    query; audit scripts only produce seq_scan. Compare seq_scan against the
--    median across peers to find the audit/monitor floor.
select relname,
       n_live_tup, n_dead_tup,
       n_tup_ins, n_tup_upd, n_tup_del,
       seq_scan, coalesce(idx_scan, 0) as idx_scan,
       last_autovacuum, last_autoanalyze
  from pg_stat_user_tables
 order by (coalesce(idx_scan, 0) + seq_scan) asc;

-- 5. Exact emptiness. NEVER decide this from n_live_tup, which is a stale
--    planner estimate. Generates the count statements; run the output.
select format('select %L as tbl, count(*) from %I.%I;', relname, schemaname, relname)
  from pg_stat_user_tables
 where n_live_tup = 0
 order by relname;

-- 6. Index cost vs benefit. An index with idx_scan near zero is paid for on
--    every insert and never read. Check size against shared_buffers.
select t.relname                              as tbl,
       i.relname                              as idx,
       am.amname                              as method,
       coalesce(s.idx_scan, 0)                as scans,
       pg_size_pretty(pg_relation_size(i.oid)) as size,
       pg_get_indexdef(i.oid)                 as definition
  from pg_class i
  join pg_index x  on x.indexrelid = i.oid
  join pg_class t  on t.oid = x.indrelid
  join pg_namespace n on n.oid = i.relnamespace and n.nspname = 'public'
  join pg_am am on am.oid = i.relam
  left join pg_stat_user_indexes s on s.indexrelid = i.oid
 order by coalesce(s.idx_scan, 0) asc, pg_relation_size(i.oid) desc;

select name, setting, unit from pg_settings
 where name in ('shared_buffers', 'work_mem', 'effective_cache_size',
                'max_connections', 'max_parallel_workers_per_gather');

-- 7. Duplicate indexes on the same expression.
select indrelid::regclass as tbl, count(*) as copies,
       array_agg(indexrelid::regclass) as indexes
  from pg_index
 group by indrelid, indkey::text, indexprs::text, indpred::text
having count(*) > 1;

-- 8. Work spilling to disk. temp_bytes is a first-class latency signal.
select temp_files, pg_size_pretty(temp_bytes) as temp_written,
       blks_read, blks_hit,
       round(100.0 * blks_hit / nullif(blks_hit + blks_read, 0), 2) as cache_hit_pct,
       deadlocks
  from pg_stat_database
 where datname = current_database();

select round((total_exec_time / 1000)::numeric, 0) as sec, calls,
       round((temp_blks_written * 8192 / 1e9)::numeric, 2) as temp_gb,
       left(regexp_replace(query, '\s+', ' ', 'g'), 140) as statement
  from pg_stat_statements
 where temp_blks_written > 0
 order by temp_blks_written desc
 limit 10;

-- 9. TOAST ratio. A large gap between total size and heap+index size means the
--    row is wide, and any predicate on the wide column de-TOASTs on every scan.
select c.relname,
       pg_size_pretty(pg_total_relation_size(c.oid))                     as total,
       pg_size_pretty(pg_relation_size(c.oid))                           as heap,
       pg_size_pretty(pg_indexes_size(c.oid))                            as indexes,
       pg_size_pretty(pg_total_relation_size(c.oid)
                      - pg_relation_size(c.oid)
                      - pg_indexes_size(c.oid))                          as toast,
       s.seq_scan
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace and n.nspname = 'public'
  left join pg_stat_user_tables s on s.relid = c.oid
 where c.relkind = 'r'
 order by pg_total_relation_size(c.oid) desc
 limit 15;

-- 10. Column population contract check. Any column a reader consumes that comes
--     back 100% empty on a populated table is a write-contract gap. Generates
--     the per-column statements. Set the table filter first:
--       set bl.tbl = 'rss_sources';
select format(
         'select %L as col, count(*) as rows, count(%I) as populated from %I.%I;',
         table_name || '.' || column_name, column_name, table_schema, table_name)
  from information_schema.columns
 where table_schema = 'public' and is_nullable = 'YES'
   and table_name = coalesce(current_setting('bl.tbl', true), table_name)
 order by table_name, ordinal_position;
