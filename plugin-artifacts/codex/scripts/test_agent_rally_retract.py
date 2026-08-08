#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``scripts/agent_rally.py retract`` — end-to-end fact withdrawal."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rally_point import changes, channel_paths, retraction  # noqa: E402

CLI = HERE / "agent_rally.py"


class AgentRallyRetractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-rally-retract-"))
        self.apps = self.tmp / "apps"
        self.workdir = self.tmp / "repo"
        self.workdir.mkdir()
        self._old_apps = os.environ.get("BUILD_LOOP_APPS_ROOT")
        self._old_internal = os.environ.get("BUILD_LOOP_BRIDGE_INTERNAL_ONLY")
        os.environ["BUILD_LOOP_APPS_ROOT"] = str(self.apps)
        os.environ["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = "1"
        subprocess.run(
            ["git", "init", "-q", str(self.workdir)], check=True, capture_output=True
        )
        self._clear_cache()
        self.slug = channel_paths.app_slug(self.workdir)
        self.channel = channel_paths.ensure_channel_dir(self.slug)

    def tearDown(self) -> None:
        for key, old in (
            ("BUILD_LOOP_APPS_ROOT", self._old_apps),
            ("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", self._old_internal),
        ):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self._clear_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _clear_cache() -> None:
        try:
            from rally_point.discovery_bridge import clear_cache

            clear_cache()
        except Exception:
            pass

    # -- helpers ----------------------------------------------------------

    def _seed(self, event_id: str, *, kind: str = "decision", revision: int = 1) -> None:
        record = changes.make_record(
            kind=kind, tool="codex", model="m", run_id="r",
            app_slug=self.slug, payload={"subject": event_id}, revision=revision,
        )
        record["event_id"] = event_id
        changes.append_change(self.channel, record)

    def _retract(self, *extra: str, expect: int | None = 0) -> dict:
        proc = subprocess.run(
            [
                sys.executable, str(CLI), "retract",
                "--workdir", str(self.workdir),
                "--session-id", "me",
                "--tool", "claude_code",
                *extra,
            ],
            capture_output=True, text=True, env=os.environ.copy(),
        )
        if expect is not None:
            self.assertEqual(proc.returncode, expect, proc.stderr or proc.stdout)
        return json.loads(proc.stdout)

    def _visible(self) -> list[str]:
        records, _off = changes.read_changes_since(self.channel, 0)
        return [r.get("event_id") for r in records]

    # -- tests ------------------------------------------------------------

    def test_retract_hides_the_fact_from_every_reader(self) -> None:
        self._seed("fact_bad")
        self._seed("fact_good", revision=2)

        out = self._retract("--fact", "fact_bad", "--reason", "posted in error")
        self.assertEqual(out["action"], "retracted")
        self.assertTrue(out["accepted"])
        self.assertTrue(out["target_found"])

        visible = self._visible()
        self.assertNotIn("fact_bad", visible)
        self.assertIn("fact_good", visible)

    def test_retraction_record_survives_and_carries_the_reason(self) -> None:
        self._seed("fact_bad")
        self._retract("--fact", "fact_bad", "--reason", "wrong sha")

        records, _off = changes.read_changes_since(self.channel, 0)
        retractions = [r for r in records if retraction.is_retraction(r)]
        self.assertEqual(len(retractions), 1)
        self.assertEqual(retraction.target_of(retractions[0]), "fact_bad")
        self.assertIn("wrong sha", json.dumps(retractions[0]["payload"]))

    def test_log_is_never_mutated(self) -> None:
        self._seed("fact_bad")
        before = (self.channel / "changes.jsonl").read_text(encoding="utf-8")
        self._retract("--fact", "fact_bad", "--reason", "oops")
        after = (self.channel / "changes.jsonl").read_text(encoding="utf-8")
        self.assertTrue(after.startswith(before), "existing lines were rewritten")

    def test_superseded_by_is_recorded_and_resolved(self) -> None:
        self._seed("fact_bad")
        self._seed("fact_fixed", revision=2)
        out = self._retract(
            "--fact", "fact_bad", "--reason", "corrected",
            "--superseded-by", "fact_fixed",
        )
        self.assertEqual(out["superseded_by"], "fact_fixed")
        self.assertTrue(out["superseded_by_found"])

        records, _off = changes.read_changes_since(self.channel, 0)
        idx = retraction.index(records)
        self.assertEqual(idx["fact_bad"]["superseded_by"], "fact_fixed")

    def test_unknown_target_is_rejected_not_silently_posted(self) -> None:
        out = self._retract("--fact", "fact_typo", "--reason", "x", expect=1)
        self.assertEqual(out["action"], "retract-target-not-found")
        self.assertFalse(out["accepted"])
        self.assertEqual(self._visible(), [])

    def test_force_posts_a_retraction_for_an_unreadable_target(self) -> None:
        out = self._retract("--fact", "fact_elsewhere", "--reason", "x", "--force")
        self.assertEqual(out["action"], "retracted")
        self.assertFalse(out["target_found"])

    def test_double_retract_is_a_reported_noop(self) -> None:
        self._seed("fact_bad")
        self._retract("--fact", "fact_bad", "--reason", "first")
        out = self._retract("--fact", "fact_bad", "--reason", "again", expect=1)
        self.assertEqual(out["action"], "retract-noop")
        self.assertIn("already retracted", out["detail"])

    def test_retracting_a_retraction_is_refused(self) -> None:
        self._seed("fact_bad")
        self._retract("--fact", "fact_bad", "--reason", "first")
        raw, _off = changes.read_changes_since(
            self.channel, 0, resolve_retractions=False
        )
        retraction_id = [
            r["event_id"] for r in raw if retraction.is_retraction(r)
        ][0]
        out = self._retract("--fact", retraction_id, "--reason", "undo", expect=1)
        self.assertEqual(out["action"], "retract-refused")


if __name__ == "__main__":
    unittest.main()
