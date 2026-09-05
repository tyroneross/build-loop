#!/usr/bin/env python3
"""Tests for the read-only PostgreSQL table map.

The psql runner is monkeypatched with fixture output modelled on a real
production instance: an HNSW index with 33 lifetime scans against 402k table
inserts, a table reporting n_live_tup 0 while serving 222k index scans, and a
vector-search function whose proconfig carries enable_seqscan but no HNSW GUCs.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("db_table_map.py")
SPEC = importlib.util.spec_from_file_location("db_table_map", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

MB = 1024 * 1024


def psql_output(sections: dict[str, list[list[object]]]) -> str:
    lines: list[str] = []
    for name, rows in sections.items():
        lines.append(f"{MODULE.SECTION_MARK}{name}{MODULE.SECTION_END}")
        for row in rows:
            lines.append("\t".join(str(cell) for cell in row))
    return "\n".join(lines) + "\n"


FIXTURE: dict[str, list[list[object]]] = {
    "window": [
        [
            "2026-02-01 00:00:00+00",
            "2026-09-05 12:00:00+00",
            "",
            "appdb",
            "PostgreSQL 17.4 on aarch64-unknown-linux-gnu",
        ]
    ],
    "settings": [
        ["max_connections", "60", ""],
        ["shared_buffers", "32768", "8kB"],
        ["work_mem", "3584", "kB"],
    ],
    # schema name total heap idx toast live dead ins upd del seq idx_scan vac ana reloptions
    "tables": [
        [
            "public", "article_embedding_chunks",
            3 * 1024 * MB, 400 * MB, 2400 * MB, 8 * MB,
            398_000, 1_200, 402_158, 12, 40, 90, 33,
            "2026-09-01 03:00:00+00", "2026-09-01 03:10:00+00", "",
        ],
        [
            "public", "article_categories",
            40 * MB, 20 * MB, 20 * MB, 0,
            0, 0, 5_000, 10, 4_900, 12, 222_793,
            "", "", "",
        ],
        [
            "public", "ingest_queue",
            8 * MB, 4 * MB, 4 * MB, 0,
            0, 900, 12_000, 0, 12_000, 3, 0,
            "", "", "fillfactor=70",
        ],
        [
            "public", "reference_lookup",
            1 * MB, 512 * 1024, 512 * 1024, 0,
            148, 0, 0, 0, 0, 40, 0,
            "", "", "",
        ],
        [
            "public", "legacy_import_staging",
            0, 0, 0, 0,
            0, 0, 0, 0, 0, 0, 0,
            "", "", "",
        ],
    ],
    "indexes": [
        [
            "public", "article_embedding_chunks", "idx_embedding_chunks_hnsw",
            "hnsw", 2203 * MB, 33,
            "CREATE INDEX idx_embedding_chunks_hnsw ON public.article_embedding_chunks "
            "USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')",
        ],
        [
            "public", "article_categories", "article_categories_pkey",
            "btree", 8 * MB, 222_793,
            "CREATE UNIQUE INDEX article_categories_pkey ON public.article_categories USING btree (id)",
        ],
    ],
    "special_columns": [
        ["public", "article_embedding_chunks", "embedding", "vector"],
        ["public", "article_categories", "search_vector", "tsvector"],
    ],
    "foreign_keys": [
        [
            "article_categories_article_id_fkey",
            "public", "article_categories",
            "public", "article_embedding_chunks",
        ]
    ],
    "functions": [
        ["public", "similarity_search_article_chunks", "enable_seqscan=off", "vector"],
        ["public", "refresh_counters", "search_path=public", ""],
        # A platform function that only carries a search_path GUC. Its body uses
        # `<>`, which a bare `<+>` regex would misread as a vector operator.
        ["storage", "search", 'search_path=""', ""],
    ],
    "database_temp": [["336253", str(2053 * 1024 * MB), "900000", "8100000", "0"]],
    "statements": [
        [
            "221092", "220000000", "996.0", "39.0", "1.00", "0",
            "insert into article_embedding_chunks (article_id, embedding) values ($1, $2)",
        ],
        [
            "5000", "1500000", "300.0", "4.0", "12.00", "10",
            "select * from entities where properties->>'name' ilike $1 and similarity(name, $2) > $3",
        ],
    ],
    "statement_temp": [["91", "5100000", "with recent as (select ...) select * from recent"]],
}


def fake_runner(has_pgss: bool = True, fixture: dict[str, list[list[object]]] | None = None):
    """Return a run_psql stand-in: first call is the extension probe."""
    calls: list[str] = []
    payload = FIXTURE if fixture is None else fixture

    def runner(dsn: str, script: str, timeout_s: int, psql: str = "psql") -> str:
        calls.append(script)
        MODULE.assert_read_only(script)
        if "pg_extension" in script:
            return psql_output({"probe": [["1" if has_pgss else "0"]]})
        sections = dict(payload)
        if not has_pgss:
            sections.pop("statements", None)
            sections.pop("statement_temp", None)
        return psql_output(sections)

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def build_fixture_map(has_pgss: bool = True, fixture=None) -> dict:
    runner = fake_runner(has_pgss, fixture)
    with mock.patch.object(MODULE, "run_psql", runner):
        sections, flag = MODULE.collect("postgres://u:p@h/db", 20)
    return MODULE.build_map(sections, MODULE.DEFAULT_DOMAIN_RULES, "postgres://u:***@h/db", flag)


class DsnTests(unittest.TestCase):
    def test_strips_psql_unsupported_uri_params(self) -> None:
        dsn = (
            "postgres://user:pw@host:6543/postgres"
            "?pgbouncer=true&connection_limit=1&pool_timeout=0&sslmode=require"
        )
        cleaned = MODULE.sanitize_dsn(dsn)
        self.assertNotIn("pgbouncer", cleaned)
        self.assertNotIn("connection_limit", cleaned)
        self.assertNotIn("pool_timeout", cleaned)
        self.assertIn("sslmode=require", cleaned)
        self.assertIn("user:pw@host:6543", cleaned)

    def test_leaves_a_clean_uri_and_a_keyword_dsn_untouched(self) -> None:
        clean = "postgres://user@host/db?sslmode=require"
        self.assertEqual(MODULE.sanitize_dsn(clean), clean)
        kv = "host=localhost dbname=app user=app"
        self.assertEqual(MODULE.sanitize_dsn(kv), kv)

    def test_redacts_the_password(self) -> None:
        self.assertEqual(
            MODULE.redact_dsn("postgres://user:secret@host:5432/db?sslmode=require"),
            "host:5432/db",
        )
        self.assertNotIn("secret", MODULE.redact_dsn("host=h password=secret"))
        self.assertEqual(MODULE.redact_dsn("host=h port=5432 dbname=app password=secret"), "h:5432/app")
        # No URL shape and no user survive: secret scanners flag both.
        self.assertNotIn("://", MODULE.redact_dsn("postgres://user:secret@host:5432/db"))
        self.assertNotIn("user", MODULE.redact_dsn("postgres://user:secret@host:5432/db"))

    def test_resolve_dsn_prefers_flag_then_env(self) -> None:
        env = {"DATABASE_URL": "a", "DIRECT_URL": "b"}
        self.assertEqual(MODULE.resolve_dsn("flag", env), "flag")
        self.assertEqual(MODULE.resolve_dsn(None, env), "a")
        self.assertEqual(MODULE.resolve_dsn(None, {"DIRECT_URL": "b"}), "b")
        with self.assertRaises(SystemExit):
            MODULE.resolve_dsn(None, {})


class ScriptSafetyTests(unittest.TestCase):
    def test_script_is_wrapped_in_a_read_only_transaction(self) -> None:
        script = MODULE.build_script(MODULE.SECTION_SQL, 20)
        self.assertTrue(script.startswith("BEGIN READ ONLY;"))
        self.assertIn("SET LOCAL statement_timeout = '20s';", script)
        self.assertTrue(script.strip().endswith("ROLLBACK;"))
        MODULE.assert_read_only(script)

    def test_statement_timeout_is_configurable(self) -> None:
        self.assertIn(
            "SET LOCAL statement_timeout = '5s';", MODULE.build_script(MODULE.SECTION_SQL, 5)
        )

    def test_function_query_excludes_extension_internals(self) -> None:
        # Regression: a live run flagged 107 pgvector-internal C functions
        # (vector_add, halfvec_in, ...) as "vector function without HNSW GUCs".
        sql = dict(MODULE.SECTION_SQL)["functions"]
        self.assertIn("deptype = 'e'", sql)
        self.assertIn("lanname in ('sql', 'plpgsql')", sql)
        self.assertIn("select distinct", sql)

    def test_function_query_escapes_the_plus_and_skips_platform_schemas(self) -> None:
        sql = dict(MODULE.SECTION_SQL)["functions"]
        # `<+>` unescaped matches the plpgsql not-equals operator `<>`, which
        # flags every platform function in a Supabase database.
        self.assertIn(r"<\+>", sql)
        self.assertNotIn("<+>", sql)
        self.assertIn("'storage'", sql)
        self.assertIn("prorettype", sql)
        self.assertIn("'vector', 'halfvec', 'sparsevec'", sql)

    def test_ddl_and_dml_are_refused(self) -> None:
        for bad in (
            "BEGIN READ ONLY; drop table article_embeddings; ROLLBACK;",
            "BEGIN READ ONLY; insert into t values (1); ROLLBACK;",
            "BEGIN READ ONLY; update t set a = 1; ROLLBACK;",
            "BEGIN READ ONLY; set work_mem = '1GB'; ROLLBACK;",
            "BEGIN READ ONLY; vacuum full t; ROLLBACK;",
        ):
            with self.assertRaises(ValueError):
                MODULE.assert_read_only(bad)

    def test_run_psql_refuses_before_spawning_a_process(self) -> None:
        with mock.patch.object(MODULE.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                MODULE.run_psql("postgres://h/db", "truncate table t;", 20)
        run.assert_not_called()


class ParsingTests(unittest.TestCase):
    def test_sections_split_on_markers(self) -> None:
        parsed = MODULE.parse_sections(psql_output({"a": [["1", "x"]], "b": []}))
        self.assertEqual(parsed["a"], [["1", "x"]])
        self.assertEqual(parsed["b"], [])

    def test_shared_buffers_blocks_convert_to_bytes(self) -> None:
        self.assertEqual(
            MODULE._setting_bytes({"setting": "32768", "unit": "8kB"}), 256 * MB
        )


class LivenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = build_fixture_map()
        self.tables = {t["key"]: t for t in self.data["tables"]}

    def test_index_scans_beat_a_zero_live_tuple_estimate(self) -> None:
        table = self.tables["article_categories"]
        self.assertEqual(table["n_live_tup"], 0)
        self.assertEqual(table["liveness"], "live")
        self.assertIn("222,793", table["liveness_reason"])

    def test_written_and_drained_reads_as_written_only(self) -> None:
        table = self.tables["ingest_queue"]
        self.assertEqual(table["liveness"], "written-only")
        self.assertIn("drained queue", table["liveness_reason"])

    def test_populated_but_untouched_reads_as_idle(self) -> None:
        self.assertEqual(self.tables["reference_lookup"]["liveness"], "idle")

    def test_no_reads_and_no_writes_reads_as_never_written(self) -> None:
        self.assertEqual(self.tables["legacy_import_staging"]["liveness"], "never-written")

    def test_estimates_are_labelled_as_estimates(self) -> None:
        self.assertIn("estimates", self.tables["ingest_queue"]["estimates_note"])


class CollectionTests(unittest.TestCase):
    def test_table_facts_indexes_columns_and_keys_are_attached(self) -> None:
        data = build_fixture_map()
        tables = {t["key"]: t for t in data["tables"]}
        chunks = tables["article_embedding_chunks"]
        self.assertEqual(data["table_count"], 5)
        self.assertEqual(chunks["domain"], "embedding")
        self.assertEqual(chunks["indexes"][0]["access_method"], "hnsw")
        self.assertEqual(chunks["vector_columns"], [{"column": "embedding", "type": "vector"}])
        self.assertEqual(
            tables["article_categories"]["tsvector_columns"],
            [{"column": "search_vector", "type": "tsvector"}],
        )
        self.assertEqual(
            tables["article_categories"]["fk_out"][0]["references"], "article_embedding_chunks"
        )
        self.assertEqual(
            tables["article_embedding_chunks"]["fk_in"][0]["from"], "article_categories"
        )
        self.assertEqual(tables["article_categories"]["domain"], "taxonomy")
        # "staging" must not match the taxonomy rule through the substring "tag".
        self.assertEqual(tables["legacy_import_staging"]["domain"], MODULE.DEFAULT_DOMAIN)
        self.assertEqual(tables["ingest_queue"]["reloptions"], "fillfactor=70")
        self.assertEqual(data["shared_buffers_bytes"], 256 * MB)

    def test_functions_carry_their_proconfig(self) -> None:
        data = build_fixture_map()
        function = next(
            f for f in data["functions"] if f["name"] == "similarity_search_article_chunks"
        )
        self.assertEqual(function["proconfig"], ["enable_seqscan=off"])
        self.assertTrue(function["uses_vector_ops"])

    def test_custom_domain_rules_override_the_defaults(self) -> None:
        runner = fake_runner()
        with mock.patch.object(MODULE, "run_psql", runner):
            sections, flag = MODULE.collect("postgres://h/db", 20)
        data = MODULE.build_map(sections, [["ingestion", "queue|staging"]], "dsn", flag)
        tables = {t["key"]: t for t in data["tables"]}
        self.assertEqual(tables["ingest_queue"]["domain"], "ingestion")
        self.assertEqual(tables["article_categories"]["domain"], MODULE.DEFAULT_DOMAIN)

    def test_missing_pg_stat_statements_degrades_instead_of_failing(self) -> None:
        data = build_fixture_map(has_pgss=False)
        self.assertFalse(data["pg_stat_statements"])
        self.assertIsNone(data["statements"])
        markdown = MODULE.render_markdown(data)
        self.assertIn("`pg_stat_statements` is not installed", markdown)

    def test_load_domain_rules_rejects_a_malformed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.json"
            path.write_text(json.dumps([["only-one-field"]]), encoding="utf-8")
            with self.assertRaises(SystemExit):
                MODULE.load_domain_rules(str(path))


class ShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.shapes = build_fixture_map()["shapes"]
        self.by_shape: dict[str, list[dict]] = {}
        for finding in self.shapes:
            self.by_shape.setdefault(finding["shape"], []).append(finding)

    def test_cold_hnsw_index_matches_index_maintenance_on_writes(self) -> None:
        finding = self.by_shape["index-maintenance-on-writes"][0]
        self.assertIn("idx_embedding_chunks_hnsw", finding["object"])
        self.assertIn("33 lifetime scans", finding["evidence"])
        self.assertIn("402,158", finding["evidence"])

    def test_oversized_hnsw_index_matches_the_insert_shape(self) -> None:
        finding = self.by_shape["vector-insert-above-cache"][0]
        self.assertIn("2.2 GB", finding["evidence"])
        self.assertIn("256.0 MB", finding["evidence"])

    def test_vector_function_without_hnsw_gucs_is_flagged_as_drift(self) -> None:
        drift = [
            f for f in self.by_shape["vector-read-above-cache"]
            if "similarity_search_article_chunks" in f["object"]
        ]
        self.assertEqual(len(drift), 1)
        self.assertIn("enable_seqscan=off", drift[0]["evidence"])
        self.assertIn("ALTER FUNCTION", drift[0]["action"])

    def test_a_platform_function_with_no_vector_ops_is_never_flagged(self) -> None:
        flagged = [f["object"] for f in self.shapes]
        self.assertNotIn("storage.search", flagged)
        self.assertNotIn("public.refresh_counters", flagged)

    def test_jsonb_plus_trigram_statement_matches_the_per_row_shape(self) -> None:
        finding = self.by_shape["per-row-jsonb-trigram"][0]
        self.assertIn("5,000 calls", finding["evidence"])

    def test_every_finding_names_a_known_shape(self) -> None:
        for finding in self.shapes:
            self.assertIn(finding["shape"], MODULE.SHAPES)


class MarkdownTests(unittest.TestCase):
    def test_every_section_is_rendered(self) -> None:
        markdown = MODULE.render_markdown(build_fixture_map())
        for heading in MODULE.MD_SECTIONS:
            self.assertIn(f"## {heading}", markdown)
        self.assertNotIn(f"## {MODULE.MD_DIFF_SECTION}", markdown)

    def test_body_carries_the_counter_window_and_the_estimate_warning(self) -> None:
        markdown = MODULE.render_markdown(build_fixture_map())
        self.assertIn("2026-02-01 00:00:00+00", markdown)
        self.assertIn("never reset", markdown)
        self.assertIn("planner estimates", markdown)
        self.assertIn("no DDL or DML was issued", markdown)

    def test_liveness_section_lists_the_non_live_tables(self) -> None:
        markdown = MODULE.render_markdown(build_fixture_map())
        self.assertIn("`legacy_import_staging`", markdown)
        self.assertIn("`ingest_queue`", markdown)

    def test_diff_section_appears_only_with_a_previous_map(self) -> None:
        current = build_fixture_map()
        markdown = MODULE.render_markdown(current, MODULE.diff_maps(current, current))
        self.assertIn(f"## {MODULE.MD_DIFF_SECTION}", markdown)


class DiffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = build_fixture_map()
        self.previous = copy.deepcopy(self.current)
        self.previous["generated_at"] = "2026-08-01T00:00:00Z"

    def _diff(self) -> dict:
        return MODULE.diff_maps(self.previous, self.current)

    def test_identical_maps_report_no_change(self) -> None:
        diff = self._diff()
        self.assertEqual(diff["new_tables"], [])
        self.assertEqual(diff["removed_tables"], [])
        self.assertEqual(diff["size_deltas"], [])
        self.assertEqual(diff["stalled_indexes"], [])
        self.assertEqual(diff["liveness_changes"], [])

    def test_new_and_removed_tables_are_named(self) -> None:
        self.previous["tables"] = [
            t for t in self.previous["tables"] if t["key"] != "ingest_queue"
        ]
        self.previous["tables"].append(dict(self.current["tables"][0], key="dropped_table"))
        diff = self._diff()
        self.assertEqual(diff["new_tables"], ["ingest_queue"])
        self.assertEqual(diff["removed_tables"], ["dropped_table"])

    def test_size_moves_over_ten_percent_are_reported(self) -> None:
        for table in self.previous["tables"]:
            if table["key"] == "article_embedding_chunks":
                table["total_bytes"] = int(table["total_bytes"] / 2)
            if table["key"] == "article_categories":
                table["total_bytes"] = int(table["total_bytes"] * 1.05)
        diff = self._diff()
        moved = {entry["table"] for entry in diff["size_deltas"]}
        self.assertIn("article_embedding_chunks", moved)
        self.assertNotIn("article_categories", moved)
        self.assertGreater(diff["size_deltas"][0]["pct"], 10)

    def test_an_index_read_flat_while_inserts_grew_is_flagged(self) -> None:
        for table in self.previous["tables"]:
            if table["key"] == "article_embedding_chunks":
                table["n_tup_ins"] = 300_000
        diff = self._diff()
        stalled = diff["stalled_indexes"]
        self.assertEqual(len(stalled), 1)
        self.assertEqual(stalled[0]["index"], "idx_embedding_chunks_hnsw")
        self.assertEqual(stalled[0]["scan_delta"], 0)
        self.assertEqual(stalled[0]["insert_delta"], 102_158)

    def test_liveness_flips_are_reported(self) -> None:
        for table in self.previous["tables"]:
            if table["key"] == "ingest_queue":
                table["liveness"] = "live"
        diff = self._diff()
        self.assertEqual(diff["liveness_changes"][0]["table"], "ingest_queue")
        self.assertEqual(diff["liveness_changes"][0]["after"], "written-only")

    def test_a_counter_reset_between_maps_invalidates_the_deltas(self) -> None:
        self.previous["window"]["stats_reset"] = "2026-07-01 00:00:00+00"
        diff = self._diff()
        self.assertTrue(diff["counters_reset_between_maps"])
        rendered = "\n".join(MODULE._render_diff(diff)).lower()
        self.assertIn("counters reset between the two maps", rendered)


class CliTests(unittest.TestCase):
    def test_main_writes_both_artifacts_and_the_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prev_path = Path(tmp) / "prev.json"
            previous = build_fixture_map()
            for table in previous["tables"]:
                if table["key"] == "article_embedding_chunks":
                    table["n_tup_ins"] = 300_000
            prev_path.write_text(json.dumps(previous), encoding="utf-8")

            out_json = Path(tmp) / "audits" / "map.json"
            out_md = Path(tmp) / "audits" / "map.md"
            runner = fake_runner()
            with mock.patch.object(MODULE, "run_psql", runner):
                code = MODULE.main(
                    [
                        "--dsn",
                        "postgres://u:secret@h/db?pgbouncer=true",
                        "--out-json",
                        str(out_json),
                        "--out-md",
                        str(out_md),
                        "--prev",
                        str(prev_path),
                        "--statement-timeout",
                        "10",
                    ]
                )
            self.assertEqual(code, 0)
            data = json.loads(out_json.read_text(encoding="utf-8"))
            markdown = out_md.read_text(encoding="utf-8")

        self.assertNotIn("secret", data["dsn"])
        self.assertNotIn("secret", markdown)
        self.assertEqual(data["diff"]["stalled_indexes"][0]["index"], "idx_embedding_chunks_hnsw")
        self.assertIn(f"## {MODULE.MD_DIFF_SECTION}", markdown)
        self.assertIn("SET LOCAL statement_timeout = '10s';", runner.calls[-1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
