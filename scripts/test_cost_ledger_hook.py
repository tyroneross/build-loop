#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for cost_ledger_hook.py — the Stop-hook cost-attribution producer.

Covers the acceptance criteria for activating the dead pipeline:
  1. one Agent dispatch in a build-loop context -> >=1 regex-valid tagged row
  2. background/parallel dispatches produce rows (dispatch_mode=fan-out)
  3. idempotency (re-run writes no duplicates)
  4. scoping (no .build-loop/state.json -> no rows, exit 0)
  5. fail-open (bad/absent transcript -> exit 0, no crash)
  6. ACTIVATION-PATH: hooks.json registers the script on Stop (the test class
     whose absence let the original pipeline stay dead)

Run:
  uv run pytest scripts/test_cost_ledger_hook.py -v
  python3 -m unittest scripts.test_cost_ledger_hook
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
HOOK = SCRIPTS / "cost_ledger_hook.py"
HOOKS_JSON = REPO / "hooks" / "hooks.json"
TASK_ID_RE = re.compile(r"^t-[0-9a-f]{8}$")


def _transcript(path: Path, dispatches: list[dict]) -> None:
    """Write a synthetic session transcript with Agent tool_use + tool_result rows."""
    lines = []
    for d in dispatches:
        lines.append({
            "type": "assistant",
            "timestamp": "2026-07-07T12:00:00Z",
            "message": {"content": [{
                "type": "tool_use", "name": "Agent", "id": d["id"],
                "input": {
                    "subagent_type": d["subagent_type"],
                    "model": d.get("model"),
                    "run_in_background": d.get("run_in_background", False),
                    "prompt": "x",
                },
            }]},
        })
        if d.get("completed", True):
            lines.append({
                "type": "user",
                "message": {"content": [{
                    "type": "tool_result", "tool_use_id": d["id"],
                    "content": [{"type": "text", "text": "done"}],
                }]},
            })
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def _run_hook(cwd: Path, transcript: Path, session_id: str, ledger: Path, env_extra=None):
    payload = json.dumps({
        "session_id": session_id,
        "transcript_path": str(transcript),
        "cwd": str(cwd),
        "hook_event_name": "Stop",
    })
    env = {"PATH": "/usr/bin:/bin", "BUILD_LOOP_COST_LEDGER": str(ledger)}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(HOOK)], input=payload, text=True,
        capture_output=True, env=env, timeout=30,
    )


def _rows(ledger: Path) -> list[dict]:
    if not ledger.exists():
        return []
    return [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]


def _make_context(tmp: Path, run_id="bl-testrun-001") -> Path:
    bl = tmp / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    (bl / "state.json").write_text(
        json.dumps({"execution": {"run_id": run_id}}), encoding="utf-8")
    return tmp


class CostLedgerHookTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.ledger = self.tmp / "ledger.jsonl"

    def tearDown(self):
        self._td.cleanup()

    def test_one_dispatch_writes_tagged_row(self):
        ctx = _make_context(self.tmp)
        tr = self.tmp / "sess.jsonl"
        _transcript(tr, [{"id": "toolu_A", "subagent_type": "implementer",
                          "model": "claude-sonnet-5"}])
        r = _run_hook(ctx, tr, "sess-uuid-1", self.ledger)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = _rows(self.ledger)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(TASK_ID_RE.match(row["task_id"]), row["task_id"])
        self.assertEqual(row["agent"], "implementer")
        self.assertEqual(row["model"], "claude-sonnet-5")
        self.assertEqual(row["source"], "build-loop")
        self.assertEqual(row["dispatch_mode"], "inline")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["run_id"], "bl-testrun-001")

    def test_background_dispatch_is_fanout(self):
        ctx = _make_context(self.tmp)
        tr = self.tmp / "sess.jsonl"
        _transcript(tr, [{"id": "toolu_BG", "subagent_type": "explore",
                          "run_in_background": True, "completed": False}])
        r = _run_hook(ctx, tr, "sess-bg", self.ledger)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = _rows(self.ledger)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dispatch_mode"], "fan-out")
        self.assertEqual(rows[0]["status"], "dispatched")

    def test_idempotent_rerun(self):
        ctx = _make_context(self.tmp)
        tr = self.tmp / "sess.jsonl"
        _transcript(tr, [
            {"id": "toolu_1", "subagent_type": "a"},
            {"id": "toolu_2", "subagent_type": "b"},
        ])
        _run_hook(ctx, tr, "sess-x", self.ledger)
        _run_hook(ctx, tr, "sess-x", self.ledger)  # second pass — no dupes
        rows = _rows(self.ledger)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["task_id"] for r in rows}), 2)

    def test_deterministic_task_id(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("clh", HOOK)
        clh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(clh)
        a = clh.compute_task_id("sess", "toolu_9")
        b = clh.compute_task_id("sess", "toolu_9")
        self.assertEqual(a, b)
        self.assertTrue(TASK_ID_RE.match(a))
        self.assertNotEqual(a, clh.compute_task_id("sess", "toolu_8"))

    def test_scoping_no_build_loop_context(self):
        # No .build-loop/state.json -> must write nothing, exit 0.
        tr = self.tmp / "sess.jsonl"
        _transcript(tr, [{"id": "toolu_Z", "subagent_type": "x"}])
        r = _run_hook(self.tmp, tr, "sess-noctx", self.ledger)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_rows(self.ledger), [])

    def test_fail_open_missing_transcript(self):
        ctx = _make_context(self.tmp)
        r = _run_hook(ctx, self.tmp / "does-not-exist.jsonl", "sess-none", self.ledger)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_rows(self.ledger), [])

    def test_fail_open_unwritable_ledger(self):
        # Ledger path points into a non-writable location -> exit 0, no crash.
        ctx = _make_context(self.tmp)
        tr = self.tmp / "sess.jsonl"
        _transcript(tr, [{"id": "toolu_W", "subagent_type": "x"}])
        bad = Path("/proc/nonexistent-dir/ledger.jsonl")  # unwritable on all CI OSes
        r = _run_hook(ctx, tr, "sess-w", self.ledger, env_extra={"BUILD_LOOP_COST_LEDGER": str(bad)})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_activation_path_hook_registered(self):
        # The regression-class assertion: the script MUST be wired into hooks.json.
        # Removing this registration should fail the test (mutation guard).
        data = json.loads(HOOKS_JSON.read_text())
        stop = data.get("hooks", {}).get("Stop", [])
        cmds = [hk.get("command", "") for grp in stop for hk in grp.get("hooks", [])]
        self.assertTrue(
            any("cost_ledger_hook.py" in c for c in cmds),
            "cost_ledger_hook.py is not registered on the Stop event in hooks.json",
        )


if __name__ == "__main__":
    unittest.main()
