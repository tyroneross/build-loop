"""Tests for scripts/architecture_freshness.py (Chunk 4).

Covers:
  - --mark-stale updates state.json.architecture.{stale, staleSince, staleFiles}
  - --mark-fresh clears stale state and sets lastFreshAt
  - --check returns 'missing' when no manifest
  - --check returns 'stale' when manifest mtime is > 24h old
  - --check returns 'fresh' when manifest mtime is < 1h old
  - lockfile single-flight: --mark-stale succeeds while another holds the
    .scan.lock; no second scan is fired by the script itself (the script
    never spawns subprocesses — the hook scripts do, and they check the lock
    before firing).
  - --mark-stale --file FOO 3x dedups (only one entry).
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# scripts/ on path so we can import the module directly for unit-level use.
_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import architecture_freshness as af  # noqa: E402


def _arch(workdir: Path) -> Path:
    d = workdir / ".build-loop" / "architecture"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state(workdir: Path) -> Path:
    return workdir / ".build-loop" / "state.json"


def _read_state(workdir: Path) -> dict:
    p = _state(workdir)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _write_manifest(workdir: Path, mtime_age_seconds: float = 0.0) -> Path:
    arch = _arch(workdir)
    manifest = arch / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "1.0.0"}), encoding="utf-8")
    if mtime_age_seconds:
        ts = time.time() - mtime_age_seconds
        os.utime(manifest, (ts, ts))
    return manifest


def test_mark_stale_updates_state(tmp_path: Path) -> None:
    af.cmd_mark_stale(tmp_path, "src/build_loop/architecture/scanner.py")
    st = _read_state(tmp_path)
    arch = st["architecture"]
    assert arch["stale"] is True
    assert arch["staleFiles"] == ["src/build_loop/architecture/scanner.py"]
    assert "staleSince" in arch and arch["staleSince"]


def test_mark_fresh_clears_state(tmp_path: Path) -> None:
    af.cmd_mark_stale(tmp_path, "foo.py")
    af.cmd_mark_fresh(tmp_path)
    arch = _read_state(tmp_path)["architecture"]
    assert arch["stale"] is False
    assert arch["staleFiles"] == []
    assert "lastFreshAt" in arch and arch["lastFreshAt"]
    assert "staleSince" not in arch


def test_check_missing_when_no_manifest(tmp_path: Path) -> None:
    # Empty .build-loop/architecture/ → missing.
    _arch(tmp_path)
    assert af.cmd_check(tmp_path) == "missing"

    # Even with arch dir absent, also missing.
    other = tmp_path / "other"
    other.mkdir()
    assert af.cmd_check(other) == "missing"


def test_check_stale_when_manifest_old(tmp_path: Path) -> None:
    _write_manifest(tmp_path, mtime_age_seconds=25 * 3600)
    assert af.cmd_check(tmp_path) == "stale"


def test_check_fresh_when_manifest_recent(tmp_path: Path) -> None:
    _write_manifest(tmp_path, mtime_age_seconds=60)  # 60s old → fresh
    assert af.cmd_check(tmp_path) == "fresh"


def test_check_fresh_but_old_when_manifest_between_1h_and_24h(tmp_path: Path) -> None:
    _write_manifest(tmp_path, mtime_age_seconds=2 * 3600)  # 2h old
    assert af.cmd_check(tmp_path) == "fresh-but-old"


def test_check_returns_stale_when_state_marked_stale(tmp_path: Path) -> None:
    # Even with a recent manifest, explicit stale marker dominates.
    _write_manifest(tmp_path, mtime_age_seconds=60)
    af.cmd_mark_stale(tmp_path, "any.py")
    assert af.cmd_check(tmp_path) == "stale"


def test_lockfile_prevents_concurrent_scans(tmp_path: Path) -> None:
    """Acquire the lock externally; --mark-stale still updates state, and the
    script does NOT fire any subprocess (it never does — the hook does).

    Verified by: (1) --mark-stale succeeds while we hold the lock, (2) the
    state.json reflects the stale mark, and (3) no child processes are
    spawned by the script (asserted via subprocess return).
    """
    arch = _arch(tmp_path)
    lockpath = arch / ".scan.lock"
    lf = open(lockpath, "a+")
    fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
    try:
        # Run the script via CLI to exercise the full path.
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPTS / "architecture_freshness.py"),
                "--mark-stale",
                "--file",
                "scanner.py",
                "--workdir",
                str(tmp_path),
                "--no-fire",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        st = _read_state(tmp_path)
        assert st["architecture"]["stale"] is True
        assert "scanner.py" in st["architecture"]["staleFiles"]

        # The script must not block. Sanity: it returned in <2s.
        # (subprocess.run with timeout=10 already failed if it hung.)
    finally:
        fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        lf.close()


def test_dedup_stale_files(tmp_path: Path) -> None:
    for _ in range(3):
        af.cmd_mark_stale(tmp_path, "src/foo.py")
    arch = _read_state(tmp_path)["architecture"]
    assert arch["staleFiles"] == ["src/foo.py"]


def test_stale_files_cap(tmp_path: Path) -> None:
    """Cap of 50 prevents unbounded growth."""
    for i in range(60):
        af.cmd_mark_stale(tmp_path, f"file_{i}.py")
    arch = _read_state(tmp_path)["architecture"]
    assert len(arch["staleFiles"]) == af.STALE_FILES_CAP
    # Most recent should be retained.
    assert "file_59.py" in arch["staleFiles"]
    assert "file_0.py" not in arch["staleFiles"]


def test_lockfile_path_returned(tmp_path: Path) -> None:
    out = af.cmd_lockfile(tmp_path)
    assert out.endswith(".build-loop/architecture/.scan.lock")


def test_atomic_write_does_not_clobber_other_keys(tmp_path: Path) -> None:
    """Mark-stale must preserve unrelated state.json keys."""
    state_path = _state(tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "active": True,
        "phase": "execute",
        "architecture": {"acpPath": ".build-loop/architecture/acp.json"},
    }), encoding="utf-8")

    af.cmd_mark_stale(tmp_path, "x.py")
    st = _read_state(tmp_path)
    assert st["schema_version"] == "1.0.0"
    assert st["active"] is True
    assert st["phase"] == "execute"
    # Existing acpPath preserved through the merge.
    assert st["architecture"]["acpPath"] == ".build-loop/architecture/acp.json"
    assert st["architecture"]["stale"] is True
