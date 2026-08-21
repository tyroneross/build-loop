#!/usr/bin/env python3
"""Tests for the deterministic build-loop-memory file locator."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from scripts import memory_locator as locator


def _write_index(root: Path, rows: list[dict]) -> Path:
    path = root / "indexes" / "INDEX.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _row(root: Path, rel: str, *, title: str, project: str, tags: list[str]) -> dict:
    path = root / rel
    return {
        "canonical_path": rel,
        "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
        "id": rel.removesuffix(".md").replace("/", "-"),
        "project": project,
        "status": "active",
        "tags": tags,
        "title": title,
        "type": "lesson",
    }


def test_fresh_index_returns_ranked_project_and_global_paths(tmp_path: Path) -> None:
    project_file = tmp_path / "projects" / "build-loop" / "lessons" / "hook-hygiene.md"
    global_file = tmp_path / "lessons" / "generic-timeout.md"
    other_file = tmp_path / "projects" / "other" / "lessons" / "hook-hygiene.md"
    for path, body in (
        (project_file, "# Hook hygiene\nResolve exit 127 and timeout failures."),
        (global_file, "# Timeout handling\nGeneric timeout guidance."),
        (other_file, "# Hook hygiene\nOther project."),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    rows = [
        _row(tmp_path, str(project_file.relative_to(tmp_path)), title="Hook hygiene", project="build-loop", tags=["exit-127", "timeout"]),
        _row(tmp_path, str(global_file.relative_to(tmp_path)), title="Timeout handling", project="_global", tags=["timeout"]),
        _row(tmp_path, str(other_file.relative_to(tmp_path)), title="Hook hygiene", project="other", tags=["exit-127", "timeout"]),
    ]
    _write_index(tmp_path, rows)

    receipt = locator.locate(
        "hook exit 127 timeout",
        project="build-loop",
        memory_root=tmp_path,
        emit_telemetry=False,
    )

    assert receipt["engine"] == "index-jsonl"
    assert receipt["index_fresh"] is True
    assert receipt["results"][0]["path"] == str(project_file.relative_to(tmp_path))
    assert all(result["project"] != "other" for result in receipt["results"])


def test_newer_update_ledger_forces_rg_fallback(tmp_path: Path) -> None:
    target = tmp_path / "projects" / "build-loop" / "lessons" / "wrong-directory.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Resolve tool state from git root\nAvoid cwd wrong directory writes.", encoding="utf-8")
    index = _write_index(tmp_path, [])
    ledger = tmp_path / "indexes" / "updates.jsonl"
    ledger.write_text("{}\n", encoding="utf-8")
    os.utime(index, ns=(1, 1))
    os.utime(ledger, ns=(2, 2))

    receipt = locator.locate(
        "cwd git root wrong directory",
        project="build-loop",
        memory_root=tmp_path,
        emit_telemetry=False,
    )

    assert receipt["engine"] in {"rg", "python-scan"}
    assert receipt["index_fresh"] is False
    assert receipt["results"][0]["path"] == str(target.relative_to(tmp_path))


def test_checksum_change_for_ranked_file_forces_fallback(tmp_path: Path) -> None:
    target = tmp_path / "lessons" / "ci-suite.md"
    target.parent.mkdir(parents=True)
    target.write_text("# CI must run full suite", encoding="utf-8")
    row = _row(tmp_path, str(target.relative_to(tmp_path)), title="CI full suite", project="_global", tags=["ci", "tests"])
    _write_index(tmp_path, [row])
    target.write_text("# CI must run the full test suite\nUpdated canonical content.", encoding="utf-8")

    receipt = locator.locate(
        "ci full test suite",
        project="build-loop",
        memory_root=tmp_path,
        emit_telemetry=False,
    )

    assert receipt["engine"] in {"rg", "python-scan"}
    assert receipt["index_fresh"] is False
    assert receipt["results"][0]["path"] == str(target.relative_to(tmp_path))


def test_unrelated_query_returns_empty_instead_of_junk(tmp_path: Path) -> None:
    target = tmp_path / "lessons" / "hooks.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Hook hygiene\nExit code handling.", encoding="utf-8")
    _write_index(tmp_path, [_row(tmp_path, "lessons/hooks.md", title="Hook hygiene", project="_global", tags=["hooks"])])

    receipt = locator.locate(
        "quantum banana telescope",
        project="build-loop",
        memory_root=tmp_path,
        emit_telemetry=False,
    )

    assert receipt["results"] == []


def test_empty_query_does_not_scan_corpus(tmp_path: Path) -> None:
    receipt = locator.locate(
        "the memory and build",
        project="build-loop",
        memory_root=tmp_path,
        emit_telemetry=False,
    )

    assert receipt["engine"] == "none"
    assert receipt["results"] == []
    assert "query_has_no_terms" in receipt["reasons"]


def test_cli_plain_output_is_fetchable_absolute_path(tmp_path: Path, capsys) -> None:
    target = tmp_path / "lessons" / "routing.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Protocol routing\nSingle source prevents drift.", encoding="utf-8")
    _write_index(tmp_path, [_row(tmp_path, "lessons/routing.md", title="Protocol routing", project="_global", tags=["single-source", "drift"])])

    code = locator.main([
        "--query", "protocol routing single source drift",
        "--project", "build-loop",
        "--memory-root", str(tmp_path),
        "--no-telemetry",
    ])

    assert code == 0
    assert capsys.readouterr().out.strip() == str(target.resolve())
