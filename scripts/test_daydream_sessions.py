"""Adversarial contracts for the local, observational Daydream session miner."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import io
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parent))
import daydream_sessions as ds


class SessionMiningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, rows):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    def event(self, **overrides):
        return {"schema": ds.NORMALIZED_SCHEMA, "role": "user", "session_id": "session-1",
                "timestamp": "2026-10-21T05:00:00Z", "content": "Actually use the existing adapter instead.",
                **overrides}

    def mine(self, path, host="muse", **config):
        return ds.mine_sessions({"discover": False, **config}, sources=[
            {"path": str(path), "format": "normalized", "host": host}])

    def test_locators_and_unknown_quality_without_raw_text(self):
        text = "Actually use sk-proj-SUPERSECRET instead of the old password."
        path = self.write("private.jsonl", [self.event(content=text)])
        before = path.read_bytes()
        report = self.mine(path)
        self.assertTrue(report["coverage"]["complete"])
        incident = report["incidents"][0]
        self.assertEqual(incident["signal"], "correction_heuristic")
        self.assertEqual(incident["evidence"][0]["sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(incident["evidence"][0]["line"], 1)
        self.assertEqual(report["segments"][0]["effort"], "unknown")
        self.assertEqual(report["segments"][0]["quality"]["code"], "unknown")
        self.assertNotIn("SUPERSECRET", json.dumps(report))
        self.assertEqual(before, path.read_bytes())

    def test_native_codex_model_effort_follow_turn_context(self):
        rows = [
            {"type": "session_meta", "payload": {"id": "codex-one", "cwd": "/repos/project"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Actually fix the parser."}},
            {"type": "turn_context", "payload": {"model": "model-a", "effort": "high"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Also add the export."}},
            {"type": "turn_context", "payload": {"model": "model-b", "effort": "low"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "That works."}},
        ]
        path = self.write("codex.jsonl", rows)
        report = ds.mine_sessions(sources=[str(path)])
        segments = {segment["model"]: segment for segment in report["segments"]}
        self.assertEqual(set(segments), {"unknown", "model-a", "model-b"})
        self.assertEqual(segments["model-a"]["effort"], "high")
        self.assertEqual(segments["model-b"]["effort"], "low")
        self.assertEqual(segments["unknown"]["signals"], {"correction_heuristic": 1})
        self.assertEqual(report["counts"]["distinct_sessions"], 1)

    def test_native_codex_ui_api_duplicate_representation_is_one_incident(self):
        path = self.write("codex.jsonl", [
            {"type": "session_meta", "payload": {"id": "one"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Actually fix it."}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Actually fix it."}]}},
        ])
        report = ds.mine_sessions(sources=[str(path)])
        self.assertEqual(report["counts"]["incidents"], 1)
        self.assertEqual(report["incidents"][0]["evidence"][0]["line"], 2)

    def test_native_claude_uses_explicit_model_and_tool_output(self):
        path = self.write("claude.jsonl", [
            {"type": "assistant", "sessionId": "claude-one", "message": {"model": "claude-fable-5-1", "content": [
                {"type": "tool_use", "id": "test-call", "name": "Bash", "input": {"command": "python3 -m unittest scripts.test_adapter"}}]}},
            {"type": "user", "sessionId": "claude-one", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "test-call", "content": [{"type": "text", "text": "3 passed in 0.5s"}], "is_error": False}]}},
        ])
        report = ds.mine_sessions(sources=[str(path)])
        self.assertEqual(report["segments"][0]["model"], "claude-fable-5-1")
        self.assertEqual(report["segments"][0]["effort"], "unknown")
        self.assertEqual(report["counts"]["signals"], {"test_pass_observed": 1})

    def test_normalized_supported_for_each_requested_host(self):
        path = self.write("export.jsonl", [self.event()])
        for host in ds.HOSTS:
            with self.subTest(host=host):
                self.assertEqual(self.mine(path, host)["segments"][0]["host"], host)

    def test_scope_extension_not_labeled_as_failure(self):
        path = self.write("scope.jsonl", [self.event(content="Also add integration tests instead.")])
        self.assertEqual(self.mine(path)["counts"]["signals"], {"new_scope_heuristic": 1})

    def test_meta_quoted_and_fenced_corrections_are_not_feedback(self):
        path = self.write("meta.jsonl", [
            self.event(content="Actually wrong", isMeta=True),
            self.event(content="> Actually wrong\n```\nActually wrong\n```"),
            self.event(content="SessionStart: Actually wrong"),
            self.event(content="No, that's correct."),
        ])
        self.assertEqual(self.mine(path)["counts"]["incidents"], 0)

    def test_positive_feedback_and_test_output_are_not_code_or_ui_grades(self):
        path = self.write("success.jsonl", [self.event(content="Looks good, that works."),
            self.event(role="tool_result", content="1 passed", test_outcome="pass")])
        report = self.mine(path)
        self.assertEqual(set(report["counts"]["signals"]), {"positive_feedback_heuristic", "test_pass_observed"})
        self.assertEqual(report["positive_exemplars"][0]["ui_acceptance"], "unknown")
        self.assertTrue(all(value == "unknown" for value in report["segments"][0]["quality"].values()))
        self.assertFalse(report["method"]["causal_rankings"])

    def test_documents_are_not_independent_incidents(self):
        rows = [self.event(event_id="same-event")]
        one, two = self.write("one.jsonl", rows), self.write("two.jsonl", rows)
        report = ds.mine_sessions(sources=[{"path": str(p), "format": "normalized", "host": "muse"} for p in (one, two)])
        self.assertEqual(report["counts"]["documents"], 2)
        self.assertEqual(report["counts"]["incidents"], 1)
        self.assertEqual(report["counts"]["distinct_sessions"], 1)
        self.assertEqual(report["segments"][0]["document_count"], 2)
        self.assertEqual(len(report["incidents"][0]["evidence"]), 2)

    def test_stable_ids_across_reruns(self):
        path = self.write("one.jsonl", [self.event()])
        self.assertEqual(self.mine(path), self.mine(path))

    def test_conflicting_source_session_ids_reject_whole_document(self):
        path = self.write("mixed.jsonl", [self.event(), self.event(session_id="two")])
        report = self.mine(path)
        self.assertFalse(report["coverage"]["complete"])
        self.assertEqual(report["documents"][0]["reason"], "conflicting_session_identity")
        self.assertEqual(report["counts"]["incidents"], 0)

    def test_unsupported_native_record_retains_partial_coverage(self):
        path = self.write("native.jsonl", [
            {"type": "user", "sessionId": "one", "message": {"content": "Actually fix it."}},
            {"type": "attachment", "private": "not echoed"},
        ])
        report = ds.mine_sessions(sources=[str(path)])
        self.assertFalse(report["coverage"]["complete"])
        self.assertEqual(report["documents"][0]["status"], "partial")
        self.assertEqual(report["counts"]["incidents"], 1)

    def test_malformed_missing_and_unsupported_are_coverage_gaps(self):
        path = self.root / "bad.jsonl"
        path.write_text('{"schema":')
        for candidate in (path, self.root / "missing.jsonl"):
            report = self.mine(candidate)
            self.assertFalse(report["coverage"]["complete"])
            self.assertEqual(report["counts"]["complete_documents"], 0)
        other = self.write("unknown.jsonl", [{"role": "user", "content": "Actually fix it"}])
        self.assertEqual(self.mine(other)["documents"][0]["reason"], "unsupported_normalized_record")

    def test_no_sources_is_a_gap_not_zero_success(self):
        report = ds.mine_sessions({"discover": False}, sources=[])
        self.assertFalse(report["coverage"]["complete"])
        self.assertEqual(report["coverage"]["gaps"][0]["reason"], "no_sources_selected")

    def test_symlink_and_fifo_are_rejected_without_blocking(self):
        path = self.write("good.jsonl", [self.event()])
        link = self.root / "link.jsonl"
        link.symlink_to(path)
        self.assertEqual(self.mine(link)["documents"][0]["reason"], "symlink_source")
        import os
        fifo = self.root / "fifo.jsonl"
        os.mkfifo(fifo)
        self.assertFalse(self.mine(fifo)["coverage"]["complete"])

    def test_budgets_are_visible_and_do_not_publish_partial_document(self):
        path = self.write("big.jsonl", [self.event(), self.event(event_id="two")])
        for budget in ({"max_bytes_per_document": 5}, {"max_events_per_document": 1}, {"max_total_bytes": 5}):
            with self.subTest(budget=budget):
                report = self.mine(path, **budget)
                self.assertFalse(report["coverage"]["complete"])
                self.assertEqual(report["counts"]["incidents"], 0)
        with self.assertRaises(ValueError):
            self.mine(path, max_documents=0)

    def test_wrong_native_host_cannot_relabel_codex_as_cursor(self):
        path = self.write("native.jsonl", [{"type": "session_meta", "payload": {"id": "one"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Actually fix it."}}])
        report = ds.mine_sessions(sources=[{"path": str(path), "host": "cursor"}])
        self.assertEqual(report["documents"][0]["reason"], "unsupported_native_host")

    def test_discovery_reports_binary_and_missing_hosts(self):
        root = self.root / "antigravity"
        root.mkdir()
        (root / "session.pb").write_bytes(b"opaque")
        result = ds.discover_sources({"activity_db": False, "discovery_roots": {"antigravity": str(root)}})
        anti = next(row for row in result["hosts"] if row["host"] == "antigravity")
        self.assertEqual(anti["unsupported_files"], 1)
        self.assertFalse(result["sources"])

    def test_activity_db_reads_only_pointers_and_does_not_assign_model_hint(self):
        path = self.write("native.jsonl", [{"type": "user", "sessionId": "one", "message": {"content": "Actually fix it."}}])
        db = self.root / "activity.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE sessions (path,model,cwd,entrypoint,agent,started_at)")
        con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", (str(path), "final-model", "/project", "sdk", "claude", "2026"))
        con.commit()
        con.close()
        before = db.read_bytes()
        result = ds.discover_sources({"activity_db": str(db), "discovery_roots": {}})
        self.assertEqual(result["activity_db"]["status"], "queried")
        self.assertNotIn("model", result["sources"][0])
        self.assertEqual(before, db.read_bytes())

    def test_secret_metadata_is_not_exported(self):
        path = self.write("one.jsonl", [self.event(model="sk-secret-value", harness="https://user:pass@host")])
        report = self.mine(path)
        self.assertEqual(report["segments"][0]["model"], "unknown")
        self.assertEqual(report["segments"][0]["harness"], "unknown")

    def test_non_signal_assistant_code_never_earns_quality_score(self):
        path = self.write("code.jsonl", [self.event(role="assistant", content="```python\nprint('good code')\n```", model="model-a")])
        report = self.mine(path)
        self.assertEqual(report["counts"]["incidents"], 0)
        self.assertEqual(report["segments"][0]["quality"]["code"], "unknown")

    def test_headless_brief_is_not_human_correction(self):
        path = self.write("headless.jsonl", [
            {"type": "session_meta", "payload": {"id": "worker", "source": {"subagent": {}}}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Actually use this instead, looks good."}},
        ])
        report = ds.mine_sessions(sources=[str(path)])
        self.assertEqual(report["counts"]["incidents"], 0)
        self.assertEqual(report["segments"][0]["interaction_kind"], "headless")

    def test_malformed_input_charges_total_read_budget(self):
        bad = self.root / "bad.jsonl"
        bad.write_bytes(b"!" * 40)
        good = self.write("good.jsonl", [self.event()])
        report = ds.mine_sessions({"max_total_bytes": 50}, sources=[
            {"path": str(p), "host": "muse", "format": "normalized"} for p in (bad, good)])
        self.assertEqual(report["coverage"]["bytes_read"], 40)
        self.assertEqual(report["counts"]["incidents"], 0)

    def test_file_read_containing_passed_fixture_is_not_test_evidence(self):
        path = self.write("read.jsonl", [
            {"type": "user", "sessionId": "one", "message": {"content": [
                {"type": "tool_result", "content": "3 passed in 0.1s"}]}},
        ])
        self.assertEqual(ds.mine_sessions(sources=[str(path)])["counts"]["incidents"], 0)

    def test_observed_rosslabs_native_envelope(self):
        path = self.write("harness.jsonl", [
            {"id": "evt-one", "event": {"type": "UserMessage", "content": "Actually use the existing adapter instead."}},
            {"id": "evt-two", "event": {"type": "ToolResult", "output": {"contents": "10 passed"}}},
        ])
        report = ds.mine_sessions(sources=[{"path": str(path), "host": "rosslabs", "format": "rosslabs"}])
        self.assertTrue(report["coverage"]["complete"])
        self.assertEqual(report["counts"]["signals"], {"correction_heuristic": 1})
        self.assertEqual(report["segments"][0]["model"], "unknown")

    def test_discovery_budget_does_not_starve_later_hosts(self):
        codex = self.root / "codex"
        codex.mkdir()
        for index in range(20):
            (codex / str(index)).mkdir()
        anti = self.root / "antigravity"
        anti.mkdir()
        (anti / "session.pb").write_bytes(b"opaque")
        result = ds.discover_sources({"activity_db": False, "max_discovery_entries": 12,
                                     "discovery_roots": {"codex": str(codex), "antigravity": str(anti)}})
        record = next(row for row in result["hosts"] if row["host"] == "antigravity")
        self.assertEqual(record["unsupported_files"], 1)

    def test_cli_output_is_private_compact_and_never_overwrites(self):
        path = self.write("one.jsonl", [self.event()])
        output = self.root / "report.json"
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = ds.main(["--source", str(path), "--normalized-host", "muse", "--output", str(output)])
        self.assertEqual(code, 0)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("incidents", json.loads(stream.getvalue()))
        before = output.read_bytes()
        with self.assertRaises(FileExistsError):
            ds.main(["--source", str(path), "--normalized-host", "muse", "--output", str(output)])
        self.assertEqual(before, output.read_bytes())

    def test_native_temporary_snapshot_removed_even_on_rejection(self):
        path = self.write("native.jsonl", [{"type": "user", "sessionId": "one", "message": {"content": "Actually fix it."}}])
        seen = []
        original = ds.adapter.detect_format
        def detect(snapshot):
            seen.append(snapshot)
            return original(snapshot)
        with patch.object(ds.adapter, "detect_format", side_effect=detect):
            report = ds.mine_sessions(sources=[str(path)])
        self.assertTrue(report["coverage"]["complete"])
        self.assertTrue(seen)
        self.assertTrue(all(not snapshot.exists() for snapshot in seen))
        self.assertFalse(report["coverage"]["raw_snapshots_retained"])

    def test_missing_effort_on_later_turn_stays_unknown(self):
        path = self.write("native.jsonl", [
            {"type": "session_meta", "payload": {"id": "one"}},
            {"type": "turn_context", "payload": {"model": "model-a", "effort": "high"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Also add this."}},
            {"type": "turn_context", "payload": {"model": "model-b"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Actually use the adapter instead."}},
        ])
        report = ds.mine_sessions(sources=[str(path)])
        segments = {s["model"]: s for s in report["segments"]}
        self.assertEqual(segments["model-b"]["effort"], "unknown")

    def test_malformed_native_message_is_a_gap_without_partial_incidents(self):
        path = self.write("native.jsonl", [
            {"type": "user", "sessionId": "one", "message": {"content": "Actually fix it."}},
            {"type": "assistant", "sessionId": "one", "message": "bad schema"},
        ])
        report = ds.mine_sessions(sources=[str(path)])
        self.assertFalse(report["coverage"]["complete"])
        self.assertEqual(report["documents"][0]["reason"], "unsupported_event_content")
        self.assertEqual(report["counts"]["incidents"], 0)

    def test_normalized_host_conflict_rejects_source(self):
        path = self.write("conflict.jsonl", [self.event(host="codex")])
        report = self.mine(path, host="muse")
        self.assertEqual(report["documents"][0]["reason"], "conflicting_host_identity")

    def test_real_followup_definition_with_actually_is_not_correction(self):
        # Redacted regression from the interactive review: both topic clauses
        # remain verbatim; no session, account or unrelated prompt text retained.
        path = self.write("clarification.jsonl", [self.event(
            content="What are different options for text encoding? Define what headless actually means.")])
        report = self.mine(path)
        self.assertNotIn("correction_heuristic", report["counts"]["signals"])
        self.assertEqual(report["counts"]["signals"], {"new_scope_heuristic": 1})

    def test_explicit_no_actually_remains_correction(self):
        path = self.write("explicit.jsonl", [self.event(content="No, actually use UTF-8 instead of ASCII.")])
        self.assertEqual(self.mine(path)["counts"]["signals"], {"correction_heuristic": 1})

    def test_internal_actually_is_not_itself_rejection(self):
        for text in ("Define what headless actually means.", "How does UTF-8 actually encode text?", "This is what the code actually does.", "Actually, how does UTF-8 encode text?"):
            with self.subTest(text=text):
                signals = ds._signals({"type": "user", "message": {"content": text}})
                self.assertNotIn("correction_heuristic", signals)

    def test_method_fingerprints_current_rules_and_scanner(self):
        path = self.write("one.jsonl", [self.event()])
        method = self.mine(path)["method"]
        self.assertEqual(method["rules_version"], "2026-10-21.2")
        self.assertEqual(method["scanner_sha256"], hashlib.sha256(Path(ds.__file__).read_bytes()).hexdigest())

    def test_repeated_distinct_feedback_without_ids_counts_occurrences_not_documents(self):
        rows = [self.event(content="Actually fix it."), self.event(role="assistant", content="Revised implementation."),
                self.event(content="Actually fix it.")]
        one, two = self.write("one.jsonl", rows), self.write("two.jsonl", rows)
        report = ds.mine_sessions(sources=[{"path": str(path), "format": "normalized", "host": "muse"} for path in (one, two)])
        self.assertEqual(report["counts"]["documents"], 2)
        self.assertEqual(report["counts"]["incidents"], 2)
        self.assertEqual(report["counts"]["signals"], {"correction_heuristic": 2})


class CanonicalSourceTests(unittest.TestCase):
    def test_parent_alias_emits_canonical_reviewable_source(self):
        from daydream_review import build_review_packets
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            real = root / "real"
            real.mkdir()
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            path = real / "session.jsonl"
            rows = [{"schema": ds.NORMALIZED_SCHEMA, "session_id": "one", "role": role, "content": text}
                    for role, text in [("user", "Please explain the interface."),
                                       ("assistant", "Here is the explanation."),
                                       ("user", "No, actually explain the encoding.")]]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            report = ds.mine_sessions({"discover": False}, sources=[{
                "path": str(alias / path.name), "host": "muse", "format": "normalized"}])
            self.assertEqual(report["documents"][0]["path"], str(path))
            packets = build_review_packets(report)
            self.assertEqual(len(packets["packets"]), 1)
            self.assertEqual(packets["gaps"], [])

    def test_normalized_host_means_normalized_even_for_native_capable_hosts(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / "normalized.jsonl"
            path.write_text(json.dumps({"schema": ds.NORMALIZED_SCHEMA,
                "session_id": "one", "role": "user", "content": "Explain this."}) + "\n")
            for host in ("codex", "claude", "rosslabs"):
                with self.subTest(host=host), redirect_stdout(io.StringIO()):
                    self.assertEqual(ds.main(["--source", str(path), "--normalized-host", host]), 0)


if __name__ == "__main__":
    unittest.main()
