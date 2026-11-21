#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for index_retention.py. Zero deps. Run: python3 test_index_retention.py

The memory writer is reached only through ``subprocess.run``; most tests stub
that boundary. ``RealWriterTests`` runs the real writer against a temporary
memory store so no test touches the canonical store.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import index_retention  # noqa: E402

SCRIPT = HERE / "index_retention.py"
WRITER = Path(index_retention.__file__).resolve().with_name("memory_writer.py")


class _ReceiptFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.retained = self.tmp / "recovery" / "bench.json"
        self.retained.parent.mkdir()
        self.retained.write_bytes(b'{"p95_ms": 41}\n')
        self.digest = hashlib.sha256(self.retained.read_bytes()).hexdigest()

    def tearDown(self):
        self._tmp.cleanup()

    def receipt(self, name="receipt.json", **overrides):
        data = {
            "event": "artifact-retained",
            "id": "ret-001",
            "relative_path": "bench/bench.json",
            "retained_path": str(self.retained),
            "sha256": self.digest,
            "purpose": "Latency baseline for the search pane",
            "consumer": "bench",
            "build_revision": "abc1234",
            "head_at_capture": "def5678",
            "verification": {"status": "verified", "tool": "shasum", "result": "match",
                             "sha256": self.digest},
        }
        data.update(overrides)
        path = self.tmp / name
        path.write_text(json.dumps(data))
        return path

    def argv(self, *receipts):
        return ["index_retention.py", *map(str, receipts), "--name", "Search latency",
                "--project", "demo-proj", "--workdir", str(self.tmp),
                "--run-id", "run-42", "--host", "claude_code"]

    def run_main(self, argv, writer_result=None):
        """Run main() with the writer subprocess stubbed. Returns (calls, exit, stdout, stderr)."""
        if writer_result is None:
            writer_result = subprocess.CompletedProcess([], 0, stdout='{"ok": true}\n', stderr="")
        out, err = io.StringIO(), io.StringIO()
        exit_exc = None
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(index_retention.subprocess, "run", return_value=writer_result) as run, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                index_retention.main()
            except SystemExit as exc:
                exit_exc = exc
        return run, exit_exc, out.getvalue(), err.getvalue()


class IndexingTests(_ReceiptFixture):
    def test_matching_receipt_calls_writer_with_expected_args_and_body(self):
        run, exit_exc, out, _ = self.run_main(self.argv(self.receipt()))
        self.assertIsNone(exit_exc)
        self.assertEqual(out, '{"ok": true}\n')
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command, [
            sys.executable, str(WRITER), "--scope", "project", "--project", "demo-proj",
            "write", "--type", "reference", "--name", "Search latency",
            "--description", "Search latency", "--host", "claude_code",
            "--run-id", "run-42", "--workdir", str(self.tmp), "--json",
        ])
        kwargs = run.call_args.kwargs
        self.assertTrue(kwargs["text"])
        self.assertTrue(kwargs["capture_output"])
        body = kwargs["input"]
        self.assertTrue(body.startswith("# Search latency\n"))
        for expected in (
            "## bench/bench.json",
            "Latency baseline for the search pane",
            f"Receipt ret-001. SHA-256 {self.digest}.",
            f"Recovery copy: {self.retained}",
            "Producer revision: abc1234. Checkout revision: def5678.",
            "Verification: verified · shasum · match.\n",
        ):
            self.assertIn(expected, body)

    def test_multiple_receipts_and_missing_optional_fields(self):
        other = self.tmp / "recovery" / "trace.bin"
        other.write_bytes(b"\x00\x01trace")
        other_digest = hashlib.sha256(other.read_bytes()).hexdigest()
        bare = self.receipt("bare.json", id="ret-002", relative_path="trace.bin",
                            retained_path=str(other), sha256=other_digest, purpose=None,
                            build_revision=None, head_at_capture=None,
                            staged_head="0a0b0c0", verification=None)
        run, exit_exc, _, _ = self.run_main(self.argv(self.receipt(), bare))
        self.assertIsNone(exit_exc)
        body = run.call_args.kwargs["input"]
        self.assertLess(body.index("## bench/bench.json"), body.index("## trace.bin"))
        trace_section = body[body.index("## trace.bin"):]
        self.assertIn("\nbench\n", trace_section)  # purpose falls back to consumer
        self.assertIn("Producer revision: unknown. Checkout revision: 0a0b0c0.", trace_section)
        self.assertIn("Verification: unchecked", trace_section)


class RejectionTests(_ReceiptFixture):
    def assert_parser_error(self, exit_exc, err, fragment):
        self.assertIsNotNone(exit_exc)
        self.assertEqual(exit_exc.code, 2)
        self.assertIn(fragment, err)

    def test_retained_file_digest_mismatch_exits_without_writing(self):
        receipt = self.receipt(sha256="0" * 64,
                               verification={"status": "verified", "sha256": "0" * 64})
        run, exit_exc, _, err = self.run_main(self.argv(receipt))
        self.assert_parser_error(exit_exc, err, "retention or verification digest mismatch")
        run.assert_not_called()

    def test_verification_block_digest_mismatch_exits_without_writing(self):
        receipt = self.receipt(verification={"status": "verified", "sha256": "f" * 64})
        run, exit_exc, _, err = self.run_main(self.argv(receipt))
        self.assert_parser_error(exit_exc, err, "retention or verification digest mismatch")
        run.assert_not_called()

    def test_non_retention_receipt_is_rejected(self):
        receipt = self.receipt(event="artifact-captured")
        run, exit_exc, _, err = self.run_main(self.argv(receipt))
        self.assert_parser_error(exit_exc, err, "is not an artifact retention receipt")
        run.assert_not_called()

    def test_malformed_receipts_are_clean_errors(self):
        no_digest = self.receipt("nodigest.json")
        data = json.loads(no_digest.read_text())
        del data["sha256"]
        no_digest.write_text(json.dumps(data))
        bad_json = self.tmp / "bad.json"
        bad_json.write_text("{not json")
        cases = (
            (self.receipt("gone.json", retained_path=str(self.tmp / "gone.bin")), "cannot verify retained copy"),
            (no_digest, "malformed receipt (sha256)"),
            (self.receipt("nullpath.json", retained_path=None), "malformed receipt (retained_path)"),
            (self.receipt("noid.json", id=""), "malformed receipt (id)"),
            (self.receipt("badproof.json", verification=[]), "malformed receipt (verification)"),
            (bad_json, "unreadable receipt"),
        )
        for receipt, fragment in cases:
            with self.subTest(receipt.name):
                run, exit_exc, _, err = self.run_main(self.argv(receipt))
                self.assert_parser_error(exit_exc, err, fragment)
                run.assert_not_called()

    def test_one_bad_receipt_blocks_the_whole_batch(self):
        good = self.receipt()
        bad = self.receipt("bad.json", event="worktree-removed")
        run, exit_exc, _, err = self.run_main(self.argv(good, bad))
        self.assert_parser_error(exit_exc, err, "is not an artifact retention receipt")
        run.assert_not_called()


class WriterOutcomeTests(_ReceiptFixture):
    def test_auto_commit_blocked_exits_nonzero_with_pending_message(self):
        stderr = "WARN: memory auto-commit blocked by peer-lock: /x/.git/index.lock\n"
        result = subprocess.CompletedProcess([], 0, stdout='{"ok": true}\n', stderr=stderr)
        _, exit_exc, out, err = self.run_main(self.argv(self.receipt()), result)
        self.assertIsNotNone(exit_exc)
        self.assertEqual(exit_exc.code,
                         "Reference saved locally. Git-backed search indexing is pending.")
        self.assertEqual(out, '{"ok": true}\n')
        self.assertIn("memory auto-commit blocked", err)

    def test_writer_failure_propagates_its_exit_code(self):
        result = subprocess.CompletedProcess([], 3, stdout="", stderr="boom\n")
        _, exit_exc, _, err = self.run_main(self.argv(self.receipt()), result)
        self.assertIsNotNone(exit_exc)
        self.assertEqual(exit_exc.code, 3)
        self.assertIn("boom", err)


class RealWriterTests(_ReceiptFixture):
    """End-to-end runs of both scripts, pointed at a temporary memory store."""

    def run_real(self, **extra_env):
        self.store = self.tmp / "memory-store"
        self.store.mkdir(exist_ok=True)
        env = {**os.environ,
               "BUILD_LOOP_MEMORY_STORE_ROOT": str(self.store),
               "BUILD_LOOP_MEMORY_AUTOCOMMIT": "0",
               "BUILD_LOOP_CONTENT_INDEX_WRITETHROUGH": "0",
               "BUILD_LOOP_MEMORY_CONTENT": "0", **extra_env}
        env.pop("BUILD_LOOP_MEMORY_DISABLE", None)
        if "BUILD_LOOP_MEMORY_BUSY" not in extra_env:
            env.pop("BUILD_LOOP_MEMORY_BUSY", None)
        argv = [sys.executable, str(SCRIPT), *self.argv(self.receipt())[1:]]
        return subprocess.run(argv, capture_output=True, text=True, env=env, timeout=60)

    def test_busy_store_exits_nonzero_instead_of_reporting_success(self):
        proc = self.run_real(BUILD_LOOP_MEMORY_BUSY="1")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("reference queued for the next closeout drain", proc.stderr)
        self.assertEqual(list(self.store.rglob("*.md")), [])

    def test_real_writer_saves_reference_into_temp_store(self):
        proc = self.run_real()
        store = self.store
        self.assertEqual(proc.returncode, 0, proc.stderr)
        written = [p for p in store.rglob("*.md") if "Search latency" in p.read_text()]
        self.assertEqual(len(written), 1, sorted(map(str, store.rglob("*"))))
        text = written[0].read_text()
        self.assertIn("demo-proj", str(written[0].relative_to(store)))
        self.assertIn(f"Receipt ret-001. SHA-256 {self.digest}.", text)
        self.assertIn("type: reference", text)


if __name__ == "__main__":
    unittest.main()
