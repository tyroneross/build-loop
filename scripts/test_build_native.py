# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/build_native.py install gating and --check.

Build steps are replaced by a recorder, so these run without cargo; the
digest itself is covered by tests/test_native_sources_digest.py.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "build_native.py"
_spec = importlib.util.spec_from_file_location("build_native", _SCRIPT)
bn = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("build_native", bn)
_spec.loader.exec_module(bn)


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "r"
    for rel in bn.MIRRORED + ("Cargo.toml", "Cargo.lock", "crates/bl/src/main.rs"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(rel.encode() + b"\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    bn.write_digest(repo)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e",
                    "commit", "-q", "-m", "base"], check=True)
    return repo


def _runner(fail_on: str | None = None, produce: bool = True):
    """Stand-in for run_step; ``cargo build`` writes the binary unless told not to."""
    calls: list[str] = []
    argvs: list[list[str]] = []

    def run(label, argv, repo, env=None):
        calls.append(label)
        argvs.append(argv)
        if label == fail_on:
            raise bn.BuildError(f"{label} failed (exit 1)")
        if label == "cargo build" and produce:
            built = Path(argv[argv.index("--target-dir") + 1]) / "release" / "bl"
            built.parent.mkdir(parents=True, exist_ok=True)
            built.write_text("#!/bin/sh\nexit 0\n")
    run.argvs = argvs
    return run, calls


def test_installs_binary_and_sidecar_after_every_step_passes(repo, tmp_path):
    dest = tmp_path / "dest"
    run, calls = _runner()
    digest = bn.build_and_install(repo, dest, run)
    assert calls == ["cargo build", "cargo test", "parity harness"]
    assert (dest / "bl").read_text() == (repo / "target/release/bl").read_text()
    assert (dest / "bl").stat().st_mode & 0o111
    assert (dest / "bl.sources").read_text() == digest + "\n"
    assert bn.installed_status(repo, dest) == (True, f"fresh ({digest[:12]})")


@pytest.mark.parametrize("step", ["cargo build", "cargo test", "parity harness"])
def test_failed_step_installs_nothing(repo, tmp_path, step):
    dest = tmp_path / "dest"
    run, _ = _runner(fail_on=step)
    with pytest.raises(bn.BuildError, match=step):
        bn.build_and_install(repo, dest, run)
    assert not (dest / "bl").exists() and not (dest / "bl.sources").exists()


def test_failed_launcher_parity_removes_sidecar(repo):
    dest = repo / "bin" / "native"
    run, calls = _runner(fail_on="launcher parity")
    with pytest.raises(bn.BuildError):
        bn.build_and_install(repo, dest, run)
    assert calls[-1] == "launcher parity"
    assert (dest / "bl").exists() and not (dest / "bl.sources").exists()


def test_stale_tracked_digest_is_rewritten_by_the_build(repo, tmp_path):
    target = repo / "scripts/rally_point/hooks.py"
    target.write_bytes(target.read_bytes() + b"#")
    run, _ = _runner()
    digest = bn.build_and_install(repo, tmp_path / "dest", run, allow_dirty=True)
    assert bn.tracked_digest(repo) == digest == bn.compute_digest(repo)


def test_dirty_watched_file_refuses_install(repo, tmp_path):
    dest = tmp_path / "dest"
    target = repo / "crates/bl/src/main.rs"
    target.write_bytes(target.read_bytes() + b"//")
    run, calls = _runner()
    with pytest.raises(bn.BuildError, match="uncommitted changes in crates/bl/src/main.rs"):
        bn.build_and_install(repo, dest, run)
    assert calls == [] and not dest.exists()
    # Staged but uncommitted is dirty too; the digest file alone is not.
    subprocess.run(["git", "-C", str(repo), "add", "crates/bl/src/main.rs"], check=True)
    assert bn.dirty_sources(repo) == ["crates/bl/src/main.rs"]
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "HEAD", "--", "crates/bl/src/main.rs"],
                   check=True)
    (repo / bn.DIGEST_FILE).write_text("0" * 64 + "\n")
    assert bn.dirty_sources(repo) == []


def test_cli_dirty_tree_is_a_one_line_error(repo, tmp_path):
    # A copy of the script inside the mini repo builds that repo, not this one.
    (repo / "scripts" / "build_native.py").write_text(_SCRIPT.read_text())
    (repo / "Cargo.lock").write_text("changed\n")
    cp = subprocess.run([sys.executable, str(repo / "scripts" / "build_native.py"),
                         "--dest", str(tmp_path / "d")], cwd=str(repo), capture_output=True,
                        text=True, timeout=30)
    assert cp.returncode == 1
    assert cp.stderr.count("\n") == 1 and "uncommitted changes in Cargo.lock" in cp.stderr
    assert not (tmp_path / "d").exists()


def test_every_cargo_step_pins_the_target_dir(repo, tmp_path):
    run, _ = _runner()
    bn.build_and_install(repo, tmp_path / "dest", run)
    cargo = [a for a in run.argvs if a[0] == "cargo"]
    assert len(cargo) == 2
    for argv in cargo:
        assert argv[argv.index("--target-dir") + 1] == str(repo / "target")


def test_missing_built_binary_is_an_error(repo, tmp_path):
    stale = repo / "target" / "release" / "bl"
    stale.parent.mkdir(parents=True)
    stale.write_text("old binary")
    run, calls = _runner(produce=False)
    with pytest.raises(bn.BuildError, match="left no binary"):
        bn.build_and_install(repo, tmp_path / "dest", run)
    assert "parity harness" not in calls and not (tmp_path / "dest").exists()


def test_check_turns_stale_when_a_mirrored_file_changes(repo, tmp_path):
    dest = tmp_path / "dest"
    bn.build_and_install(repo, dest, _runner()[0])
    target = repo / "hooks/_bounded_hook.sh"
    target.write_bytes(target.read_bytes() + b"#")
    fresh, reason = bn.installed_status(repo, dest)
    assert not fresh and "out of date" in reason
    bn.write_digest(repo)  # what the commit hook does
    fresh, reason = bn.installed_status(repo, dest)
    assert not fresh and "shell hook runs" in reason


def test_check_without_sidecar(repo, tmp_path):
    dest = tmp_path / "dest"
    bn.build_and_install(repo, dest, _runner()[0])
    (dest / "bl.sources").unlink()
    assert bn.installed_status(repo, dest)[0] is False


def test_cli_check_exit_codes(tmp_path):
    cp = subprocess.run([sys.executable, str(_SCRIPT), "--check", "--dest", str(tmp_path)],
                        capture_output=True, text=True)
    assert cp.returncode == 1
    assert cp.stderr.count("\n") == 1 and "no executable" in cp.stderr
