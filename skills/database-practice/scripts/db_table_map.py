#!/usr/bin/env python3
"""db_table_map.py — read-only PostgreSQL table map, tracked over time.

Produces a per-table map (domain, size, counters, indexes, FKs, liveness) plus
the global sections a cost claim needs (counter window, function GUCs, vector
index cost vs benefit, top statements, temp spill), as JSON and as Markdown.

The JSON is the tracking artifact: re-run with ``--prev <old.json>`` and the
map reports what changed — new and removed tables, size moves over 10%, indexes
whose ``idx_scan`` stayed flat while their table kept taking inserts, and
liveness verdicts that flipped.

Every statement runs inside ``BEGIN READ ONLY`` with a ``SET LOCAL
statement_timeout``, and the generated script is whitelist-checked before it is
handed to ``psql``: only ``select``, ``with``, ``begin``, ``set local
statement_timeout`` and ``rollback`` may start a statement. The script cannot
issue DDL or DML.

Usage::

    python3 db_table_map.py --dsn "$DIRECT_URL" \
        --out-json docs/04-operations/database-audits/2026-09-05-database-map.json \
        --out-md   docs/04-operations/database-audits/2026-09-05-database-map.md \
        --prev     docs/04-operations/database-audits/2026-08-01-database-map.json

With no ``--dsn`` the DSN is read from ``DATABASE_URL`` or ``DIRECT_URL``.
With no ``--out-md`` the Markdown map is written to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# psql refuses to connect when the URI carries client-library-only parameters.
# Prisma and Supabase pooler URLs routinely carry all three.
UNSUPPORTED_URI_PARAMS = ("pgbouncer", "connection_limit", "pool_timeout")

SECTION_MARK = "@@bl-section:"
SECTION_END = "@@"

# Only these tokens may begin a statement in the generated script.
ALLOWED_STATEMENT_STARTS = ("select", "with", "begin", "rollback", "set")

DEFAULT_DOMAIN_RULES: list[list[str]] = [
    ["auth", r"^(auth|users?|accounts?|sessions?|roles?|permissions?|api_keys?)"],
    ["queue", r"(queue|job|task|worker|outbox|dead_letter|retry)"],
    ["embedding", r"(embedding|vector|chunk)"],
    ["graph", r"(entit|node|edge|relation|graph|mention|pair|triple)"],
    ["audit", r"(audit|event|log|history|snapshot|ledger)"],
    ["cache", r"(cache|summary|rollup|agg|_mv$|materialized|pack)"],
    ["taxonomy", r"(categor|topic|taxonom|classif|(^|_)(tag|label)s?($|_))"],
    ["content", r"(article|post|document|content|feed|source|podcast|story|page)"],
]
DEFAULT_DOMAIN = "other"

VECTOR_ACCESS_METHODS = ("hnsw", "ivfflat")
SPECIAL_ACCESS_METHODS = VECTOR_ACCESS_METHODS + ("gin", "gist", "brin")

# An index is "cold" when the workload almost never reads it but every insert
# maintains it. 100 scans over a multi-month window is noise, not a read path.
COLD_INDEX_SCAN_CEILING = 100
COLD_INDEX_INSERT_FLOOR = 10_000
# Size delta that counts as a real move between two maps.
SIZE_DELTA_PCT = 10.0

SHAPES: dict[str, str] = {
    "vector-insert-above-cache": "Insert against a vector index larger than cache",
    "toast-predicate": "Predicate on a TOASTed column",
    "per-row-jsonb-trigram": "Per-row lookup through jsonb + trigram",
    "vector-read-above-cache": "Vector similarity read",
    "index-maintenance-on-writes": "Index maintenance charged to writes",
}

MD_SECTIONS = (
    "Counter window",
    "Server settings",
    "Domain rollup",
    "Tables",
    "Liveness",
    "Vector, text, and GIN indexes",
    "Function GUCs",
    "Top statements by total_exec_time",
    "Temp spill",
    "Shapes",
)
MD_DIFF_SECTION = "Diff vs previous map"


# ---------------------------------------------------------------------------
# DSN handling
# ---------------------------------------------------------------------------

def sanitize_dsn(dsn: str) -> str:
    """Drop client-library-only URI parameters psql rejects.

    ``pgbouncer``, ``connection_limit`` and ``pool_timeout`` are Prisma/pooler
    parameters. libpq treats an unknown URI parameter as a fatal connection
    error, so a DSN copied out of ``.env`` fails before it reaches the server.
    Non-URI (keyword/value) DSNs are returned untouched.
    """
    if "://" not in dsn:
        return dsn
    parts = urlsplit(dsn)
    if not parts.query:
        return dsn
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in UNSUPPORTED_URI_PARAMS
    ]
    query = urlencode(kept)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def redact_dsn(dsn: str) -> str:
    """Return the DSN with any password removed, safe to write into a report."""
    if "://" not in dsn:
        return re.sub(r"password=\S+", "password=***", dsn)
    parts = urlsplit(dsn)
    netloc = parts.netloc
    if "@" in netloc:
        creds, host = netloc.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        netloc = f"{user}:***@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def resolve_dsn(explicit: str | None, env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    for candidate in (explicit, env.get("DATABASE_URL"), env.get("DIRECT_URL")):
        if candidate:
            return candidate
    raise SystemExit(
        "no DSN: pass --dsn, or set DATABASE_URL or DIRECT_URL in the environment"
    )


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

USER_SCHEMA_FILTER = "n.nspname not in ('pg_catalog', 'information_schema') and n.nspname !~ '^pg_'"

# `<+>` as a bare regex means "one or more '<' then '>'", which matches the
# plpgsql not-equals operator `<>` and floods the map with every platform
# function. The plus is escaped; the standard_conforming_strings default keeps
# the backslash literal on its way to the regex engine.
VECTOR_OPERATOR_REGEX = r"<=>|<->|<#>|<\+>"

# Platform-managed schemas. A function here is only interesting when it
# genuinely touches vectors; its search_path GUC is the provider's business.
SYSTEM_MANAGED_SCHEMAS = (
    "auth", "cron", "extensions", "graphql", "graphql_public", "net",
    "pgbouncer", "pgsodium", "realtime", "storage", "supabase_functions",
    "supabase_migrations", "vault",
)
SYSTEM_MANAGED_SCHEMA_LIST = ", ".join(f"'{name}'" for name in SYSTEM_MANAGED_SCHEMAS)

SECTION_SQL: list[tuple[str, str]] = [
    (
        "window",
        """
select pg_postmaster_start_time()::text,
       now()::text,
       coalesce((select stats_reset::text from pg_stat_database
                  where datname = current_database()), ''),
       current_database(),
       version();
""",
    ),
    (
        "settings",
        """
select name, setting, coalesce(unit, '')
  from pg_settings
 where name in ('shared_buffers', 'work_mem', 'maintenance_work_mem',
                'effective_cache_size', 'max_connections',
                'max_parallel_workers_per_gather', 'server_version')
 order by name;
""",
    ),
    (
        "tables",
        """
select s.schemaname,
       s.relname,
       pg_total_relation_size(c.oid),
       pg_relation_size(c.oid),
       pg_indexes_size(c.oid),
       greatest(pg_total_relation_size(c.oid)
                - pg_relation_size(c.oid)
                - pg_indexes_size(c.oid), 0),
       s.n_live_tup, s.n_dead_tup,
       s.n_tup_ins, s.n_tup_upd, s.n_tup_del,
       s.seq_scan, coalesce(s.idx_scan, 0),
       coalesce(s.last_autovacuum::text, ''),
       coalesce(s.last_autoanalyze::text, ''),
       coalesce(array_to_string(c.reloptions, ' '), '')
  from pg_stat_user_tables s
  join pg_class c on c.oid = s.relid
 order by pg_total_relation_size(c.oid) desc;
""",
    ),
    (
        "indexes",
        f"""
select n.nspname,
       t.relname,
       i.relname,
       am.amname,
       pg_relation_size(i.oid),
       coalesce(si.idx_scan, 0),
       replace(replace(pg_get_indexdef(i.oid), chr(9), ' '), chr(10), ' ')
  from pg_class i
  join pg_index x on x.indexrelid = i.oid
  join pg_class t on t.oid = x.indrelid
  join pg_namespace n on n.oid = i.relnamespace
  join pg_am am on am.oid = i.relam
  left join pg_stat_user_indexes si on si.indexrelid = i.oid
 where {USER_SCHEMA_FILTER}
 order by pg_relation_size(i.oid) desc;
""",
    ),
    (
        "special_columns",
        """
select table_schema, table_name, column_name, udt_name
  from information_schema.columns
 where udt_name in ('vector', 'halfvec', 'sparsevec', 'tsvector')
   and table_schema not in ('pg_catalog', 'information_schema')
 order by table_schema, table_name, ordinal_position;
""",
    ),
    (
        "foreign_keys",
        f"""
select con.conname,
       sn.nspname, src.relname,
       tn.nspname, tgt.relname
  from pg_constraint con
  join pg_class src on src.oid = con.conrelid
  join pg_namespace sn on sn.oid = src.relnamespace
  join pg_class tgt on tgt.oid = con.confrelid
  join pg_namespace tn on tn.oid = tgt.relnamespace
  join pg_namespace n on n.oid = src.relnamespace
 where con.contype = 'f' and {USER_SCHEMA_FILTER}
 order by src.relname, con.conname;
""",
    ),
    (
        "functions",
        f"""
with candidate as (
  select n.nspname as schema_name,
         p.proname as function_name,
         coalesce(array_to_string(p.proconfig, ' '), '') as proconfig,
         (p.prosrc ~ '{VECTOR_OPERATOR_REGEX}'
          or exists (select 1 from pg_type ty
                      where ty.typname in ('vector', 'halfvec', 'sparsevec')
                        and (ty.oid = p.prorettype
                             or ty.oid = any(coalesce(p.proallargtypes,
                                                      p.proargtypes::oid[]))))) as uses_vector
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    join pg_language l on l.oid = p.prolang
   where {USER_SCHEMA_FILTER}
     -- only functions someone wrote: extension internals (pgvector's own
     -- vector_add, halfvec_in, ...) are C code that no ALTER FUNCTION should touch
     and l.lanname in ('sql', 'plpgsql')
     and not exists (select 1 from pg_depend d
                      where d.classid = 'pg_proc'::regclass
                        and d.objid = p.oid and d.deptype = 'e')
)
select distinct schema_name,
       function_name,
       proconfig,
       case when uses_vector then 'vector' else '' end
  from candidate
 where uses_vector
    or (proconfig <> '' and schema_name not in ({SYSTEM_MANAGED_SCHEMA_LIST}))
 order by schema_name, function_name;
""",
    ),
    (
        "database_temp",
        """
select temp_files, temp_bytes, blks_read, blks_hit, deadlocks
  from pg_stat_database
 where datname = current_database();
""",
    ),
]

STATEMENTS_SQL = """
select calls,
       round(total_exec_time::numeric, 0),
       round(mean_exec_time::numeric, 1),
       round((100.0 * total_exec_time / nullif(sum(total_exec_time) over (), 0))::numeric, 2),
       round((rows::numeric / nullif(calls, 0)), 2),
       temp_blks_written,
       left(regexp_replace(query, '\\s+', ' ', 'g'), 200)
  from pg_stat_statements
 where dbid = (select oid from pg_database where datname = current_database())
 order by total_exec_time desc
 limit 25;
"""

STATEMENT_TEMP_SQL = """
select calls,
       temp_blks_written,
       left(regexp_replace(query, '\\s+', ' ', 'g'), 160)
  from pg_stat_statements
 where temp_blks_written > 0
   and dbid = (select oid from pg_database where datname = current_database())
 order by temp_blks_written desc
 limit 10;
"""

PROBE_SQL = """
select count(*) from pg_extension where extname = 'pg_stat_statements';
"""


def build_script(sections: list[tuple[str, str]], statement_timeout_s: int) -> str:
    """Wrap the section queries in a read-only, timeout-bounded transaction."""
    parts = [
        "BEGIN READ ONLY;",
        f"SET LOCAL statement_timeout = '{statement_timeout_s}s';",
    ]
    for name, sql in sections:
        parts.append(f"select '{SECTION_MARK}{name}{SECTION_END}';")
        parts.append(sql.strip())
    parts.append("ROLLBACK;")
    return "\n".join(parts) + "\n"


def assert_read_only(script: str) -> None:
    """Reject any statement that is not a read.

    Whitelist, not blacklist: a statement may only begin with ``select``,
    ``with``, ``begin``, ``rollback``, or ``set local statement_timeout``.
    """
    for raw in script.split(";"):
        stripped = "\n".join(
            line for line in raw.splitlines() if not line.strip().startswith("--")
        ).strip()
        if not stripped:
            continue
        head = stripped.lower().split(None, 1)[0]
        if head not in ALLOWED_STATEMENT_STARTS:
            raise ValueError(f"refusing to run a non-read statement: {stripped[:60]!r}")
        if head == "set" and not stripped.lower().startswith("set local statement_timeout"):
            raise ValueError(f"refusing to run a non-read statement: {stripped[:60]!r}")


def run_psql(dsn: str, script: str, timeout_s: int, psql: str = "psql") -> str:
    """Execute a read-only script through psql and return raw tab-separated rows."""
    assert_read_only(script)
    cmd = [
        psql,
        sanitize_dsn(dsn),
        "-X",
        "-A",
        "-t",
        "-F",
        "\t",
        "-v",
        "ON_ERROR_STOP=1",
        "-f",
        "-",
    ]
    env = dict(os.environ)
    env.setdefault("PGCONNECT_TIMEOUT", "10")
    try:
        proc = subprocess.run(
            cmd,
            input=script,
            text=True,
            capture_output=True,
            timeout=timeout_s + 30,
            env=env,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(f"psql not found on PATH: {exc}") from exc
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - timing dependent
        raise SystemExit(f"psql timed out after {timeout_s + 30}s") from exc
    if proc.returncode != 0:
        raise SystemExit(f"psql failed ({proc.returncode}): {proc.stderr.strip()[:800]}")
    return proc.stdout


def parse_sections(output: str) -> dict[str, list[list[str]]]:
    """Split marker-delimited psql output into ``{section: [row, ...]}``."""
    sections: dict[str, list[list[str]]] = {}
    current: str | None = None
    for line in output.splitlines():
        if line.startswith(SECTION_MARK) and line.endswith(SECTION_END):
            current = line[len(SECTION_MARK) : -len(SECTION_END)]
            sections.setdefault(current, [])
            continue
        if current is None or not line.strip():
            continue
        sections[current].append(line.split("\t"))
    return sections


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------

def _int(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def classify_domain(name: str, rules: list[list[str]]) -> str:
    for domain, pattern in rules:
        if re.search(pattern, name, re.IGNORECASE):
            return domain
    return DEFAULT_DOMAIN


def liveness(table: dict[str, Any]) -> tuple[str, str]:
    """Return ``(verdict, reason)`` from the runtime counters alone.

    Per the constitution: ``idx_scan > 0`` means an application issued a
    filtered query (audit scripts produce ``seq_scan`` only), and ``n_live_tup``
    is a stale planner estimate that must never decide emptiness.
    """
    idx_scan = table["idx_scan"]
    ins = table["n_tup_ins"]
    upd = table["n_tup_upd"]
    dele = table["n_tup_del"]
    writes = ins + upd + dele
    live_est = table["n_live_tup"]

    if idx_scan > 0:
        return "live", f"{idx_scan:,} index scans in the window"
    if ins > 0:
        if live_est <= 0:
            return "written-only", f"{ins:,} inserts, no index reads, ~0 live rows (drained queue)"
        return "written-only", f"{ins:,} inserts, no index reads (write-only sink)"
    if writes > 0 or live_est > 0 or table["seq_scan"] > 0:
        return "idle", (
            f"no index reads and no inserts; {table['seq_scan']:,} seq scans, "
            f"~{live_est:,} live rows (estimate)"
        )
    return "never-written", "no reads and no writes since the counters started"


def build_map(
    sections: dict[str, list[list[str]]],
    domain_rules: list[list[str]],
    dsn_label: str,
    has_pg_stat_statements: bool,
) -> dict[str, Any]:
    window_rows = sections.get("window", [[]])
    window_row = window_rows[0] if window_rows and window_rows[0] else ["", "", "", "", ""]
    window = {
        "postmaster_start_time": window_row[0] if len(window_row) > 0 else "",
        "collected_at": window_row[1] if len(window_row) > 1 else "",
        "stats_reset": window_row[2] if len(window_row) > 2 else "",
        "database": window_row[3] if len(window_row) > 3 else "",
        "server_version": window_row[4] if len(window_row) > 4 else "",
    }

    settings = {row[0]: {"setting": row[1], "unit": row[2] if len(row) > 2 else ""}
                for row in sections.get("settings", []) if len(row) >= 2}
    shared_buffers_bytes = _setting_bytes(settings.get("shared_buffers"))

    tables: dict[str, dict[str, Any]] = {}
    for row in sections.get("tables", []):
        if len(row) < 16:
            continue
        schema, name = row[0], row[1]
        key = name if schema == "public" else f"{schema}.{name}"
        table = {
            "key": key,
            "schema": schema,
            "name": name,
            "domain": classify_domain(name, domain_rules),
            "total_bytes": _int(row[2]),
            "heap_bytes": _int(row[3]),
            "index_bytes": _int(row[4]),
            "toast_bytes": _int(row[5]),
            "n_live_tup": _int(row[6]),
            "n_dead_tup": _int(row[7]),
            "n_tup_ins": _int(row[8]),
            "n_tup_upd": _int(row[9]),
            "n_tup_del": _int(row[10]),
            "seq_scan": _int(row[11]),
            "idx_scan": _int(row[12]),
            "last_autovacuum": row[13],
            "last_autoanalyze": row[14],
            "reloptions": row[15],
            "indexes": [],
            "vector_columns": [],
            "tsvector_columns": [],
            "fk_out": [],
            "fk_in": [],
            "estimates_note": "n_live_tup / n_dead_tup are planner estimates, not counts",
        }
        verdict, reason = liveness(table)
        table["liveness"] = verdict
        table["liveness_reason"] = reason
        tables[key] = table

    indexes: list[dict[str, Any]] = []
    for row in sections.get("indexes", []):
        if len(row) < 7:
            continue
        schema, table_name, index_name = row[0], row[1], row[2]
        key = table_name if schema == "public" else f"{schema}.{table_name}"
        entry = {
            "table": key,
            "name": index_name,
            "access_method": row[3],
            "size_bytes": _int(row[4]),
            "idx_scan": _int(row[5]),
            "definition": row[6],
        }
        indexes.append(entry)
        if key in tables:
            tables[key]["indexes"].append(entry)

    for row in sections.get("special_columns", []):
        if len(row) < 4:
            continue
        schema, table_name, column, udt = row
        key = table_name if schema == "public" else f"{schema}.{table_name}"
        if key not in tables:
            continue
        bucket = "tsvector_columns" if udt == "tsvector" else "vector_columns"
        tables[key][bucket].append({"column": column, "type": udt})

    for row in sections.get("foreign_keys", []):
        if len(row) < 5:
            continue
        conname, src_schema, src, tgt_schema, tgt = row
        src_key = src if src_schema == "public" else f"{src_schema}.{src}"
        tgt_key = tgt if tgt_schema == "public" else f"{tgt_schema}.{tgt}"
        if src_key in tables:
            tables[src_key]["fk_out"].append({"constraint": conname, "references": tgt_key})
        if tgt_key in tables:
            tables[tgt_key]["fk_in"].append({"constraint": conname, "from": src_key})

    functions = []
    for row in sections.get("functions", []):
        if len(row) < 4:
            continue
        proconfig = row[2]
        functions.append(
            {
                "schema": row[0],
                "name": row[1],
                "proconfig": [item for item in proconfig.split(" ") if item],
                "uses_vector_ops": row[3] == "vector",
            }
        )

    statements = []
    for row in sections.get("statements", []):
        if len(row) < 7:
            continue
        statements.append(
            {
                "calls": _int(row[0]),
                "total_exec_ms": _float(row[1]),
                "mean_exec_ms": _float(row[2]),
                "pct_of_db_time": _float(row[3]),
                "rows_per_call": _float(row[4]),
                "temp_blks_written": _int(row[5]),
                "statement": row[6],
            }
        )

    temp_rows = sections.get("database_temp", [])
    temp = {}
    if temp_rows and len(temp_rows[0]) >= 5:
        row = temp_rows[0]
        blks_read, blks_hit = _int(row[2]), _int(row[3])
        total = blks_read + blks_hit
        temp = {
            "temp_files": _int(row[0]),
            "temp_bytes": _int(row[1]),
            "blks_read": blks_read,
            "blks_hit": blks_hit,
            "cache_hit_pct": round(100.0 * blks_hit / total, 2) if total else None,
            "deadlocks": _int(row[4]),
        }
    temp["top_spilling_statements"] = [
        {"calls": _int(r[0]), "temp_blks_written": _int(r[1]), "statement": r[2]}
        for r in sections.get("statement_temp", [])
        if len(r) >= 3
    ]

    domains: dict[str, dict[str, Any]] = {}
    for table in tables.values():
        bucket = domains.setdefault(
            table["domain"],
            {"tables": 0, "total_bytes": 0, "idx_scan": 0, "writes": 0},
        )
        bucket["tables"] += 1
        bucket["total_bytes"] += table["total_bytes"]
        bucket["idx_scan"] += table["idx_scan"]
        bucket["writes"] += table["n_tup_ins"] + table["n_tup_upd"] + table["n_tup_del"]

    data: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dsn": dsn_label,
        "window": window,
        "settings": settings,
        "shared_buffers_bytes": shared_buffers_bytes,
        "pg_stat_statements": has_pg_stat_statements,
        "table_count": len(tables),
        "domains": domains,
        "tables": [tables[key] for key in sorted(tables)],
        "indexes": indexes,
        "functions": functions,
        "statements": statements if has_pg_stat_statements else None,
        "temp": temp,
    }
    data["shapes"] = detect_shapes(data)
    return data


def _setting_bytes(setting: dict[str, str] | None) -> int | None:
    """Convert a pg_settings row to bytes. Blocks are 8kB unless stated."""
    if not setting:
        return None
    try:
        value = int(setting["setting"])
    except (KeyError, TypeError, ValueError):
        return None
    unit = (setting.get("unit") or "").strip()
    factors = {"": 1, "B": 1, "kB": 1024, "8kB": 8192, "MB": 1024**2, "GB": 1024**3}
    if unit not in factors:
        return None
    return value * factors[unit]


# ---------------------------------------------------------------------------
# Shape matching
# ---------------------------------------------------------------------------

def detect_shapes(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Match collected facts against the five cost shapes in the skill body."""
    findings: list[dict[str, Any]] = []
    shared_buffers = data.get("shared_buffers_bytes")
    tables = {table["key"]: table for table in data["tables"]}

    for index in sorted(data["indexes"], key=lambda i: -i["size_bytes"]):
        table = tables.get(index["table"])
        if table is None:
            continue
        am = index["access_method"]
        ins = table["n_tup_ins"]
        oversized = shared_buffers is not None and index["size_bytes"] > shared_buffers

        cold = (
            am in SPECIAL_ACCESS_METHODS
            and index["idx_scan"] <= COLD_INDEX_SCAN_CEILING
            and ins >= COLD_INDEX_INSERT_FLOOR
        )
        if cold:
            findings.append(
                {
                    "shape": "index-maintenance-on-writes",
                    "title": SHAPES["index-maintenance-on-writes"],
                    "object": f"{index['table']}.{index['name']}",
                    "evidence": (
                        f"{am} index of {human_bytes(index['size_bytes'])} with "
                        f"{index['idx_scan']:,} lifetime scans against "
                        f"{ins:,} table inserts"
                    ),
                    "action": "Drop it, or accept the write cost explicitly and record why",
                }
            )
        if am in VECTOR_ACCESS_METHODS and oversized and ins >= COLD_INDEX_INSERT_FLOOR:
            findings.append(
                {
                    "shape": "vector-insert-above-cache",
                    "title": SHAPES["vector-insert-above-cache"],
                    "object": f"{index['table']}.{index['name']}",
                    "evidence": (
                        f"{human_bytes(index['size_bytes'])} {am} graph against "
                        f"shared_buffers {human_bytes(shared_buffers)}; "
                        f"{ins:,} inserts each maintain it as random I/O"
                    ),
                    "action": (
                        "Split fixed from marginal insert cost, then batch harder "
                        "or shrink the graph (m / dimensions) to fit cache"
                    ),
                }
            )
        if am in VECTOR_ACCESS_METHODS and oversized and index["idx_scan"] > COLD_INDEX_SCAN_CEILING:
            findings.append(
                {
                    "shape": "vector-read-above-cache",
                    "title": SHAPES["vector-read-above-cache"],
                    "object": f"{index['table']}.{index['name']}",
                    "evidence": (
                        f"{index['idx_scan']:,} scans against a "
                        f"{human_bytes(index['size_bytes'])} graph that cannot fit "
                        f"shared_buffers ({human_bytes(shared_buffers)})"
                    ),
                    "action": "Size the graph to cache, or raise the cache",
                }
            )

    for table in data["tables"]:
        if (
            table["toast_bytes"] > table["heap_bytes"]
            and table["toast_bytes"] > 64 * 1024 * 1024
            and (table["seq_scan"] > 0 or table["idx_scan"] > 0)
        ):
            findings.append(
                {
                    "shape": "toast-predicate",
                    "title": SHAPES["toast-predicate"],
                    "object": table["key"],
                    "evidence": (
                        f"{human_bytes(table['toast_bytes'])} TOAST against "
                        f"{human_bytes(table['heap_bytes'])} heap on "
                        f"~{table['n_live_tup']:,} rows (estimate)"
                    ),
                    "action": (
                        "Check every predicate and sort touching the wide column; "
                        "maintain a derived scalar beside it and filter on that"
                    ),
                }
            )

    for statement in data.get("statements") or []:
        text = statement["statement"].lower()
        jsonb = "->>" in text or "->" in text or "@>" in text or "jsonb" in text
        fuzzy = "similarity(" in text or " ilike " in text or "%" in text or "trgm" in text
        if jsonb and fuzzy and statement["calls"] > 1000 and statement["mean_exec_ms"] >= 50:
            findings.append(
                {
                    "shape": "per-row-jsonb-trigram",
                    "title": SHAPES["per-row-jsonb-trigram"],
                    "object": statement["statement"][:80],
                    "evidence": (
                        f"{statement['calls']:,} calls at "
                        f"{statement['mean_exec_ms']:.0f} ms mean "
                        f"({statement['pct_of_db_time']:.1f}% of DB time)"
                    ),
                    "action": (
                        "Composite index on the real filter columns; move the fuzzy "
                        "match behind an exact one"
                    ),
                }
            )

    vector_functions = [f for f in data["functions"] if f["uses_vector_ops"]]
    for function in vector_functions:
        config = " ".join(function["proconfig"])
        if "hnsw.ef_search" not in config and "hnsw.iterative_scan" not in config:
            findings.append(
                {
                    "shape": "vector-read-above-cache",
                    "title": "Vector function carries no HNSW GUCs",
                    "object": f"{function['schema']}.{function['name']}",
                    "evidence": (
                        f"proconfig = {config or '(none)'} — no ef_search and no "
                        "iterative_scan, so the function runs at the pgvector defaults"
                    ),
                    "action": (
                        "ALTER FUNCTION ... SET hnsw.ef_search / hnsw.iterative_scan; "
                        "session SET is lost under a transaction-mode pooler"
                    ),
                }
            )
    return findings


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def diff_maps(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Compare two maps: new/removed tables, size moves, stalled indexes, liveness."""
    prev_tables = {t["key"]: t for t in previous.get("tables", [])}
    curr_tables = {t["key"]: t for t in current.get("tables", [])}

    counters_reset = (
        previous.get("window", {}).get("stats_reset")
        != current.get("window", {}).get("stats_reset")
    ) or (
        previous.get("window", {}).get("postmaster_start_time")
        != current.get("window", {}).get("postmaster_start_time")
    )

    size_deltas = []
    liveness_changes = []
    for key, curr in curr_tables.items():
        prev = prev_tables.get(key)
        if prev is None:
            continue
        before, after = prev["total_bytes"], curr["total_bytes"]
        if before > 0:
            pct = 100.0 * (after - before) / before
            if abs(pct) >= SIZE_DELTA_PCT:
                size_deltas.append(
                    {
                        "table": key,
                        "before_bytes": before,
                        "after_bytes": after,
                        "pct": round(pct, 1),
                    }
                )
        if prev.get("liveness") != curr.get("liveness"):
            liveness_changes.append(
                {
                    "table": key,
                    "before": prev.get("liveness"),
                    "after": curr.get("liveness"),
                    "reason": curr.get("liveness_reason", ""),
                }
            )

    prev_indexes = {(i["table"], i["name"]): i for i in previous.get("indexes", [])}
    stalled = []
    for index in current.get("indexes", []):
        prev_index = prev_indexes.get((index["table"], index["name"]))
        if prev_index is None:
            continue
        scan_delta = index["idx_scan"] - prev_index["idx_scan"]
        prev_table = prev_tables.get(index["table"])
        curr_table = curr_tables.get(index["table"])
        if prev_table is None or curr_table is None:
            continue
        ins_delta = curr_table["n_tup_ins"] - prev_table["n_tup_ins"]
        if scan_delta <= 0 and ins_delta > 0:
            stalled.append(
                {
                    "index": index["name"],
                    "table": index["table"],
                    "access_method": index["access_method"],
                    "scan_delta": scan_delta,
                    "insert_delta": ins_delta,
                    "size_bytes": index["size_bytes"],
                }
            )

    return {
        "previous_generated_at": previous.get("generated_at"),
        "counters_reset_between_maps": counters_reset,
        "new_tables": sorted(set(curr_tables) - set(prev_tables)),
        "removed_tables": sorted(set(prev_tables) - set(curr_tables)),
        "size_deltas": sorted(size_deltas, key=lambda d: -abs(d["pct"])),
        "stalled_indexes": sorted(stalled, key=lambda d: -d["insert_delta"]),
        "liveness_changes": sorted(liveness_changes, key=lambda d: d["table"]),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def human_bytes(value: int | None) -> str:
    if value is None:
        return "n/a"
    step = 1024.0
    amount = float(value)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(amount) < step or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= step
    return f"{amount:.1f} TB"


def render_markdown(data: dict[str, Any], diff: dict[str, Any] | None = None) -> str:
    window = data["window"]
    out: list[str] = []
    out.append(f"# Database map — {window.get('database') or 'database'}")
    out.append("")
    out.append(
        f"Generated {data['generated_at']} from `{data['dsn']}`. "
        f"{data['table_count']} user tables. Read-only collection; no DDL or DML was issued."
    )
    out.append("")

    out.append("## Counter window")
    out.append("")
    out.append("Every counter below is *since* these timestamps. State the window with any claim.")
    out.append("")
    out.append("| Field | Value |")
    out.append("|---|---|")
    out.append(f"| postmaster start | {window.get('postmaster_start_time', '')} |")
    out.append(f"| stats_reset | {window.get('stats_reset') or 'never reset'} |")
    out.append(f"| collected at | {window.get('collected_at', '')} |")
    out.append(f"| server | {window.get('server_version', '')[:80]} |")
    out.append(
        f"| pg_stat_statements | {'available' if data['pg_stat_statements'] else 'NOT INSTALLED — time attribution skipped'} |"
    )
    out.append("")

    out.append("## Server settings")
    out.append("")
    out.append("| Setting | Value |")
    out.append("|---|---|")
    for name in sorted(data["settings"]):
        entry = data["settings"][name]
        unit = entry.get("unit") or ""
        out.append(f"| {name} | {entry['setting']}{(' ' + unit) if unit else ''} |")
    out.append("")
    if data.get("shared_buffers_bytes"):
        out.append(
            f"`shared_buffers` resolves to {human_bytes(data['shared_buffers_bytes'])}. "
            "Any index larger than that is traversed from disk on every maintenance write."
        )
        out.append("")

    out.append("## Domain rollup")
    out.append("")
    out.append("| Domain | Tables | Total size | Index scans | Writes |")
    out.append("|---|---:|---:|---:|---:|")
    for domain in sorted(data["domains"], key=lambda d: -data["domains"][d]["total_bytes"]):
        bucket = data["domains"][domain]
        out.append(
            f"| {domain} | {bucket['tables']} | {human_bytes(bucket['total_bytes'])} "
            f"| {bucket['idx_scan']:,} | {bucket['writes']:,} |"
        )
    out.append("")

    out.append("## Tables")
    out.append("")
    out.append(
        "`live~` and `dead~` are planner estimates and never decide emptiness "
        "(constitution, Cost Attribution rule 7). Use `count(*)` for that."
    )
    out.append("")
    out.append("| Table | Domain | Total | Heap | Idx | TOAST | live~ | ins/upd/del | seq/idx scan | Liveness |")
    out.append("|---|---|---:|---:|---:|---:|---:|---|---|---|")
    for table in sorted(data["tables"], key=lambda t: -t["total_bytes"]):
        out.append(
            "| {key} | {domain} | {total} | {heap} | {idx} | {toast} | {live:,} "
            "| {ins:,}/{upd:,}/{dele:,} | {seq:,}/{idx_scan:,} | {liveness} |".format(
                key=table["key"],
                domain=table["domain"],
                total=human_bytes(table["total_bytes"]),
                heap=human_bytes(table["heap_bytes"]),
                idx=human_bytes(table["index_bytes"]),
                toast=human_bytes(table["toast_bytes"]),
                live=table["n_live_tup"],
                ins=table["n_tup_ins"],
                upd=table["n_tup_upd"],
                dele=table["n_tup_del"],
                seq=table["seq_scan"],
                idx_scan=table["idx_scan"],
                liveness=table["liveness"],
            )
        )
    out.append("")

    out.append("## Liveness")
    out.append("")
    buckets: dict[str, list[dict[str, Any]]] = {}
    for table in data["tables"]:
        buckets.setdefault(table["liveness"], []).append(table)
    for verdict in ("live", "written-only", "idle", "never-written"):
        entries = buckets.get(verdict, [])
        out.append(f"**{verdict}** — {len(entries)} table(s)")
        out.append("")
        if verdict != "live":
            for table in sorted(entries, key=lambda t: t["key"]):
                out.append(f"- `{table['key']}` — {table['liveness_reason']}")
            if entries:
                out.append("")
    out.append(
        "No verdict here retires anything. A retirement still needs the full gate in the skill body."
    )
    out.append("")

    out.append("## Vector, text, and GIN indexes")
    out.append("")
    out.append("| Index | Table | AM | Size | Scans | Table inserts |")
    out.append("|---|---|---|---:|---:|---:|")
    tables_by_key = {t["key"]: t for t in data["tables"]}
    special = [i for i in data["indexes"] if i["access_method"] in SPECIAL_ACCESS_METHODS]
    for index in sorted(special, key=lambda i: -i["size_bytes"]):
        table = tables_by_key.get(index["table"], {})
        out.append(
            f"| {index['name']} | {index['table']} | {index['access_method']} "
            f"| {human_bytes(index['size_bytes'])} | {index['idx_scan']:,} "
            f"| {table.get('n_tup_ins', 0):,} |"
        )
    if not special:
        out.append("| _none_ | | | | | |")
    out.append("")

    out.append("## Function GUCs")
    out.append("")
    out.append("| Function | proconfig | Uses vector ops |")
    out.append("|---|---|---|")
    for function in data["functions"]:
        config = " ".join(function["proconfig"]) or "_none_"
        out.append(
            f"| {function['schema']}.{function['name']} | {config} "
            f"| {'yes' if function['uses_vector_ops'] else 'no'} |"
        )
    if not data["functions"]:
        out.append("| _none_ | | |")
    out.append("")
    out.append(
        "A vector-search function with no `hnsw.ef_search` / `hnsw.iterative_scan` in "
        "`proconfig` runs at the pgvector defaults regardless of what the repo's SQL "
        "file says. That gap is drift, and only `ALTER FUNCTION ... SET` survives a "
        "transaction-mode pooler."
    )
    out.append("")

    out.append("## Top statements by total_exec_time")
    out.append("")
    if data["pg_stat_statements"]:
        out.append("| % DB time | Calls | Mean ms | rows/call | temp blks | Statement |")
        out.append("|---:|---:|---:|---:|---:|---|")
        for statement in data["statements"] or []:
            out.append(
                f"| {statement['pct_of_db_time']:.2f} | {statement['calls']:,} "
                f"| {statement['mean_exec_ms']:.1f} | {statement['rows_per_call']:.2f} "
                f"| {statement['temp_blks_written']:,} | `{statement['statement'][:120]}` |"
            )
    else:
        out.append(
            "`pg_stat_statements` is not installed, so no time attribution was collected. "
            "Every cost claim in a plan needs a measured share — install the extension "
            "before proposing a performance fix."
        )
    out.append("")

    out.append("## Temp spill")
    out.append("")
    temp = data["temp"]
    out.append("| Field | Value |")
    out.append("|---|---|")
    out.append(f"| temp_files | {temp.get('temp_files', 0):,} |")
    out.append(f"| temp_bytes | {human_bytes(temp.get('temp_bytes', 0))} |")
    out.append(f"| cache hit | {temp.get('cache_hit_pct')}% |")
    out.append(f"| deadlocks | {temp.get('deadlocks', 0):,} |")
    out.append("")
    for entry in temp.get("top_spilling_statements", []):
        out.append(
            f"- {entry['temp_blks_written']:,} temp blocks over {entry['calls']:,} calls: "
            f"`{entry['statement'][:100]}`"
        )
    if temp.get("top_spilling_statements"):
        out.append("")

    out.append("## Shapes")
    out.append("")
    out.append(
        "Each finding names which of the five cost shapes it matches. The shape picks "
        "the first action; it does not authorize the change."
    )
    out.append("")
    if data["shapes"]:
        out.append("| Shape | Object | Evidence | First action |")
        out.append("|---|---|---|---|")
        for finding in data["shapes"]:
            out.append(
                f"| {finding['title']} | `{finding['object']}` | {finding['evidence']} "
                f"| {finding['action']} |"
            )
    else:
        out.append("No shape matched the collected counters.")
    out.append("")

    if diff is not None:
        out.extend(_render_diff(diff))
    return "\n".join(out) + "\n"


def _render_diff(diff: dict[str, Any]) -> list[str]:
    out = [f"## {MD_DIFF_SECTION}", ""]
    out.append(f"Previous map generated {diff.get('previous_generated_at')}.")
    out.append("")
    if diff.get("counters_reset_between_maps"):
        out.append(
            "**The counters reset between the two maps.** Every delta below is a floor, "
            "not a measurement — the server restarted or statistics were reset."
        )
        out.append("")
    out.append(f"- New tables: {', '.join(diff['new_tables']) or 'none'}")
    out.append(f"- Removed tables: {', '.join(diff['removed_tables']) or 'none'}")
    out.append("")
    out.append(f"### Size moves over {SIZE_DELTA_PCT:.0f}%")
    out.append("")
    if diff["size_deltas"]:
        out.append("| Table | Before | After | Change |")
        out.append("|---|---:|---:|---:|")
        for entry in diff["size_deltas"]:
            out.append(
                f"| {entry['table']} | {human_bytes(entry['before_bytes'])} "
                f"| {human_bytes(entry['after_bytes'])} | {entry['pct']:+.1f}% |"
            )
    else:
        out.append("None.")
    out.append("")
    out.append("### Indexes maintained but not read since the last map")
    out.append("")
    if diff["stalled_indexes"]:
        out.append("| Index | Table | AM | New scans | New inserts | Size |")
        out.append("|---|---|---|---:|---:|---:|")
        for entry in diff["stalled_indexes"]:
            out.append(
                f"| {entry['index']} | {entry['table']} | {entry['access_method']} "
                f"| {entry['scan_delta']:,} | {entry['insert_delta']:,} "
                f"| {human_bytes(entry['size_bytes'])} |"
            )
    else:
        out.append("None.")
    out.append("")
    out.append("### Liveness changes")
    out.append("")
    if diff["liveness_changes"]:
        for entry in diff["liveness_changes"]:
            out.append(
                f"- `{entry['table']}`: {entry['before']} → {entry['after']} ({entry['reason']})"
            )
    else:
        out.append("None.")
    out.append("")
    return out


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def collect(dsn: str, statement_timeout_s: int, psql: str = "psql") -> tuple[dict[str, list[list[str]]], bool]:
    """Run the probe, then the main script. Returns parsed sections + extension flag."""
    probe_out = run_psql(
        dsn, build_script([("probe", PROBE_SQL)], statement_timeout_s), statement_timeout_s, psql
    )
    probe = parse_sections(probe_out).get("probe", [])
    has_pgss = bool(probe and probe[0] and _int(probe[0][0]) > 0)

    sections = list(SECTION_SQL)
    if has_pgss:
        sections.append(("statements", STATEMENTS_SQL))
        sections.append(("statement_temp", STATEMENT_TEMP_SQL))
    output = run_psql(dsn, build_script(sections, statement_timeout_s), statement_timeout_s, psql)
    return parse_sections(output), has_pgss


def load_domain_rules(path: str | None) -> list[list[str]]:
    if not path:
        return DEFAULT_DOMAIN_RULES
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or any(len(item) != 2 for item in raw):
        raise SystemExit("--domain-rules must be a JSON list of [domain, regex] pairs")
    for _, pattern in raw:
        re.compile(pattern)
    return [[str(domain), str(pattern)] for domain, pattern in raw]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only PostgreSQL table map for build-loop:database-practice.",
    )
    parser.add_argument("--dsn", help="Postgres DSN (default: $DATABASE_URL, then $DIRECT_URL)")
    parser.add_argument("--out-json", help="Write the machine-readable map here")
    parser.add_argument("--out-md", help="Write the Markdown map here (default: stdout)")
    parser.add_argument("--domain-rules", help="JSON file: list of [domain, regex] pairs")
    parser.add_argument("--prev", help="Previous --out-json map to diff against")
    parser.add_argument(
        "--statement-timeout",
        type=int,
        default=20,
        help="SET LOCAL statement_timeout, in seconds (default 20)",
    )
    parser.add_argument("--psql", default="psql", help="psql binary to use (default: psql)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    dsn = resolve_dsn(args.dsn)
    rules = load_domain_rules(args.domain_rules)

    sections, has_pgss = collect(dsn, args.statement_timeout, args.psql)
    data = build_map(sections, rules, redact_dsn(dsn), has_pgss)

    diff = None
    if args.prev:
        previous = json.loads(Path(args.prev).read_text(encoding="utf-8"))
        diff = diff_maps(previous, data)
        data["diff"] = diff

    markdown = render_markdown(data, diff)
    if args.out_json:
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    if args.out_md:
        Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_md).write_text(markdown, encoding="utf-8")
    else:
        sys.stdout.write(markdown)

    print(
        f"[db_table_map] {data['table_count']} tables, {len(data['indexes'])} indexes, "
        f"{len(data['shapes'])} shape findings",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
