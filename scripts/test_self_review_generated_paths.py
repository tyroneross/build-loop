#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the shared generated-artifact definition and its two consumers.

The defect these lock down: `scan_churn` ranked files by how often git touched
them in a rolling window, so build products a commit hook regenerates on every
commit always topped the list. 180 of 1,233 queued proposals were that one
false-positive class. Suppression used to exist only in the drain, downstream
of a producer that kept emitting.

`scan_churn` is exercised against a REAL temporary git repository rather than a
stubbed `git log`, so the test fails if the churn scan's git plumbing breaks —
not only if the filter predicate does.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from self_review.efficiency import scan_churn  # noqa: E402
from self_review.generated_paths import (  # noqa: E402
    GENERATED_PATH_HINTS,
    is_generated_path,
    matched_generated_hint,
)

# Enough commits to clear efficiency._CHURN_THRESHOLD (5).
_TOUCHES = 7

GENERATED = "architecture/model.json"
AUTHORED = "scripts/real_source.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A git repo where one generated and one authored file both churn hard."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    for i in range(_TOUCHES):
        for rel in (GENERATED, AUTHORED):
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"revision {i}\n")
        _git(tmp_path, "add", GENERATED, AUTHORED)
        _git(tmp_path, "commit", "-q", "-m", f"touch {i}")
    return tmp_path


def _churn_files(repo: Path) -> list[str]:
    errors: list[str] = []
    findings = scan_churn(repo, window_days=7, errors=errors)
    assert errors == [], f"churn scan reported errors: {errors}"
    assert all(f["kind"] == "high_churn_file" for f in findings)
    return [f["evidence"] for f in findings]


def test_generated_artifact_produces_no_churn_finding(repo: Path):
    """The regression: a build product must not be reported as a hot spot."""
    assert not any(GENERATED in ev for ev in _churn_files(repo))


def test_authored_source_still_produces_a_churn_finding(repo: Path):
    """The other half — suppression must not silence real authored churn."""
    assert any(AUTHORED in ev for ev in _churn_files(repo))


def test_generated_artifact_never_crowds_out_authored_churn(tmp_path: Path):
    """Filtering happens BEFORE ranking, not after the top-5 slice.

    `scan_churn` only ever considers the 5 hottest files. Six build products
    churning harder than the authored file fill that window completely, so a
    filter applied AFTER the slice returns nothing at all — the authored hot
    spot is never even seen. Filtering before the slice is what makes it
    visible. A two-file fixture cannot tell the two orderings apart.
    """
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")

    generated = [
        "architecture/model.json",
        "architecture/ARCHITECTURE.md",
        "docs/build-loop-flow-mockup.html",
        "docs/INDEX.md",
        "docs/INDEX.jsonl",
        ".build-loop/state.json",
    ]
    assert len(generated) > 5, "fixture must exceed scan_churn's top-5 window"

    # Every build product out-churns the authored file, so on a post-slice
    # filter the authored file never reaches the ranking at all.
    for i in range(_TOUCHES + 3):
        paths = list(generated)
        if i < _TOUCHES:
            paths.append(AUTHORED)
        for rel in paths:
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"revision {i}\n")
        _git(tmp_path, "add", *paths)
        _git(tmp_path, "commit", "-q", "-m", f"touch {i}")

    evidence = _churn_files(tmp_path)
    assert any(AUTHORED in ev for ev in evidence), evidence
    assert not any(
        g in ev for ev in evidence for g in generated
    ), evidence


def test_drain_and_producer_share_one_definition():
    """Two copies of this list is how the two sides drift back apart."""
    import drain_self_review_proposals as drain_mod

    assert drain_mod.GENERATED_PATH_HINTS is GENERATED_PATH_HINTS


@pytest.mark.parametrize("hint", GENERATED_PATH_HINTS)
def test_every_hint_is_recognised(hint: str):
    assert is_generated_path(f"some/prefix/{hint}")


def test_authored_paths_are_not_generated():
    for path in (
        "scripts/self_review/efficiency.py",
        "agents/build-orchestrator.md",
        "docs/architecture-notes.md",
    ):
        assert not is_generated_path(path), path


def test_matched_hint_names_the_reason():
    assert matched_generated_hint("churn on .build-loop/state.json") == ".build-loop/"
    assert matched_generated_hint("scripts/foo.py") is None
    assert matched_generated_hint("") is None


def test_drain_blob_shape_is_still_matched():
    """The drain passes free text, not a clean path. Substring must hold."""
    import drain_self_review_proposals as drain_mod

    suppressed, reason = drain_mod.is_non_actionable({
        "kind": "self_missing_test",
        "finding": "'architecture/model.json' has no colocated test",
        "script": "",
    })
    assert suppressed
    assert "generated artifact" in reason
