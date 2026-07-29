#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from model_run_history import Turn, apply_token_usage, build_report, parse_jsonl


PROMPT = "Fix the parser and run pytest before reporting completion."


def write_turn(
    path: Path,
    *,
    turn_id: str,
    model: str,
    effort: str,
    prompt: str,
    cwd: str,
    duration_ms: int,
    total_tokens: int,
    completed: bool = True,
    before_prompt: tuple[str, ...] = (),
    after_prompt: tuple[str, ...] = (),
) -> None:
    def user_message(text: str) -> dict:
        return {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
                "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
            },
        }

    records = [
        {
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": turn_id, "started_at": 100},
        },
        {
            "type": "turn_context",
            "payload": {
                "turn_id": turn_id,
                "model": model,
                "effort": effort,
                "cwd": cwd,
            },
        },
        *(user_message(text) for text in before_prompt),
        user_message(prompt),
        *(user_message(text) for text in after_prompt),
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "input": "await tools.exec_command({cmd: 'uv run pytest -q'})",
                "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": total_tokens - 200,
                        "cached_input_tokens": 10,
                        "output_tokens": 100,
                        "reasoning_output_tokens": 40,
                        "total_tokens": total_tokens - 100,
                    },
                    "total_token_usage": {
                        "input_tokens": 999999,
                        "cached_input_tokens": 999999,
                        "output_tokens": 999999,
                        "reasoning_output_tokens": 999999,
                        "total_tokens": 999999,
                    }
                },
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 50,
                        "cached_input_tokens": 0,
                        "output_tokens": 50,
                        "reasoning_output_tokens": 0,
                        "total_tokens": 100,
                    },
                    "total_token_usage": {"total_tokens": 1000099},
                },
            },
        },
    ]
    if completed:
        records.append(
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": turn_id,
                    "started_at": 100,
                    "completed_at": 101,
                    "duration_ms": duration_ms,
                    "time_to_first_token_ms": 50,
                },
            }
        )
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


class ModelRunHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        log = self.root / "rollout.jsonl"
        write_turn(
            log,
            turn_id="sol-high",
            model="gpt-5.6-sol",
            effort="high",
            prompt=PROMPT,
            cwd="/private/repo",
            duration_ms=1000,
            total_tokens=1000,
        )
        write_turn(
            log,
            turn_id="terra-xhigh",
            model="gpt-5.6-terra",
            effort="xhigh",
            prompt=PROMPT,
            cwd="/private/repo",
            duration_ms=2000,
            total_tokens=2000,
        )
        write_turn(
            log,
            turn_id="sol-xhigh",
            model="gpt-5.6-sol",
            effort="xhigh",
            prompt="A different task that must not enter the SOL-high arm.",
            cwd="/private/repo",
            duration_ms=3000,
            total_tokens=3000,
        )
        self.turns = parse_jsonl(log, self.root)
        self.report = build_report(
            self.turns,
            [
                ("sol-hi", ("gpt-5.6-sol", "high")),
                ("tera-xhi", ("gpt-5.6-terra", "xhigh")),
            ],
            max_candidates=10,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_model_and_effort_form_the_treatment_identity(self) -> None:
        self.assertEqual(self.report["arms"]["sol-hi"]["metrics"]["turns"], 1)
        self.assertEqual(self.report["arms"]["tera-xhi"]["metrics"]["turns"], 1)
        self.assertEqual(self.report["arms"]["sol-hi"]["metrics"]["median_total_tokens"], 1000)
        self.assertEqual(self.report["arms"]["tera-xhi"]["metrics"]["median_duration_ms"], 2000)

    def test_exact_repeats_become_controlled_harness_candidates(self) -> None:
        self.assertEqual(self.report["exact_repeat_candidate_count"], 1)
        candidate = self.report["exact_repeat_candidates"][0]
        self.assertEqual(candidate["arms"], {"sol-hi": 1, "tera-xhi": 1})
        self.assertEqual(self.report["next_step"]["artifact_schema"], "abc-comparison/v2")

    def test_forked_rollout_copies_do_not_count_as_executions(self) -> None:
        report = build_report(
            self.turns + self.turns,
            [
                ("sol-hi", ("gpt-5.6-sol", "high")),
                ("tera-xhi", ("gpt-5.6-terra", "xhigh")),
            ],
            max_candidates=10,
        )
        self.assertEqual(report["arms"]["sol-hi"]["metrics"]["turns"], 1)
        self.assertEqual(report["arms"]["tera-xhi"]["metrics"]["turns"], 1)
        self.assertEqual(report["exact_repeat_candidates"][0]["arms"], {"sol-hi": 1, "tera-xhi": 1})
        self.assertGreater(
            report["evidence"]["input_turn_records"],
            report["evidence"]["unique_treatment_turns"],
        )

    def test_output_never_contains_prompt_or_workspace_text(self) -> None:
        rendered = json.dumps(self.report)
        self.assertNotIn(PROMPT, rendered)
        self.assertNotIn("/private/repo", rendered)
        self.assertFalse(self.report["privacy"]["prompt_text_emitted"])
        self.assertFalse(self.report["privacy"]["workspace_names_emitted"])

    def test_cohort_metrics_are_labeled_directional(self) -> None:
        self.assertEqual(len(self.report["directional_cohorts"]), 1)
        cohort = self.report["directional_cohorts"][0]
        self.assertEqual(cohort["evidence_level"], "directional_observational")
        self.assertEqual(self.report["evidence"]["quality_verdict"], "unsupported")
        self.assertEqual(
            self.report["arms"]["sol-hi"]["metrics"]["verification_signal_turns"],
            1,
        )
        self.assertEqual(
            self.report["arms"]["sol-hi"]["metrics"]["verification_signal_rate"],
            1.0,
        )

    def test_injected_user_envelopes_do_not_replace_human_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            root = Path(tmp_raw)
            log = root / "rollout.jsonl"
            injected_context = (
                "<recommended_plugins>\nplugin metadata\n</recommended_plugins>\n"
                "<environment_context>host metadata</environment_context>"
            )
            injected_skill = (
                "<skill>\n<name>build-loop:build-loop</name>\n"
                "<path>/private/cache/SKILL.md</path>\nshared skill body\n</skill>"
            )
            write_turn(
                log,
                turn_id="sol",
                model="gpt-5.6-sol",
                effort="high",
                prompt="Audit the parser before changing it.",
                cwd="/private/repo",
                duration_ms=1000,
                total_tokens=1000,
                before_prompt=(injected_context,),
                after_prompt=(injected_skill,),
            )
            write_turn(
                log,
                turn_id="terra",
                model="gpt-5.6-terra",
                effort="xhigh",
                prompt="Implement the approved parser change.",
                cwd="/private/repo",
                duration_ms=1000,
                total_tokens=1000,
                before_prompt=(injected_context,),
                after_prompt=(injected_skill,),
            )
            turns = parse_jsonl(log, root)
            report = build_report(
                turns,
                [
                    ("sol-hi", ("gpt-5.6-sol", "high")),
                    ("tera-xhi", ("gpt-5.6-terra", "xhigh")),
                ],
                max_candidates=10,
            )

        self.assertEqual(report["exact_repeat_candidate_count"], 0)
        self.assertEqual(
            {turn.turn_id: turn.task_class for turn in turns},
            {"sol": "code_review", "terra": "code_change"},
        )

    def test_session_cumulative_tokens_are_not_reported_as_turn_usage(self) -> None:
        turn = Turn(turn_id="legacy", session="legacy.jsonl")
        apply_token_usage(
            turn,
            {"total_token_usage": {"input_tokens": 9000, "total_tokens": 9999}},
        )
        self.assertEqual(turn.tokens, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
