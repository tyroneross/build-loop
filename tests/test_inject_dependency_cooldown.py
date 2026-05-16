"""Tests for ``scripts/inject_dependency_cooldown.py``.

Covers the T-3 contract:
1. npm < 11.10 (this machine) → status=fallback-hook, no inert key claimed.
2. pnpm lockfile → pnpm-workspace.yaml gets minimumReleaseAge: 10080 +
   exclude; merges with pre-existing YAML keys.
3. yarn lockfile → .yarnrc.yml keys written.
4. Idempotency: a second run produces a byte-identical file.
5. Allowlist: default @tyroneross/*; config-supplied extra is unioned.
6. No package.json → skipped, exit 0.
7. --check reports enforced true/false without writing.
8. Threshold/date math is exposed via threshold_days (date math itself
   lives in the hook; here we assert minutes = days*1440 for pnpm/yarn).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "inject_dependency_cooldown.py"


def _run(workdir: Path, *extra: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir), "--json", *extra],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode, json.loads(proc.stdout)


def _mk(tmp_path: Path, *, pkg=True, lockfile=None, config=None) -> Path:
    wd = tmp_path
    if pkg:
        (wd / "package.json").write_text('{"name":"t"}')
    if lockfile:
        (wd / lockfile).write_text("")
    (wd / ".build-loop").mkdir(exist_ok=True)
    if config is not None:
        (wd / ".build-loop" / "config.json").write_text(json.dumps(config))
    else:
        (wd / ".build-loop" / "config.json").write_text("{}")
    return wd


def test_npm_below_threshold_is_fallback_hook(tmp_path):
    # Machine npm is 10.9.4 (< 11.10.0). Must NOT claim the inert key works.
    wd = _mk(tmp_path)  # no lockfile → npm
    rc, env = _run(wd)
    assert rc == 0
    assert env["status"] == "fallback-hook"
    assert env["enforced"] is False
    assert not (wd / ".npmrc").exists()  # no inert key written


def test_pnpm_writes_and_merges(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")
    (wd / "pnpm-workspace.yaml").write_text("packages:\n  - 'pkgs/*'\n")
    rc, env = _run(wd)
    assert rc == 0 and env["status"] == "configured"
    assert env["package_manager"] == "pnpm" and env["enforced"] is True
    body = (wd / "pnpm-workspace.yaml").read_text()
    assert "minimumReleaseAge: 10080" in body  # 7 * 1440
    assert '"@tyroneross/*"' in body
    assert "packages:" in body  # pre-existing key preserved


def test_yarn_writes(tmp_path):
    wd = _mk(tmp_path, lockfile="yarn.lock")
    rc, env = _run(wd)
    assert rc == 0 and env["package_manager"] == "yarn"
    body = (wd / ".yarnrc.yml").read_text()
    assert "npmMinimalAgeGate: 10080" in body
    assert "npmPreapprovedPackages:" in body


def test_idempotent(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")
    _run(wd)
    first = (wd / "pnpm-workspace.yaml").read_text()
    rc, env = _run(wd)
    second = (wd / "pnpm-workspace.yaml").read_text()
    assert first == second  # byte-identical
    assert env["changed"] is False


def test_allowlist_union(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml",
             config={"dependencyCooldown": {"allowlist": ["@acme/*", "mylib"]}})
    rc, env = _run(wd, "--check")
    assert env["allowlist"] == ["@tyroneross/*", "@acme/*", "mylib"]
    # Default is always first and not removable.
    assert env["allowlist"][0] == "@tyroneross/*"


def test_no_package_json_skips(tmp_path):
    wd = tmp_path
    (wd / ".build-loop").mkdir()
    (wd / ".build-loop" / "config.json").write_text("{}")
    rc, env = _run(wd)
    assert rc == 0 and env["status"] == "skipped"
    assert env["enforced"] is False


def test_check_does_not_write(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")
    rc, env = _run(wd, "--check")
    assert rc == 0
    assert env["enforced"] is False  # nothing written yet
    assert not (wd / "pnpm-workspace.yaml").exists()
    # Now write, then --check should report enforced.
    _run(wd)
    rc, env = _run(wd, "--check")
    assert env["enforced"] is True


def test_threshold_minutes_math(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")
    rc, env = _run(wd)
    assert env["threshold_days"] == 7
    assert "10080" in (wd / "pnpm-workspace.yaml").read_text()  # 7*24*60
