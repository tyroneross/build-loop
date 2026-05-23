# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Tests for the G5 relevance-aware registry surfaces.

Covers:
  - build_capability_registry._parse_capability_header parses an authored
    header and tolerates an absent one
  - script_relevance verdicts: deprecated/oneshot -> attic; unknown ->
    review; active+referenced -> keep
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import build_capability_registry as reg  # noqa: E402
import script_relevance as sr  # noqa: E402


# --- header parsing -------------------------------------------------------

def test_parse_full_header():
    text = (
        "#!/usr/bin/env python3\n"
        "# capability:\n"
        "#   purpose: Do a thing.\n"
        "#   application: coordination\n"
        "#   status: active\n"
        '"""docstring"""\n'
    )
    h = reg._parse_capability_header(text)
    assert h == {
        "purpose": "Do a thing.",
        "application": "coordination",
        "status": "active",
    }


def test_parse_absent_header():
    text = '#!/usr/bin/env python3\n"""just a docstring"""\n'
    assert reg._parse_capability_header(text) == {}


def test_parse_partial_header():
    text = "# capability:\n#   status: deprecated\n"
    h = reg._parse_capability_header(text)
    assert h == {"status": "deprecated"}


# --- relevance verdicts ---------------------------------------------------

def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".build-loop").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    return repo


def _write_script(repo: Path, name: str, status: str | None) -> None:
    hdr = ""
    if status is not None:
        hdr = (
            "# capability:\n"
            "#   purpose: test script.\n"
            "#   application: meta\n"
            f"#   status: {status}\n"
        )
    (repo / "scripts" / name).write_text(
        f"#!/usr/bin/env python3\n{hdr}'''doc'''\n", encoding="utf-8"
    )


def _commit(repo: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=repo, check=True)


def test_deprecated_script_is_attic_candidate(tmp_path: Path):
    repo = _make_repo(tmp_path)
    _write_script(repo, "old_migration.py", "oneshot-complete")
    _write_script(repo, "live_tool.py", "active")
    _commit(repo)
    report = sr.analyze(repo, stale_days=120)
    by = {r["script"]: r for r in report["scripts"]}
    assert by["scripts/old_migration.py"]["verdict"] == "attic"


def test_unknown_status_is_review(tmp_path: Path):
    repo = _make_repo(tmp_path)
    _write_script(repo, "no_header.py", None)
    _commit(repo)
    report = sr.analyze(repo, stale_days=120)
    by = {r["script"]: r for r in report["scripts"]}
    assert by["scripts/no_header.py"]["verdict"] == "review"
    assert by["scripts/no_header.py"]["status"] == "unknown"


def test_active_referenced_script_is_keep(tmp_path: Path):
    repo = _make_repo(tmp_path)
    _write_script(repo, "live_tool.py", "active")
    # another file references it -> not an orphan
    (repo / "scripts" / "caller.py").write_text(
        "# capability:\n#   purpose: c.\n#   application: meta\n"
        "#   status: active\n'''calls scripts/live_tool.py'''\n",
        encoding="utf-8",
    )
    _commit(repo)
    report = sr.analyze(repo, stale_days=120)
    by = {r["script"]: r for r in report["scripts"]}
    assert by["scripts/live_tool.py"]["verdict"] == "keep"
