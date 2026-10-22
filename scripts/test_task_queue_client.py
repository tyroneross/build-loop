#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for task_queue_client.py. Hermetic: a fake MCP server stands in for the queue.

Run: python3 -m pytest scripts/test_task_queue_client.py -q
"""
from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import task_queue_client as tq  # noqa: E402

FULL = "e307f7daf0a055d5150db3403f083338"


def card(title, repo, status="todo", cid=FULL):
    return {"id": cid, "title": title, "status": status, "target_repo": repo}


class TestNormalize(unittest.TestCase):
    def test_prefix_case_punctuation(self) -> None:
        self.assertEqual(tq.normalize_title("fix(pty): Ez-ptyd rejects  a command!"),
                         "ez ptyd rejects a command")
        self.assertEqual(tq.normalize_title("[bug] Ez-ptyd rejects a command"),
                         "ez ptyd rejects a command")

    def test_distinct_words_stay_distinct(self) -> None:
        self.assertNotEqual(tq.normalize_title("Leak in search index"),
                            tq.normalize_title("Leak in search indexer"))

    def test_repo_key_worktree_forms(self) -> None:
        canon = "easy-terminal"
        for r in ("/Users/x/dev/easy-terminal", "easy-terminal", "/Users/x/dev/easy-terminal/",
                  "/Users/x/dev/easy-terminal.worktrees/lane-a",
                  "/Users/x/dev/easy-terminal/.build-loop/worktrees/abc",
                  "/Users/x/dev/easy-terminal/.claude/worktrees/agent-1",
                  "/Users/x/dev/Easy-Terminal"):
            self.assertEqual(tq.repo_key(r), canon, r)
        self.assertNotEqual(tq.repo_key("/x/easy-terminal-web"), canon)
        self.assertEqual(tq.repo_key(""), "")


class TestFindDuplicate(unittest.TestCase):
    cards = [card("Fix ez-ptyd rejecting a pane command", "/Users/x/dev/easy-terminal")]

    def test_match(self) -> None:
        hit = tq.find_duplicate(self.cards, title="fix: ez-ptyd rejecting a pane command.",
                                repo="/Users/x/dev/easy-terminal.worktrees/z")
        self.assertEqual(hit["id"], FULL)

    def test_no_match_on_other_repo_title_or_closed(self) -> None:
        self.assertIsNone(tq.find_duplicate(self.cards, title="Fix ez-ptyd rejecting a pane command",
                                            repo="build-loop"))
        self.assertIsNone(tq.find_duplicate(self.cards, title="Fix ez-ptyd rejecting a pane resize",
                                            repo="easy-terminal"))
        closed = [card("Fix ez-ptyd rejecting a pane command", "easy-terminal", status="done")]
        self.assertIsNone(tq.find_duplicate(closed, title="Fix ez-ptyd rejecting a pane command",
                                            repo="easy-terminal"))

    def test_empty_title_never_matches(self) -> None:
        self.assertIsNone(tq.find_duplicate([card("!!!", "r")], title="???", repo="r"))


class TestTrailer(unittest.TestCase):
    def test_emit_and_parse_both_keys(self) -> None:
        self.assertEqual(tq.trailer(FULL), f"Task: {FULL}")
        msg = f"subject\n\nbody line\n\nTask: {FULL}\nOC-Task: 1a2b3c4d\nTask: {FULL}\n"
        self.assertEqual(tq.parse_trailer_ids(msg), [FULL, "1a2b3c4d"])

    def test_prose_mention_is_not_a_trailer(self) -> None:
        self.assertEqual(tq.parse_trailer_ids("We fixed the Task: list rendering today"), [])

    def test_resolve_open_id(self) -> None:
        cards = [card("a", "r"), card("b", "r", cid="e307aaaa" + "0" * 24),
                 card("c", "r", status="done", cid="ffff0000" + "0" * 24)]
        self.assertEqual(tq.resolve_open_id(cards, "e307f7da")["id"], FULL)
        self.assertIsNone(tq.resolve_open_id(cards, "e307"), "ambiguous prefix")
        self.assertIsNone(tq.resolve_open_id(cards, "ffff0000"), "closed card gets no trailer")


def _fake_mcp(dirpath: Path, by_status: dict, receipt_rc: int = 0) -> Path:
    """Executable that answers MCP initialize + list_tasks, and logs `receipt` calls."""
    p = dirpath / "fake-oc"
    p.write_text(f"""#!{sys.executable}
import json, sys
BY = {json.dumps(by_status)!r}
by = json.loads(BY)
args = sys.argv[1:]
if args and args[0] == "receipt":
    open(sys.argv[0] + ".calls", "a").write(json.dumps(args) + "\\n")
    sys.exit({receipt_rc})
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        r = {{"protocolVersion": "2025-06-18", "capabilities": {{}}}}
    else:
        rows = by.get(m["params"]["arguments"]["status"], [])
        r = {{"content": [{{"type": "text", "text": json.dumps(rows)}}]}}
    print(json.dumps({{"jsonrpc": "2.0", "id": m["id"], "result": r}}), flush=True)
""")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


class TestRossLabsOCClient(unittest.TestCase):
    def test_list_open_unions_open_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake = _fake_mcp(Path(d), {"todo": [card("a", "r")],
                                       "review": [card("b", "r", status="review", cid="1" * 32)],
                                       "done": [card("c", "r", status="done", cid="2" * 32)]})
            cards = tq.RossLabsOCClient(str(fake)).list_open()
            self.assertEqual(sorted(c["title"] for c in cards), ["a", "b"])

    def test_attach_calls_receipt_and_raises_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake = _fake_mcp(Path(d), {})
            tq.RossLabsOCClient(str(fake)).attach(FULL, "same finding")
            call = json.loads(Path(str(fake) + ".calls").read_text().splitlines()[0])
            self.assertEqual(call[:3], ["receipt", FULL, "AGENT STATUS"])
            bad = _fake_mcp(Path(d) / ".." / Path(d).name, {}, receipt_rc=1)
            with self.assertRaises(tq.QueueUnavailable):
                tq.RossLabsOCClient(str(bad)).attach(FULL, "x")

    def test_non_mcp_binary_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "noise"
            p.write_text("#!/bin/sh\necho 'added abc'\n")
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
            with self.assertRaises(tq.QueueUnavailable):
                tq.RossLabsOCClient(str(p)).list_open()


if __name__ == "__main__":
    unittest.main(verbosity=2)
