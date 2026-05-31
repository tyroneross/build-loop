#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Test suite: MECE validator hook in post.py (R1 C4).

Fixtures:
  1. Well-formed handoff posts succeed (revision advances, record appended).
  2. Handoff missing ownership.owns -> returns None, revision unchanged,
     rejections.jsonl gains 1 line with reason="missing_mece_fields".
  3. Handoff with ownership.interface_contract="" -> returns None,
     rejection logged with reason="empty_required_string".
  4. Non-handoff kinds (phase, feedback, message) skip validation even if
     payload lacks ownership -> succeed.
  5. Validator internal failure does NOT raise; post() returns None
     gracefully.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Allow running from repo root: python3 scripts/test_post_mece_hook.py
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.rally_point.post import post  # noqa: E402


def _good_ownership() -> dict:
    return {
        "owns": ["scripts/rally_point/post.py"],
        "does_not_own": ["scripts/rally_point/rally.py"],
        "interface_contract": "post() returns new revision int on success",
        "integration_checkpoint": "test_post_mece_hook.py exit 0",
        # G2 lateral-limit fields (required by mece_gate since 2026-05-22).
        # Empty lists are valid explicit "no tool boundary" declarations.
        "allowed_tools": [],
        "denied_tools": [],
    }


def _read_changes(channel_dir: Path) -> list[dict]:
    changes_path = channel_dir / "changes.jsonl"
    if not changes_path.exists():
        return []
    lines = [l.strip() for l in changes_path.read_text().splitlines() if l.strip()]
    return [json.loads(l) for l in lines]


def _read_rejections(channel_dir: Path) -> list[dict]:
    rejections_path = channel_dir / "rejections.jsonl"
    if not rejections_path.exists():
        return []
    lines = [l.strip() for l in rejections_path.read_text().splitlines() if l.strip()]
    return [json.loads(l) for l in lines]


def _current_revision(channel_dir: Path) -> int:
    rev_path = channel_dir / "revision"
    if not rev_path.exists():
        return 0
    try:
        return int(rev_path.read_text().strip())
    except ValueError:
        return 0


class TestMECEHook(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.channel = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    # ------------------------------------------------------------------
    # Fixture 1: well-formed handoff succeeds
    # ------------------------------------------------------------------
    def test_valid_handoff_advances_revision(self) -> None:
        rev_before = _current_revision(self.channel)
        result = post(
            channel_dir=self.channel,
            kind="handoff",
            tool="test_tool",
            model="test-model",
            run_id="r1-test",
            app_slug="test-app",
            payload={"ownership": _good_ownership(), "session_id": "s1"},
        )
        self.assertIsNotNone(result, "Valid handoff must return a revision int")
        self.assertGreater(result, rev_before, "Revision must advance on success")
        changes = _read_changes(self.channel)
        self.assertEqual(len(changes), 1, "Exactly one change record expected")
        self.assertEqual(changes[0]["kind"], "handoff")
        rejections = _read_rejections(self.channel)
        self.assertEqual(len(rejections), 0, "No rejections expected for valid handoff")

    # ------------------------------------------------------------------
    # Fixture 2: missing ownership.owns -> rejected
    # ------------------------------------------------------------------
    def test_missing_owns_returns_none(self) -> None:
        ownership = _good_ownership()
        del ownership["owns"]
        rev_before = _current_revision(self.channel)
        result = post(
            channel_dir=self.channel,
            kind="handoff",
            tool="test_tool",
            model="test-model",
            run_id="r1-test",
            app_slug="test-app",
            payload={"ownership": ownership, "session_id": "s2"},
        )
        self.assertIsNone(result, "Missing owns must cause post() to return None")
        self.assertEqual(
            _current_revision(self.channel),
            rev_before,
            "Revision must NOT advance on rejected handoff",
        )
        changes = _read_changes(self.channel)
        self.assertEqual(len(changes), 0, "No change record on rejection")
        rejections = _read_rejections(self.channel)
        self.assertEqual(len(rejections), 1, "Exactly one rejection record expected")
        self.assertEqual(rejections[0]["reason"], "missing_mece_fields")
        self.assertIn("owns", rejections[0]["missing_or_invalid"])

    # ------------------------------------------------------------------
    # Fixture 3: empty interface_contract -> rejected with empty_required_string
    # ------------------------------------------------------------------
    def test_empty_interface_contract_rejected(self) -> None:
        ownership = _good_ownership()
        ownership["interface_contract"] = ""
        rev_before = _current_revision(self.channel)
        result = post(
            channel_dir=self.channel,
            kind="handoff",
            tool="test_tool",
            model="test-model",
            run_id="r1-test",
            app_slug="test-app",
            payload={"ownership": ownership, "session_id": "s3"},
        )
        self.assertIsNone(result)
        self.assertEqual(_current_revision(self.channel), rev_before)
        rejections = _read_rejections(self.channel)
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0]["reason"], "empty_required_string")
        self.assertIn("interface_contract", rejections[0]["missing_or_invalid"])

    # ------------------------------------------------------------------
    # Fixture 4: non-handoff kinds skip MECE validation entirely
    # ------------------------------------------------------------------
    def test_non_handoff_kinds_skip_validation(self) -> None:
        for kind in ("phase", "feedback", "message"):
            with self.subTest(kind=kind):
                channel = Path(self._tmpdir.name) / f"sub_{kind}"
                result = post(
                    channel_dir=channel,
                    kind=kind,
                    tool="test_tool",
                    model="test-model",
                    run_id="r1-test",
                    app_slug="test-app",
                    # Intentionally no ownership field
                    payload={"data": "some value"},
                )
                self.assertIsNotNone(
                    result,
                    f"kind={kind} must succeed without ownership in payload",
                )
                changes = _read_changes(channel)
                self.assertEqual(len(changes), 1)
                rejections = _read_rejections(channel)
                self.assertEqual(len(rejections), 0)

    # ------------------------------------------------------------------
    # Fixture 5: validator internal failure does NOT raise; post returns None
    # ------------------------------------------------------------------
    def test_validator_exception_does_not_propagate(self) -> None:
        with patch(
            "scripts.rally_point.mece_gate.validate_handoff",
            side_effect=RuntimeError("injected failure"),
        ):
            result = post(
                channel_dir=self.channel,
                kind="handoff",
                tool="test_tool",
                model="test-model",
                run_id="r1-test",
                app_slug="test-app",
                payload={"ownership": _good_ownership()},
            )
        # post() outer try/except swallows the RuntimeError -> None
        self.assertIsNone(result, "Validator exception must not propagate; post returns None")


if __name__ == "__main__":
    unittest.main(verbosity=2)
