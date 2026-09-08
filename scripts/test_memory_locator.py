#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the deterministic build-loop-memory file locator."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import memory_locator as locator
from scripts import memory_telemetry


def _write_index(root: Path, rows: list[dict]) -> Path:
    path = root / "indexes" / "INDEX.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _row(
    root: Path,
    rel: str,
    *,
    title: str,
    project: str,
    tags: list[str],
    status: str = "active",
) -> dict:
    path = root / rel
    return {
        "canonical_path": rel,
        "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
        "id": rel.removesuffix(".md").replace("/", "-"),
        "project": project,
        "status": status,
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


def test_locator_emits_ranked_exposure_and_empty_result_telemetry(tmp_path: Path) -> None:
    first = tmp_path / "lessons" / "first.md"
    second = tmp_path / "lessons" / "second.md"
    for path, body in (
        (first, "# Exposure rank primary\nExposure rank telemetry primary result."),
        (second, "# Exposure rank\nExposure rank telemetry."),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _write_index(tmp_path, [
        _row(tmp_path, "lessons/first.md", title="Exposure rank primary", project="_global", tags=["telemetry"]),
        _row(tmp_path, "lessons/second.md", title="Exposure rank", project="_global", tags=["telemetry"]),
    ])
    telemetry_path = tmp_path / "isolated-telemetry.jsonl"

    ranked = locator.locate(
        "exposure rank telemetry",
        project="build-loop",
        memory_root=tmp_path,
        telemetry_path=telemetry_path,
    )
    empty = locator.locate(
        "quantum banana telescope",
        project="build-loop",
        memory_root=tmp_path,
        telemetry_path=telemetry_path,
    )

    ranked_row, empty_row = memory_telemetry.read_rows(telemetry_path)
    assert ranked_row["memory_ids_seen"] == [result["id"] for result in ranked["results"]]
    assert ranked_row["ranks"] == list(range(len(ranked["results"])))
    assert ranked_row["scores"] == [result["score"] for result in ranked["results"]]
    assert ranked_row["shown_count"] == len(ranked["results"])
    assert empty["results"] == []
    assert empty_row["ranks"] == []
    assert empty_row["scores"] == []
    assert empty_row["shown_count"] == 0
    assert empty_row["zero_result"] is True


def test_inactive_statuses_are_excluded_from_index_and_fallback(tmp_path: Path) -> None:
    statuses = ("archived", "deleted", "inactive", "superseded")
    paths: dict[str, Path] = {}
    for status in ("active", *statuses):
        path = tmp_path / "projects" / "build-loop" / "decisions" / f"{status}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nstatus: {status}\n---\n# Lifecycle decision filters\n{status} lifecycle decision filters.\n",
            encoding="utf-8",
        )
        paths[status] = path

    fallback = locator.locate(
        "lifecycle decision filters",
        project="build-loop",
        memory_root=tmp_path,
        emit_telemetry=False,
    )

    assert fallback["engine"] in {"rg", "python-scan"}
    assert [result["path"] for result in fallback["results"]] == [
        str(paths["active"].relative_to(tmp_path))
    ]
    assert locator._fallback_row(paths["superseded"], tmp_path)[0]["status"] == "superseded"

    _write_index(tmp_path, [
        _row(
            tmp_path,
            str(path.relative_to(tmp_path)),
            title="Lifecycle decision filters",
            project="build-loop",
            tags=["lifecycle", "filters"],
            status=status,
        )
        for status, path in paths.items()
    ])
    indexed = locator.locate(
        "lifecycle decision filters",
        project="build-loop",
        memory_root=tmp_path,
        emit_telemetry=False,
    )

    assert indexed["engine"] == "index-jsonl"
    assert [result["path"] for result in indexed["results"]] == [
        str(paths["active"].relative_to(tmp_path))
    ]


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


def test_index_path_cannot_escape_memory_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-memory.md"
    outside.write_text("# Secret outside file\nquantum banana telescope", encoding="utf-8")
    malicious = {
        "canonical_path": "../outside-memory.md",
        "checksum": hashlib.sha256(outside.read_bytes()).hexdigest(),
        "id": "outside",
        "project": "_global",
        "status": "active",
        "tags": ["quantum", "banana", "telescope"],
        "title": "Outside memory",
        "type": "lesson",
    }
    _write_index(tmp_path, [malicious])

    receipt = locator.locate(
        "quantum banana telescope",
        project="build-loop",
        memory_root=tmp_path,
        emit_telemetry=False,
    )

    assert receipt["results"] == []


def test_read_only_lookup_does_not_create_missing_memory_root(tmp_path: Path) -> None:
    missing = tmp_path / "missing-memory"

    receipt = locator.locate(
        "hook timeout",
        project="build-loop",
        memory_root=missing,
    )

    assert not missing.exists()
    assert receipt["telemetry_correlation_id"] is None
    assert "telemetry_skipped_memory_root_missing" in receipt["reasons"]


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


def test_default_scope_hides_other_projects_and_all_projects_reveals_them(tmp_path: Path) -> None:
    """A repo-scoped agent cannot name the project that holds the answer.

    Without ALL_PROJECTS the sibling project's lesson is invisible, which is the
    defect this flag exists to remove -- so the test asserts BOTH regimes.
    """
    mine = tmp_path / "projects" / "build-loop" / "lessons" / "local-note.md"
    theirs = tmp_path / "projects" / "easy-terminal" / "lessons" / "ptyd-pane-ledger.md"
    for path, body in (
        (mine, "# Local note\nUnrelated local guidance."),
        (theirs, "# Ptyd pane ledger\nThe ptyd pane event ledger reconciles panes."),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _write_index(tmp_path, [
        _row(tmp_path, "projects/build-loop/lessons/local-note.md",
             title="Local note", project="build-loop", tags=[]),
        _row(tmp_path, "projects/easy-terminal/lessons/ptyd-pane-ledger.md",
             title="Ptyd pane ledger", project="easy-terminal", tags=["ptyd"]),
    ])

    scoped = locator.locate("ptyd pane ledger", project="build-loop",
                              memory_root=tmp_path, emit_telemetry=False)
    assert not any("easy-terminal" in r["path"] for r in scoped["results"])

    everywhere = locator.locate("ptyd pane ledger", project=locator.ALL_PROJECTS,
                                  memory_root=tmp_path, emit_telemetry=False)
    assert any("easy-terminal" in r["path"] for r in everywhere["results"])


def test_all_projects_scan_reaches_lanes_without_an_index(tmp_path: Path) -> None:
    """Fallback scan must widen too -- index freshness must not gate the flag."""
    target = tmp_path / "projects" / "spectra" / "retrospectives" / "2026-08-08" / "capture-run.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Capture run\nSpectra capture walkthrough recording notes.", encoding="utf-8")

    found = locator.locate("spectra capture walkthrough", project=locator.ALL_PROJECTS,
                           memory_root=tmp_path, emit_telemetry=False)
    assert any("spectra" in r["path"] for r in found["results"])


def test_retrospectives_and_architecture_are_searchable_lanes() -> None:
    """These lanes hold real content; omitting them made it unreachable."""
    for lane in ("retrospectives", "architecture", "prompts"):
        assert lane in locator.CANONICAL_LANES
