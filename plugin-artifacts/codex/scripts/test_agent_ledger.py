#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the agent-activity ledger (scripts/agent_ledger.py).

Covers the instrument's contract: canonical field shape, vocab validation,
append-only durability (incl. a torn final line), summarize aggregation, and
the CLI surface the orchestrator shells out to.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "agent_ledger.py"

sys.path.insert(0, str(HERE))
import agent_ledger  # noqa: E402


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LEDGER), *args],
        check=False,
        capture_output=True,
        text=True,
    )


class BuildRowTests(unittest.TestCase):
    def test_canonical_fields_present_and_ordered(self) -> None:
        row = agent_ledger.build_row(run_id="r1", agent="advisor", action="author")
        self.assertEqual(tuple(row.keys()), agent_ledger.LEDGER_FIELDS)

    def test_required_fields_enforced(self) -> None:
        with self.assertRaises(ValueError):
            agent_ledger.build_row(run_id="", agent="advisor", action="author")
        with self.assertRaises(ValueError):
            agent_ledger.build_row(run_id="r1", agent="", action="author")

    def test_unknown_action_rejected(self) -> None:
        with self.assertRaises(ValueError):
            agent_ledger.build_row(run_id="r1", agent="x", action="bogus")

    def test_unknown_status_rejected(self) -> None:
        with self.assertRaises(ValueError):
            agent_ledger.build_row(run_id="r1", agent="x", action="execute", status="great")

    def test_rung_bounds_enforced(self) -> None:
        with self.assertRaises(ValueError):
            agent_ledger.build_row(run_id="r1", agent="x", action="execute", rung=4)
        # valid rungs do not raise
        for r in (0, 1, 2, 3):
            agent_ledger.build_row(run_id="r1", agent="x", action="execute", rung=r)

    def test_ts_autostamped_when_absent(self) -> None:
        row = agent_ledger.build_row(run_id="r1", agent="x", action="author")
        self.assertTrue(row["ts"].endswith("Z"))


class AppendReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._rally_tool = mock.patch.dict(
            os.environ,
            {"BUILD_LOOP_RALLY_TOOL": "codex:test-agent-ledger"},
            clear=False,
        )
        self._rally_tool.start()
        self.addCleanup(self._rally_tool.stop)

    def test_append_then_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".build-loop" / "agent-ledger.jsonl"
            row = agent_ledger.build_row(
                run_id="r1", agent="advisor", action="author",
                phase="2", tier="frontier", model="fable", rung=1, status="pass",
                trigger="riskSurfaceChange", refs={"output": "docs/plans/x.md"},
            )
            env = agent_ledger.append(path, row)
            self.assertTrue(env["ok"], env)
            rows = agent_ledger.read(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["agent"], "advisor")
            self.assertEqual(rows[0]["model"], "fable")
            self.assertEqual(rows[0]["rung"], 1)

    def test_append_is_additive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.jsonl"
            for i in range(3):
                agent_ledger.append(
                    path,
                    agent_ledger.build_row(run_id="r1", agent="implementer", action="execute", chunk_id=f"c{i}"),
                )
            self.assertEqual(len(agent_ledger.read(path)), 3)

    def test_read_tolerates_torn_final_line(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.jsonl"
            agent_ledger.append(path, agent_ledger.build_row(run_id="r1", agent="x", action="author"))
            # Simulate a crash mid-append: a partial JSON line with no newline.
            with path.open("a", encoding="utf-8") as fh:
                fh.write('{"ts": "2026-06-10T00:00:00Z", "run_id": "r1"')  # truncated
            rows = agent_ledger.read(path)
            self.assertEqual(len(rows), 1, "torn final line must be skipped, valid row kept")

    def test_append_quarantines_torn_tail_then_preserves_next_row(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "agent-ledger.jsonl"
            first = agent_ledger.build_row(
                run_id="first", agent="advisor", action="author"
            )
            second = agent_ledger.build_row(
                run_id="second", agent="cursor", action="verify"
            )
            self.assertTrue(agent_ledger.append(path, first)["ok"])
            torn = b'{"run_id":"torn"'
            with path.open("ab") as fh:
                fh.write(torn)

            result = agent_ledger.append(path, second)

            self.assertTrue(result["ok"], result)
            self.assertEqual(
                [row["run_id"] for row in agent_ledger.read(path)],
                ["first", "second"],
            )
            quarantined = list(
                (path.parent / "agent-ledger-corrupt-tails").glob("*.partial")
            )
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(quarantined[0].read_bytes(), torn)

    def test_torn_tail_quarantine_keeps_newest_with_bounded_count_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "agent-ledger.jsonl"
            latest = b""
            for index in range(agent_ledger.MAX_CORRUPT_TAIL_FILES + 8):
                latest = (
                    f'{{"torn":"newest-{index}","padding":"'.encode("utf-8")
                    + (b"x" * (96 * 1024))
                )
                with path.open("ab") as fh:
                    fh.write(latest)
                agent_ledger._append_local_row(
                    path,
                    agent_ledger.build_row(
                        run_id=f"row-{index}", agent="cursor", action="verify"
                    ),
                )

            quarantine = path.parent / "agent-ledger-corrupt-tails"
            artifacts = list(quarantine.glob("*.partial"))
            self.assertLessEqual(
                len(artifacts), agent_ledger.MAX_CORRUPT_TAIL_FILES
            )
            self.assertLessEqual(
                sum(artifact.stat().st_size for artifact in artifacts),
                agent_ledger.MAX_CORRUPT_TAIL_BYTES,
            )
            self.assertTrue(
                any(artifact.read_bytes() == latest for artifact in artifacts),
                "newest torn-tail evidence must survive bounded pruning",
            )

    def test_reconciliation_waits_for_partial_append_and_advances_only_complete_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "agent-ledger.jsonl"
            first = agent_ledger.build_row(
                run_id="first", agent="cursor", action="verify"
            )
            second = agent_ledger.build_row(
                run_id="second", agent="cursor", action="verify"
            )
            agent_ledger._append_local_row(path, first)
            partial_written = threading.Event()
            finish_write = threading.Event()
            original_write_all = agent_ledger._write_all
            writer_errors: list[BaseException] = []
            reconcile_results: list[dict] = []

            def blocking_write(fd: int, data: bytes) -> None:
                split = max(1, len(data) // 2)
                original_write_all(fd, data[:split])
                partial_written.set()
                if not finish_write.wait(timeout=2):
                    raise TimeoutError("test did not release partial append")
                original_write_all(fd, data[split:])

            def write_second() -> None:
                try:
                    agent_ledger._append_local_row(path, second)
                except BaseException as exc:  # pragma: no cover - assertion below
                    writer_errors.append(exc)

            def reconcile() -> None:
                reconcile_results.append(
                    agent_ledger._reconcile_to_rally(path, root)
                )

            projected = agent_ledger._projection_result(
                "projected", backend="rally", transport="rally-cli"
            )
            with (
                mock.patch.object(agent_ledger, "_write_all", blocking_write),
                mock.patch.object(
                    agent_ledger, "_project_to_rally", return_value=projected
                ),
            ):
                writer = threading.Thread(target=write_second)
                writer.start()
                self.assertTrue(partial_written.wait(timeout=1))
                reconciler = threading.Thread(target=reconcile)
                reconciler.start()
                time.sleep(0.05)
                self.assertTrue(
                    reconciler.is_alive(),
                    "reconciliation observed a line while its writer lock was held",
                )
                finish_write.set()
                writer.join(timeout=2)
                reconciler.join(timeout=2)

            self.assertFalse(writer_errors)
            self.assertEqual(len(reconcile_results), 1)
            marker = json.loads(
                (path.parent / agent_ledger.SYNC_MARKER_NAME).read_text()
            )
            self.assertEqual(marker["cursor"], 2)
            self.assertEqual(marker["cursor_offset"], path.stat().st_size)
            self.assertEqual(marker["terminal"], [])

    def test_read_missing_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(agent_ledger.read(Path(td) / "nope.jsonl"), [])

    def test_append_fail_open_on_bad_path(self) -> None:
        # A path whose parent cannot be created (a file in the way) fails open,
        # returning ok=False rather than raising.
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            path = blocker / "sub" / "ledger.jsonl"  # parent is a file
            env = agent_ledger.append(path, agent_ledger.build_row(run_id="r1", agent="x", action="author"))
            self.assertFalse(env["ok"])
            self.assertIsNotNone(env["error"])

    def test_canonical_repo_ledger_projects_exact_row_through_post(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".git").mkdir(parents=True)
            path = agent_ledger.default_ledger_path(repo)
            channel = Path(td) / "channel"
            row = agent_ledger.build_row(
                run_id="r1",
                agent="advisor",
                action="author",
                model="fable",
                ts="2026-08-14T00:00:00Z",
            )
            resolved = SimpleNamespace(
                channel_dir=str(channel),
                app_slug="repo",
                resolved_via="repo-local-rally-cli",
                backend="rally",
            )
            resolve = mock.Mock(return_value=resolved)
            post = mock.Mock(return_value=41)

            with (
                mock.patch.dict(
                    os.environ, {"BUILD_LOOP_RALLY_TOOL": "codex:r1-test"}
                ),
                mock.patch.object(
                    agent_ledger, "_rally_adapter", return_value=(resolve, post)
                ),
            ):
                env = agent_ledger.append(path, row)

            self.assertTrue(env["ok"], env)
            self.assertEqual(env["projection"]["status"], "projected")
            self.assertEqual(env["projection"]["backend"], "rally")
            resolve.assert_called_once_with(repo.resolve())
            post.assert_called_once()
            call = post.call_args.kwargs
            self.assertEqual(call["channel_dir"], channel)
            self.assertEqual(call["kind"], "artifact")
            self.assertEqual(call["tool"], "codex:r1-test")
            self.assertNotEqual(call["tool"], row["agent"])
            self.assertEqual(call["model"], row["model"])
            self.assertEqual(call["run_id"], row["run_id"])
            self.assertEqual(call["workdir"], repo.resolve())
            self.assertEqual(call["payload"]["agent_ledger"], row)
            self.assertNotIn("evidence", call["payload"])
            self.assertEqual(
                call["payload"]["subject"],
                agent_ledger._projection_payload(row)["subject"],
            )
            self.assertEqual(agent_ledger.read(path), [row])
            marker = json.loads(
                (path.parent / agent_ledger.SYNC_MARKER_NAME).read_text()
            )
            self.assertEqual(marker["cursor"], 1)
            self.assertEqual(marker["pending"], [])

    def test_native_projection_uses_host_session_actor_and_payload_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".git").mkdir(parents=True)
            path = agent_ledger.default_ledger_path(repo)
            row = agent_ledger.build_row(
                run_id="native-row",
                agent="advisor",
                action="author",
                ts="2026-08-14T00:00:00Z",
            )
            resolved = SimpleNamespace(
                channel_dir=str(repo / ".rally"),
                app_slug="repo",
                resolved_via="repo-local-rally-cli",
                backend="rally",
                transport="rally-cli",
                raw={},
            )
            post = mock.Mock(return_value=44)

            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "BUILD_LOOP_RALLY_TOOL": "codex:explicit-ledger-actor",
                        "CODEX_THREAD_ID": "thread-ledger",
                    },
                    clear=False,
                ),
                mock.patch.object(
                    agent_ledger,
                    "_rally_adapter",
                    return_value=(mock.Mock(return_value=resolved), post),
                ),
            ):
                env = agent_ledger.append(path, row)

            self.assertTrue(env["ok"], env)
            posted = post.call_args.kwargs
            self.assertEqual(posted["tool"], "codex:explicit-ledger-actor")
            self.assertEqual(posted["local_tool"], "codex")
            self.assertEqual(posted["local_session_id"], "thread-ledger")
            self.assertEqual(posted["payload"]["host_tool"], "codex")
            self.assertEqual(posted["payload"]["session_id"], "thread-ledger")
            self.assertEqual(posted["payload"]["agent_ledger"], row)

    def test_local_projection_keeps_base_tool_and_original_payload_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".git").mkdir(parents=True)
            path = agent_ledger.default_ledger_path(repo)
            row = agent_ledger.build_row(
                run_id="local-row", agent="cursor", action="verify"
            )
            resolved = SimpleNamespace(
                channel_dir=str(Path(td) / "fallback"),
                app_slug="repo",
                resolved_via="build-loop-internal",
                backend="build-loop-local",
                transport="fact-v1",
            )
            post = mock.Mock(return_value=2)

            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "BUILD_LOOP_RALLY_TOOL": "cursor",
                        "CURSOR_SESSION_ID": "cursor-local-session",
                    },
                    clear=False,
                ),
                mock.patch.object(
                    agent_ledger,
                    "_rally_adapter",
                    return_value=(mock.Mock(return_value=resolved), post),
                ),
            ):
                env = agent_ledger.append(path, row)

            self.assertTrue(env["ok"], env)
            posted = post.call_args.kwargs
            self.assertEqual(posted["tool"], "cursor")
            self.assertNotIn("host_tool", posted["payload"])
            self.assertNotIn("session_id", posted["payload"])

    def test_arbitrary_ledger_path_stays_local_without_explicit_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "custom-ledger.jsonl"
            row = agent_ledger.build_row(run_id="r1", agent="x", action="author")
            adapter = mock.Mock()

            with mock.patch.object(agent_ledger, "_rally_adapter", adapter):
                env = agent_ledger.append(path, row)

            self.assertTrue(env["ok"], env)
            self.assertEqual(env["projection"]["status"], "skipped")
            self.assertEqual(env["projection"]["reason"], "no-workdir")
            adapter.assert_not_called()

    def test_explicit_workdir_projects_an_arbitrary_ledger_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            path = Path(td) / "custom-ledger.jsonl"
            row = agent_ledger.build_row(run_id="r1", agent="cursor", action="verify")
            resolved = SimpleNamespace(
                channel_dir=str(Path(td) / "channel"),
                app_slug="repo",
                resolved_via="build-loop-internal",
                backend="build-loop-local",
            )
            resolve = mock.Mock(return_value=resolved)
            post = mock.Mock(return_value=7)

            with mock.patch.object(agent_ledger, "_rally_adapter", return_value=(resolve, post)):
                env = agent_ledger.append(path, row, workdir=repo)

            self.assertTrue(env["ok"], env)
            self.assertEqual(env["projection"]["backend"], "build-loop-local")
            resolve.assert_called_once_with(repo.resolve())
            post.assert_called_once()

    def test_projection_subject_is_stable_for_the_exact_row(self) -> None:
        row = agent_ledger.build_row(
            run_id="r1",
            agent="cursor",
            action="verify",
            ts="2026-08-14T00:00:00Z",
        )
        first = agent_ledger._projection_payload(row)["subject"]
        second = agent_ledger._projection_payload(dict(row))["subject"]
        changed = agent_ledger._projection_payload({**row, "note": "different"})[
            "subject"
        ]
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_projection_failure_does_not_change_local_append_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".git").mkdir(parents=True)
            path = agent_ledger.default_ledger_path(repo)
            row = agent_ledger.build_row(run_id="r1", agent="x", action="author")
            resolved = SimpleNamespace(
                channel_dir=str(Path(td) / "channel"),
                app_slug="repo",
                resolved_via="repo-local-rally-cli",
                backend="rally",
            )
            post = mock.Mock(side_effect=RuntimeError("rally unavailable"))

            with mock.patch.object(
                agent_ledger,
                "_rally_adapter",
                return_value=(mock.Mock(return_value=resolved), post),
            ):
                env = agent_ledger.append(path, row)

            self.assertTrue(env["ok"], env)
            self.assertEqual(env["projection"]["status"], "failed")
            self.assertEqual(env["projection"]["backend"], "rally")
            self.assertIn("rally unavailable", env["projection"]["error"])
            self.assertEqual(agent_ledger.read(path), [row])

    def test_outcome_unknown_preserves_typed_native_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".git").mkdir(parents=True)
            path = agent_ledger.default_ledger_path(repo)
            row = agent_ledger.build_row(
                run_id="r-unknown", agent="advisor", action="author"
            )
            resolved = SimpleNamespace(
                channel_dir=str(repo / ".rally"),
                app_slug="repo",
                resolved_via="repo-local-rally-cli",
                backend="rally",
                transport="rally-cli",
            )

            def ambiguous_post(**kwargs):
                kwargs["outcome"].update(
                    {
                        "status": "outcome_unknown",
                        "backend": "rally",
                        "transport": "rally-cli",
                        "reason": "native mutation timed out",
                        "event_id": "evt-maybe-committed",
                        "remedy": "read Rally before deciding whether to retry",
                    }
                )
                return None

            with mock.patch.object(
                agent_ledger,
                "_rally_adapter",
                return_value=(mock.Mock(return_value=resolved), ambiguous_post),
            ):
                env = agent_ledger.append(path, row)

            projection = env["projection"]
            self.assertTrue(env["ok"], env)
            self.assertEqual(projection["status"], "outcome_unknown")
            self.assertIsNone(projection["ok"])
            self.assertEqual(projection["reason"], "native mutation timed out")
            self.assertEqual(projection["event_id"], "evt-maybe-committed")
            self.assertEqual(
                projection["remedy"], "read Rally before deciding whether to retry"
            )
            marker = json.loads(
                (path.parent / agent_ledger.SYNC_MARKER_NAME).read_text()
            )
            self.assertEqual(marker["pending"], [])
            self.assertEqual(marker["terminal"][0]["status"], "outcome_unknown")
            self.assertEqual(
                marker["terminal"][0]["remedy"],
                "read Rally before deciding whether to retry",
            )

            later = agent_ledger.build_row(
                run_id="r-after-unknown", agent="verifier", action="verify"
            )
            later_post = mock.Mock(return_value=9)
            with mock.patch.object(
                agent_ledger,
                "_rally_adapter",
                return_value=(mock.Mock(return_value=resolved), later_post),
            ):
                later_env = agent_ledger.append(path, later)
            self.assertEqual(later_env["projection"]["status"], "projected")
            later_post.assert_called_once()
            self.assertEqual(
                later_post.call_args.kwargs["payload"]["agent_ledger"], later
            )

    def test_missing_marker_uses_native_receipt_before_reposting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".git").mkdir(parents=True)
            path = agent_ledger.default_ledger_path(repo)
            path.parent.mkdir(parents=True)
            row = agent_ledger.build_row(
                run_id="r-committed", agent="advisor", action="author",
                ts="2026-08-14T00:00:00Z",
            )
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            resolved = SimpleNamespace(
                channel_dir=str(repo / ".rally"),
                app_slug="repo",
                resolved_via="repo-local-rally-cli",
                backend="rally",
                transport="rally-cli",
            )
            receipt = agent_ledger._projection_result(
                "projected",
                backend="rally",
                transport="rally-cli",
                revision=77,
                reason="already-present-in-rally",
                event_id="evt-existing",
                write_attempted=False,
            )
            post = mock.Mock(return_value=78)
            with (
                mock.patch.object(
                    agent_ledger,
                    "_rally_adapter",
                    return_value=(mock.Mock(return_value=resolved), post),
                ),
                mock.patch.object(
                    agent_ledger,
                    "_native_projection_receipts",
                    return_value=(True, {agent_ledger._row_digest(row): receipt}, None),
                ),
            ):
                projection = agent_ledger._reconcile_to_rally(
                    path, repo, current_index=0
                )

            self.assertEqual(projection["status"], "projected")
            self.assertEqual(projection["event_id"], "evt-existing")
            self.assertFalse(projection["write_attempted"])
            post.assert_not_called()
            marker = json.loads(
                (path.parent / agent_ledger.SYNC_MARKER_NAME).read_text()
            )
            self.assertEqual(marker["cursor"], 1)
            self.assertEqual(marker["pending"], [])

    def test_native_receipt_requires_current_repo_and_exact_decoded_row(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            other = Path(td) / "other"
            repo.mkdir()
            other.mkdir()
            row = agent_ledger.build_row(
                run_id="r-receipt", agent="advisor", action="author",
                ts="2026-08-14T00:00:00Z",
            )
            payload = agent_ledger._projection_payload(row)
            from rally_point.payload_codec import encode_event

            exact_fact = {
                "subject": payload["subject"],
                "seq": 12,
                "event_id": "evt-exact",
                "evidence": encode_event(
                    kind="artifact",
                    payload=payload,
                    model="",
                    run_id=row["run_id"],
                    app_slug="repo",
                ),
            }
            wrong_row = {**row, "note": "different"}
            wrong_payload = agent_ledger._projection_payload(wrong_row)
            wrong_fact = {
                "subject": payload["subject"],
                "seq": 13,
                "event_id": "evt-wrong-payload",
                "evidence": encode_event(
                    kind="artifact",
                    payload=wrong_payload,
                    model="",
                    run_id=row["run_id"],
                    app_slug="repo",
                ),
            }
            native_result = SimpleNamespace(
                ok=True,
                reason=None,
                payload={
                    "data": {
                        "recent": {
                            "rows": [
                                {"repo_root": str(other), "fact": exact_fact},
                                {"repo_root": str(repo), "fact": wrong_fact},
                                {"repo_root": str(repo), "fact": exact_fact},
                            ]
                        }
                    }
                },
            )
            envelope = SimpleNamespace(
                app_slug="repo",
                raw={
                    "whoami": {
                        "data": {"whoami": {"repo_root": str(repo)}}
                    }
                },
            )
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "BUILD_LOOP_RALLY_TOOL": "cursor",
                        "CURSOR_SESSION_ID": "cursor-receipt",
                    },
                    clear=False,
                ),
                mock.patch(
                    "scripts.rally_point.discovery_bridge.maybe_auto_migrate"
                ) as migrate,
                mock.patch(
                    "scripts.rally_point.backend_adapter.resolve_context",
                    return_value=object(),
                ),
                mock.patch(
                    "scripts.rally_point.backend_adapter.invoke_native",
                    return_value=native_result,
                ) as invoke,
            ):
                available, receipts, reason = agent_ledger._native_projection_receipts(
                    repo, envelope
                )

            self.assertTrue(available, reason)
            self.assertIsNone(reason)
            self.assertEqual(list(receipts), [agent_ledger._row_digest(row)])
            self.assertEqual(receipts[agent_ledger._row_digest(row)]["event_id"], "evt-exact")
            migrate.assert_called_once_with(repo, envelope)
            self.assertEqual(invoke.call_args.kwargs["tool"], "cursor:cursor-receipt")
            self.assertEqual(invoke.call_args.kwargs["session_id"], "cursor-receipt")

    def test_more_than_terminal_ring_oversize_rows_do_not_block_later_row(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".git").mkdir(parents=True)
            path = agent_ledger.default_ledger_path(repo)
            path.parent.mkdir(parents=True)
            oversized = [
                agent_ledger.build_row(
                    run_id=f"large-{index}",
                    agent="advisor",
                    action="author",
                    note="x" * 40000,
                    ts=f"2026-08-14T00:{index:02d}:00Z",
                )
                for index in range(agent_ledger.MAX_TERMINAL_DIAGNOSTICS + 6)
            ]
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in oversized),
                encoding="utf-8",
            )
            later = agent_ledger.build_row(
                run_id="small-later", agent="verifier", action="verify"
            )
            resolved = SimpleNamespace(
                channel_dir=str(Path(td) / "fallback"),
                app_slug="repo",
                resolved_via="build-loop-internal",
                backend="build-loop-local",
                transport="fact-v1",
            )
            post = mock.Mock(return_value=1)
            with mock.patch.object(
                agent_ledger,
                "_rally_adapter",
                return_value=(mock.Mock(return_value=resolved), post),
            ):
                env = agent_ledger.append(path, later)

            self.assertEqual(env["projection"]["status"], "projected")
            post.assert_called_once()
            self.assertEqual(post.call_args.kwargs["payload"]["agent_ledger"], later)
            marker = json.loads(
                (path.parent / agent_ledger.SYNC_MARKER_NAME).read_text()
            )
            self.assertEqual(marker["cursor"], len(oversized) + 1)
            self.assertEqual(marker["pending"], [])
            self.assertEqual(
                len(marker["terminal"]), agent_ledger.MAX_TERMINAL_DIAGNOSTICS
            )
            self.assertTrue(
                all(item["status"] == "oversize" for item in marker["terminal"])
            )

    def test_hot_append_streams_from_marker_without_full_ledger_read(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".git").mkdir(parents=True)
            path = agent_ledger.default_ledger_path(repo)
            path.parent.mkdir(parents=True)
            rows = [
                agent_ledger.build_row(
                    run_id=f"old-{index}",
                    agent="advisor",
                    action="author",
                    ts="2026-08-14T00:00:00Z",
                )
                for index in range(5000)
            ]
            raw_lines = [
                (json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n").encode()
                for row in rows
            ]
            path.write_bytes(b"".join(raw_lines))
            prefix = agent_ledger._EMPTY_PREFIX_SHA256
            for raw in raw_lines:
                prefix = agent_ledger._advance_prefix_digest(
                    prefix, agent_ledger.hashlib.sha256(raw).hexdigest(), len(raw)
                )
            device, inode = agent_ledger._ledger_identity(path)
            marker = {
                "schema": agent_ledger.SYNC_MARKER_SCHEMA,
                "cursor": len(rows),
                "cursor_offset": path.stat().st_size,
                "prefix_sha256": prefix,
                "prefix_tail_sha256": agent_ledger._prefix_tail_probe(
                    path, path.stat().st_size
                ),
                "ledger_device": device,
                "ledger_inode": inode,
                "pending": [],
                "terminal": [],
            }
            (path.parent / agent_ledger.SYNC_MARKER_NAME).write_text(
                json.dumps(marker), encoding="utf-8"
            )
            current = agent_ledger.build_row(
                run_id="current", agent="cursor", action="verify"
            )
            resolved = SimpleNamespace(
                channel_dir=str(Path(td) / "fallback"),
                app_slug="repo",
                resolved_via="build-loop-internal",
                backend="build-loop-local",
            )
            post = mock.Mock(return_value=7)
            with (
                mock.patch.object(
                    agent_ledger,
                    "_rally_adapter",
                    return_value=(mock.Mock(return_value=resolved), post),
                ),
                mock.patch.object(
                    agent_ledger,
                    "read",
                    side_effect=AssertionError("hot append must not materialize ledger"),
                ),
            ):
                result = agent_ledger.append(path, current)

            self.assertEqual(result["projection"]["status"], "projected")
            post.assert_called_once()
            updated = json.loads(
                (path.parent / agent_ledger.SYNC_MARKER_NAME).read_text()
            )
            self.assertEqual(updated["cursor"], 5001)
            self.assertEqual(updated["cursor_offset"], path.stat().st_size)

    def test_concurrent_build_loops_share_one_append_and_projection_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            path = agent_ledger.default_ledger_path(repo)
            apps = root / "apps"
            script = (
                "import sys; sys.path.insert(0, %r); import agent_ledger as a; "
                "i=sys.argv[2]; row=a.build_row(run_id='run-'+i,agent='cursor',"
                "action='verify',ts='2026-08-14T00:00:00Z'); "
                "print(a.append(a.default_ledger_path(__import__('pathlib').Path(sys.argv[1])),row))"
            ) % str(HERE)
            env = dict(os.environ)
            env.update(
                BUILD_LOOP_BRIDGE_INTERNAL_ONLY="1",
                BUILD_LOOP_APPS_ROOT=str(apps),
                BUILD_LOOP_RALLY_TOOL="cursor",
            )
            procs = [
                subprocess.Popen(
                    [sys.executable, "-c", script, str(repo), str(index)],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for index in range(6)
            ]
            results = [proc.communicate(timeout=30) for proc in procs]
            self.assertTrue(all(proc.returncode == 0 for proc in procs), results)

            final = agent_ledger.build_row(
                run_id="run-final",
                agent="cursor",
                action="verify",
                ts="2026-08-14T00:00:00Z",
            )
            with mock.patch.dict(os.environ, env, clear=False):
                from rally_point import discovery_bridge

                discovery_bridge.clear_cache()
                final_result = agent_ledger.append(path, final, workdir=repo)
            self.assertTrue(final_result["ok"], final_result)
            self.assertEqual(len(agent_ledger.read(path)), 7)
            marker = json.loads(
                (path.parent / agent_ledger.SYNC_MARKER_NAME).read_text()
            )
            self.assertEqual(marker["cursor"], 7)
            self.assertEqual(marker["pending"], [])

            from rally_point import payload_codec

            facts = [
                json.loads(line)
                for line in (apps / "repo" / "changes.jsonl").read_text().splitlines()
            ]
            projected = []
            for fact in facts:
                event = payload_codec.decode_event(fact.get("evidence"))
                payload = event.get("payload") if isinstance(event, dict) else None
                row = payload.get("agent_ledger") if isinstance(payload, dict) else None
                if isinstance(row, dict):
                    projected.append(agent_ledger._row_digest(row))
            self.assertEqual(len(projected), 7)
            self.assertEqual(len(set(projected)), 7)

    def test_existing_rows_backfill_once_and_marker_skips_them_later(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".git").mkdir(parents=True)
            path = agent_ledger.default_ledger_path(repo)
            path.parent.mkdir(parents=True)
            old_row = agent_ledger.build_row(
                run_id="old", agent="advisor", action="author",
                ts="2026-08-13T00:00:00Z",
            )
            path.write_text(json.dumps(old_row) + "\n", encoding="utf-8")
            current_row = agent_ledger.build_row(
                run_id="current", agent="implementer", action="execute",
                ts="2026-08-14T00:00:00Z",
            )
            resolved = SimpleNamespace(
                channel_dir=str(repo / ".rally"),
                app_slug="repo",
                resolved_via="repo-local-rally-cli",
                backend="rally",
                transport="rally-cli",
            )
            first_post = mock.Mock(side_effect=[31, 32])
            with mock.patch.object(
                agent_ledger,
                "_rally_adapter",
                return_value=(mock.Mock(return_value=resolved), first_post),
            ):
                env = agent_ledger.append(path, current_row)

            self.assertEqual(env["projection"]["status"], "projected")
            self.assertEqual(
                [call.kwargs["payload"]["agent_ledger"] for call in first_post.call_args_list],
                [old_row, current_row],
            )
            marker_path = path.parent / agent_ledger.SYNC_MARKER_NAME
            marker = json.loads(marker_path.read_text())
            self.assertEqual(marker["schema"], agent_ledger.SYNC_MARKER_SCHEMA)
            self.assertEqual(marker["cursor"], 2)
            self.assertEqual(marker["pending"], [])
            self.assertEqual(
                list(marker_path.parent.glob(f".{marker_path.name}.*.tmp")), []
            )

            later_row = agent_ledger.build_row(
                run_id="later", agent="verifier", action="verify",
                ts="2026-08-14T01:00:00Z",
            )
            later_post = mock.Mock(return_value=33)
            with mock.patch.object(
                agent_ledger,
                "_rally_adapter",
                return_value=(mock.Mock(return_value=resolved), later_post),
            ):
                later_env = agent_ledger.append(path, later_row)

            self.assertEqual(later_env["projection"]["status"], "projected")
            later_post.assert_called_once()
            self.assertEqual(
                later_post.call_args.kwargs["payload"]["agent_ledger"], later_row
            )

    def test_failed_row_retries_on_next_append_then_clears_pending_hole(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".git").mkdir(parents=True)
            path = agent_ledger.default_ledger_path(repo)
            first_row = agent_ledger.build_row(
                run_id="first", agent="advisor", action="author"
            )
            resolved = SimpleNamespace(
                channel_dir=str(repo / ".rally"),
                app_slug="repo",
                resolved_via="repo-local-rally-cli",
                backend="rally",
                transport="rally-cli",
            )

            def failed_post(**kwargs):
                kwargs["outcome"].update(
                    {
                        "status": "failed",
                        "backend": "rally",
                        "transport": "rally-cli",
                        "reason": "temporary transport failure",
                    }
                )
                return None

            with mock.patch.object(
                agent_ledger,
                "_rally_adapter",
                return_value=(mock.Mock(return_value=resolved), failed_post),
            ):
                first_env = agent_ledger.append(path, first_row)
            self.assertEqual(first_env["projection"]["status"], "failed")

            second_row = agent_ledger.build_row(
                run_id="second", agent="implementer", action="execute"
            )
            recovered_post = mock.Mock(side_effect=[41, 42])
            with mock.patch.object(
                agent_ledger,
                "_rally_adapter",
                return_value=(mock.Mock(return_value=resolved), recovered_post),
            ):
                second_env = agent_ledger.append(path, second_row)

            self.assertEqual(second_env["projection"]["status"], "projected")
            self.assertEqual(
                [
                    call.kwargs["payload"]["agent_ledger"]
                    for call in recovered_post.call_args_list
                ],
                [first_row, second_row],
            )
            marker = json.loads(
                (path.parent / agent_ledger.SYNC_MARKER_NAME).read_text()
            )
            self.assertEqual(marker["cursor"], 2)
            self.assertEqual(marker["pending"], [])

    def test_native_post_failover_reports_build_loop_local_backend(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".git").mkdir(parents=True)
            path = agent_ledger.default_ledger_path(repo)
            row = agent_ledger.build_row(
                run_id="r-failover", agent="advisor", action="author"
            )
            resolved = SimpleNamespace(
                channel_dir=str(repo / ".rally"),
                app_slug="repo",
                resolved_via="repo-local-rally-cli",
                backend="rally",
                transport="rally-cli",
            )

            def fallback_post(**kwargs):
                kwargs["outcome"].update(
                    {
                        "status": "posted",
                        "backend": "build-loop-local",
                        "transport": "fact-v1",
                        "revision": 3,
                    }
                )
                return 3

            with mock.patch.object(
                agent_ledger,
                "_rally_adapter",
                return_value=(mock.Mock(return_value=resolved), fallback_post),
            ):
                env = agent_ledger.append(path, row)

            self.assertTrue(env["ok"], env)
            self.assertEqual(env["projection"]["status"], "projected")
            self.assertEqual(env["projection"]["backend"], "build-loop-local")
            self.assertEqual(env["projection"]["transport"], "fact-v1")
            marker = json.loads(
                (path.parent / agent_ledger.SYNC_MARKER_NAME).read_text()
            )
            self.assertEqual(marker["cursor"], 1)
            self.assertEqual(marker["pending"], [])

            # A committed fallback fact is not re-posted row-by-row. The next
            # native post owns maybe_auto_migrate() for the fallback spool.
            recovered = agent_ledger.build_row(
                run_id="r-recovered", agent="verifier", action="verify"
            )

            def native_post(**kwargs):
                kwargs["outcome"].update(
                    {
                        "status": "posted",
                        "backend": "rally",
                        "transport": "rally-cli",
                        "revision": 4,
                    }
                )
                return 4

            native_post_mock = mock.Mock(side_effect=native_post)
            with mock.patch.object(
                agent_ledger,
                "_rally_adapter",
                return_value=(mock.Mock(return_value=resolved), native_post_mock),
            ):
                recovered_env = agent_ledger.append(path, recovered)
            self.assertEqual(recovered_env["projection"]["backend"], "rally")
            native_post_mock.assert_called_once()
            self.assertEqual(
                native_post_mock.call_args.kwargs["payload"]["agent_ledger"],
                recovered,
            )

    def test_projection_guard_prevents_recursive_posting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".git").mkdir(parents=True)
            path = agent_ledger.default_ledger_path(repo)
            row = agent_ledger.build_row(run_id="r1", agent="x", action="author")
            resolved = SimpleNamespace(
                channel_dir=str(Path(td) / "channel"),
                app_slug="repo",
                resolved_via="repo-local-rally-cli",
                backend="rally",
            )

            def recursive_post(**_kwargs: object) -> int:
                nested = agent_ledger.append(path, row, workdir=repo)
                self.assertEqual(nested["projection"]["reason"], "recursive-projection")
                return 5

            with mock.patch.object(
                agent_ledger,
                "_rally_adapter",
                return_value=(mock.Mock(return_value=resolved), recursive_post),
            ):
                env = agent_ledger.append(path, row)

            self.assertTrue(env["ok"], env)
            self.assertEqual(env["projection"]["status"], "projected")
            self.assertEqual(len(agent_ledger.read(path)), 2)

    @unittest.skipUnless(shutil.which("rally"), "rally binary not installed")
    def test_real_agent_ledger_projection_uses_standalone_rally(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            home.mkdir()
            repo = root / "ledger-native"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            row = agent_ledger.build_row(
                run_id="real-rally-row",
                agent="cursor",
                action="verify",
                model="cursor-agent",
                status="pass",
                ts="2026-08-14T00:00:00Z",
            )
            updates = {
                "HOME": str(home),
                "BUILD_LOOP_RALLY_TOOL": "codex:real-rally-row",
            }
            with mock.patch.dict(os.environ, updates, clear=False):
                for key in (
                    "AGENT_RALLY_BINARY",
                    "AGENT_RALLY_DISCOVER",
                    "AGENT_RALLY_APPS_ROOT",
                    "BUILD_LOOP_APPS_ROOT",
                    "BUILD_LOOP_BRIDGE_INTERNAL_ONLY",
                ):
                    os.environ.pop(key, None)
                from rally_point import discovery_bridge

                discovery_bridge.clear_cache()
                env = agent_ledger.append(
                    agent_ledger.default_ledger_path(repo), row, workdir=repo
                )
                discovery_bridge.clear_cache()

            self.assertTrue(env["ok"], env)
            self.assertEqual(env["projection"]["status"], "projected")
            self.assertEqual(env["projection"]["backend"], "rally")
            self.assertFalse((repo / ".rally" / "changes.jsonl").exists())
            raw_rows = []
            for log in (repo / ".rally" / "log").glob("*.jsonl"):
                raw_rows.extend(
                    json.loads(line)
                    for line in log.read_text().splitlines()
                    if line.strip()
                )
            matching = [
                item for item in raw_rows
                if (item.get("payload") or {}).get("tool")
                == "codex:real-rally-row"
                and (item.get("payload") or {}).get("kind") == "artifact"
                and str((item.get("payload") or {}).get("subject", "")).startswith(
                    "agent-ledger:"
                )
            ]
            self.assertEqual(len(matching), 1)
            from rally_point.payload_codec import decode_event

            evidence = matching[0]["payload"].get("evidence") or []
            projected_payload = decode_event(evidence)
            self.assertIsNotNone(projected_payload)
            self.assertEqual(projected_payload["payload"]["agent_ledger"], row)

    def _project_with_future_kind(self, run_id: str, *, gate: bool) -> dict:
        """Append one row while build-loop maps onto a kind rally does not know.

        Simulates the observed 2026-08-30 dogfood failure: build-loop learned a
        fact kind ahead of the installed binary. ``gate=False`` forces the
        capability probe to answer "unknown", reproducing the pre-fix behavior
        against the same real binary.
        """
        # Mirror ``agent_ledger._rally_adapter``'s import order exactly. Both
        # ``scripts.rally_point.post`` and ``rally_point.post`` are importable,
        # and they are DIFFERENT module objects — patching the one the adapter
        # did not load makes this test pass vacuously.
        try:
            from scripts.rally_point import discovery_bridge, kind_capability
            from scripts.rally_point import post as post_mod
        except ImportError:
            from rally_point import discovery_bridge, kind_capability  # type: ignore
            from rally_point import post as post_mod  # type: ignore

        row = agent_ledger.build_row(
            run_id=run_id,
            agent="cursor",
            action="verify",
            model="cursor-agent",
            status="pass",
            ts="2026-08-30T00:00:00Z",
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            home.mkdir()
            repo = root / "ledger-future-kind"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            path = agent_ledger.default_ledger_path(repo)

            envelope = SimpleNamespace(
                backend="rally",
                resolved_via="test",
                transport="rally-cli",
                channel_dir=repo / ".rally",
                app_slug="build-loop",
                raw={"rally_binary": "/fake/rally"},
            )

            def fake_post(**kwargs):
                native_kind, reason = post_mod._negotiated_native_kind(
                    kwargs["kind"], "/fake/rally"
                )
                outcome = kwargs["outcome"]
                outcome.update(backend="rally", transport="rally-cli")
                if native_kind != "artifact":
                    outcome.update(
                        status="rejected",
                        reason=f"unsupported native fact kind: {native_kind}",
                    )
                    return None
                outcome.update(status="posted", revision=1, reason=reason)
                return 1

            patches = [
                mock.patch.object(
                    post_mod, "_static_native_kind", return_value="session.closed"
                ),
                mock.patch.object(
                    kind_capability,
                    "supported_kinds",
                    return_value=(
                        frozenset({"claim", "release", "artifact"}) if gate else None
                    ),
                ),
                mock.patch.object(
                    agent_ledger,
                    "_rally_adapter",
                    return_value=(lambda _workdir: envelope, fake_post),
                ),
                mock.patch.object(
                    agent_ledger,
                    "_native_projection_receipts",
                    return_value=(True, {}, None),
                ),
            ]

            updates = {"HOME": str(home), "BUILD_LOOP_RALLY_TOOL": f"codex:{run_id}"}
            with mock.patch.dict(os.environ, updates, clear=False):
                for key in (
                    "AGENT_RALLY_BINARY",
                    "AGENT_RALLY_DISCOVER",
                    "AGENT_RALLY_APPS_ROOT",
                    "BUILD_LOOP_APPS_ROOT",
                    "BUILD_LOOP_BRIDGE_INTERNAL_ONLY",
                ):
                    os.environ.pop(key, None)
                discovery_bridge.clear_cache()
                kind_capability.clear_cache()
                with contextlib.ExitStack() as stack:
                    for patch in patches:
                        stack.enter_context(patch)
                    env = agent_ledger.append(path, row, workdir=repo)
                discovery_bridge.clear_cache()
                kind_capability.clear_cache()

            # The local append receipt is the authoritative record and must be
            # readable back off disk no matter what the projection decided.
            self.assertTrue(env["ok"], env)
            self.assertIsNone(env["error"], env)
            self.assertEqual(agent_ledger.read(path), [row])
            return env

    def test_future_event_kind_is_rejected_without_the_capability_gate(self) -> None:
        """Pre-fix behavior, reproduced live: the row lands, the projection does not."""
        env = self._project_with_future_kind("future-kind-ungated", gate=False)
        self.assertNotEqual(env["projection"]["status"], "projected")

    def test_future_event_kind_projects_through_the_capability_gate(self) -> None:
        """Post-fix: the unknown kind demotes to artifact and the write lands."""
        env = self._project_with_future_kind("future-kind-gated", gate=True)
        projection = env["projection"]
        self.assertEqual(projection["status"], "projected", projection)
        self.assertEqual(projection["backend"], "rally")
        self.assertEqual(projection["transport"], "rally-cli")
        self.assertIsInstance(projection["revision"], int)
        # Proves the demotion actually fired rather than the test passing
        # vacuously because the forced future kind never reached the wire.
        self.assertIn("session.closed", projection["reason"] or "")
        self.assertIn("artifact", projection["reason"] or "")


class SummarizeTests(unittest.TestCase):
    def test_summarize_aggregates_by_action_status_rung_and_advisor(self) -> None:
        rows = [
            agent_ledger.build_row(run_id="r1", agent="advisor", action="author", model="fable", rung=1, status="pass"),
            agent_ledger.build_row(run_id="r1", agent="implementer", action="execute", model="sonnet", rung=0, status="pass"),
            agent_ledger.build_row(run_id="r1", agent="implementer", action="execute", model="sonnet", rung=0, status="fail"),
            agent_ledger.build_row(run_id="r1", agent="advisor", action="re-plan", model="fable", rung=2, status="pass"),
        ]
        s = agent_ledger.summarize(rows)
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["by_action"]["execute"], 2)
        self.assertEqual(s["by_status"]["pass"], 3)
        self.assertEqual(s["by_status"]["fail"], 1)
        self.assertEqual(s["by_agent_model"]["advisor:fable"], 2)
        self.assertEqual(s["by_rung"]["0"], 2)
        self.assertEqual(s["advisor_invocations"], 2)


class CliTests(unittest.TestCase):
    def test_cli_append_read_summarize(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "ledger.jsonl")
            r = run_cli(
                "--path", path, "append",
                "--run-id", "r1", "--agent", "advisor", "--action", "author",
                "--tier", "frontier", "--model", "fable", "--rung", "1", "--status", "pass",
                "--refs", json.dumps({"output": "docs/plans/x.md"}),
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(json.loads(r.stdout)["ok"])

            r2 = run_cli("--path", path, "read")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            rows = json.loads(r2.stdout)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["refs"]["output"], "docs/plans/x.md")

            r3 = run_cli("--path", path, "summarize")
            self.assertEqual(r3.returncode, 0, r3.stderr)
            self.assertEqual(json.loads(r3.stdout)["advisor_invocations"], 1)

    def test_cli_rejects_bad_action(self) -> None:
        r = run_cli("--path", "/tmp/x.jsonl", "append", "--run-id", "r1", "--agent", "x", "--action", "bogus")
        self.assertNotEqual(r.returncode, 0)

    def test_cli_append_io_failure_is_fail_open(self) -> None:
        # An I/O write failure (a file where the parent dir should be) must exit 0
        # with ok:false — a telemetry outage never wedges the build. Input/caller
        # errors (above) still exit nonzero; only runtime write failures fail open.
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            bad_path = str(blocker / "sub" / "ledger.jsonl")  # parent is a file
            r = run_cli("--path", bad_path, "append", "--run-id", "r1", "--agent", "x", "--action", "author")
            self.assertEqual(r.returncode, 0, "I/O write failure must fail open (exit 0)")
            self.assertFalse(json.loads(r.stdout)["ok"])

    def test_cli_rejects_non_object_refs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = run_cli(
                "--path", str(Path(td) / "l.jsonl"), "append",
                "--run-id", "r1", "--agent", "x", "--action", "author", "--refs", "[1,2,3]",
            )
            self.assertNotEqual(r.returncode, 0, "a non-object refs value must be rejected as a caller error")

    def test_cli_rejects_bad_refs_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = run_cli(
                "--path", str(Path(td) / "l.jsonl"), "append",
                "--run-id", "r1", "--agent", "x", "--action", "author", "--refs", "{not json",
            )
            self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
