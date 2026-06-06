# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Doctrinal invariant tests for the mandatory-Phase-6 doc rewrite (v0.30.0).

These tests enforce the F-criteria from
``docs/specs/2026-06-06-mandatory-learn-design.md``:

1. Phase 6 is documented as **mandatory** (not optional) in all 6 doc files.
2. ``autoSelfImprove`` is documented as a **migration no-op** wherever it
   appears in those files.
3. The new helper script imports cleanly and exposes ``scan(workdir)``.
4. The gating-outcomes block (accruing / deferred / full) is present in the
   three primary doctrinal files.
5. The detector agent's brief carries the second signal source
   (``enforce-from-retro/`` → ``enforce_recurrence``).
6. Version bump 0.30.0 has landed in all three manifest files.

Run under: ``env -u PYTHONPATH python3 -m pytest tests/test_phase_6_gating_docs.py``
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files that carry the mandatory-Phase-6 doctrine. Edits to any of these
# without updating the others is the failure mode we are guarding against.
DOC_FILES_PHASE_6 = [
    REPO_ROOT / "skills" / "build-loop" / "references" / "phase-6-learn.md",
    REPO_ROOT / "references" / "learn-protocol.md",
    REPO_ROOT / "agents" / "build-orchestrator.md",
    REPO_ROOT / "skills" / "build-loop" / "SKILL.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "CLAUDE.md",
]

# Subset where the full gating-outcomes block must appear verbatim. These
# are the three primary doctrinal docs; the others only summarize.
GATING_OUTCOMES_FILES = [
    REPO_ROOT / "skills" / "build-loop" / "references" / "phase-6-learn.md",
    REPO_ROOT / "references" / "learn-protocol.md",
    REPO_ROOT / "agents" / "build-orchestrator.md",
]

# Manifest files that share a top-level version number.
VERSION_FILES = [
    REPO_ROOT / ".claude-plugin" / "plugin.json",
    REPO_ROOT / "package.json",
    REPO_ROOT / ".codex-plugin" / "plugin.json",
]
EXPECTED_VERSION = "0.30.0"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# Match lines that contain BOTH "(optional)" and a Phase-6/Learn reference
# nearby. We scope to the same line to avoid false positives on unrelated
# "(optional)" occurrences (e.g. mockup-gallery sections, optional config keys).
_OPTIONAL_PHASE_6 = re.compile(
    r"^(?=.*\(optional\))(?=.*(?:Phase\s*6|Learn)).*$",
    re.M,
)


@pytest.mark.parametrize("path", DOC_FILES_PHASE_6, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_phase_6_is_not_marked_optional(path: Path) -> None:
    """No line mentions Phase 6 / Learn AND "(optional)" together."""
    body = _read(path)
    hits = _OPTIONAL_PHASE_6.findall(body)
    assert not hits, (
        f"{path.relative_to(REPO_ROOT)} still contains a Phase-6/Learn line "
        f"marked '(optional)':\n  " + "\n  ".join(hits[:5])
    )


@pytest.mark.parametrize("path", DOC_FILES_PHASE_6, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_phase_6_is_marked_mandatory(path: Path) -> None:
    """Each Phase-6 doc carries the word 'mandatory' near Phase 6 / Learn."""
    body = _read(path)
    # A line, paragraph, or table cell containing 'Phase 6' or 'Learn' AND
    # 'mandatory' within 400 chars on either side (loose proximity).
    found = False
    for m in re.finditer(r"(?:Phase\s*6|Learn)", body):
        start = max(0, m.start() - 400)
        end = min(len(body), m.end() + 400)
        window = body[start:end].lower()
        if "mandatory" in window:
            found = True
            break
    assert found, (
        f"{path.relative_to(REPO_ROOT)} mentions Phase 6 / Learn but no "
        "occurrence of 'mandatory' within 400 chars of any mention."
    )


@pytest.mark.parametrize("path", DOC_FILES_PHASE_6, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_autoSelfImprove_is_documented_as_migration_no_op(path: Path) -> None:
    """Every mention of ``autoSelfImprove`` in these files is paired with
    ``migration no-op`` or ``deprecated`` nearby (within 600 chars)."""
    body = _read(path)
    for m in re.finditer(r"autoSelfImprove", body):
        start = max(0, m.start() - 600)
        end = min(len(body), m.end() + 600)
        window = body[start:end].lower()
        assert "migration no-op" in window or "deprecated" in window, (
            f"{path.relative_to(REPO_ROOT)} mentions 'autoSelfImprove' at "
            f"offset {m.start()} without 'migration no-op' or 'deprecated' "
            "within 600 chars."
        )


@pytest.mark.parametrize("path", GATING_OUTCOMES_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_gating_outcomes_block_is_present(path: Path) -> None:
    """The three-state outcome documentation (accruing / deferred / full)
    is present in each primary doctrinal file."""
    body = _read(path).lower()
    for state in ("accruing", "deferred"):
        assert state in body, (
            f"{path.relative_to(REPO_ROOT)} is missing the gating-outcome "
            f"state '{state}'."
        )
    # 'full' is too common to grep alone; require the canonical "Learn:"
    # report-line wording for accruing as a stronger marker.
    assert "learn: accruing" in body, (
        f"{path.relative_to(REPO_ROOT)} is missing the 'Learn: accruing "
        "(N/3 runs)' Review-G report-line example."
    )


def test_helper_script_imports_and_exposes_scan() -> None:
    """``scripts/enforce_retro_signals.py`` imports without error and
    exposes ``scan(workdir)`` returning the documented envelope shape."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import enforce_retro_signals as ers  # noqa: E402

    assert callable(getattr(ers, "scan", None)), "scan() not exposed"
    out = ers.scan(REPO_ROOT / "_does_not_exist")
    assert out == {"scannedFiles": 0, "patterns": []}, (
        "scan(missing-dir) must return empty envelope, not raise."
    )


def test_detector_agent_documents_second_signal_source() -> None:
    """``agents/recurring-pattern-detector.md`` documents Signal source 2."""
    body = _read(REPO_ROOT / "agents" / "recurring-pattern-detector.md")
    # Must reference the proposals dir and the new pattern type.
    assert "enforce-from-retro" in body, (
        "recurring-pattern-detector.md must mention `enforce-from-retro/`."
    )
    assert "enforce_recurrence" in body, (
        "recurring-pattern-detector.md must document the new "
        "`enforce_recurrence` pattern type."
    )
    # Must mention the helper script as an option, but the agent must still
    # be able to read the dir directly.
    assert "enforce_retro_signals.py" in body, (
        "recurring-pattern-detector.md must cite the helper script "
        "`scripts/enforce_retro_signals.py`."
    )


@pytest.mark.parametrize("path", VERSION_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_version_is_bumped_to_0_30_0(path: Path) -> None:
    """Manifest files share version ``0.30.0``."""
    data = json.loads(_read(path))
    assert data.get("version") == EXPECTED_VERSION, (
        f"{path.relative_to(REPO_ROOT)} has version={data.get('version')!r}, "
        f"expected {EXPECTED_VERSION!r}."
    )
