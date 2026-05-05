"""Atomic JSON storage for the architecture engine.

Layout (mirrors NavGator):
    .build-loop/architecture/
        index.json
        graph.json
        file_map.json
        hashes.json
        reverse-deps.json
        timeline.json
        manifest.json
        components/COMP_*.json   (omitted in v0.1 — index.json carries them)
        connections/CONN_*.json  (omitted in v0.1 — index.json carries them)

Every doc carries ``schema_version``. Writes are atomic via temp-file + rename.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .schemas import SCHEMA_VERSION

ARCH_DIR_NAME = ".build-loop/architecture"


def arch_dir(repo_root: Path | str) -> Path:
    return Path(repo_root) / ARCH_DIR_NAME


def ensure_arch_dir(repo_root: Path | str) -> Path:
    d = arch_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    return d


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write JSON atomically: write to .tmp in same dir, fsync, rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Embed schema_version on every persisted doc.
    if isinstance(payload, dict) and "schema_version" not in payload:
        payload = {"schema_version": SCHEMA_VERSION, **payload}

    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return None
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# High-level helpers per artifact
# ---------------------------------------------------------------------------

def write_index(repo_root: Path | str, index: Dict[str, Any]) -> Path:
    p = ensure_arch_dir(repo_root) / "index.json"
    atomic_write_json(p, index)
    return p


def write_graph(repo_root: Path | str, graph: Dict[str, Any]) -> Path:
    p = ensure_arch_dir(repo_root) / "graph.json"
    atomic_write_json(p, graph)
    return p


def write_file_map(repo_root: Path | str, file_map: Dict[str, Any]) -> Path:
    p = ensure_arch_dir(repo_root) / "file_map.json"
    atomic_write_json(p, file_map)
    return p


def write_hashes(repo_root: Path | str, hashes: Dict[str, Any]) -> Path:
    p = ensure_arch_dir(repo_root) / "hashes.json"
    atomic_write_json(p, hashes)
    return p


def write_reverse_deps(repo_root: Path | str, reverse_deps: Dict[str, Any]) -> Path:
    p = ensure_arch_dir(repo_root) / "reverse-deps.json"
    atomic_write_json(p, reverse_deps)
    return p


def write_timeline(repo_root: Path | str, timeline: Dict[str, Any]) -> Path:
    p = ensure_arch_dir(repo_root) / "timeline.json"
    atomic_write_json(p, timeline)
    return p


def write_manifest(repo_root: Path | str, manifest: Dict[str, Any]) -> Path:
    p = ensure_arch_dir(repo_root) / "manifest.json"
    atomic_write_json(p, manifest)
    return p


def read_index(repo_root: Path | str) -> Optional[Dict[str, Any]]:
    return read_json(arch_dir(repo_root) / "index.json")


def read_hashes(repo_root: Path | str) -> Dict[str, Any]:
    return read_json(arch_dir(repo_root) / "hashes.json") or {"schema_version": SCHEMA_VERSION, "files": {}}


def read_manifest(repo_root: Path | str) -> Optional[Dict[str, Any]]:
    return read_json(arch_dir(repo_root) / "manifest.json")
