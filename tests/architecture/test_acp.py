"""Tests for ACP builder + slicer (Chunk 3).

These rely on Chunk 1's scan output existing under
``<repo_root>/.build-loop/architecture/`` for the real-repo test, and on
synthetic fixtures for the unit-style cases.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Make scripts/ importable.
_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import build_acp  # noqa: E402
import slice_acp  # noqa: E402

from build_loop.architecture.storage import (  # noqa: E402
    arch_dir,
    atomic_write_json,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def real_repo_root() -> Path:
    """The build-loop repo itself, with Chunk 1's scan in place."""
    repo = _REPO
    if not (arch_dir(repo) / "index.json").exists():
        pytest.skip("requires `python -m build_loop.architecture scan` to have run first")
    return repo


def _write_synthetic_arch(repo: Path, components: List[Dict[str, Any]],
                          connections: List[Dict[str, Any]],
                          file_map: Dict[str, str],
                          reverse_deps: Dict[str, List[str]]) -> None:
    """Write a minimal `.build-loop/architecture/` for unit tests."""
    arch = arch_dir(repo)
    arch.mkdir(parents=True, exist_ok=True)
    atomic_write_json(arch / "index.json", {
        "schema_version": "1.0.0",
        "components": components,
        "connections": connections,
    })
    atomic_write_json(arch / "manifest.json", {
        "schema_version": "1.0.0",
        "last_full_scan_at": 1700000000000,
        "last_incremental_at": 0,
        "generated_at": 1700000000000,
    })
    atomic_write_json(arch / "file_map.json", {"files": file_map})
    atomic_write_json(arch / "reverse-deps.json", {"reverse_deps": reverse_deps})


def _make_component(cid: str, name: str, file: str, layer: str = "backend") -> Dict[str, Any]:
    return {
        "component_id": cid,
        "name": name,
        "type": "component",
        "role": {"purpose": "test", "layer": layer, "critical": False},
        "source": {"detection_method": "auto", "config_files": [file], "confidence": 1.0},
        "connects_to": [],
        "connected_from": [],
        "status": "active",
        "tags": [],
        "metadata": {"file": file, "kind": "source-file"},
        "timestamp": 0,
        "last_updated": 0,
    }


def _make_connection(frm: str, to: str, cid: str = None) -> Dict[str, Any]:
    return {
        "connection_id": cid or f"CONN_{frm}_{to}",
        "from": {"component_id": frm, "stable_id": "", "location": {"file": "", "line": 0}},
        "to": {"component_id": to, "stable_id": "", "location": {"file": "", "line": 0}},
        "connection_type": "imports",
        "code_reference": {"file": "", "symbol": "", "symbol_type": "import", "line_start": 0},
        "detected_from": "test",
        "confidence": 1.0,
        "semantic": {"classification": "production", "confidence": 1.0},
    }


# ---------------------------------------------------------------------------
# 1. Real-repo build test
# ---------------------------------------------------------------------------

def test_build_acp_from_real_repo(real_repo_root: Path) -> None:
    acp = build_acp.build_acp(real_repo_root)

    # Required keys.
    for key in (
        "schema_version", "scan_ts", "scan_type", "summary",
        "top_risk", "recent_violations", "files_touched_slice", "lessons_in_scope",
    ):
        assert key in acp, f"missing key {key}"

    assert acp["schema_version"] == "1.0.0"
    assert acp["scan_type"] in ("full", "incremental")
    assert acp["summary"]["components"] >= 50
    assert acp["summary"]["connections"] >= 1
    assert isinstance(acp["summary"]["layers"], list) and acp["summary"]["layers"]
    assert isinstance(acp["summary"]["components_by_type"], dict)
    assert isinstance(acp["summary"]["connections_by_type"], dict)

    # top_risk non-empty (this repo has clear hotspots like src/types).
    assert acp["top_risk"], "expected non-empty top_risk on the real repo"
    for r in acp["top_risk"]:
        assert {"component_id", "name", "blast_radius", "layer", "kind"} <= r.keys()
        assert r["kind"] in ("hotspot", "hub", "cycle-member")

    # Slice fields default-empty for the full ACP.
    assert acp["files_touched_slice"] is None
    assert acp["lessons_in_scope"] == []


# ---------------------------------------------------------------------------
# 2. Slice size cap
# ---------------------------------------------------------------------------

def test_acp_size_under_4kb_after_slice(real_repo_root: Path, tmp_path: Path) -> None:
    # Build the full ACP first so the slicer has something to read.
    acp = build_acp.build_acp(real_repo_root)
    full_path = tmp_path / "acp.json"
    atomic_write_json(full_path, acp)

    sliced = slice_acp.slice_acp(
        repo_root=real_repo_root,
        acp_path=full_path,
        files=["src/build_loop/architecture/storage.py"],
        depth=1,
        lessons_match=False,
    )
    encoded = json.dumps(sliced, indent=2).encode("utf-8")
    assert len(encoded) <= 4096, f"slice was {len(encoded)} bytes, must be ≤4096"


# ---------------------------------------------------------------------------
# 3. File → component resolution
# ---------------------------------------------------------------------------

def test_slice_resolves_files_to_components(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    components = [
        _make_component("COMP_A", "src/a", "src/a.py"),
        _make_component("COMP_B", "src/b", "src/b.py"),
    ]
    file_map = {"src/a.py": "COMP_A", "src/b.py": "COMP_B"}
    _write_synthetic_arch(repo, components, [], file_map, {})

    full = build_acp.build_acp(repo)
    full_path = repo / "acp.json"
    atomic_write_json(full_path, full)

    sliced = slice_acp.slice_acp(
        repo_root=repo,
        acp_path=full_path,
        files=["src/a.py"],
        depth=1,
    )
    assert len(sliced["files_touched_slice"]) == 1
    entry = sliced["files_touched_slice"][0]
    assert entry["component_id"] == "COMP_A"
    assert entry["file"] == "src/a.py"
    assert entry["layer"] == "backend"
    assert "blast_radius_from_root" in entry


# ---------------------------------------------------------------------------
# 4. Reverse deps at depth 1
# ---------------------------------------------------------------------------

def test_slice_includes_reverse_deps_at_depth_1(tmp_path: Path) -> None:
    """A → B → C; slice on B at depth 1 must include A (incoming) and C (outgoing)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    components = [
        _make_component("COMP_A", "a", "a.py"),
        _make_component("COMP_B", "b", "b.py"),
        _make_component("COMP_C", "c", "c.py"),
    ]
    connections = [
        _make_connection("COMP_A", "COMP_B"),
        _make_connection("COMP_B", "COMP_C"),
    ]
    file_map = {"a.py": "COMP_A", "b.py": "COMP_B", "c.py": "COMP_C"}
    reverse_deps = {"COMP_B": ["COMP_A"], "COMP_C": ["COMP_B"]}
    _write_synthetic_arch(repo, components, connections, file_map, reverse_deps)

    full = build_acp.build_acp(repo)
    full_path = repo / "acp.json"
    atomic_write_json(full_path, full)

    # Hand-verify _collect_neighbors directly.
    in_scope = slice_acp._collect_neighbors(
        ["COMP_B"], connections, reverse_deps, depth=1
    )
    assert "COMP_A" in in_scope, "should include incoming neighbor at depth 1"
    assert "COMP_B" in in_scope, "seed always included"
    assert "COMP_C" in in_scope, "should include outgoing neighbor at depth 1"


# ---------------------------------------------------------------------------
# 5. Lessons signature regex match
# ---------------------------------------------------------------------------

def test_lessons_match_signature_regex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    components = [_make_component("COMP_A", "a", "a.py")]
    file_map = {"a.py": "COMP_A"}
    _write_synthetic_arch(repo, components, [], file_map, {})

    # Lessons fixture with a circular-import signature.
    arch = arch_dir(repo)
    atomic_write_json(arch / "lessons.json", {
        "schema_version": "1.0.0",
        "lessons": [
            {
                "id": "LSN_001",
                "category": "import-hygiene",
                "pattern": "circular import via local module",
                "signature": ["circular.*import"],
                "severity": "warn",
            }
        ],
    })

    # Plant a target file that contains the signature, and stub git output.
    target = repo / "src" / "buggy.py"
    target.parent.mkdir(parents=True)
    target.write_text("# detected: circular import via shared.module\n")

    def _fake_git_staged(_repo: Path):
        return [target]

    monkeypatch.setattr(slice_acp, "_git_staged_files", _fake_git_staged)

    full = build_acp.build_acp(repo)
    full_path = repo / "acp.json"
    atomic_write_json(full_path, full)

    sliced = slice_acp.slice_acp(
        repo_root=repo,
        acp_path=full_path,
        files=["a.py"],
        depth=1,
        lessons_match=True,
    )
    assert sliced["lessons_in_scope"], "expected the planted lesson to match"
    matched = sliced["lessons_in_scope"][0]
    assert matched["id"] == "LSN_001"
    assert matched["matched_signature"] == "circular.*import"
    assert matched["matched_file"].endswith("buggy.py")


# ---------------------------------------------------------------------------
# 6. top_risk sorted by blast_radius DESC
# ---------------------------------------------------------------------------

def test_top_risk_sorted_by_blast_radius(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    # 3 components — A has 5 reverse deps, B has 3, C has 1.
    components = [
        _make_component(f"COMP_X{i}", f"x{i}", f"x{i}.py") for i in range(10)
    ] + [
        _make_component("COMP_A", "a", "a.py"),
        _make_component("COMP_B", "b", "b.py"),
        _make_component("COMP_C", "c", "c.py"),
    ]
    file_map = {f"x{i}.py": f"COMP_X{i}" for i in range(10)}
    file_map.update({"a.py": "COMP_A", "b.py": "COMP_B", "c.py": "COMP_C"})

    reverse_deps = {
        "COMP_A": [f"COMP_X{i}" for i in range(5)],
        "COMP_B": [f"COMP_X{i}" for i in range(3)],
        "COMP_C": ["COMP_X0"],
    }
    _write_synthetic_arch(repo, components, [], file_map, reverse_deps)

    full = build_acp.build_acp(repo)
    risks = full["top_risk"]
    blasts = [r["blast_radius"] for r in risks]
    assert blasts == sorted(blasts, reverse=True), f"top_risk must be sorted DESC; got {blasts}"
    # Specifically A=5, B=3, C=1.
    ids = [r["component_id"] for r in risks if r["component_id"] in ("COMP_A", "COMP_B", "COMP_C")]
    assert ids[:3] == ["COMP_A", "COMP_B", "COMP_C"]
