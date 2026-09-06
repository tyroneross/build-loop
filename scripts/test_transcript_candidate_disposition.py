#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""A mined candidate reaches a terminal state, and a closed one stops recurring.

Covers the three pieces that close the miner's read->effect loop:

  disposition  stable ids, the fixed/waived/escalated ledger, and the rule that
               suppression happens BEFORE the per-shape caps
  categories   repeated_tool_sequence carries judgeable command shapes without
               widening what the miner captures
  memory_route corrections are DRAFTED, never decided, and never twice

Nothing here writes to the real memory store or the real ledger: every path is
injected. `_write_via_memory_writer` is exercised against a temp memory dir in
one test so the frontmatter contract is graded against the actual writer rather
than against a stub that agrees with itself.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from transcript_pattern_miner import categories, disposition, memory_route, report  # noqa: E402
from transcript_pattern_miner.session import SessionAggregate, _tool_shape  # noqa: E402

NOW = dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.timezone.utc)


def correction(quote="use the venv python", count=10, projects=("a", "b")):
    return {"kind": "feedback_candidate", "shape": "user_correction_cluster",
            "count": count, "last_seen": "2026-08-24T06:02:43+00:00",
            "representative_quote": quote, "projects": list(projects),
            "rationale": "r"}


class CandidateIdentityIsStable(unittest.TestCase):

    def test_the_same_pattern_gets_the_same_id_across_runs(self):
        self.assertEqual(disposition.candidate_id(correction()),
                         disposition.candidate_id(correction()))

    def test_a_growing_cluster_is_still_the_same_candidate(self):
        """Keying on the count would silently reopen a closed cluster weekly."""
        self.assertEqual(disposition.candidate_id(correction(count=4)),
                         disposition.candidate_id(correction(count=10)))

    def test_a_later_sighting_does_not_change_the_id(self):
        a = correction()
        b = correction()
        b["last_seen"] = "2026-09-05T00:00:00+00:00"
        self.assertEqual(disposition.candidate_id(a), disposition.candidate_id(b))

    def test_a_different_pattern_gets_a_different_id(self):
        self.assertNotEqual(disposition.candidate_id(correction()),
                            disposition.candidate_id(correction(quote="something else")))

    def test_sequences_key_on_the_sequence_not_the_session_count(self):
        base = {"shape": "repeated_tool_sequence", "sequence": ["Bash:command"] * 3}
        grown = dict(base, session_count=99, sample_commands=[{"commands": ["x"]}])
        self.assertEqual(disposition.candidate_id(base), disposition.candidate_id(grown))

    def test_an_unknown_shape_still_gets_a_distinct_stable_id(self):
        a = {"shape": "brand_new_shape", "thing": "one"}
        b = {"shape": "brand_new_shape", "thing": "two"}
        self.assertEqual(disposition.candidate_id(a), disposition.candidate_id(dict(a)))
        self.assertNotEqual(disposition.candidate_id(a), disposition.candidate_id(b))

    def test_stamp_puts_the_id_on_every_candidate(self):
        stamped = disposition.stamp([correction(), correction(quote="x")])
        self.assertTrue(all(c["candidate_id"] for c in stamped))


class TheLedger(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_terminal_state_requires_a_record(self):
        """A disposition with nothing to point at is an assertion, not a disposition."""
        for state in disposition.TERMINAL_STATES:
            with self.assertRaises(ValueError, msg=state):
                disposition.close(self.out, "abc", state, "   ")

    def test_reopen_needs_no_record(self):
        row = disposition.close(self.out, "abc", "open", "", now=NOW)
        self.assertEqual(row["state"], "open")

    def test_an_unknown_state_is_refused(self):
        with self.assertRaises(ValueError):
            disposition.close(self.out, "abc", "resolved", "sha")

    def test_the_last_row_wins(self):
        disposition.close(self.out, "abc", "fixed", "sha1", now=NOW)
        disposition.close(self.out, "abc", "open", "", now=NOW)
        rows = disposition.load(disposition.ledger_path(self.out))
        self.assertEqual(rows["abc"]["state"], "open")
        self.assertFalse(disposition.is_closed(rows["abc"]))

    def test_a_reopen_can_be_closed_again(self):
        disposition.close(self.out, "abc", "fixed", "sha1", now=NOW)
        disposition.close(self.out, "abc", "open", "", now=NOW)
        disposition.close(self.out, "abc", "escalated", "BL-123", now=NOW)
        rows = disposition.load(disposition.ledger_path(self.out))
        self.assertTrue(disposition.is_closed(rows["abc"]))
        self.assertEqual(rows["abc"]["record"], "BL-123")

    def test_the_file_is_append_only(self):
        disposition.close(self.out, "a", "fixed", "s1", now=NOW)
        disposition.close(self.out, "b", "waived", "w.md", now=NOW)
        disposition.close(self.out, "a", "escalated", "BL-9", now=NOW)
        lines = disposition.ledger_path(self.out).read_text().strip().splitlines()
        self.assertEqual(len(lines), 3, "a close must never rewrite an earlier row")

    def test_one_malformed_line_does_not_hide_the_good_ones(self):
        disposition.close(self.out, "a", "fixed", "s1", now=NOW)
        with disposition.ledger_path(self.out).open("a") as fh:
            fh.write("{ not json\n")
        disposition.close(self.out, "b", "fixed", "s2", now=NOW)
        self.assertEqual(set(disposition.load(disposition.ledger_path(self.out))),
                         {"a", "b"})

    def test_routed_is_not_a_terminal_state(self):
        """Drafting is an effect; confirming is a decision."""
        disposition.mark_routed(self.out, "abc", "/mem/x.md", now=NOW)
        row = disposition.load(disposition.ledger_path(self.out))["abc"]
        self.assertEqual(row["state"], "routed")
        self.assertFalse(disposition.is_closed(row))
        self.assertNotIn("routed", disposition.TERMINAL_STATES)

    def test_a_missing_ledger_reads_as_empty_rather_than_raising(self):
        self.assertEqual(disposition.load(self.out / "nope.jsonl"), {})


class SuppressionRunsBeforeTheCaps(unittest.TestCase):
    """The crowding-out fix, which is most of what the ledger buys."""

    def pool(self, n=6):
        return disposition.stamp(
            [correction(quote=f"correction number {i}", count=10 - i) for i in range(n)])

    def test_with_nothing_closed_the_cap_still_applies(self):
        open_list, suppressed = report.rank(self.pool(), {})
        self.assertEqual(len(open_list), report.SHAPE_CAPS["user_correction_cluster"])
        self.assertEqual(suppressed, [])

    def test_a_closed_candidate_frees_its_slot_for_a_newer_one(self):
        pool = self.pool()
        closed = {pool[0]["candidate_id"]: {"state": "fixed", "record": "sha",
                                            "ts": NOW.isoformat()}}
        open_list, suppressed = report.rank(pool, closed)
        ids = [c["candidate_id"] for c in open_list]
        self.assertNotIn(pool[0]["candidate_id"], ids)
        self.assertIn(pool[3]["candidate_id"], ids,
                      "the 4th-ranked candidate should have moved into the freed slot")
        self.assertEqual(len(open_list), 3)
        self.assertEqual([c["candidate_id"] for c in suppressed],
                         [pool[0]["candidate_id"]])

    def test_a_suppressed_candidate_keeps_its_disposition_visible(self):
        """Closed and back anyway means the fix did not hold. Report it."""
        pool = self.pool()
        closed = {pool[0]["candidate_id"]: {"state": "waived", "record": "w.md",
                                            "rationale": "by design",
                                            "ts": NOW.isoformat()}}
        _, suppressed = report.rank(pool, closed)
        self.assertEqual(suppressed[0]["disposition"]["state"], "waived")
        self.assertEqual(suppressed[0]["disposition"]["record"], "w.md")
        self.assertEqual(suppressed[0]["count"], 10,
                         "the recurrence count must survive suppression")

    def test_a_reopened_candidate_is_not_suppressed(self):
        pool = self.pool()
        closed = {pool[0]["candidate_id"]: {"state": "open", "record": "",
                                            "ts": NOW.isoformat()}}
        open_list, suppressed = report.rank(pool, closed)
        self.assertIn(pool[0]["candidate_id"], [c["candidate_id"] for c in open_list])
        self.assertEqual(suppressed, [])

    def test_the_per_shape_caps_match_the_original_ranking(self):
        self.assertEqual(report.SHAPE_CAPS,
                         {"user_correction_cluster": 3, "repeated_tool_sequence": 2,
                          "bash_ritual": 1, "cross_project_file": 1})
        self.assertEqual(report.TOTAL_CAP, 5)


class SequencesCarryJudgeableCommands(unittest.TestCase):
    """`Bash:command -> Bash:command -> ToolSearch:query` names a shape, not work."""

    def aggregate(self, session_id, shapes):
        agg = SessionAggregate(session_id)
        agg.tool_sequence = ["Bash:command", "Bash:command", "ToolSearch:query"]
        agg.tool_shapes = shapes
        return agg

    def test_a_recurring_sequence_carries_its_command_shapes(self):
        shapes = ["Bash: git status", "Bash: git diff --stat", "ToolSearch:query"]
        aggs = [self.aggregate(f"s{i}", list(shapes)) for i in range(3)]
        out = categories.repeated_tool_sequences(aggs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["session_count"], 3)
        self.assertEqual(out[0]["sample_commands"][0]["commands"], shapes)
        self.assertEqual(out[0]["sample_commands"][0]["occurrences"], 3)

    def test_distinct_renderings_reveal_one_shape_hiding_many_jobs(self):
        aggs = [self.aggregate("s0", ["Bash: git status", "Bash: git diff", "ToolSearch:query"]),
                self.aggregate("s1", ["Bash: pytest", "Bash: ruff check", "ToolSearch:query"]),
                self.aggregate("s2", ["Bash: ls", "Bash: cat <arg>", "ToolSearch:query"])]
        out = categories.repeated_tool_sequences(aggs)
        self.assertEqual(out[0]["distinct_renderings"], 3,
                         "three unrelated jobs sharing one abstract shape is the "
                         "finding a reader needs in order to say 'not worth automating'")

    def test_a_shape_with_no_concrete_repetition_ranks_below_a_real_ritual(self):
        """Measured over 14 days of real transcripts on 2026-09-06.

        `Bash -> Bash -> Skill` appeared in 54 sessions with 54 DISTINCT
        renderings -- every occurrence a different command. Ranking by session
        count alone put that, plus its mirror image, in both sequence slots.
        """
        ritual = ["Bash: git status", "Bash: git diff --stat", "ToolSearch:query"]
        noise_shapes = ["Bash:command", "Bash:command", "Skill:skill"]
        aggs = []
        for i in range(3):
            a = SessionAggregate(f"r{i}")
            a.tool_sequence = ["Bash:command", "Bash:command", "ToolSearch:query"]
            a.tool_shapes = list(ritual)
            aggs.append(a)
        for i in range(20):
            a = SessionAggregate(f"n{i}")
            a.tool_sequence = list(noise_shapes)
            a.tool_shapes = [f"Bash: cmd{i}a", f"Bash: cmd{i}b", "Skill:skill"]
            aggs.append(a)

        out = categories.repeated_tool_sequences(aggs)
        self.assertEqual(out[0]["sequence"][-1], "ToolSearch:query",
                         "the 3-session real ritual must outrank the 20-session "
                         "shape whose every occurrence differs")
        self.assertEqual(out[0]["top_rendering_occurrences"], 3)
        self.assertEqual(out[1]["top_rendering_occurrences"], 1)

    def test_the_rationale_states_which_case_it_is(self):
        noisy = {"session_count": 54, "distinct_renderings": 54,
                 "top_rendering_occurrences": 1}
        real = {"session_count": 12, "distinct_renderings": 2,
                "top_rendering_occurrences": 11}
        self.assertIn("not a ritual worth automating", report._sequence_rationale(noisy))
        self.assertIn("11×", report._sequence_rationale(real))

    def test_a_short_shape_list_slices_empty_rather_than_mis_attributing(self):
        aggs = [self.aggregate(f"s{i}", []) for i in range(3)]
        out = categories.repeated_tool_sequences(aggs)
        self.assertEqual(out[0]["sample_commands"], [])
        self.assertEqual(out[0]["session_count"], 3)

    def test_only_bash_carries_command_text(self):
        self.assertEqual(_tool_shape("Bash", {"command": "git status -s"}, "Bash:command"),
                         "Bash: git status -s")
        self.assertEqual(_tool_shape("Read", {"file_path": "/secret/notes.md"},
                                     "Read:file_path"), "Read:file_path")

    def test_bash_values_are_stripped_by_the_miners_own_normalizer(self):
        """No widening: `normalize_bash` is the posture manual_command_rituals
        has always used. Paths, tokens and messages must not survive it."""
        shape = _tool_shape(
            "Bash",
            {"command": "curl -H 'Authorization: Bearer sk-live-abcdef' "
                        "https://api.example.com/v1/secret"},
            "Bash:command")
        self.assertNotIn("sk-live-abcdef", shape)
        self.assertNotIn("api.example.com", shape)

    def test_a_commit_message_does_not_survive_normalization(self):
        shape = _tool_shape("Bash", {"command": "git commit -m 'fix the login bug for Jane'"},
                            "Bash:command")
        self.assertNotIn("Jane", shape)
        self.assertNotIn("login", shape)

    def test_an_empty_bash_command_falls_back_to_the_abstract_label(self):
        self.assertEqual(_tool_shape("Bash", {"command": ""}, "Bash:command"),
                         "Bash:command")


class CorrectionsAreDraftedNeverDecided(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.written = []

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, candidate, title, body, *, memory_dir=None, now=None):
        self.written.append({"title": title, "body": body,
                             "candidate_id": candidate["candidate_id"]})
        return f"/mem/{candidate['candidate_id']}.md"

    def route(self, candidates, **kw):
        kw.setdefault("window_label", "last 7 day(s)")
        kw.setdefault("now", NOW)
        kw.setdefault("runner", self.runner)
        return memory_route.route(disposition.stamp(candidates), self.out, **kw)

    def test_a_strong_cluster_is_drafted(self):
        out = self.route([correction(count=10)])
        self.assertEqual(out[0]["action"], "drafted")
        self.assertEqual(len(self.written), 1)

    def test_a_weak_cluster_is_not(self):
        out = self.route([correction(count=4)])
        self.assertEqual(out[0]["action"], "skipped")
        self.assertIn("< 5", out[0]["reason"])
        self.assertEqual(self.written, [])

    def test_only_correction_clusters_are_routed(self):
        out = self.route([{"shape": "repeated_tool_sequence", "sequence": ["a", "b", "c"]}])
        self.assertEqual(out, [])

    def test_the_same_cluster_is_never_drafted_twice(self):
        """Otherwise the read->effect gap becomes a write amplifier."""
        self.route([correction()])
        second = self.route([correction()])
        self.assertEqual(second[0]["action"], "skipped")
        self.assertIn("already drafted", second[0]["reason"])
        self.assertEqual(len(self.written), 1)

    def test_an_already_closed_cluster_is_not_drafted(self):
        candidate = disposition.stamp([correction()])[0]
        disposition.close(self.out, candidate["candidate_id"], "waived", "w.md", now=NOW)
        out = self.route([correction()])
        self.assertEqual(out[0]["action"], "skipped")
        self.assertEqual(self.written, [])

    def test_drafting_records_routed_and_not_a_terminal_state(self):
        self.route([correction()])
        row = list(disposition.load(disposition.ledger_path(self.out)).values())[0]
        self.assertEqual(row["state"], "routed")
        self.assertFalse(disposition.is_closed(row))

    def test_a_write_failure_is_reported_and_does_not_kill_the_run(self):
        def boom(*a, **kw):
            raise RuntimeError("memory store busy")
        out = self.route([correction(), correction(quote="second one")], runner=boom)
        self.assertEqual([r["action"] for r in out], ["failed", "failed"],
                         "one failure must not abort the rest of the batch")
        self.assertEqual(disposition.load(disposition.ledger_path(self.out)), {},
                         "a failed write must not be recorded as routed")

    def test_the_draft_body_says_it_is_a_candidate_before_anything_else(self):
        body = memory_route.draft_body(disposition.stamp([correction()])[0], "last 7 day(s)")
        self.assertIn("CANDIDATE", body.splitlines()[0])
        self.assertIn("not a rule yet", body)
        self.assertIn("disposition.py close", body,
                      "the draft must name how to close it")

    def test_the_draft_body_carries_the_evidence_a_human_needs(self):
        body = memory_route.draft_body(
            disposition.stamp([correction(count=10,
                                          projects=("project-one", "project-two"))])[0],
            "last 7 day(s)")
        self.assertIn("10 times", body)
        self.assertIn("project-one", body)
        self.assertIn("use the venv python", body)


class TheRealWriterStampsTheCandidateMarkers(unittest.TestCase):
    """Graded against memory_writer.py itself, not against a stub that agrees.

    `status: candidate` is the whole safety property: a behavioural rule an
    agent wrote for itself from its own transcripts, that nobody ever saw, is a
    self-reinforcing loop. If the stamp silently stopped landing, every other
    test here would still pass.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Path(self.tmp.name) / "memory"
        self.mem.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_injected_memory_dir_is_honoured(self):
        """The regression that nearly wrote a test artifact into live memory.

        The first cut passed `file_rel="lessons/<name>.md"`. `_normalize_file_rel`
        strips a recognized lane prefix AND re-points memory_dir at the real
        store root, so the injected temp directory was silently discarded and
        the file landed in build-loop-memory/lessons/. Asserting the returned
        path is under the injected root is what convicts that.
        """
        candidate = disposition.stamp([correction()])[0]
        path = Path(memory_route._write_via_memory_writer(
            candidate, "t", "body", memory_dir=self.mem, now=NOW))
        self.assertTrue(
            str(path).startswith(str(self.mem)),
            f"wrote outside the injected memory dir: {path}")
        self.assertTrue(path.exists(), "returned a path that was never written")

    def test_the_written_file_carries_status_candidate(self):
        candidate = disposition.stamp([correction()])[0]
        path = memory_route._write_via_memory_writer(
            candidate, "Repeated correction: venv",
            memory_route.draft_body(candidate, "last 7 day(s)"),
            memory_dir=self.mem, now=NOW)
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("status: candidate", text)
        self.assertIn("confirmation_required: true", text.lower())
        self.assertIn("transcript_pattern_miner", text)
        self.assertIn(candidate["candidate_id"], text)


class ClusteringIsReproducibleAcrossProcesses(unittest.TestCase):
    """A stable id is worthless if the field it hashes changes per process.

    `cluster_corrections` picked its representative with
    `sorted(set_of_quotes, key=len)[0]`. Sorting a SET of strings leaves ties to
    set-iteration order, which depends on PYTHONHASHSEED and is randomized per
    interpreter. Two runs over byte-identical input therefore disagreed on the
    top correction ids -- measured 2026-09-06 across a frozen copy of 127
    session files -- so no disposition could ever suppress anything.

    Run in SUBPROCESSES with explicit, different seeds. Inside one process the
    set order is fixed, so a same-process assertion passes against the defect.
    """

    SNIPPET = (
        "import json, sys, datetime as dt\n"
        "sys.path.insert(0, {scripts!r})\n"
        "from transcript_pattern_miner import categories\n"
        "from transcript_pattern_miner.session import SessionAggregate\n"
        "agg = SessionAggregate('s1')\n"
        "ts = dt.datetime(2026, 9, 5, tzinfo=dt.timezone.utc)\n"
        # Four equal-length corrective messages sharing four 3-grams (the
        # clusterer needs two), differing only in the last token: one cluster,
        # a four-way tie for shortest, four possible winners.
        "for text in ('please no dont use the thing aaa',\n"
        "             'please no dont use the thing bbb',\n"
        "             'please no dont use the thing ccc',\n"
        "             'please no dont use the thing ddd'):\n"
        "    agg.user_messages.append((ts, text, 'proj'))\n"
        "out = categories.cluster_corrections([agg])\n"
        "print(json.dumps([c['representative_quote'] for c in out]))\n"
    )

    def run_with_seed(self, seed):
        import os
        import subprocess
        env = {**os.environ, "PYTHONHASHSEED": str(seed)}
        code = self.SNIPPET.format(scripts=str(ROOT / "scripts"))
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_the_representative_quote_is_the_same_under_any_hash_seed(self):
        seeds = (0, 1, 7, 12345, 99991)
        results = [self.run_with_seed(seed) for seed in seeds]
        self.assertTrue(results[0], "the fixture should produce a cluster")
        for seed, result in zip(seeds[1:], results[1:]):
            self.assertEqual(
                result, results[0],
                f"PYTHONHASHSEED={seed} produced a different representative "
                "quote, so candidate ids are not reproducible across runs")


class TheCandidatesFileKeepsBothLists(unittest.TestCase):
    """The file consumers read must not make 'closed' and 'absent' look alike."""

    def test_show_reports_open_and_suppressed_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            pool = disposition.stamp([correction(quote=f"q{i}", count=9 - i)
                                      for i in range(4)])
            closed = {pool[0]["candidate_id"]: {"state": "fixed", "record": "sha",
                                                "ts": NOW.isoformat()}}
            open_list, suppressed = report.rank(pool, closed)
            (out / ".candidates.json").write_text(json.dumps({
                "candidates": open_list, "suppressed": suppressed}))
            rc = disposition.main(["--out-dir", str(out), "show", "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(len(suppressed), 1)
            self.assertEqual(len(open_list), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
