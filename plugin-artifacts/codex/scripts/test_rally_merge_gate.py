#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for rally_merge_gate.py — the pre-merge conflict gate."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "rally_merge_gate.py"
sys.path.insert(0, str(HERE))

from rally_merge_gate import claim_paths, others_claims, overlaps  # type: ignore  # noqa: E402


def _claim(tool: str, paths: list[str], subject: str = "") -> dict:
    return {"tool": tool, "subject": subject,
            "evidence": [f"claimhash:{p}=deadbeef{i}" for i, p in enumerate(paths)]}


def _room(claims: list[dict]) -> dict:
    return {"data": {"room": {"active_claims": claims}}}


def run_cli(room: dict | None, diff: list[str] | None, *args: str) -> subprocess.CompletedProcess[str]:
    extra: list[str] = []
    if room is not None:
        rf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(room, rf); rf.close()
        extra += ["--room-json", rf.name]
    if diff is not None:
        df = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        df.write("\n".join(diff)); df.close()
        extra += ["--diff-files", df.name]
    return subprocess.run([sys.executable, str(SCRIPT), *args, *extra],
                          check=False, capture_output=True, text=True)


class PureCoreTests(unittest.TestCase):
    def test_claim_paths_extracts_from_evidence(self) -> None:
        c = _claim("codex", ["scripts/a.py", "scripts/b.py"])
        self.assertEqual(claim_paths(c), {"scripts/a.py", "scripts/b.py"})

    def test_claim_paths_empty_for_no_path_claim(self) -> None:
        self.assertEqual(claim_paths({"tool": "codex", "evidence": ["note:broad audit"]}), set())

    def test_others_claims_excludes_self_and_bad_entries(self) -> None:
        claims = [_claim("me", ["x"]), _claim("codex", ["y"]), {"no_tool": 1}, "junk"]
        got = others_claims(claims, "me")
        self.assertEqual([c["tool"] for c in got], ["codex"])

    def test_overlaps_only_nonempty_intersections(self) -> None:
        claims = [_claim("codex", ["scripts/a.py"]), _claim("gemini", ["scripts/z.py"])]
        hits = overlaps({"scripts/a.py"}, claims, "me")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["tool"], "codex")
        self.assertEqual(hits[0]["overlap"], ["scripts/a.py"])


class CheckCliTests(unittest.TestCase):
    def test_clean_merge_passes(self) -> None:
        room = _room([_claim("codex", ["scripts/z.py"])])
        r = run_cli(room, ["scripts/a.py"], "check", "--tool", "me")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(json.loads(r.stdout)["gated"])

    def test_overlap_with_other_tool_warns(self) -> None:
        room = _room([_claim("codex", ["scripts/a.py"], subject="codex building a")])
        r = run_cli(room, ["scripts/a.py", "scripts/b.py"], "check", "--tool", "me")
        self.assertEqual(r.returncode, 3, r.stdout)
        env = json.loads(r.stdout)
        self.assertTrue(env["gated"])
        self.assertEqual(env["overlaps"][0]["tool"], "codex")
        self.assertEqual(env["overlaps"][0]["overlap"], ["scripts/a.py"])

    def test_self_claim_ignored(self) -> None:
        room = _room([_claim("me", ["scripts/a.py"])])  # the merger's own claim
        r = run_cli(room, ["scripts/a.py"], "check", "--tool", "me")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(json.loads(r.stdout)["gated"])

    def test_no_changed_files_passes(self) -> None:
        r = run_cli(_room([]), [], "check", "--tool", "me")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("no changed files", json.loads(r.stdout).get("note", ""))

    def test_rally_outage_fails_open(self) -> None:
        # --room-json pointing at a missing file → fetch error → exit 0 + warning
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "check", "--tool", "me",
             "--diff-files", "-", "--room-json", "/nonexistent/room.json"],
            input="scripts/a.py\n", check=False, capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout)
        env = json.loads(r.stdout)
        self.assertFalse(env["gated"])
        self.assertIn("warning", env)


if __name__ == "__main__":
    unittest.main()
