# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/rally_point/sync_rally.py — vendored-substrate drift detector.

Mirrors the read-only compare-and-report contract of sync_skills.py:
  - clean manifest (upstream hash matches baseline) -> exit 0, no drift
  - tampered baseline hash (upstream moved since baseline) -> DRIFT, exit 1
  - missing upstream source file -> MISSING, exit 1
  - source: null entry -> skipped cleanly (build-loop-original, not counted as drift)
  - malformed manifest -> exit 2
  - --json shape
Never overwrites any file.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SYNC = _HERE / "sync_rally.py"


def _load_sync_with(manifest_path: Path):
    """Import sync_rally as a fresh module with MANIFEST pointed at a fixture."""
    spec = importlib.util.spec_from_file_location("_sync_rally_test", _SYNC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.MANIFEST = manifest_path
    return mod


@pytest.fixture()
def upstream(tmp_path: Path) -> Path:
    """A fake upstream repo dir with one source file."""
    root = tmp_path / "agent-rally-point-fake"
    src_dir = root / "build" / "lib" / "agent_rally_point"
    src_dir.mkdir(parents=True)
    (src_dir / "changes.py").write_text("UPSTREAM_CONTENT_V1\n", encoding="utf-8")
    return root


def _write_manifest(tmp_path: Path, files: dict, repo_dirname: str) -> Path:
    p = tmp_path / "_provenance.json"
    p.write_text(json.dumps({"_upstream_repo": repo_dirname, "files": files}), encoding="utf-8")
    return p


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_clean_manifest_exit_0(tmp_path, upstream, monkeypatch):
    h = _hash((upstream / "build/lib/agent_rally_point/changes.py").read_bytes())
    manifest = _write_manifest(
        tmp_path,
        {"changes.py": {"source": "build/lib/agent_rally_point/changes.py", "source_hash": h}},
        upstream.name,
    )
    mod = _load_sync_with(manifest)
    monkeypatch.setattr(mod, "find_upstream_root", lambda _name: upstream)
    rc = mod.main(["--json"])
    assert rc == 0


def test_tampered_baseline_is_drift_exit_1(tmp_path, upstream, monkeypatch, capsys):
    manifest = _write_manifest(
        tmp_path,
        {"changes.py": {"source": "build/lib/agent_rally_point/changes.py",
                        "source_hash": "deadbeef" * 8}},
        upstream.name,
    )
    mod = _load_sync_with(manifest)
    monkeypatch.setattr(mod, "find_upstream_root", lambda _name: upstream)
    rc = mod.main(["--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["drift_count"] == 1
    assert out["drift"][0]["kind"] == "DRIFT"


def test_missing_upstream_source_exit_1(tmp_path, upstream, monkeypatch, capsys):
    manifest = _write_manifest(
        tmp_path,
        {"gone.py": {"source": "build/lib/agent_rally_point/gone.py",
                     "source_hash": "a" * 64}},
        upstream.name,
    )
    mod = _load_sync_with(manifest)
    monkeypatch.setattr(mod, "find_upstream_root", lambda _name: upstream)
    rc = mod.main(["--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["drift"][0]["kind"] == "MISSING"


def test_null_source_skipped(tmp_path, upstream, monkeypatch, capsys):
    h = _hash((upstream / "build/lib/agent_rally_point/changes.py").read_bytes())
    manifest = _write_manifest(
        tmp_path,
        {
            "changes.py": {"source": "build/lib/agent_rally_point/changes.py", "source_hash": h},
            "fact_v1.py": {"source": None, "source_hash": None},
        },
        upstream.name,
    )
    mod = _load_sync_with(manifest)
    monkeypatch.setattr(mod, "find_upstream_root", lambda _name: upstream)
    rc = mod.main(["--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["checked"] == 1
    assert out["skipped_build_loop_original"] == 1
    assert out["drift_count"] == 0


def test_malformed_manifest_exit_2(tmp_path):
    p = tmp_path / "_provenance.json"
    p.write_text("{ not json", encoding="utf-8")
    mod = _load_sync_with(p)
    with pytest.raises(SystemExit) as ei:
        mod.main(["--json"])
    assert ei.value.code == 2


def test_does_not_overwrite_anything(tmp_path, upstream, monkeypatch):
    src = upstream / "build/lib/agent_rally_point/changes.py"
    before = src.read_bytes()
    manifest = _write_manifest(
        tmp_path,
        {"changes.py": {"source": "build/lib/agent_rally_point/changes.py",
                        "source_hash": "deadbeef" * 8}},
        upstream.name,
    )
    mod = _load_sync_with(manifest)
    monkeypatch.setattr(mod, "find_upstream_root", lambda _name: upstream)
    mod.main(["--json"])
    assert src.read_bytes() == before  # read-only contract
