# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/retrospective/sections (F3 of the retro+backlog spec).

F3 — An item prompted ≥2× in the thread appears in section 8 AND produces an
enforce-candidate file. This module tests the section-builder; write.py tests
verify the file is materialized.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from retrospective.sections import (  # noqa: E402
    SECTION_KEYS,
    build,
    cluster_repeated_prompts,
    extract_user_prompts,
)


def _make_transcript(tmpdir: Path, user_turns: list[str]) -> Path:
    """Create a fixture JSONL with the given user-turn texts."""
    p = tmpdir / "fixture.jsonl"
    lines = []
    for i, text in enumerate(user_turns):
        rec = {
            "type": "user",
            "timestamp": f"2026-06-04T12:00:{i:02}Z",
            "message": {"role": "user", "content": text},
        }
        lines.append(json.dumps(rec))
    # Add an assistant turn between user turns (realism) — also a tool_use
    # block to confirm the extractor skips them correctly.
    assistant = {
        "type": "assistant",
        "timestamp": "2026-06-04T12:00:30Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    }
    lines.append(json.dumps(assistant))
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


class ExtractUserPromptsTests(unittest.TestCase):
    def test_extracts_string_content(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = _make_transcript(Path(tmp.name), ["hello", "world"])
        out = extract_user_prompts(p)
        self.assertEqual([o["text"] for o in out], ["hello", "world"])

    def test_extracts_list_content_text_blocks(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = Path(tmp.name) / "f.jsonl"
        p.write_text(json.dumps({
            "type": "user",
            "message": {
                "content": [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}],
            },
        }) + "\n")
        out = extract_user_prompts(p)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "first\nsecond")

    def test_skips_tool_result_only_turns(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = Path(tmp.name) / "f.jsonl"
        p.write_text(json.dumps({
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": "stdout"}]},
        }) + "\n")
        out = extract_user_prompts(p)
        self.assertEqual(out, [])

    def test_none_transcript_returns_empty(self) -> None:
        self.assertEqual(extract_user_prompts(None), [])

    def test_unreadable_transcript_returns_empty(self) -> None:
        out = extract_user_prompts(Path("/nonexistent/zzz.jsonl"))
        self.assertEqual(out, [])


class ClusterRepeatedPromptsTests(unittest.TestCase):
    def test_two_identical_prompts_cluster(self) -> None:
        prompts = [
            {"text": "Please add a retry to the API call.", "ts": "1"},
            {"text": "please add a retry to the api call", "ts": "2"},  # normalized match
        ]
        clusters = cluster_repeated_prompts(prompts, threshold=2)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["count"], 2)

    def test_threshold_three_does_not_cluster_two(self) -> None:
        prompts = [{"text": "same thing", "ts": "1"}, {"text": "same thing", "ts": "2"}]
        self.assertEqual(cluster_repeated_prompts(prompts, threshold=3), [])

    def test_no_clusters_when_all_unique(self) -> None:
        prompts = [{"text": f"unique-{i}", "ts": str(i)} for i in range(5)]
        self.assertEqual(cluster_repeated_prompts(prompts), [])

    def test_clusters_sorted_by_count_desc(self) -> None:
        prompts = (
            [{"text": "alpha"}] * 2 +
            [{"text": "beta"}] * 4 +
            [{"text": "gamma"}] * 3
        )
        clusters = cluster_repeated_prompts(prompts, threshold=2)
        self.assertEqual([c["count"] for c in clusters], [4, 3, 2])

    def test_punctuation_and_case_normalized(self) -> None:
        prompts = [
            {"text": "Fix the BUG!"},
            {"text": "fix the bug"},
            {"text": "Fix  the   Bug."},
        ]
        clusters = cluster_repeated_prompts(prompts, threshold=2)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["count"], 3)


class BuildSectionsTests(unittest.TestCase):
    """End-to-end builder tests including F3."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_all_nine_keys_present_with_empty_inputs(self) -> None:
        sec = build(None, None, None, None, "run-x")
        for k in SECTION_KEYS:
            self.assertIn(k, sec, f"missing section: {k}")
        # Plus metadata
        self.assertIn("enforce_candidates", sec)
        self.assertIn("meta", sec)

    def test_F3_prompted_twice_appears_in_section_8_and_enforce(self) -> None:
        """F3 acceptance — an item prompted ≥2× appears in section 8 AND produces
        an enforce-candidate."""
        p = _make_transcript(self.dir, [
            "please always commit small chunks",
            "please always commit small chunks",  # exact repeat
            "anything else",
        ])
        sec = build(p, {}, None, None, "run-x")
        section_8 = sec["user_prompts_and_repeats"]
        self.assertIn("Prompted ≥2×", section_8)
        # enforce candidates carry the prompted-≥2× signal
        self.assertGreater(len(sec["enforce_candidates"]), 0)
        # the same text appears in "what should be enforced" section
        self.assertIn("user-prompted", sec["what_should_be_enforced"].lower())

    def test_unique_prompts_emit_no_repeat_block(self) -> None:
        p = _make_transcript(self.dir, ["alpha", "beta", "gamma"])
        sec = build(p, {}, None, None, "run-x")
        self.assertIn("No prompts repeated this thread", sec["user_prompts_and_repeats"])
        self.assertEqual(sec["enforce_candidates"], [])

    def test_judge_failures_appear_in_issues(self) -> None:
        state = {"runs": [{
            "judge_decisions": [{
                "judge_id": "independent-auditor",
                "checkpoint_id": "build",
                "verdict": "nay",
                "variances": [{"why_it_matters": "API contract broken"}],
            }],
        }]}
        sec = build(None, state, None, None, "run-x")
        self.assertIn("API contract broken", sec["issues_with_causal_tree"])
        # also surfaces as enforce candidate
        self.assertTrue(any("Enforce gate" in e for e in sec["enforce_candidates"]))

    def test_intent_restated_line_surfaces_in_takeaways(self) -> None:
        intent = "# Build Intent\n\n## Restated intent\nDo the thing well.\n\nmore..."
        sec = build(None, {}, intent, None, "run-x")
        self.assertIn("Do the thing well.", sec["key_takeaways"])

    def test_meta_carries_counts(self) -> None:
        p = _make_transcript(self.dir, ["a", "a", "b"])
        sec = build(p, {}, None, None, "run-x", prompted_threshold=2)
        self.assertEqual(sec["meta"]["prompt_count"], 3)
        self.assertEqual(sec["meta"]["cluster_count"], 1)
        self.assertTrue(sec["meta"]["transcript_present"])

    def test_no_transcript_yields_empty_prompts_section(self) -> None:
        sec = build(None, {}, None, None, "run-x")
        self.assertIn("no user prompts captured", sec["user_prompts_and_repeats"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
