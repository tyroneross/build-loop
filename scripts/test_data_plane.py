# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the adapter-neutral run data-plane lifecycle."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import data_plane as dp  # noqa: E402


def _manifest(repo: Path, run_id: str, *, surfaces: list[dict] | None = None) -> tuple[Path, dict]:
    worktree = repo / ".build-loop" / "worktrees" / run_id
    worktree.mkdir(parents=True)
    manifest = dp.initialize_manifest(
        repo,
        run_id=run_id,
        worktree_path=worktree,
        branch=f"bl/{run_id}",
    )
    manifest["surfaces"] = surfaces or []
    path = dp.manifest_path(repo, run_id)
    path.write_text(json.dumps(manifest, indent=2))
    return path, manifest


def _surface(**overrides: object) -> dict:
    surface = {
        "id": "sqlite",
        "kind": "sqlite",
        "authority": "canonical",
        "isolation": "per_worktree",
        "writable": True,
        "resource_key": "sqlite:fixture",
        "path": "db.sqlite",
        "status": "active",
    }
    surface.update(overrides)
    return surface


def test_initialize_creates_one_idempotent_manifest_and_data_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    worktree = repo / ".build-loop" / "worktrees" / "run-1"
    worktree.mkdir(parents=True)

    first = dp.initialize_manifest(repo, run_id="run-1", worktree_path=worktree, branch="bl/run-1")
    second = dp.initialize_manifest(repo, run_id="run-1", worktree_path=worktree, branch="bl/run-1")

    assert first == second
    assert dp.manifest_path(repo, "run-1").is_file()
    assert Path(first["data_root"]).is_dir()
    assert Path(first["data_root"]).is_relative_to(repo.resolve())
    assert not Path(first["data_root"]).is_relative_to(worktree.resolve())


def test_validator_rejects_escaping_path_and_duplicate_surface_id(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    path, manifest = _manifest(
        repo,
        "run-1",
        surfaces=[_surface(path="../shared.sqlite"), _surface(resource_key="sqlite:other")],
    )

    result = dp.validate_manifest_file(repo, path, expected_run_id="run-1")

    assert result["ok"] is False
    assert any("escapes" in error for error in result["errors"])
    assert any("duplicate surface id" in error for error in result["errors"])
    assert manifest["run_id"] == "run-1"


def test_validator_rejects_repointed_data_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    path, manifest = _manifest(repo, "run-1", surfaces=[_surface()])
    manifest["data_root"] = str(tmp_path / "other-run-data")
    path.write_text(json.dumps(manifest, indent=2))

    result = dp.validate_manifest_file(repo, path, expected_run_id="run-1")

    assert result["ok"] is False
    assert any("does not match the allocated" in error for error in result["errors"])


def test_validator_blocks_cross_run_writable_collision_but_allows_one_serialized_writer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    first_path, _ = _manifest(repo, "run-1", surfaces=[_surface(resource_key="index:main")])
    second_path, _ = _manifest(repo, "run-2", surfaces=[_surface(resource_key="index:main")])

    collision = dp.validate_manifest_file(repo, second_path, expected_run_id="run-2")
    assert collision["ok"] is False
    assert any("resource collision" in error for error in collision["errors"])

    serialized = _surface(
        isolation="shared_serialized",
        writer="index-writer",
        path=None,
        resource_key="index:main",
    )
    first = dp.load_manifest(first_path)
    first["surfaces"] = [serialized]
    first_path.write_text(json.dumps(first, indent=2))
    second = dp.load_manifest(second_path)
    second["surfaces"] = [dict(serialized, id="index-2")]
    second_path.write_text(json.dumps(second, indent=2))

    assert dp.validate_manifest_file(repo, second_path, expected_run_id="run-2")["ok"] is True


def test_add_surface_validates_collision_before_persisting(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    first_path, _ = _manifest(repo, "run-1", surfaces=[_surface(resource_key="index:main")])
    second_path, _ = _manifest(repo, "run-2")

    with pytest.raises(dp.DataPlaneError, match="resource collision"):
        dp.add_surface(repo, second_path, _surface(resource_key="index:main"))
    assert dp.load_manifest(second_path)["surfaces"] == []

    updated = dp.add_surface(repo, second_path, _surface(resource_key="index:run-2"))
    assert updated["surfaces"][0]["resource_key"] == "index:run-2"
    assert dp.load_manifest(first_path)["surfaces"][0]["resource_key"] == "index:main"


def test_terminal_check_requires_owned_writable_surfaces_to_close(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    path, _ = _manifest(repo, "run-1", surfaces=[_surface()])

    blocked = dp.check_terminal_manifest(repo, path, expected_run_id="run-1")
    assert blocked["ok"] is False
    assert any("not terminal" in error for error in blocked["errors"])

    dp.set_surface_status(path, surface_id="sqlite", status="retained")
    assert dp.check_terminal_manifest(repo, path, expected_run_id="run-1")["ok"] is True


def test_shared_readonly_and_external_namespace_contracts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    path, manifest = _manifest(
        repo,
        "run-1",
        surfaces=[
            _surface(
                id="raw",
                kind="files",
                isolation="shared_readonly",
                writable=False,
                resource_key="raw:shared",
                path=None,
            ),
            _surface(
                id="cloud",
                kind="service",
                authority="external",
                isolation="external_namespaced",
                resource_key="service:run-1",
                path=None,
                namespace="run-1",
            ),
        ],
    )
    assert dp.validate_manifest_file(repo, path, expected_run_id="run-1")["ok"] is True
    assert manifest["surfaces"][0]["writable"] is False


def test_terminal_zero_surface_manifest_is_valid(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    path, _ = _manifest(repo, "run-1")
    assert dp.check_terminal_manifest(repo, path, expected_run_id="run-1")["ok"] is True
