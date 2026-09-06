#!/usr/bin/env python3
"""Regression contract for the public/private documentation boundary."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_policy_is_wired_into_review_and_fact_checking() -> None:
    skill = read("skills/build-loop/SKILL.md")
    fact_checker = read("agents/fact-checker.md")
    review = read("references/phase-4-review.md")
    policy = read("references/public-repository-documentation-boundary.md")

    assert "public-repository-documentation-boundary.md" in skill
    assert "public_current" in fact_checker
    assert "private_archived" in fact_checker
    assert "private-memory receipt" in skill
    assert "blocks the documentation review" in skill
    assert "`blocked[]` is a Review failure" in review
    assert "Private repositories retain" in review
    assert "Require a successful writer receipt before deleting" in policy
    assert "Deleting a file from the current tree does not remove it from Git history" in policy
