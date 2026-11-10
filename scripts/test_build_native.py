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
    built = repo / "target" / "release" / "bl"
    built.parent.mkdir(parents=True)
    built.write_text("#!/bin/sh\nexit 0\n")
    return repo


def _runner(fail_on: str | None = None):
    calls: list[str] = []

    def run(label, argv, repo, env=None):
        calls.append(label)
        if label == fail_on:
            raise bn.BuildError(f"{label} failed (exit 1)")
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
    digest = bn.build_and_install(repo, tmp_path / "dest", run)
    assert bn.tracked_digest(repo) == digest == bn.compute_digest(repo)


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
