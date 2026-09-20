# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""The review surface must read the canonical queue, not only the legacy path."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import knowledge_review as kr  # noqa: E402


def test_legacy_episodic_queue_still_wins_when_present(tmp_path: Path) -> None:
    legacy = tmp_path / ".episodic" / "decisions" / "_review"
    legacy.mkdir(parents=True)
    assert kr.review_dir_for(tmp_path) == legacy


def test_canonical_project_queue_is_used_when_no_legacy_dir(tmp_path: Path) -> None:
    resolved = kr.review_dir_for(tmp_path)
    assert resolved.name == "_review"
    assert ".episodic" not in str(resolved)
    assert "decisions" in str(resolved)
