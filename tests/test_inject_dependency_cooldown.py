"""Tests for ``scripts/inject_dependency_cooldown.py`` (v0.11.1 corrected contract).

Covers the bugfix contract:
1. npm path writes ``.npmrc`` ``min-release-age=<days>`` (kebab, DAYS) — NOT
   the old camelCase ``minimumReleaseAge``. No exclude key (npm has none).
   ``allowlist_mechanism == "hook"``.
2. npm ``--check`` reports ``enforced:false`` when the key is written but the
   package manager does NOT recognize it (false-positive fix) — proven with a
   fake-npm shim emitting "Unknown project config".
3. pnpm lockfile -> ``pnpm-workspace.yaml`` ``minimumReleaseAge`` MINUTES +
   exclude, AND ``.npmrc`` kebab ``minimum-release-age`` MINUTES for 10.x.
   ``allowlist_mechanism == "native"``.
4. yarn lockfile -> ``.yarnrc.yml`` ``npmMinimalAgeGate`` numeric MINUTES +
   ``npmPreapprovedPackages``. ``allowlist_mechanism == "native"``.
5. Idempotency: a second run produces a byte-identical file.
6. Allowlist: default @tyroneross/*; config-supplied extra is unioned.
7. No package.json -> skipped, exit 0.
8. Real npm enforcement: when machine npm recognizes the correct key,
   --check after a write reports enforced:true.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "inject_dependency_cooldown.py"


def _npm_supports_native() -> bool:
    """Machine npm >= 11.10.0 (native min-release-age)."""
    npm = shutil.which("npm")
    if not npm:
        return False
    try:
        out = subprocess.run([npm, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", out.stdout.strip())
    if not m:
        return False
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (11, 10, 0)


def _run(workdir: Path, *extra: str, env_path: str | None = None) -> tuple[int, dict]:
    env = dict(os.environ)
    if env_path is not None:
        env["PATH"] = env_path
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir), "--json", *extra],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
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


def _fake_npm_bin(tmp_path: Path, *, reject: bool) -> str:
    """Create a fake `npm` on PATH. `reject=True` emits the
    "Unknown project config" warning that the false-positive fix detects."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    npm = bindir / "npm"
    if reject:
        body = (
            "#!/bin/bash\n"
            'if [ "$1" = "--version" ]; then echo "11.14.1"; exit 0; fi\n'
            'if [ "$1" = "config" ] && [ "$2" = "get" ]; then\n'
            '  echo \'npm warn Unknown project config "min-release-age". '
            "This will stop working in the next major version of npm.' >&2\n"
            '  echo "7"; exit 0\n'
            "fi\n"
            "exit 0\n"
        )
    else:
        body = (
            "#!/bin/bash\n"
            'if [ "$1" = "--version" ]; then echo "11.14.1"; exit 0; fi\n'
            'if [ "$1" = "config" ] && [ "$2" = "get" ]; then echo "7"; exit 0; fi\n'
            "exit 0\n"
        )
    npm.write_text(body)
    npm.chmod(0o755)
    return f"{bindir}:{os.environ.get('PATH','')}"


# --- npm: correct key, no exclude, mechanism=hook --------------------------
@pytest.mark.skipif(not _npm_supports_native(), reason="machine npm < 11.10.0")
def test_npm_writes_correct_kebab_key(tmp_path):
    wd = _mk(tmp_path)  # no lockfile -> npm
    rc, env = _run(wd)
    assert rc == 0
    body = (wd / ".npmrc").read_text()
    assert "min-release-age=7" in body  # kebab, DAYS
    assert "minimumReleaseAge" not in body  # NOT the old buggy camelCase key
    assert "minimumReleaseAgeExclude" not in body  # npm has no native exclude
    assert env["package_manager"] == "npm"
    assert env["allowlist_mechanism"] == "hook"
    assert env["enforced"] is True  # real npm recognizes it


# --- false-positive fix: written-but-unrecognized -> enforced:false --------
def test_npm_unrecognized_key_reports_not_enforced(tmp_path):
    wd = _mk(tmp_path)
    (wd / ".npmrc").write_text("min-release-age=7\n")  # key present
    fake_path = _fake_npm_bin(tmp_path, reject=True)  # but npm rejects it
    rc, env = _run(wd, "--check", env_path=fake_path)
    assert rc == 0
    assert env["enforced"] is False  # THE false-positive fix
    assert "Unknown project config" in env["reason"]
    assert env["status"] == "fallback-hook"


def test_npm_recognized_key_reports_enforced(tmp_path):
    wd = _mk(tmp_path)
    (wd / ".npmrc").write_text("min-release-age=7\n")
    fake_path = _fake_npm_bin(tmp_path, reject=False)  # npm accepts it
    rc, env = _run(wd, "--check", env_path=fake_path)
    assert rc == 0
    assert env["enforced"] is True
    assert env["allowlist_mechanism"] == "hook"


def test_npm_no_key_reports_not_enforced(tmp_path):
    wd = _mk(tmp_path)  # no .npmrc
    fake_path = _fake_npm_bin(tmp_path, reject=False)
    rc, env = _run(wd, "--check", env_path=fake_path)
    assert env["enforced"] is False
    assert "not yet injected" in env["reason"]


# --- pnpm: minutes in workspace yaml + kebab minutes in .npmrc -------------
def test_pnpm_writes_minutes_and_npmrc_compat(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")
    (wd / "pnpm-workspace.yaml").write_text("packages:\n  - 'pkgs/*'\n")
    rc, env = _run(wd)
    assert rc == 0 and env["status"] == "configured"
    assert env["package_manager"] == "pnpm"
    assert env["allowlist_mechanism"] == "native"
    ws = (wd / "pnpm-workspace.yaml").read_text()
    assert "minimumReleaseAge: 10080" in ws  # 7 * 1440 minutes
    assert '"@tyroneross/*"' in ws
    assert "minimumReleaseAgeExclude:" in ws
    assert "packages:" in ws  # pre-existing key preserved
    npmrc = (wd / ".npmrc").read_text()
    assert "minimum-release-age=10080" in npmrc  # pnpm 10.x kebab, MINUTES


# --- yarn: numeric minutes ------------------------------------------------
def test_yarn_writes_numeric_minutes(tmp_path):
    wd = _mk(tmp_path, lockfile="yarn.lock")
    rc, env = _run(wd)
    assert rc == 0 and env["package_manager"] == "yarn"
    assert env["allowlist_mechanism"] == "native"
    body = (wd / ".yarnrc.yml").read_text()
    assert "npmMinimalAgeGate: 10080" in body  # numeric minutes (string 7d bugged)
    assert "npmPreapprovedPackages:" in body


def test_idempotent_pnpm(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")
    _run(wd)
    first = (wd / "pnpm-workspace.yaml").read_text()
    first_npmrc = (wd / ".npmrc").read_text()
    rc, env = _run(wd)
    assert (wd / "pnpm-workspace.yaml").read_text() == first
    assert (wd / ".npmrc").read_text() == first_npmrc
    assert env["changed"] is False


def test_allowlist_union(tmp_path):
    wd = _mk(
        tmp_path,
        lockfile="pnpm-lock.yaml",
        config={"dependencyCooldown": {"allowlist": ["@acme/*", "mylib"]}},
    )
    rc, env = _run(wd, "--check")
    assert env["allowlist"] == ["@tyroneross/*", "@acme/*", "mylib"]
    assert env["allowlist"][0] == "@tyroneross/*"  # default first, not removable


def test_no_package_json_skips(tmp_path):
    wd = tmp_path
    (wd / ".build-loop").mkdir()
    (wd / ".build-loop" / "config.json").write_text("{}")
    rc, env = _run(wd)
    assert rc == 0 and env["status"] == "skipped"
    assert env["enforced"] is False
    assert env["allowlist_mechanism"] is None


def test_threshold_minutes_math(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")
    rc, env = _run(wd)
    assert env["threshold_days"] == 7
    assert "10080" in (wd / "pnpm-workspace.yaml").read_text()  # 7*24*60
