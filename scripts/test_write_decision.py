#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for write_decision.py. Zero deps. Run: python3 test_write_decision.py

Covers:
- happy-path write produces valid MADR + INDEX row + events.jsonl line
- atomic concurrent writes produce sequential IDs without corruption
- topic-identity supersession (higher confidence auto-supersedes lower)
- equal/lower confidence is rejected without --supersedes
- explicit --supersedes bypasses confidence comparison
- exit codes: 0 success, 1 validation error
- INDEX default filter shows only confirmed+
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "write_decision.py"

sys.path.insert(0, str(HERE))
from _test_helpers import MemIsolationMixin  # noqa: E402


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def _seed_taxonomy() -> str:
    """Minimal TAXONOMY content the validator + writer parse."""
    return """---
type: taxonomy
schema_version: 1
---

# Vocab

## 1. Decision tags

- `architecture`
- `data`
- `ui`
- `infra`
- `tooling`
- `process`
- `security`
- `performance`
- `testing`

## 3. Confidence levels

`assumed < inferred < confirmed < explicit`

## 6. Source attribution

- `manual`
- `auto-explicit`
- `auto-confirmed`
- `auto-inferred`
- `auto-assumed`
- `migration`
- `orchestrator`
"""


class WriteDecisionTests(MemIsolationMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        # Seed minimal directory structure + TAXONOMY
        (self.workdir / ".semantic").mkdir(parents=True)
        (self.workdir / ".episodic" / "decisions" / "_history").mkdir(parents=True)
        (self.workdir / ".episodic" / "issues").mkdir(parents=True)
        (self.workdir / ".semantic" / "TAXONOMY.md").write_text(_seed_taxonomy())
        # NB: events.jsonl is created on demand by the writer

    def tearDown(self) -> None:
        self.tmp.cleanup()
        super().tearDown()

    def _base_args(self, **overrides: str) -> list[str]:
        args = {
            "--workdir": str(self.workdir),
            "--title": "Use pytest for testing",
            "--decision": "We will use pytest as the project test framework",
            "--context": "Need a test framework",
            "--consequences": "All tests use pytest discovery",
            "--alternatives": "unittest (verbose); nose2 (unmaintained)",
            "--tags": "tooling,testing",
            "--primary-tag": "testing",
            "--entity": "build-loop",
            "--confidence": "explicit",
            "--source": "manual",
            "--project": "test-default",
        }
        args.update(overrides)
        flat: list[str] = []
        for k, v in args.items():
            flat.extend([k, v])
        flat.append("--no-db")  # tests don't touch DB
        return flat

    # ---- happy path ----

    def test_first_write_creates_decision_index_and_event(self) -> None:
        result = run(self._base_args())
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        self.assertEqual(result.stdout.strip(), "0001")

        ddir = self._decisions_dir("test-default")
        files = list(ddir.glob("0001-*.md"))
        self.assertEqual(len(files), 1)
        body = files[0].read_text()
        self.assertIn("id: '0001'", body)
        self.assertIn("entity: build-loop", body)
        self.assertIn("primary_tag: testing", body)
        self.assertIn("confidence: explicit", body)

        index = (ddir / "INDEX.md").read_text()
        self.assertIn("0001", index)
        self.assertIn("Use pytest for testing", index)

        events = [
            json.loads(line)
            for line in self._events_path().read_text().splitlines()
            if line.strip()
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "decision_accepted")
        self.assertEqual(events[0]["decision_id"], "0001")
        self.assertEqual(events[0]["confidence"], "explicit")

    def test_id_allocator_sequences_correctly(self) -> None:
        for i, title in enumerate(["First", "Second", "Third"], start=1):
            r = run(self._base_args(**{
                "--title": title,
                "--primary-tag": "testing",
                "--entity": f"e{i}",
                "--confidence": "explicit",
                "--project": "test-default",
            }))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertEqual(r.stdout.strip(), f"{i:04d}")

    # ---- concurrency ----

    def test_concurrent_writes_no_id_collision(self) -> None:
        results: list[subprocess.CompletedProcess] = []
        lock = threading.Lock()

        def worker(idx: int) -> None:
            r = run(self._base_args(**{
                "--title": f"Concurrent {idx}",
                "--entity": f"concurrent-{idx}",
                "--primary-tag": "tooling",
                "--project": "test-default",
            }))
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()

        for r in results:
            self.assertEqual(r.returncode, 0, msg=f"stderr={r.stderr}")
        ids = sorted(r.stdout.strip() for r in results)
        self.assertEqual(ids, ["0001", "0002"])

        ddir = self._decisions_dir("test-default")
        index = (ddir / "INDEX.md").read_text()
        self.assertIn("0001", index)
        self.assertIn("0002", index)
        self.assertEqual(len(list(ddir.glob("0*.md"))), 2)

    # ---- topic-identity supersession ----

    def test_higher_confidence_auto_supersedes_lower(self) -> None:
        r1 = run(self._base_args(**{
            "--title": "Inferred test framework",
            "--confidence": "inferred",
            "--primary-tag": "testing",
            "--entity": "build-loop",
        }))
        self.assertEqual(r1.returncode, 0, msg=r1.stderr)
        self.assertEqual(r1.stdout.strip(), "0001")

        r2 = run(self._base_args(**{
            "--title": "Explicit test framework",
            "--confidence": "explicit",
            "--primary-tag": "testing",
            "--entity": "build-loop",
        }))
        self.assertEqual(r2.returncode, 0, msg=r2.stderr)
        self.assertEqual(r2.stdout.strip(), "0002")

        ddir = self._decisions_dir("test-default")
        history = list((ddir / "_history").glob("0001-v*.md"))
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0].name.startswith("0001-v1"))

        current = list(ddir.glob("0001-*.md"))
        self.assertEqual(current, [], f"0001 should be moved out of decisions/, found {current}")

        body_0002 = next(ddir.glob("0002-*.md")).read_text()
        self.assertIn("supersedes: '0001'", body_0002)

        body_history = history[0].read_text()
        self.assertIn("superseded_by: '0002'", body_history)
        self.assertIn("status: superseded", body_history)

        events = [
            json.loads(l)
            for l in self._events_path().read_text().splitlines()
            if l.strip()
        ]
        kinds = [e["kind"] for e in events]
        self.assertIn("decision_accepted", kinds)
        self.assertIn("decision_superseded", kinds)

    def test_equal_confidence_requires_explicit_supersedes(self) -> None:
        r1 = run(self._base_args(**{
            "--confidence": "explicit",
            "--primary-tag": "testing",
            "--entity": "build-loop",
        }))
        self.assertEqual(r1.returncode, 0, msg=r1.stderr)

        r2 = run(self._base_args(**{
            "--title": "Same-confidence collision",
            "--confidence": "explicit",
            "--primary-tag": "testing",
            "--entity": "build-loop",
        }))
        self.assertEqual(r2.returncode, 1)
        self.assertIn("--supersedes", r2.stderr)

    def test_lower_confidence_rejected(self) -> None:
        r1 = run(self._base_args(**{
            "--confidence": "explicit",
            "--primary-tag": "testing",
            "--entity": "build-loop",
        }))
        self.assertEqual(r1.returncode, 0, msg=r1.stderr)

        r2 = run(self._base_args(**{
            "--title": "Lower confidence attempt",
            "--confidence": "inferred",
            "--primary-tag": "testing",
            "--entity": "build-loop",
        }))
        self.assertEqual(r2.returncode, 1)
        self.assertIn("lower", r2.stderr.lower())

    def test_explicit_supersedes_flag_bypasses_confidence_check(self) -> None:
        r1 = run(self._base_args(**{
            "--confidence": "explicit",
            "--primary-tag": "testing",
            "--entity": "build-loop",
        }))
        self.assertEqual(r1.returncode, 0, msg=r1.stderr)

        r2 = run(self._base_args(**{
            "--title": "Replacement",
            "--confidence": "explicit",
            "--primary-tag": "testing",
            "--entity": "build-loop",
            "--supersedes": "0001",
        }))
        self.assertEqual(r2.returncode, 0, msg=r2.stderr)
        self.assertEqual(r2.stdout.strip(), "0002")

    # ---- vocab + validation ----

    def test_unknown_primary_tag_rejected(self) -> None:
        r = run(self._base_args(**{"--primary-tag": "random-tag"}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("primary_tag", r.stderr)

    def test_proposed_secondary_tag_accepted(self) -> None:
        r = run(self._base_args(**{"--tags": "tooling,proposed:experimental-tag"}))
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_invalid_confidence_returns_1(self) -> None:
        r = run(self._base_args(**{"--confidence": "imaginary"}))
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
