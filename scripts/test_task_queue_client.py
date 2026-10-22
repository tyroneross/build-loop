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
        self.assertEqual(tq.normalize_title("fix: Ez-ptyd rejects  a command"),
                         "ez-ptyd rejects a command")
        self.assertEqual(tq.normalize_title("Fix ez-ptyd rejects a command"),
                         "ez-ptyd rejects a command")
        # Codex review r3 #4: edge punctuation is content too.
        self.assertNotEqual(tq.normalize_title("Lexer rejects !"), tq.normalize_title("Lexer rejects ?"))

    def test_scope_and_label_are_kept(self) -> None:
        # Codex review r1 #4: scopes and bracket labels name different components.
        self.assertNotEqual(tq.normalize_title("[iOS] Login crashes"), tq.normalize_title("[macOS] Login crashes"))
        self.assertNotEqual(tq.normalize_title("fix(auth): Session leaks"),
                            tq.normalize_title("fix(cache): Session leaks"))
        self.assertEqual(tq.normalize_title("fix(auth): Session leaks"), "(auth) session leaks")

    def test_meaningful_symbols_and_unicode_survive(self) -> None:
        # Codex review r1 #5.
        self.assertNotEqual(tq.normalize_title("C++ parser crashes"), tq.normalize_title("C# parser crashes"))
        self.assertNotEqual(tq.normalize_title("导出 CSV 失败"), tq.normalize_title("导入 CSV 失败"))
        # Codex review r2 #4: operators and flag spelling are content.
        self.assertNotEqual(tq.normalize_title("Parser rejects x < 0"), tq.normalize_title("Parser rejects x > 0"))
        self.assertNotEqual(tq.normalize_title("CLI rejects --verbose"), tq.normalize_title("CLI rejects -verbose"))

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

    def test_same_repo_needs_identical_paths_when_both_absolute(self) -> None:
        # Codex review r1 #6: same directory name in different roots is a different repo.
        self.assertFalse(tq.same_repo("/srv/company-a/api", "/srv/company-b/api"))
        self.assertTrue(tq.same_repo("/srv/a/api", "/srv/a/api.worktrees/lane"))
        self.assertTrue(tq.same_repo("api", "/srv/a/api"), "bare name matches by directory name")
        self.assertFalse(tq.same_repo("", "api"))
        # Codex review r2 #5: relative paths with a slash are not bare names.
        self.assertFalse(tq.same_repo("org-a/api", "org-b/api"))
        # Codex review r3 #5: a repo nested inside a worktree keeps its own identity.
        self.assertFalse(tq.same_repo("/srv/app.worktrees/lane/vendor/api", "/srv/app"))
        self.assertFalse(tq.same_repo("/srv/app/.claude/worktrees/a/vendor/api", "/srv/app"))


class TestFindDuplicate(unittest.TestCase):
    cards = [card("Fix ez-ptyd rejecting a pane command", "/Users/x/dev/easy-terminal")]

    def test_match(self) -> None:
        hit = tq.find_duplicate(self.cards, title="fix: ez-ptyd rejecting a pane command",
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

    def test_resolve_open_id_requires_full_open_id(self) -> None:
        cards = [card("a", "r"), card("c", "r", status="done", cid="ffff0000" + "0" * 24)]
        self.assertEqual(tq.resolve_open_id(cards, FULL.upper())["id"], FULL)
        # Codex review r1 #9: a prefix can collide with a closed card the open list cannot see.
        self.assertIsNone(tq.resolve_open_id(cards, "e307f7da"))
        self.assertIsNone(tq.resolve_open_id(cards, "ffff0000" + "0" * 24), "closed card gets no trailer")

    def test_trailer_only_from_final_trailer_block(self) -> None:
        # Codex review r1 #8: a Task: line in the body or a quoted example is not a trailer.
        body_only = f"subject\n\nExample:\n\nTask: {FULL}\n\nmore prose after the example"
        self.assertEqual(tq.parse_trailer_ids(body_only), [])
        mixed_last = f"subject\n\nprose line\nTask: {FULL}"
        self.assertEqual(tq.parse_trailer_ids(mixed_last), [])
        self.assertEqual(tq.parse_trailer_ids(f"Task: {FULL}"), [], "subject alone has no trailers")
        # Codex review r2 #6: an indented example at the end is not a trailer block.
        indented = f"Document commit format\n\nExample:\n\n    Task: {FULL}\n"
        self.assertEqual(tq.parse_trailer_ids(indented), [])


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
            fake = _fake_mcp(Path(d), {
                "todo": [card("a", "r", cid="a" * 32)],
                "working": [card("b", "r", status="working", cid="b" * 32)],
                "needs_input": [card("c", "r", status="needs_input", cid="c" * 32)],
                "review": [card("d", "r", status="review", cid="d" * 32)],
                "done": [card("e", "r", status="done", cid="e" * 32)]})
            cards = tq.RossLabsOCClient(str(fake)).list_open()
            # Codex review r1 #12: every open status must be queried.
            self.assertEqual(sorted(c["title"] for c in cards), ["a", "b", "c", "d"])

    def test_attach_calls_receipt_and_raises_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake = _fake_mcp(Path(d), {})
            tq.RossLabsOCClient(str(fake)).attach(FULL, "same finding")
            call = json.loads(Path(str(fake) + ".calls").read_text().splitlines()[0])
            self.assertEqual(call[:3], ["receipt", FULL, "AGENT STATUS"])
            bad = _fake_mcp(Path(d) / ".." / Path(d).name, {}, receipt_rc=1)
            with self.assertRaises(tq.QueueUnavailable):
                tq.RossLabsOCClient(str(bad)).attach(FULL, "x")

    def test_malformed_payload_is_unavailable_not_empty(self) -> None:
        # Codex review r1 #3: `null` / non-list payloads must not read as "no open cards".
        with tempfile.TemporaryDirectory() as d:
            fake = _fake_mcp(Path(d), {"todo": None})
            with self.assertRaises(tq.QueueUnavailable):
                tq.RossLabsOCClient(str(fake)).list_open()

    def test_slow_server_times_out_even_with_a_descendant_holding_stdout(self) -> None:
        # Codex review r1 #1 + r2 #1/#9: one deadline covers the reads, and cleanup is
        # bounded even when a grandchild keeps the pipe open.
        import time
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "slow"
            p.write_text("#!/bin/sh\nsleep 30 &\nsleep 30\n")
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
            t0 = time.monotonic()
            with self.assertRaises(tq.QueueUnavailable) as ctx:
                tq.RossLabsOCClient(str(p), timeout=0.5).list_open()
            self.assertLess(time.monotonic() - t0, 4.0)
            self.assertIn("timed out", str(ctx.exception))

    def test_non_mcp_binary_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "noise"
            p.write_text("#!/bin/sh\necho 'added abc'\n")
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
            with self.assertRaises(tq.QueueUnavailable):
                tq.RossLabsOCClient(str(p)).list_open()


if __name__ == "__main__":
    unittest.main(verbosity=2)
