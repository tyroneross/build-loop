"""Tests for scripts/version_drift_warning.py.

Real git tmpdirs, real installed_plugins.json files. No mocking magic.
"""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import version_drift_warning as vdw  # noqa: E402


def _g(wd: Path, *args: str, check=True) -> str:
    r = subprocess.run(["git", "-C", str(wd), *args], capture_output=True, text=True, check=check)
    return r.stdout.strip()


def _commit(wd: Path, name: str, msg: str):
    (wd / name).write_text(msg)
    _g(wd, "add", ".")
    _g(wd, "commit", "-qm", msg)


def _mk(tmp_path: Path, *, with_manifest=True, with_git=True, manifest_version="0.1.0",
        plugin_name="myplugin", extra_commits=0, tag_at_first=None) -> Path:
    wd = tmp_path / f"wd-{tmp_path.name[-6:]}"
    wd.mkdir(exist_ok=True)
    if with_manifest:
        (wd / ".claude-plugin").mkdir(exist_ok=True)
        (wd / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": plugin_name, "version": manifest_version}))
    if with_git:
        subprocess.run(["git", "init", "-q", "-b", "main", str(wd)], check=True, capture_output=True)
        _g(wd, "config", "user.email", "t@e")
        _g(wd, "config", "user.name", "t")
        _commit(wd, "README", "init")
        if tag_at_first:
            _g(wd, "tag", tag_at_first)
        for i in range(extra_commits):
            _commit(wd, f"f{i}", f"c{i}")
    return wd


def _ip(tmp_path: Path, *, plugin_name="myplugin", marketplace="local",
        version="0.1.0", git_sha=None) -> Path:
    p = tmp_path / "installed_plugins.json"
    entry = {"scope": "user", "version": version, "installPath": "/x", "installedAt": "2026-01-01"}
    if git_sha:
        entry["gitCommitSha"] = git_sha
    p.write_text(json.dumps({"version": 2, "plugins": {f"{plugin_name}@{marketplace}": [entry]}}))
    return p


# ---- Skip reasons --------------------------------------------------------

def test_no_manifest(tmp_path):
    wd = _mk(tmp_path, with_manifest=False)
    r = vdw.detect(wd, installed_path=_ip(tmp_path))
    assert r["drift_detected"] is False
    assert r["skip_reason"] == "no_manifest"
    assert r["plugin_name"] is None


def test_no_git(tmp_path):
    wd = _mk(tmp_path, with_git=False)
    r = vdw.detect(wd, installed_path=_ip(tmp_path))
    assert r["drift_detected"] is False
    assert r["skip_reason"] == "no_git"
    assert r["plugin_name"] == "myplugin"


def test_plugin_not_installed(tmp_path):
    wd = _mk(tmp_path)
    ip = tmp_path / "missing.json"  # not created
    r = vdw.detect(wd, installed_path=ip)
    assert r["drift_detected"] is False
    assert r["skip_reason"] == "plugin_not_installed"


def test_plugin_in_installed_but_different_name(tmp_path):
    wd = _mk(tmp_path, plugin_name="foo")
    ip = _ip(tmp_path, plugin_name="bar")
    r = vdw.detect(wd, installed_path=ip)
    assert r["skip_reason"] == "plugin_not_installed"


# ---- Drift detection -----------------------------------------------------

def test_no_drift_when_sha_matches_head(tmp_path):
    wd = _mk(tmp_path)
    head = _g(wd, "rev-parse", "HEAD")
    ip = _ip(tmp_path, git_sha=head)
    r = vdw.detect(wd, installed_path=ip)
    assert r["drift_detected"] is False
    assert r["commits_ahead"] == 0
    assert r["installed_sha"] == head


def test_drift_via_sha(tmp_path):
    wd = _mk(tmp_path)
    first = _g(wd, "rev-parse", "HEAD")
    for i in range(3):
        _commit(wd, f"f{i}", f"c{i}")
    ip = _ip(tmp_path, git_sha=first)
    r = vdw.detect(wd, installed_path=ip)
    assert r["drift_detected"] is True
    assert r["commits_ahead"] == 3
    assert "3 commits beyond" in r["warning_message"]


def test_drift_via_tag(tmp_path):
    wd = _mk(tmp_path, manifest_version="0.1.0",
                       tag_at_first="v0.1.0", extra_commits=2)
    ip = _ip(tmp_path, version="0.1.0")  # no sha — must use tag
    r = vdw.detect(wd, installed_path=ip)
    assert r["drift_detected"] is True
    assert r["commits_ahead"] == 2
    # manifest 0.1.0 manifest matches installed 0.1.0; drift is purely commit-count signal


def test_manifest_version_ahead_of_installed(tmp_path):
    """Manifest 0.11.0 with installed 0.10.0 + extra commits = drift signal."""
    wd = _mk(tmp_path, manifest_version="0.11.0",
                       tag_at_first="v0.10.0", extra_commits=5)
    ip = _ip(tmp_path, version="0.10.0")
    r = vdw.detect(wd, installed_path=ip)
    assert r["drift_detected"] is True
    assert r["commits_ahead"] == 5
    assert r["manifest_version"] == "0.11.0"
    assert r["installed_version"] == "0.10.0"


# ---- Edge cases ----------------------------------------------------------

def test_detached_head_with_sha(tmp_path):
    wd = _mk(tmp_path, extra_commits=2)
    sha = _g(wd, "rev-parse", "HEAD")
    _g(wd, "checkout", "-q", sha)
    first = _g(wd, "rev-list", "--max-parents=0", "HEAD")
    ip = _ip(tmp_path, git_sha=first)
    r = vdw.detect(wd, installed_path=ip)
    assert r["drift_detected"] is True
    assert r["commits_ahead"] == 2


def test_shallow_clone_drift_unavailable(tmp_path):
    """Shallow clone w/ unknown installed sha → commits_ahead None."""
    upstream = _mk(tmp_path)
    for i in range(3):
        _commit(upstream, f"x{i}", f"x{i}")
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{upstream}", str(shallow)],
                   check=True, capture_output=True)
    ip = _ip(tmp_path, git_sha="0" * 40)  # sha not present in shallow
    r = vdw.detect(shallow, installed_path=ip)
    assert r["commits_ahead"] is None
    assert "unavailable" in (r["warning_message"] or "")
    assert r["drift_detected"] is False


def test_corrupt_installed_plugins_json(tmp_path):
    wd = _mk(tmp_path)
    ip = tmp_path / "broken.json"
    ip.write_text("{not valid json")
    r = vdw.detect(wd, installed_path=ip)
    assert r["skip_reason"] == "plugin_not_installed"


def test_subprocess_invocation_emits_valid_json_schema(tmp_path):
    wd = _mk(tmp_path)
    head = _g(wd, "rev-parse", "HEAD")
    ip = _ip(tmp_path, git_sha=head)
    out = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "version_drift_warning.py"),
                          "--workdir", str(wd), "--json"],
                         capture_output=True, text=True, check=True,
                         env={"HOME": str(tmp_path)})  # so default installed_path resolves to nothing
    payload = json.loads(out.stdout)
    expected = {"drift_detected", "plugin_name", "manifest_version", "installed_version",
                "installed_sha", "head_sha", "commits_ahead", "warning_message", "skip_reason"}
    assert set(payload) == expected


def test_human_readable_output(tmp_path, capsys):
    wd = _mk(tmp_path, with_manifest=False)
    rc = vdw.main(["--workdir", str(wd)])
    out = capsys.readouterr().out
    assert "drift_detected: no" in out
    assert "skip_reason: no_manifest" in out
    assert rc == 0


def test_singular_commit_message(tmp_path):
    wd = _mk(tmp_path)
    first = _g(wd, "rev-parse", "HEAD")
    _commit(wd, "x", "one")
    ip = _ip(tmp_path, git_sha=first)
    r = vdw.detect(wd, installed_path=ip)
    assert r["commits_ahead"] == 1
    assert "1 commit beyond" in r["warning_message"]
    assert "1 commits" not in r["warning_message"]
