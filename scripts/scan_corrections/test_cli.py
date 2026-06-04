# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the scan_corrections CLI / __main__.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT_DIR = HERE.parent  # scripts/
PKG_DIR = HERE  # scripts/scan_corrections/


def _run_cli(args: list[str], cwd: Path, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SCRIPT_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "scan_corrections", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _write_text_turns_file(tmp_path: Path, turns: list[str]) -> Path:
    p = tmp_path / "turns.txt"
    p.write_text("\n".join(turns), encoding="utf-8")
    return p


def test_cli_writes_candidates_to_pending_dir(tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    turns_file = _write_text_turns_file(
        tmp_path,
        ["Revert that.", "Always use uv.", "Use sqlite instead of mongodb."],
    )

    r = _run_cli(
        ["--workdir", str(workdir), "--text-turns-file", str(turns_file)],
        cwd=workdir,
    )
    assert r.returncode == 0, r.stderr

    pending = workdir / ".build-loop" / "pending-lessons"
    assert pending.is_dir()
    files = sorted(pending.glob("*.md"))
    assert len(files) >= 3

    # Each file has frontmatter + body.
    for f in files:
        body = f.read_text(encoding="utf-8")
        assert body.startswith("---\n")
        assert "kind:" in body
        assert "signal_type:" in body
        assert "confidence: confirmed" in body
        assert "tier: 1-deterministic" in body
        assert "## Quote" in body


def test_cli_dedup_on_rerun(tmp_path: Path) -> None:
    """Running twice with the same input does NOT duplicate candidate files."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    turns_file = _write_text_turns_file(tmp_path, ["Revert that.", "Always use uv."])

    r1 = _run_cli(["--workdir", str(workdir), "--text-turns-file", str(turns_file)], cwd=workdir)
    assert r1.returncode == 0
    pending = workdir / ".build-loop" / "pending-lessons"
    first_count = len(list(pending.glob("*.md")))

    r2 = _run_cli(["--workdir", str(workdir), "--text-turns-file", str(turns_file)], cwd=workdir)
    assert r2.returncode == 0
    second_count = len(list(pending.glob("*.md")))

    assert second_count == first_count, "Re-running should be idempotent (id_hash dedup)"


def test_cli_no_capture_opt_out(tmp_path: Path) -> None:
    """`.build-loop/.no-capture` short-circuits before any work."""
    workdir = tmp_path / "workdir"
    (workdir / ".build-loop").mkdir(parents=True)
    (workdir / ".build-loop" / ".no-capture").write_text("opted out", encoding="utf-8")
    turns_file = _write_text_turns_file(tmp_path, ["Revert that."])

    r = _run_cli(["--workdir", str(workdir), "--text-turns-file", str(turns_file)], cwd=workdir)
    assert r.returncode == 0
    assert not (workdir / ".build-loop" / "pending-lessons").exists()


def test_cli_empty_inputs_exit_clean(tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    r = _run_cli(["--workdir", str(workdir)], cwd=workdir)
    assert r.returncode == 0
    assert "nothing to do" in r.stderr.lower()


def test_cli_print_json(tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    turns_file = _write_text_turns_file(tmp_path, ["Revert that."])
    r = _run_cli(
        ["--workdir", str(workdir), "--text-turns-file", str(turns_file), "--print-json"],
        cwd=workdir,
    )
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert "candidates" in payload
    assert len(payload["candidates"]) >= 1
    assert "written" in payload


def test_cli_swallows_errors_without_strict(tmp_path: Path) -> None:
    """Invalid file path → exit 0 without --strict."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    r = _run_cli(
        ["--workdir", str(workdir), "--text-turns-file", "/nonexistent/path/file.txt"],
        cwd=workdir,
    )
    # No --strict → exit 0 (hook safety).
    assert r.returncode == 0


def test_cli_strict_propagates_errors(tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    r = _run_cli(
        [
            "--workdir",
            str(workdir),
            "--text-turns-file",
            "/nonexistent/path/file.txt",
            "--strict",
        ],
        cwd=workdir,
    )
    assert r.returncode != 0


def test_cli_budget_exhaustion_is_clean(tmp_path: Path) -> None:
    """SCAN_CORRECTIONS_BUDGET_S=0 → bail with partial results, exit 0."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    turns_file = _write_text_turns_file(tmp_path, ["Revert that.", "Always use uv."])
    r = _run_cli(
        ["--workdir", str(workdir), "--text-turns-file", str(turns_file)],
        cwd=workdir,
        env_overrides={"SCAN_CORRECTIONS_BUDGET_S": "0"},
    )
    assert r.returncode == 0
