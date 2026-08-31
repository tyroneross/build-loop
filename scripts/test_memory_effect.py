#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_effect.py -- the read->effect loop closer.

These are planted-case tests, not smoke tests. Each one plants a specific
condition and asserts the emitter reaches the RIGHT verdict, including the
negatives it must refuse to label.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import memory_effect as me  # noqa: E402

MEM_ID = "decision-project-build-loop-use-session-pooler-for-migrations-20260524-001"
OTHER_ID = "lesson-project-build-loop-verify-the-instrument-first-20260601-002"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, check=True)
    return proc.stdout


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MemoryEffectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        self.store = root / "store"
        (self.store / "indexes").mkdir(parents=True)
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "T")
        # base commit so HEAD~1 exists
        (self.repo / "a.txt").write_text("base\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-q", "-m", "base")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def _telemetry(self) -> Path:
        return self.store / "indexes" / "TELEMETRY.jsonl"

    def _plant_read(self, *, ids: list[str], epoch: float, cid: str) -> None:
        row = {
            "ts": _iso(epoch), "kind": "memory-read", "schema_version": "1.1",
            "correlation_id": cid, "phase": "test",
            "reader_or_writer": "memory_facade.recall", "query": "pooler",
            "memory_ids_seen": ids, "memory_ids_used": [], "effect": None,
            "reason": "", "source": "test",
        }
        with self._telemetry().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    def _commit(self, *, body: str, message: str) -> str:
        (self.repo / "b.txt").write_text(body)
        _git(self.repo, "add", "b.txt")
        _git(self.repo, "commit", "-q", "-m", message)
        return _git(self.repo, "rev-parse", "HEAD").strip()

    def _rows(self, kind: str) -> list[dict]:
        p = self._telemetry()
        if not p.is_file():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("kind") == kind:
                out.append(row)
        return out

    def _run(self, **kw) -> dict:
        sha = kw.pop("sha")
        return me.analyze_commit(self.repo, self.store, sha,
                                 emit=kw.pop("emit", True),
                                 telemetry_path=self._telemetry(),
                                 source="test",
                                 project=kw.pop("project", ""))

    # -- planted cases ---------------------------------------------------
    def test_referenced_memory_emits_use_row(self):
        """PLANTED POSITIVE: commit message cites a surfaced id -> use row."""
        sha = self._commit(body="x\n", message=f"fix migration per {MEM_ID}")
        start, end = me.commit_window(self.repo, sha)
        self._plant_read(ids=[MEM_ID], epoch=(start + end) / 2, cid="mt-aaa")

        res = self._run(sha=sha)
        self.assertEqual(res["memories_surfaced"], 1)
        self.assertEqual(res["memories_referenced"], 1)
        self.assertEqual(res["use_rows_emitted"], 1)

        uses = self._rows("memory-use")
        self.assertEqual(len(uses), 1)
        self.assertEqual(uses[0]["correlation_id"], "mt-aaa")
        self.assertEqual(uses[0]["memory_ids_used"], [MEM_ID])
        self.assertEqual(uses[0]["effect"], "informed_decision")
        self.assertIn(sha[:12], uses[0]["reason"])

    def test_unreferenced_memory_emits_nothing_and_is_never_labelled_ignored(self):
        """PLANTED NEGATIVE: surfaced but not cited -> denominator only.

        This is the defect the design exists to avoid. Auto-labelling silence as
        'ignored' would poison the dataset, so assert no row of ANY kind.
        """
        sha = self._commit(body="x\n", message="unrelated refactor")
        start, end = me.commit_window(self.repo, sha)
        self._plant_read(ids=[MEM_ID], epoch=(start + end) / 2, cid="mt-bbb")

        res = self._run(sha=sha)
        self.assertEqual(res["memories_surfaced"], 1)
        self.assertEqual(res["memories_referenced"], 0)
        self.assertEqual(res["use_rows_emitted"], 0)
        self.assertEqual(self._rows("memory-use"), [])
        self.assertEqual(self._rows("memory-effect"), [])

    def test_only_the_referenced_id_is_credited(self):
        """Two surfaced, one cited -> exactly one credited, not both."""
        sha = self._commit(body="x\n", message=f"see {MEM_ID}")
        start, end = me.commit_window(self.repo, sha)
        self._plant_read(ids=[MEM_ID, OTHER_ID], epoch=(start + end) / 2, cid="mt-ccc")

        res = self._run(sha=sha)
        self.assertEqual(res["memories_surfaced"], 2)
        self.assertEqual(res["memories_referenced"], 1)
        self.assertEqual(self._rows("memory-use")[0]["memory_ids_used"], [MEM_ID])

    def test_removed_line_does_not_count_as_use(self):
        """A memory id deleted by the commit is not evidence the work used it."""
        # first land the id in the file
        self._commit(body=f"{MEM_ID}\nkeep\n", message="seed")
        # then remove it
        sha = self._commit(body="keep\n", message="drop the reference")
        start, end = me.commit_window(self.repo, sha)
        self._plant_read(ids=[MEM_ID], epoch=(start + end) / 2, cid="mt-ddd")

        res = self._run(sha=sha)
        self.assertEqual(res["memories_referenced"], 0)
        self.assertEqual(self._rows("memory-use"), [])

    def test_added_line_in_diff_counts_as_use(self):
        """Citing the id in the DIFF (not the message) still counts."""
        sha = self._commit(body=f"# per {MEM_ID}\n", message="add guard")
        start, end = me.commit_window(self.repo, sha)
        self._plant_read(ids=[MEM_ID], epoch=(start + end) / 2, cid="mt-eee")

        res = self._run(sha=sha)
        self.assertEqual(res["memories_referenced"], 1)

    def test_idempotent_across_reruns(self):
        """Re-running must not double-credit the same memory."""
        sha = self._commit(body="x\n", message=f"fix per {MEM_ID}")
        start, end = me.commit_window(self.repo, sha)
        self._plant_read(ids=[MEM_ID], epoch=(start + end) / 2, cid="mt-fff")

        self._run(sha=sha)
        self._run(sha=sha)
        self.assertEqual(len(self._rows("memory-use")), 1)

    def test_read_outside_the_commit_window_is_not_counted(self):
        """A read from long before the parent commit did not feed this work."""
        sha = self._commit(body="x\n", message=f"fix per {MEM_ID}")
        start, _end = me.commit_window(self.repo, sha)
        self._plant_read(ids=[MEM_ID], epoch=start - 10_000, cid="mt-ggg")

        res = self._run(sha=sha)
        self.assertEqual(res["reads_in_window"], 0)
        self.assertEqual(res["memories_referenced"], 0)

    def test_short_ids_are_not_matched(self):
        """Guard: a short/generic id must not match incidental prose."""
        sha = self._commit(body="x\n", message="this is a test of the system")
        start, end = me.commit_window(self.repo, sha)
        self._plant_read(ids=["test"], epoch=(start + end) / 2, cid="mt-hhh")

        res = self._run(sha=sha)
        self.assertEqual(res["memories_referenced"], 0)

    def test_dry_run_reports_without_writing(self):
        sha = self._commit(body="x\n", message=f"fix per {MEM_ID}")
        start, end = me.commit_window(self.repo, sha)
        self._plant_read(ids=[MEM_ID], epoch=(start + end) / 2, cid="mt-iii")

        res = self._run(sha=sha, emit=False)
        self.assertEqual(res["memories_referenced"], 1)
        self.assertEqual(res["use_rows_emitted"], 0)
        self.assertEqual(self._rows("memory-use"), [])


    # -- project scoping -------------------------------------------------
    def test_project_filter_excludes_another_workstreams_reads(self):
        """PLANTED: the real failure found on build-loop-memory HEAD~15..HEAD.

        A concurrent workstream's reads land in the same lane and inflate the
        denominator, driving the reference rate to 0. Scoping must drop them
        from the denominator rather than count them as un-referenced.
        """
        sha = self._commit(body="x\n", message=f"fix per {MEM_ID}")
        start, end = me.commit_window(self.repo, sha)
        mid = (start + end) / 2
        self._plant_read(ids=[MEM_ID], epoch=mid, cid="mt-mine")
        self._plant_read(ids=["decision-project-other-service-unrelated-thing-20260801-009"],
                         epoch=mid, cid="mt-theirs")

        unscoped = self._run(sha=sha)
        self.assertEqual(unscoped["memories_surfaced"], 2)
        self.assertEqual(unscoped["memories_referenced"], 1)
        self.assertEqual(unscoped["reference_rate"], 0.5)

        scoped = self._run(sha=sha, project="build-loop")
        self.assertEqual(scoped["memories_surfaced"], 1)
        self.assertEqual(scoped["memories_referenced"], 1)
        self.assertEqual(scoped["reference_rate"], 1.0)

    def test_unscoped_denominator_is_always_reported(self):
        """The filter must never be able to hide what it excluded."""
        sha = self._commit(body="x\n", message=f"fix per {MEM_ID}")
        start, end = me.commit_window(self.repo, sha)
        mid = (start + end) / 2
        self._plant_read(ids=[MEM_ID], epoch=mid, cid="mt-a")
        self._plant_read(ids=["decision-project-other-service-unrelated-thing-20260801-009"],
                         epoch=mid, cid="mt-b")

        scoped = self._run(sha=sha, project="build-loop")
        self.assertEqual(scoped["memories_surfaced"], 1)
        self.assertEqual(scoped["memories_surfaced_unscoped"], 2,
                         "excluded reads must remain visible in the report")
        self.assertEqual(scoped["reads_in_window_unscoped"], 2)

    def test_project_filter_matches_returned_paths_too(self):
        sha = self._commit(body="x\n", message="unrelated")
        start, end = me.commit_window(self.repo, sha)
        row_epoch = (start + end) / 2
        import json as _json
        with self._telemetry().open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps({
                "ts": _iso(row_epoch), "kind": "memory-read", "schema_version": "1.1",
                "correlation_id": "mt-path", "phase": "t",
                "reader_or_writer": "memory_locator", "query": "q",
                "memory_ids_seen": ["some-generic-id-000001"],
                "returned_paths": ["projects/build-loop/lessons/x.md"],
                "memory_ids_used": [], "effect": None, "reason": "", "source": "test",
            }) + "\n")
        self.assertEqual(self._run(sha=sha, project="build-loop")["reads_in_window"], 1)
        self.assertEqual(self._run(sha=sha, project="other-service")["reads_in_window"], 0)

    def test_empty_project_disables_filtering(self):
        sha = self._commit(body="x\n", message="unrelated")
        start, end = me.commit_window(self.repo, sha)
        self._plant_read(ids=["decision-project-other-service-thing-20260801-009"],
                         epoch=(start + end) / 2, cid="mt-z")
        self.assertEqual(self._run(sha=sha, project="")["reads_in_window"], 1)


    def test_shared_ledger_does_not_double_credit_across_commits(self):
        """A cached ledger is a pre-write snapshot; it must still dedupe."""
        sha1 = self._commit(body="x\n", message=f"first per {MEM_ID}")
        sha2 = self._commit(body="y\n", message=f"second per {MEM_ID}")
        s1, e1 = me.commit_window(self.repo, sha1)
        s2, e2 = me.commit_window(self.repo, sha2)
        # one read visible to BOTH commit windows
        self._plant_read(ids=[MEM_ID], epoch=max(s2, (s1 + e1) / 2), cid="mt-shared")

        led = me.Ledger(self.store)
        for sha in (sha1, sha2):
            me.analyze_commit(self.repo, self.store, sha, emit=True,
                              telemetry_path=self._telemetry(), source="test",
                              project="", ledger=led)
        self.assertEqual(len(self._rows("memory-use")), 1,
                         "the same correlation_id must be credited at most once")


if __name__ == "__main__":
    unittest.main(verbosity=2)
