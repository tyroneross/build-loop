"""Scanner integration test — fixture repo with .py + .ts files.

Builds a tiny repo on disk, scans it, asserts components + connections + hashes
land correctly, and confirms ``schema_version`` propagates.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from build_loop.architecture.scanner import scan_repo, scan_one_file
from build_loop.architecture.storage import (
    SCHEMA_VERSION as _SV,  # noqa: F401  (sanity import)
    arch_dir,
    write_file_map,
    write_hashes,
    write_index,
)


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    # 3 .py files with a chain a -> b -> c.
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "from . import b\n")
    _write(tmp_path / "pkg" / "b.py", "from . import c\n")
    _write(tmp_path / "pkg" / "c.py", "X = 1\n")

    # 2 .ts files with d -> e.
    _write(tmp_path / "ts" / "d.ts", "import { e } from './e';\nexport const D = e;\n")
    _write(tmp_path / "ts" / "e.ts", "export const e = 1;\n")

    # Add a .gitignore that excludes a "vendor" dir to prove pathspec works.
    _write(tmp_path / ".gitignore", "vendor/\n")
    _write(tmp_path / "vendor" / "skip.py", "raise RuntimeError('should be skipped')\n")
    return tmp_path


def test_scan_emits_components_and_connections(fixture_repo: Path) -> None:
    result = scan_repo(fixture_repo)

    files = {c.metadata["file"] for c in result.components}
    assert "pkg/a.py" in files
    assert "pkg/b.py" in files
    assert "pkg/c.py" in files
    assert "ts/d.ts" in files
    assert "ts/e.ts" in files
    # Vendor must be skipped via .gitignore.
    assert "vendor/skip.py" not in files

    edges = {(c.from_id, c.to_id) for c in result.connections}
    by_file = {c.metadata["file"]: c.component_id for c in result.components}
    assert (by_file["pkg/a.py"], by_file["pkg/b.py"]) in edges
    assert (by_file["pkg/b.py"], by_file["pkg/c.py"]) in edges
    assert (by_file["ts/d.ts"], by_file["ts/e.ts"]) in edges


def test_scan_persists_artifacts_with_schema_version(fixture_repo: Path) -> None:
    result = scan_repo(fixture_repo)
    write_index(fixture_repo, result.to_index())
    write_hashes(fixture_repo, {"files": result.hashes})
    write_file_map(fixture_repo, {"files": result.file_map})

    idx_path = arch_dir(fixture_repo) / "index.json"
    hashes_path = arch_dir(fixture_repo) / "hashes.json"
    file_map_path = arch_dir(fixture_repo) / "file_map.json"

    for p in (idx_path, hashes_path, file_map_path):
        assert p.exists()
        doc = json.loads(p.read_text())
        assert "schema_version" in doc

    hashes = json.loads(hashes_path.read_text())["files"]
    assert "pkg/a.py" in hashes
    assert hashes["pkg/a.py"]["hash"]  # blake2b digest non-empty


def test_scan_one_file_replaces_in_place(fixture_repo: Path) -> None:
    result = scan_repo(fixture_repo)
    initial_edges = {(c.from_id, c.to_id) for c in result.connections}

    # Modify pkg/a.py to import c directly instead of b.
    (fixture_repo / "pkg" / "a.py").write_text("from . import c\n", encoding="utf-8")

    updated = scan_one_file(fixture_repo, "pkg/a.py", prior_scan=result)
    edges = {(c.from_id, c.to_id) for c in updated.connections}
    by_file = {c.metadata["file"]: c.component_id for c in updated.components}

    assert (by_file["pkg/a.py"], by_file["pkg/c.py"]) in edges
    assert (by_file["pkg/a.py"], by_file["pkg/b.py"]) not in edges
    assert (by_file["pkg/b.py"], by_file["pkg/c.py"]) in edges
