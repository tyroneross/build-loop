"""Tests for ``scripts/inject_dependency_cooldown.py`` (v0.11.2 corrected contract).

Covers the bugfix contract:
1. npm path writes ``.npmrc`` ``min-release-age=<days>`` (kebab, DAYS) — NOT
   the old camelCase ``minimumReleaseAge``. No exclude key (npm has none).
   ``allowlist_mechanism == "hook"``.
2. npm ``--check`` reports ``enforced:false`` when the key is written but the
   package manager does NOT recognize it (false-positive fix) — proven with a
   fake-npm shim emitting "Unknown project config".
3. pnpm lockfile -> ``pnpm-workspace.yaml`` ``minimumReleaseAge`` MINUTES +
   exclude, AND ``.npmrc`` kebab ``minimum-release-age`` MINUTES for 10.16.x.
   ``allowlist_mechanism == "native"`` (pnpm >= 10.16.0 only).
4. yarn lockfile -> ``.yarnrc.yml`` ``npmMinimalAgeGate`` numeric MINUTES +
   ``npmPreapprovedPackages``. ``allowlist_mechanism == "native"``.
5. Idempotency: a second run produces a byte-identical file.
6. Allowlist: default @tyroneross/*; config-supplied extra is unioned.
7. No package.json -> skipped, exit 0.
8. Real npm enforcement: when machine npm recognizes the correct key,
   --check after a write reports enforced:true.
9. pnpm floor (v0.11.2 fix): ``minimumReleaseAge``/``minimumReleaseAgeExclude``
   do not exist before pnpm 10.16.0. A from-scratch write on pnpm < 10.16.0
   writes NOTHING and reports ``enforced:false``, ``status:fallback-hook``,
   ``allowlist_mechanism:"hook"`` — proven with a fake-pnpm shim. ``--check``
   gets the same gate.
10. pnpm ``packages`` field (v0.11.2 fix): the injector seeds
    ``packages: ['.']`` whenever ``pnpm-workspace.yaml`` has NO ``packages``
    field at all — a from-scratch write, or an existing file left with only
    cooldown/settings keys by a prior buggy run or by ``pnpm approve-builds``
    (older pnpm hard-fails install without it); an existing ``packages``
    field is preserved byte-for-byte, never rewritten.
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


def _fake_pnpm_bin_cwd_sensitive(tmp_path: Path) -> str:
    """Finding 2 regression fixture: reports a NEW-enough version only when
    invoked with cwd == the marked target directory (a ``PNPM_MARKER`` file
    dropped in it), and an OLD version otherwise. Proves the version
    detector is invoked with ``cwd=workdir`` rather than inheriting whatever
    directory the calling process happens to be in — this repo's own
    ambient package.json carries a ``packageManager`` pin that makes a
    cwd-less ``pnpm --version`` fail, which is exactly how this bug was
    field-caught."""
    bindir = tmp_path / "fakebin_pnpm_cwd"
    bindir.mkdir(exist_ok=True)
    pnpm = bindir / "pnpm"
    body = (
        "#!/bin/bash\n"
        'if [ "$1" = "--version" ]; then\n'
        '  if [ -f "PNPM_MARKER" ]; then echo "10.16.0"; else echo "1.0.0"; fi\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    pnpm.write_text(body)
    pnpm.chmod(0o755)
    return f"{bindir}:{os.environ.get('PATH','')}"


def _fake_pnpm_bin(tmp_path: Path, *, version: str) -> str:
    """Create a fake `pnpm` on PATH reporting the given ``--version``."""
    bindir = tmp_path / "fakebin_pnpm"
    bindir.mkdir(exist_ok=True)
    pnpm = bindir / "pnpm"
    body = (
        "#!/bin/bash\n"
        f'if [ "$1" = "--version" ]; then echo "{version}"; exit 0; fi\n'
        "exit 0\n"
    )
    pnpm.write_text(body)
    pnpm.chmod(0o755)
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


# --- pnpm floor (v0.11.2): < 10.16.0 has NO native cooldown ---------------
def test_pnpm_old_version_writes_nothing_and_falls_back_to_hook(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")  # no pre-existing workspace file
    fake_path = _fake_pnpm_bin(tmp_path, version="9.0.0")
    rc, env = _run(wd, env_path=fake_path)
    assert rc == 0
    assert env["status"] == "fallback-hook"
    assert env["enforced"] is False
    assert env["allowlist_mechanism"] == "hook"
    assert env["pnpm_version"] == "9.0.0"
    assert "10.16.0" in env["reason"]
    # The defect this fixes: writing an inert key (and no `packages` field)
    # broke every subsequent pnpm command. Fixed by writing nothing.
    assert not (wd / "pnpm-workspace.yaml").is_file()
    assert not (wd / ".npmrc").is_file()


def test_pnpm_old_version_check_reports_not_enforced(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")
    # Pre-existing (inert) key on disk must not fool --check.
    (wd / "pnpm-workspace.yaml").write_text(
        "packages:\n  - '.'\nminimumReleaseAge: 10080\n"
    )
    fake_path = _fake_pnpm_bin(tmp_path, version="9.5.2")
    rc, env = _run(wd, "--check", env_path=fake_path)
    assert rc == 0
    assert env["enforced"] is False
    assert env["status"] == "fallback-hook"
    assert env["allowlist_mechanism"] == "hook"


def test_pnpm_new_version_writes_and_enforces(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")
    fake_path = _fake_pnpm_bin(tmp_path, version="10.16.0")
    rc, env = _run(wd, env_path=fake_path)
    assert rc == 0
    assert env["status"] == "configured"
    assert env["enforced"] is True
    assert env["allowlist_mechanism"] == "native"
    assert env["pnpm_version"] == "10.16.0"
    ws = (wd / "pnpm-workspace.yaml").read_text()
    assert "minimumReleaseAge: 10080" in ws
    assert "minimumReleaseAgeExclude:" in ws


# --- Finding 2: version detector must run in workdir, not ambient cwd -----
def test_pnpm_version_detected_in_target_workdir(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")
    (wd / "PNPM_MARKER").write_text("")  # only the target dir has this
    fake_path = _fake_pnpm_bin_cwd_sensitive(tmp_path)
    rc, env = _run(wd, env_path=fake_path)
    assert rc == 0
    # cwd=workdir -> shim sees PNPM_MARKER -> reports 10.16.0 -> native path.
    assert env["pnpm_version"] == "10.16.0"
    assert env["status"] == "configured"
    assert env["enforced"] is True


def test_npm_version_detected_in_target_workdir(tmp_path):
    wd = _mk(tmp_path)  # no lockfile -> npm path
    (wd / "PNPM_MARKER").write_text("")  # reuse the same marker convention
    bindir = tmp_path / "fakebin_npm_cwd"
    bindir.mkdir(exist_ok=True)
    npm = bindir / "npm"
    npm.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "--version" ]; then\n'
        '  if [ -f "PNPM_MARKER" ]; then echo "11.14.1"; else echo "1.0.0"; fi\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "config" ] && [ "$2" = "get" ]; then echo "7"; exit 0; fi\n'
        "exit 0\n"
    )
    npm.chmod(0o755)
    fake_path = f"{bindir}:{os.environ.get('PATH','')}"
    rc, env = _run(wd, env_path=fake_path)
    assert rc == 0
    assert env["npm_version"] == "11.14.1"
    assert env["enforced"] is True


# --- pnpm `packages` field (v0.11.2): required for older pnpm -------------
def test_pnpm_from_scratch_write_seeds_packages_field(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")  # no pre-existing workspace file
    fake_path = _fake_pnpm_bin(tmp_path, version="10.16.0")
    rc, env = _run(wd, env_path=fake_path)
    assert rc == 0
    ws = (wd / "pnpm-workspace.yaml").read_text()
    assert "packages:" in ws
    assert "['.']" in ws
    # This is the regression test for the reported broken install: a
    # workspace file with ONLY the cooldown keys is invalid on pnpm that
    # requires `packages`.


def test_pnpm_existing_packages_field_preserved_byte_for_byte(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")
    (wd / "pnpm-workspace.yaml").write_text("packages:\n  - 'pkgs/*'\n")
    fake_path = _fake_pnpm_bin(tmp_path, version="10.16.0")
    rc, env = _run(wd, env_path=fake_path)
    assert rc == 0
    ws = (wd / "pnpm-workspace.yaml").read_text()
    assert "- 'pkgs/*'" in ws
    assert "['.']" not in ws  # never seeded over an existing packages field


def test_pnpm_already_broken_settings_only_file_self_heals(tmp_path):
    """Finding 1 regression: an EXISTING pnpm-workspace.yaml written by a
    prior buggy run (or by `pnpm approve-builds`) with only the cooldown
    keys and no `packages` field must gain one on re-run — not stay broken
    forever because `creating_new` was False."""
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")
    (wd / "pnpm-workspace.yaml").write_text(
        "minimumReleaseAge: 10080\nminimumReleaseAgeExclude: [\"@tyroneross/*\"]\n"
    )
    fake_path = _fake_pnpm_bin(tmp_path, version="10.16.0")
    rc, env = _run(wd, env_path=fake_path)
    assert rc == 0
    ws = (wd / "pnpm-workspace.yaml").read_text()
    assert "packages:" in ws
    assert "['.']" in ws
    assert "minimumReleaseAge: 10080" in ws  # cooldown keys survive
    assert "minimumReleaseAgeExclude:" in ws

    # Idempotent on this healed path too.
    first = ws
    rc2, env2 = _run(wd, env_path=fake_path)
    assert (wd / "pnpm-workspace.yaml").read_text() == first
    assert env2["changed"] is False


def test_idempotent_pnpm_from_scratch(tmp_path):
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")  # no pre-existing workspace file
    fake_path = _fake_pnpm_bin(tmp_path, version="10.16.0")
    _run(wd, env_path=fake_path)
    first_ws = (wd / "pnpm-workspace.yaml").read_text()
    first_npmrc = (wd / ".npmrc").read_text()
    rc, env = _run(wd, env_path=fake_path)
    assert (wd / "pnpm-workspace.yaml").read_text() == first_ws
    assert (wd / ".npmrc").read_text() == first_npmrc
    assert env["changed"] is False


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


def test_pnpm_block_style_exclude_round_trips_to_valid_yaml(tmp_path):
    """Finding f2 regression: a user-authored BLOCK-style
    ``minimumReleaseAgeExclude`` (the natural YAML form, and the form
    pnpm's own release notes use) must round-trip to valid YAML with the
    user's entries preserved — not leave orphaned continuation lines that
    make the file unparseable AND silently drop the user's allowlist
    entries."""
    wd = _mk(tmp_path, lockfile="pnpm-lock.yaml")
    (wd / "pnpm-workspace.yaml").write_text(
        "packages:\n"
        "  - '.'\n"
        "minimumReleaseAge: 10080\n"
        "minimumReleaseAgeExclude:\n"
        "  - '@acme/*'\n"
        "  - 'internal-lib'\n"
    )
    fake_path = _fake_pnpm_bin(tmp_path, version="10.16.0")
    rc, env = _run(wd, env_path=fake_path)
    assert rc == 0
    ws = (wd / "pnpm-workspace.yaml").read_text()

    # No orphaned continuation lines: every line is either a top-level key
    # or a "- " item that immediately follows its owning key.
    lines = ws.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("- ") and i > 0:
            assert lines[i - 1].rstrip().endswith(":") or lines[i - 1].strip().startswith(
                "- "
            ), f"orphaned continuation line: {line!r} (prev: {lines[i - 1]!r})"

    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(ws)
        assert parsed["minimumReleaseAgeExclude"] == [
            "@tyroneross/*",
            "@acme/*",
            "internal-lib",
        ]
        assert parsed["packages"] == ["."]
    except ImportError:
        # No PyYAML in this test env — structural assertion only.
        assert "@acme/*" in ws
        assert "internal-lib" in ws
        assert '  - \'@acme/*\'' not in ws  # old continuation line consumed

    # Preserved, not dropped.
    assert "@acme/*" in ws
    assert "internal-lib" in ws

    # Idempotent on this healed/merged path too.
    first = ws
    rc2, env2 = _run(wd, env_path=fake_path)
    assert (wd / "pnpm-workspace.yaml").read_text() == first
    assert env2["changed"] is False


def test_yarn_block_style_preapproved_round_trips(tmp_path):
    """Same bug shape as the pnpm exclude list, for yarn's
    ``npmPreapprovedPackages``."""
    wd = _mk(tmp_path, lockfile="yarn.lock")
    (wd / ".yarnrc.yml").write_text(
        "npmMinimalAgeGate: 10080\nnpmPreapprovedPackages:\n  - 'acme-pkg'\n"
    )
    rc, env = _run(wd)
    assert rc == 0
    rc_content = (wd / ".yarnrc.yml").read_text()
    assert "acme-pkg" in rc_content
    lines = rc_content.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("- ") and i > 0:
            assert lines[i - 1].rstrip().endswith(":") or lines[i - 1].strip().startswith(
                "- "
            ), f"orphaned continuation line: {line!r}"
