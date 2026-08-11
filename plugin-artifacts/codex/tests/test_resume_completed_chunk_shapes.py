#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Regression: resume_resolver must survive every completed_chunks shape on disk.

Named, observed failure this earns its place against (2026-08-11): resuming a
build in a repo whose `.build-loop/state.json` stored `completed_chunks` as bare
id strings raised

    AttributeError: 'str' object has no attribute 'get'

out of `_detect_concurrent_modifications`, so `/build-loop:run --resume` exited 1
with a traceback instead of a decision envelope. Two shapes were found live on
one machine the same day:

    local-smartz        -> "c1"
    decision-doctor-cc  -> {"id": "C1", "sha": "ace61d5", "status": "completed"}

Neither matches the canonical writer, which emits
`{"chunk_id", "status", "completed_at"}` with status in EXECUTION_RETURN_STATUSES.
The legacy dict did not crash — it silently failed the `status == "fixed"` test,
so the concurrent-modification check was skipped without saying so. Both are
pinned here: the string shape must not crash, and the legacy shape must warn.
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import resume_resolver  # noqa: E402


def _write_state(root: Path, completed: list, *, stale_minutes: int = 30) -> Path:
    heartbeat = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    state = {
        "execution": {
            "schema_version": resume_resolver.EXPECTED_SCHEMA_VERSION,
            "run_id": "bl-shape-0001",
            "phase": "execute",
            "last_heartbeat_at": heartbeat.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "queued_chunks": ["c3"],
            "in_flight_chunks": ["c2"],
            "completed_chunks": completed,
            "file_ownership": {"c1": ["a.py"], "c2": ["b.py"], "c3": ["c.py"]},
        }
    }
    (root / ".build-loop").mkdir(parents=True, exist_ok=True)
    path = root / ".build-loop" / "state.json"
    path.write_text(json.dumps(state, indent=2))
    return path


class CompletedChunkShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_bare_id_strings_do_not_crash(self) -> None:
        """local-smartz shape. Must return an envelope, never raise."""
        _write_state(self.root, ["c1"])
        env = resume_resolver.resolve(self.root, "bl-shape-0001")
        self.assertEqual(env["decision"], "resume")
        self.assertTrue(
            any("bare id" in w for w in env["state_warnings"]),
            f"expected a bare-id warning, got {env['state_warnings']!r}",
        )

    def test_legacy_id_key_is_read_not_ignored(self) -> None:
        """decision-doctor-cc shape: 'id' instead of 'chunk_id'."""
        _write_state(self.root, [{"id": "C1", "sha": "ace61d5", "status": "completed"}])
        env = resume_resolver.resolve(self.root, "bl-shape-0001")
        self.assertEqual(env["decision"], "resume")
        self.assertTrue(
            any("outside the canonical return statuses" in w for w in env["state_warnings"]),
            f"expected a non-canonical status warning, got {env['state_warnings']!r}",
        )

    def test_canonical_shape_produces_no_warnings(self) -> None:
        """The writer's own shape must stay warning-free, or the signal is noise."""
        _write_state(self.root, [{
            "chunk_id": "c1",
            "status": "fixed",
            "completed_at": "2026-08-11T00:00:00Z",
        }])
        env = resume_resolver.resolve(self.root, "bl-shape-0001")
        self.assertEqual(env["decision"], "resume")
        self.assertEqual(env["state_warnings"], [])

    def test_remaining_still_computed_across_shapes(self) -> None:
        """A degraded completed_chunks read must not drop queued/in-flight work."""
        _write_state(self.root, ["c1"])
        env = resume_resolver.resolve(self.root, "bl-shape-0001")
        ids = sorted(c["chunk_id"] for c in env["remaining_chunks"])
        self.assertEqual(ids, ["c2", "c3"])

    def test_mixed_shapes_in_one_list(self) -> None:
        """Nothing guarantees a single shape per file once both exist in the wild."""
        _write_state(self.root, [
            "c0",
            {"id": "C1", "status": "completed"},
            {"chunk_id": "c1b", "status": "fixed", "completed_at": "2026-08-11T00:00:00Z"},
        ])
        env = resume_resolver.resolve(self.root, "bl-shape-0001")
        self.assertEqual(env["decision"], "resume")
        self.assertEqual(len(env["state_warnings"]), 2)


class SchemaCheckOrderingTests(unittest.TestCase):
    """The no-argument path must not recommend a command the --resume path refuses."""

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_advice_is_not_offered_when_schema_is_incompatible(self) -> None:
        path = _write_state(self.root, [])
        state = json.loads(path.read_text())
        del state["execution"]["schema_version"]
        path.write_text(json.dumps(state, indent=2))

        no_arg = resume_resolver.resolve(self.root, "")
        self.assertEqual(no_arg["decision"], "abort")
        self.assertNotIn("resume with --resume", no_arg["reason"])

    def test_advice_that_is_offered_actually_works(self) -> None:
        """Whatever run-id prompt_user names must resolve on the --resume path."""
        _write_state(self.root, [])
        prompt = resume_resolver.resolve(self.root, "")
        self.assertEqual(prompt["decision"], "prompt_user")

        followed = resume_resolver.resolve(self.root, prompt["run_id"])
        self.assertEqual(
            followed["decision"], "resume",
            f"following the tool's own advice returned {followed['decision']}: {followed['reason']}",
        )


if __name__ == "__main__":
    unittest.main()
