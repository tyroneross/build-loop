# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""crates/SOURCES.digest must match the sources bin/native/bl is built from.

The pre-edit launcher runs bin/native/bl only when the binary's sidecar digest
equals this tracked digest, so a stale tracked digest would let a binary built
from older sources keep running. Fix a failure with:
    python3 scripts/build_native.py --write-digest
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_native", REPO / "scripts" / "build_native.py")
bn = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("build_native", bn)
_spec.loader.exec_module(bn)


def test_tracked_digest_is_current():
    assert bn.tracked_digest() == bn.compute_digest(), (
        "crates/SOURCES.digest is out of date; run: python3 scripts/build_native.py --write-digest"
    )


def test_every_mirrored_file_is_watched_by_the_commit_guard():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import artifact_guard
    finally:
        sys.path.pop(0)
    entry = next(a for a in artifact_guard.ARTIFACTS if a.name == "native-sources-digest")
    assert entry.watch == bn.WATCHED
    assert entry.outputs == (bn.DIGEST_FILE,)


@pytest.fixture
def mini_repo(tmp_path):
    """A git repo holding every watched path, with one-line contents."""
    repo = tmp_path / "r"
    for rel in bn.MIRRORED + ("Cargo.toml", "Cargo.lock", "crates/bl/src/main.rs"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(rel.encode() + b"\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    return repo


def test_digest_is_location_independent(mini_repo, tmp_path):
    copy = tmp_path / "elsewhere" / "r"
    shutil.copytree(mini_repo, copy)
    assert bn.compute_digest(mini_repo) == bn.compute_digest(copy)


def test_one_byte_in_a_mirrored_file_changes_the_digest(mini_repo):
    before = bn.write_digest(mini_repo)
    assert bn.tracked_digest(mini_repo) == before
    target = mini_repo / "scripts/rally_point/hooks.py"
    target.write_bytes(target.read_bytes() + b"#")
    assert bn.compute_digest(mini_repo) != before


def test_digest_file_and_untracked_files_do_not_count(mini_repo):
    before = bn.compute_digest(mini_repo)
    bn.write_digest(mini_repo)
    subprocess.run(["git", "-C", str(mini_repo), "add", bn.DIGEST_FILE], check=True)
    (mini_repo / "crates" / "scratch.swp").write_text("junk")
    assert bn.compute_digest(mini_repo) == before


def test_untracked_mirrored_file_is_an_error(mini_repo):
    subprocess.run(["git", "-C", str(mini_repo), "rm", "-q", "--cached", "hooks/_bounded_hook.sh"], check=True)
    with pytest.raises(bn.BuildError, match="not tracked"):
        bn.compute_digest(mini_repo)
