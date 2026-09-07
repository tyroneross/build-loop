# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""No-regrets continuation requires an explicit standing preference."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import context_bootstrap as cb  # noqa: E402


class DefaultFlipTests(unittest.TestCase):
    """F5 acceptance: unset → False; "never" → False (opt-out); explicit "ask" → False."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workdir = Path(self.tmp.name)

    # ----- the flip -----

    def test_fresh_repo_unset_returns_false(self) -> None:
        """A repo with no .build-loop directory at all → no continuation."""
        self.assertFalse(cb.should_continue_into_queues(self.workdir))

    def test_empty_state_json_returns_false(self) -> None:
        """state.json exists but has no session_prefs key → source='default' → False."""
        bl = self.workdir / ".build-loop"
        bl.mkdir()
        (bl / "state.json").write_text(json.dumps({"runs": []}), encoding="utf-8")
        self.assertFalse(cb.should_continue_into_queues(self.workdir))

    # ----- opt-out preserved -----

    def test_explicit_never_returns_false(self) -> None:
        cb.write_session_prefs(self.workdir, "never", source="asked")
        self.assertFalse(cb.should_continue_into_queues(self.workdir))

    def test_config_override_never_returns_false(self) -> None:
        bl = self.workdir / ".build-loop"
        bl.mkdir()
        (bl / "config.json").write_text(
            json.dumps({"sessionPrefs": {"continueFromQueues": "never"}}), encoding="utf-8"
        )
        self.assertFalse(cb.should_continue_into_queues(self.workdir))

    # ----- explicit ask still respected (legacy opt-in path) -----

    def test_explicit_ask_returns_false(self) -> None:
        """A user who explicitly answered 'ask' once still gets the question
        (source='asked' → not the unset default → no auto-drain)."""
        cb.write_session_prefs(self.workdir, "ask", source="asked")
        self.assertFalse(cb.should_continue_into_queues(self.workdir))

    def test_config_override_ask_returns_false(self) -> None:
        bl = self.workdir / ".build-loop"
        bl.mkdir()
        (bl / "config.json").write_text(
            json.dumps({"sessionPrefs": {"continueFromQueues": "ask"}}), encoding="utf-8"
        )
        self.assertFalse(cb.should_continue_into_queues(self.workdir))

    # ----- explicit always still True -----

    def test_explicit_always_returns_true(self) -> None:
        cb.write_session_prefs(self.workdir, "always", source="asked")
        self.assertTrue(cb.should_continue_into_queues(self.workdir))


if __name__ == "__main__":
    unittest.main(verbosity=2)
