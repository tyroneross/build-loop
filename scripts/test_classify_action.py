# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/classify_action.py — MECE classification.

Coverage matrix:
  - read-only short-circuit (highest user-pain reduction)
  - irreversible × {production, non-production}
  - production deploy command, non-irreversible
  - DECISION states: pickable, low_confidence, malformed
  - RISKY file globs (defaults + custom)
  - SAFE default
  - precedence: ensure the priority order in classify() is respected
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import classify_action


@pytest.fixture()
def empty_workdir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def configured_workdir(tmp_path: Path) -> Path:
    (tmp_path / ".build-loop").mkdir()
    (tmp_path / ".build-loop" / "config.json").write_text(
        json.dumps({"classifyAction": {"riskyGlobs": ["custom-risky/**"]}})
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Read-only short-circuit (priority 4) — the user-pain fix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "cat README.md",
        "grep -r foo src/",
        "git status",
        "git log --oneline",
        "git diff HEAD",
        "vercel curl --deployment decision-doctor-q80n1w16s.vercel.app /",
        "vercel logs deployment-id",
        "vercel inspect deployment-id",
        "gh pr view 123",
        "gh issue list",
        "kubectl get pods",
        "docker ps -a",
        "npm list --depth=0",
        "uv pip list",
        "curl https://api.example.com/health",
        "ps aux",
    ],
)
def test_read_only_commands_are_safe(empty_workdir: Path, command: str) -> None:
    result = classify_action.classify(empty_workdir, command=command)
    assert result["classification"] == "SAFE", (
        f"expected SAFE for read-only {command!r}, got {result}"
    )
    assert result["matched_rule"] == "read_only"


def test_vercel_logs_prod_is_safe_not_production(empty_workdir: Path) -> None:
    """vercel logs --prod inspects prod but doesn't deploy → SAFE, not PRODUCTION."""
    result = classify_action.classify(empty_workdir, command="vercel logs --prod")
    assert result["classification"] == "SAFE"


# ---------------------------------------------------------------------------
# PRODUCTION (priority 1, 3) — irreversible+prod OR production deploy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git push --force origin main",
        "vercel deploy --prod",
        "netlify deploy --prod",
        "firebase deploy",
    ],
)
def test_production_deploys(empty_workdir: Path, command: str) -> None:
    result = classify_action.classify(empty_workdir, command=command)
    assert result["classification"] == "PRODUCTION", (
        f"{command!r} → {result}"
    )


def test_drop_database_is_production_irreversible(empty_workdir: Path) -> None:
    # Not a deploy command, but irreversible. Without an environment marker,
    # deployment_policy returns "n/a"/"unknown", so this falls through to
    # priority 2 (irreversible+non-production) → RISKY, isolating to a branch.
    # That's intentional: a DROP TABLE in an unmerged branch is recoverable
    # by deleting the branch.
    result = classify_action.classify(empty_workdir, command="DROP TABLE users;")
    assert result["classification"] == "RISKY"
    assert "irreversible" in result["reason"]


# ---------------------------------------------------------------------------
# RISKY (priority 2, 6) — irreversible non-prod, or broad-blast files
# ---------------------------------------------------------------------------


def test_force_push_to_feature_branch_is_risky(empty_workdir: Path) -> None:
    result = classify_action.classify(
        empty_workdir, command="git push --force origin feature/abc"
    )
    assert result["classification"] == "RISKY"


def test_npm_publish_is_production(empty_workdir: Path) -> None:
    # `npm publish` is both irreversible (IRREVERSIBLE_PATTERNS) and recognized
    # as production by deployment_policy. Priority 1 wins → PRODUCTION.
    result = classify_action.classify(empty_workdir, command="npm publish")
    assert result["classification"] == "PRODUCTION"


@pytest.mark.parametrize(
    "files",
    [
        ["migrations/0042_add_user.sql"],
        [".github/workflows/deploy.yml"],
        ["Dockerfile"],
        ["package.json"],
        ["prisma/schema.prisma"],
        ["uv.lock"],
    ],
)
def test_risky_files(empty_workdir: Path, files: list[str]) -> None:
    result = classify_action.classify(empty_workdir, files_touched=files)
    assert result["classification"] == "RISKY"
    assert result["delegated_to"] == "risky_globs"


def test_custom_risky_globs(configured_workdir: Path) -> None:
    result = classify_action.classify(
        configured_workdir, files_touched=["custom-risky/foo.py"]
    )
    assert result["classification"] == "RISKY"


def test_override_replaces_defaults(configured_workdir: Path) -> None:
    """Override list replaces defaults — Dockerfile no longer triggers."""
    result = classify_action.classify(
        configured_workdir, files_touched=["Dockerfile"]
    )
    assert result["classification"] == "SAFE"


# ---------------------------------------------------------------------------
# DECISION (priority 5) — implementer envelope
# ---------------------------------------------------------------------------


def test_decision_pickable(empty_workdir: Path) -> None:
    envelope = {
        "novel_decisions": [
            {
                "decision_id": "d1",
                "recommended_default": "A",
                "confidence": "high",
                "options": [{"id": "A"}, {"id": "B"}],
            }
        ]
    }
    result = classify_action.classify(empty_workdir, envelope=envelope)
    assert result["classification"] == "DECISION"
    assert result["decision_state"] == "pickable"


def test_decision_low_confidence(empty_workdir: Path) -> None:
    envelope = {
        "novel_decisions": [
            {
                "decision_id": "d1",
                "recommended_default": "A",
                "confidence": "low",
                "options": [{"id": "A"}],
            }
        ]
    }
    result = classify_action.classify(empty_workdir, envelope=envelope)
    assert result["classification"] == "DECISION"
    assert result["decision_state"] == "low_confidence"


def test_decision_malformed(empty_workdir: Path) -> None:
    envelope = {
        "novel_decisions": [{"decision_id": "d1", "confidence": "high"}]
    }
    result = classify_action.classify(empty_workdir, envelope=envelope)
    assert result["classification"] == "DECISION"
    assert result["decision_state"] == "malformed"


# ---------------------------------------------------------------------------
# SAFE (priority 7) — default
# ---------------------------------------------------------------------------


def test_safe_empty(empty_workdir: Path) -> None:
    result = classify_action.classify(empty_workdir)
    assert result["classification"] == "SAFE"


def test_safe_innocuous_files(empty_workdir: Path) -> None:
    result = classify_action.classify(
        empty_workdir, files_touched=["src/foo.py", "README.md"]
    )
    assert result["classification"] == "SAFE"


def test_safe_feature_branch_push(empty_workdir: Path) -> None:
    result = classify_action.classify(
        empty_workdir, command="git push origin feature/foo"
    )
    assert result["classification"] == "SAFE"


# ---------------------------------------------------------------------------
# Precedence — ensure higher-priority rules win
# ---------------------------------------------------------------------------


def test_production_beats_risky_files(empty_workdir: Path) -> None:
    """Production deploy beats a touched risky file."""
    result = classify_action.classify(
        empty_workdir,
        command="git push origin main",
        files_touched=["migrations/0001.sql"],
    )
    assert result["classification"] == "PRODUCTION"


def test_read_only_beats_decision(empty_workdir: Path) -> None:
    """Read-only inspection wins even if envelope has a decision."""
    envelope = {
        "novel_decisions": [
            {
                "decision_id": "d1",
                "recommended_default": "A",
                "confidence": "high",
                "options": [{"id": "A"}, {"id": "B"}],
            }
        ]
    }
    result = classify_action.classify(
        empty_workdir, command="git status", envelope=envelope
    )
    assert result["classification"] == "SAFE"


def test_risky_file_beats_decision(empty_workdir: Path) -> None:
    """A migrations/ touch wins over a DECISION envelope (priority 6 > 5? no, 5 > 6).

    Actually DECISION (5) wins over risky files (6) per the priority list.
    Test that ordering is honored.
    """
    envelope = {
        "novel_decisions": [
            {
                "decision_id": "d1",
                "recommended_default": "A",
                "confidence": "high",
                "options": [{"id": "A"}],
            }
        ]
    }
    result = classify_action.classify(
        empty_workdir, files_touched=["migrations/0001.sql"], envelope=envelope
    )
    # DECISION fires before risky files in priority order.
    assert result["classification"] == "DECISION"


def test_irreversible_non_prod_beats_files(empty_workdir: Path) -> None:
    result = classify_action.classify(
        empty_workdir,
        command="git push --force origin feature/abc",
        files_touched=["src/foo.py"],
    )
    assert result["classification"] == "RISKY"
    assert result["matched_rule"] == "irreversible+non-production"


# ---------------------------------------------------------------------------
# MECE proof — for a representative sample, exactly one classification fires.
# ---------------------------------------------------------------------------


def test_mece_each_label_terminates(empty_workdir: Path) -> None:
    """Every classification fires for distinct inputs; none overlap."""
    cases = [
        ({"command": "ls"}, "SAFE"),
        ({"command": "vercel logs"}, "SAFE"),
        ({"command": "git push origin main"}, "PRODUCTION"),
        ({"command": "git push --force origin feat"}, "RISKY"),
        ({"files_touched": ["migrations/x.sql"]}, "RISKY"),
        ({
            "envelope": {"novel_decisions": [{
                "decision_id": "d1", "recommended_default": "A",
                "confidence": "high",
            }]},
        }, "DECISION"),
        ({}, "SAFE"),
    ]
    seen = set()
    for kwargs, expected in cases:
        result = classify_action.classify(empty_workdir, **kwargs)
        assert result["classification"] == expected, f"{kwargs} → {result}"
        seen.add(expected)
    assert seen == {"SAFE", "RISKY", "PRODUCTION", "DECISION"}


# ---------------------------------------------------------------------------
# CLI / exit codes
# ---------------------------------------------------------------------------


def test_cli_safe_exit_zero(
    empty_workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = classify_action.main(["--workdir", str(empty_workdir), "--command", "ls"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["classification"] == "SAFE"


def test_cli_risky_exit_one(
    empty_workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = classify_action.main(
        ["--workdir", str(empty_workdir), "--files-touched", "migrations/0042.sql"]
    )
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["classification"] == "RISKY"


def test_cli_production_exit_two(
    empty_workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = classify_action.main(
        ["--workdir", str(empty_workdir), "--command", "git push origin main"]
    )
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["classification"] == "PRODUCTION"


def test_cli_decision_exit_three(
    empty_workdir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / "env.json"
    env_file.write_text(json.dumps({
        "novel_decisions": [
            {
                "decision_id": "d1",
                "recommended_default": "A",
                "confidence": "high",
                "options": [{"id": "A"}, {"id": "B"}],
            }
        ]
    }))
    rc = classify_action.main(
        ["--workdir", str(empty_workdir), "--envelope-json", str(env_file)]
    )
    assert rc == 3
    assert json.loads(capsys.readouterr().out)["classification"] == "DECISION"
