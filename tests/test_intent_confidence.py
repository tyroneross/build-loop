# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/intent_confidence.py.

Default-high contract: the script must score "high" (auto-execute) for any
goal with zero ambiguity signals. The auto-execute fast path depends on this
script not firing false positives.

Covers each of the 7 signals plus the empty-goal edge case and the
should_explore boolean projection.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import intent_confidence as ic  # noqa: E402


# ---------------------------------------------------------------------------
# Library API — score()
# ---------------------------------------------------------------------------

def test_concrete_goal_scores_high():
    result = ic.score("Fix the typo in README.md on line 47")
    assert result["confidence"] == "high"
    assert result["signals"] == []
    assert "no ambiguity signals" in result["reason"]


def test_concrete_long_goal_scores_high():
    result = ic.score("Add a rate-limit field to the user schema and update the migration")
    assert result["confidence"] == "high"
    assert result["signals"] == []


def test_short_goal_signals():
    result = ic.score("fix it")
    assert "short_goal" in result["signals"]
    # short_goal alone (combined with no_deliverable_noun) yields medium
    assert result["confidence"] in {"medium", "low"}


def test_vague_verb_explore():
    result = ic.score("Explore the codebase and tell me what's interesting")
    assert "vague_verb" in result["signals"]
    assert result["confidence"] in {"medium", "low"}


def test_vague_verb_figure_out():
    result = ic.score("Figure out why the cache stops working after a deploy")
    assert "vague_verb" in result["signals"]


def test_branching_or_fires():
    result = ic.score("Should I add a new endpoint or extend the existing route handler")
    assert "branching_or" in result["signals"]


def test_question_mark_fires():
    result = ic.score("What should the user-facing error message look like?")
    assert "question_mark" in result["signals"]


def test_hedge_phrase_fires():
    result = ic.score("Build something like a dashboard for the build-loop run history")
    assert "hedge_phrase" in result["signals"]


def test_creative_open_brainstorm():
    result = ic.score("Brainstorm a clean rewrite of the plugin manifest format")
    assert "creative_open" in result["signals"]


def test_creative_open_greenfield():
    result = ic.score("greenfield design for a new ux-queue surface for build-loop")
    assert "creative_open" in result["signals"]


def test_no_deliverable_noun_low_concreteness():
    # No concrete noun, no path. Should fire no_deliverable_noun.
    result = ic.score("make it nicer overall and smoother to use")
    assert "no_deliverable_noun" in result["signals"]


def test_explicit_path_overrides_no_noun_signal():
    # foo/bar.py is concrete enough — should NOT fire no_deliverable_noun
    result = ic.score("update lib/auth-guard.ts to handle the new flow")
    # the path counts as concreteness even without a noun in the vocabulary
    assert "no_deliverable_noun" not in result["signals"]


def test_function_reference_is_concrete():
    result = ic.score("rename handleSubmit to onSubmit across the form components")
    # form, components are deliverable nouns; should be high
    assert result["confidence"] == "high"


def test_low_confidence_threshold():
    """3+ signals → low. Build a goal that fires several."""
    result = ic.score("explore something like a redesign or new approach?")
    # vague_verb (explore), hedge_phrase (something like), branching_or (X or Y),
    # question_mark (?), no_deliverable_noun (no concrete noun)
    assert len(result["signals"]) >= 3
    assert result["confidence"] == "low"


def test_medium_confidence_threshold():
    """1-2 signals → medium."""
    result = ic.score("Explore the new caching layer in scripts/cache_manager.py")
    # vague_verb fires, but scripts/cache_manager.py is a concrete path
    assert "vague_verb" in result["signals"]
    assert result["confidence"] in {"medium", "low"}


def test_empty_goal():
    result = ic.score("")
    assert result["confidence"] == "low"
    assert result["signals"] == ["empty_goal"]


def test_whitespace_only_goal():
    result = ic.score("   \n  \t ")
    assert result["confidence"] == "low"
    assert result["signals"] == ["empty_goal"]


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

def _run_cli(goal: str) -> dict:
    script = REPO_ROOT / "scripts" / "intent_confidence.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--goal", goal, "--json"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def test_cli_high_confidence_concrete():
    result = _run_cli("Fix typo in README.md on line 47")
    assert result["confidence"] == "high"
    assert result["should_explore"] is False


def test_cli_low_confidence_ambiguous():
    result = _run_cli("explore something like a redesign or new approach?")
    assert result["confidence"] == "low"
    assert result["should_explore"] is True


def test_cli_medium_confidence_some_signal():
    result = _run_cli("Explore the new caching layer in scripts/cache_manager.py")
    assert result["should_explore"] is True
    assert result["confidence"] in {"medium", "low"}


def test_cli_empty_goal_does_not_crash():
    """An empty --goal is unusual but must not crash; should report low."""
    result = _run_cli("")
    assert result["confidence"] == "low"
    assert result["should_explore"] is True


def test_cli_exit_code_always_zero():
    """Advisory script — never blocks. Exit 0 in all cases."""
    script = REPO_ROOT / "scripts" / "intent_confidence.py"
    for goal in ["fix typo", "", "explore something or other?"]:
        proc = subprocess.run(
            [sys.executable, str(script), "--goal", goal, "--json"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, f"non-zero exit for goal={goal!r}: {proc.stderr}"


def test_cli_non_json_output_format():
    """When --json is omitted, output is a one-line human summary."""
    script = REPO_ROOT / "scripts" / "intent_confidence.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--goal", "Fix typo in README.md line 47"],
        capture_output=True, text=True, check=True,
    )
    line = proc.stdout.strip()
    assert line.startswith("high (should_explore=False)")
