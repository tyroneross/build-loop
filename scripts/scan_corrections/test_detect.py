# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for tier-1 deterministic correction detector."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from scan_corrections.detect import (  # noqa: E402
    Candidate,
    CorrectionDetector,
    _dedup,
    detect_candidates,
    iter_user_turns_from_jsonl,
)


class TestCorrectionPatterns:
    def test_revert_directive_detected(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("Revert that — wrong file.", turn_index=0)
        assert any(c.kind == "correction" and c.signal_type == "revert" for c in cs)

    def test_negative_directive_detected(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("Don't touch the codex branch.", turn_index=0)
        assert any(c.kind == "correction" and c.signal_type == "negative_directive" for c in cs)

    def test_wrong_approach_detected(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("That's wrong — start over.", turn_index=0)
        assert any(c.kind == "correction" for c in cs)

    def test_correction_carries_prior_acted_flag(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("Revert that.", turn_index=0, prior_assistant_acted=True)
        corrections = [c for c in cs if c.kind == "correction"]
        assert corrections
        assert corrections[0].extras.get("prior_assistant_acted") is True


class TestPreferencePatterns:
    def test_always_detected(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("Always use uv for Python, never pip.", turn_index=0)
        kinds = {(c.kind, c.signal_type) for c in cs}
        assert ("preference", "always") in kinds
        assert ("preference", "never") in kinds

    def test_must_detected(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("New scripts must include a colocated test file.", turn_index=0)
        assert any(c.signal_type == "must" for c in cs)

    def test_default_to_detected(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("Default to Sonnet for execution.", turn_index=0)
        assert any(c.signal_type == "default" for c in cs)

    def test_we_use_for_detected(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("We use Postgres pgvector for repo memory.", turn_index=0)
        assert any(c.signal_type == "we_use_for" for c in cs)


class TestTradeoffPatterns:
    def test_instead_of_detected(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("Use sqlite instead of mongodb for local indexes.", turn_index=0)
        assert any(c.kind == "tradeoff" and c.signal_type == "instead_of" for c in cs)

    def test_actually_not_detected(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("Actually Sonnet not Haiku — verify everything.", turn_index=0)
        assert any(c.signal_type == "actually_not" for c in cs)

    def test_over_because_detected(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("Pick Apache-2.0 over GPL-3 because licensing constraints.", turn_index=0)
        assert any(c.signal_type == "over_because" for c in cs)


class TestScopeRouting:
    def test_global_scope_hint_routes_global(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("Across projects, always prefer Sonnet for subagent fan-out.", turn_index=0)
        assert any(c.scope == "global" for c in cs)

    def test_project_scope_default(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("Use sqlite instead of mongodb.", turn_index=0)
        assert all(c.scope == "project" for c in cs)


class TestAntiFalsePositive:
    def test_question_turn_skipped(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("Should we always retry on 429?", turn_index=0)
        # Question + short — should be skipped, even though `always` matches.
        assert cs == []

    def test_pure_greeting_skipped(self) -> None:
        d = CorrectionDetector()
        assert d.scan_turn("Hi", turn_index=0) == []
        assert d.scan_turn("Thanks!", turn_index=0) == []
        assert d.scan_turn("ok", turn_index=0) == []

    def test_empty_input_skipped(self) -> None:
        d = CorrectionDetector()
        assert d.scan_turn("", turn_index=0) == []
        assert d.scan_turn("   ", turn_index=0) == []

    def test_long_question_with_directive_still_captures(self) -> None:
        """Long turn with both a question AND a directive — directive wins."""
        d = CorrectionDetector()
        text = (
            "I'm thinking about strategy here. Always prefer the durable fix; "
            "use sqlite instead of a remote DB. Does that match what we've been doing?"
        )
        cs = d.scan_turn(text, turn_index=0)
        assert any(c.kind == "preference" for c in cs)
        assert any(c.kind == "tradeoff" for c in cs)


class TestCandidateHashing:
    def test_id_hash_stable(self) -> None:
        c1 = Candidate(
            kind="correction",
            signal_type="revert",
            quote="revert that",
            context="ctx",
            confidence="confirmed",
            scope="project",
            turn_index=0,
            captured_chars=11,
        )
        c2 = Candidate(
            kind="correction",
            signal_type="revert",
            quote="revert that",
            context="different ctx",  # context should NOT affect hash
            confidence="confirmed",
            scope="project",
            turn_index=5,  # turn_index should NOT affect hash
            captured_chars=11,
        )
        assert c1.id_hash == c2.id_hash

    def test_dedup_drops_duplicates(self) -> None:
        c1 = Candidate("correction", "revert", "revert that", "a", "confirmed", "project", 0, 11)
        c2 = Candidate("correction", "revert", "revert that", "b", "confirmed", "project", 1, 11)
        c3 = Candidate("preference", "always", "always X", "c", "confirmed", "project", 2, 8)
        out = _dedup([c1, c2, c3])
        assert len(out) == 2
        assert out[0].id_hash == c1.id_hash
        assert out[1].id_hash == c3.id_hash


class TestTranscriptParsing:
    def test_parse_jsonl_message_shape(self, tmp_path: Path) -> None:
        t = tmp_path / "transcript.jsonl"
        records = [
            {"type": "user", "message": {"role": "user", "content": "Hi"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Edit"},
                {"type": "text", "text": "Done."},
            ]}},
            {"type": "user", "message": {"role": "user", "content": "Revert that."}},
        ]
        t.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        turns = list(iter_user_turns_from_jsonl(t))
        assert len(turns) == 2
        assert turns[0] == (0, "Hi", False)
        # Prior assistant turn used a tool → prior_assistant_acted=True for the next user turn.
        assert turns[1] == (1, "Revert that.", True)

    def test_parse_jsonl_skips_malformed_lines(self, tmp_path: Path) -> None:
        t = tmp_path / "bad.jsonl"
        t.write_text(
            '{"type":"user","message":{"role":"user","content":"ok"}}\n'
            "not-json-at-all\n"
            '{"type":"user","message":{"role":"user","content":"Always X"}}\n',
            encoding="utf-8",
        )
        turns = list(iter_user_turns_from_jsonl(t))
        assert len(turns) == 2

    def test_missing_transcript_yields_nothing(self, tmp_path: Path) -> None:
        assert list(iter_user_turns_from_jsonl(tmp_path / "nope.jsonl")) == []

    def test_isMeta_records_excluded(self, tmp_path: Path) -> None:
        """isMeta records (Stop-hook injections, skill-load bodies,
        command-template scaffolding) carry type='user' / role='user' but
        must NOT count as human turns or generate correction candidates.

        Evidence — v0.29.1 retrospective sections.py fix on transcript
        dfe491e3-…: 4× Stop-hook 'git diff' + 1 SPDX skill body + 1 skill
        base-dir scaffolding all sit as type=user/isMeta=true. Without this
        guard, Stop-hook 'revert the commit' or 'doesn't work' text in
        injected diffs would fire correction candidates.
        """
        t = tmp_path / "meta_mixed.jsonl"
        records = [
            # Genuine human prompt — should be kept.
            {"type": "user", "message": {"role": "user", "content": "Hi"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Edit"},
                {"type": "text", "text": "Done."},
            ]}},
            # Stop-hook injection — isMeta=true, must be skipped even though
            # the text contains a correction-keyword ("revert").
            {"type": "user", "isMeta": True, "message": {"role": "user",
                "content": "Stop hook feedback: revert that staged change"}},
            # Skill-load body — isMeta=true, SPDX-style content.
            {"type": "user", "isMeta": True, "message": {"role": "user",
                "content": "# SPDX-FileCopyrightText: that doesn't work for our license"}},
            # Slash-command template — isMeta=true.
            {"type": "user", "isMeta": True, "message": {"role": "user",
                "content": "<command-name>foo</command-name>\nThis is wrong, undo"}},
            # Genuine human correction — should be detected.
            {"type": "user", "message": {"role": "user", "content": "Revert that."}},
        ]
        t.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        turns = list(iter_user_turns_from_jsonl(t))
        # Only the two genuine user prompts survive.
        assert len(turns) == 2, f"expected 2 real user turns, got {len(turns)}: {turns}"
        assert turns[0] == (0, "Hi", False)
        assert turns[1] == (1, "Revert that.", True)

        # End-to-end: detect_candidates must surface ONE correction (the real
        # 'Revert that.' turn) and zero candidates from any of the three
        # isMeta records — even though they contain 'revert', "doesn't work",
        # and 'undo' text that would otherwise fire.
        candidates = detect_candidates(transcript_path=t)
        for c in candidates:
            assert "Stop hook feedback" not in c.quote, (
                f"isMeta Stop-hook leaked into candidate: {c.quote!r}")
            assert "SPDX" not in c.quote, (
                f"isMeta skill-load leaked into candidate: {c.quote!r}")
            assert "command-name" not in c.quote, (
                f"isMeta command-template leaked into candidate: {c.quote!r}")
        # The real 'Revert that.' should be detected as a correction.
        assert any(
            c.signal_type == "revert" and "Revert that" in c.quote
            for c in candidates
        ), f"genuine 'Revert that.' correction not detected: {candidates}"


class TestDetectCandidatesAPI:
    def test_text_turns_path(self) -> None:
        cs = detect_candidates(
            text_turns=[
                "Revert that.",
                "Always use uv for Python.",
                "Use sqlite instead of mongodb.",
            ]
        )
        kinds = sorted({c.kind for c in cs})
        assert kinds == ["correction", "preference", "tradeoff"]

    def test_transcript_path(self, tmp_path: Path) -> None:
        t = tmp_path / "tx.jsonl"
        recs = [
            {"role": "user", "content": "Always use uv."},
            {"role": "assistant", "content": [{"type": "tool_use", "name": "Edit"}]},
            {"role": "user", "content": "Revert that."},
        ]
        t.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
        cs = detect_candidates(t)
        kinds = {c.kind for c in cs}
        assert "preference" in kinds
        assert "correction" in kinds
        # The correction should carry the prior_assistant_acted flag.
        corr = next(c for c in cs if c.kind == "correction")
        assert corr.extras.get("prior_assistant_acted") is True

    def test_none_inputs_return_empty(self) -> None:
        assert detect_candidates() == []

    def test_dedup_across_turns(self) -> None:
        cs = detect_candidates(text_turns=["Revert that.", "Revert that.", "Revert that."])
        assert len(cs) == 1


class TestF3FalsePositiveFixes:
    """f3 — false positives that were wrongly captured before the fix."""

    def test_never_mind_is_skipped(self) -> None:
        d = CorrectionDetector()
        assert d.scan_turn("never mind the error", 0) == []

    def test_never_mind_with_punctuation_is_skipped(self) -> None:
        d = CorrectionDetector()
        assert d.scan_turn("Never mind the error.", 0) == []

    def test_must_have_been_is_skipped(self) -> None:
        d = CorrectionDetector()
        assert d.scan_turn("must have been my mistake", 0) == []

    def test_must_be_causing_is_skipped(self) -> None:
        d = CorrectionDetector()
        assert d.scan_turn("must be causing the timeout", 0) == []

    def test_should_probably_check_is_skipped(self) -> None:
        d = CorrectionDetector()
        assert d.scan_turn("should probably check", 0) == []

    def test_stop_overthinking_is_skipped(self) -> None:
        d = CorrectionDetector()
        assert d.scan_turn("stop overthinking it", 0) == []

    # True positives must still fire.
    def test_revert_that_still_fires(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("revert that global change", 0)
        assert any(c.signal_type == "revert" for c in cs)

    def test_must_work_on_any_machine_still_fires(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("it must work on any user machine", 0)
        assert any(c.signal_type == "must" for c in cs)

    def test_always_use_project_config_still_fires(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("always use project config", 0)
        assert any(c.signal_type == "always" for c in cs)

    def test_you_must_always_fires(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("you must always X", 0)
        assert any(c.signal_type == "must" for c in cs)

    def test_never_store_secrets_fires(self) -> None:
        d = CorrectionDetector()
        cs = d.scan_turn("never store secrets in code", 0)
        assert any(c.signal_type == "never" for c in cs)

    def test_combined_revert_plus_must_work(self) -> None:
        """'revert that, it must work on any machine' → both revert + must fire."""
        d = CorrectionDetector()
        cs = d.scan_turn("revert that, it must work on any machine", 0)
        assert any(c.signal_type == "revert" for c in cs)
        assert any(c.signal_type == "must" for c in cs)


class TestBoundaryConditions:
    def test_max_candidates_per_turn_respected(self) -> None:
        d = CorrectionDetector(max_candidates_per_turn=2)
        # A turn with many patterns — but only 2 should land.
        text = "Always X. Never Y. Default to Z. We use A for B."
        cs = d.scan_turn(text, turn_index=0)
        assert len(cs) <= 2

    def test_context_window_clamps_to_text_bounds(self) -> None:
        d = CorrectionDetector(context_window=500)
        cs = d.scan_turn("Revert that.", turn_index=0)
        assert cs
        # Context can't exceed full text length.
        assert len(cs[0].context) <= len("Revert that.")
