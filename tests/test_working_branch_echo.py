"""Tests for scripts/working_branch_echo.py."""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import working_branch_echo as wbe  # noqa: E402


def _g(wd: Path, *args: str):
    return subprocess.run(["git", "-C", str(wd), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _mkrepo(tmp_path: Path, name="repo") -> Path:
    wd = tmp_path / name
    wd.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(wd)], check=True, capture_output=True)
    _g(wd, "config", "user.email", "t@e")
    _g(wd, "config", "user.name", "t")
    (wd / "README").write_text("hi")
    _g(wd, "add", ".")
    _g(wd, "commit", "-qm", "init")
    return wd


def test_no_git(tmp_path):
    nongit = tmp_path / "no-git"
    nongit.mkdir()
    r = wbe.detect(nongit)
    assert r["skip_reason"] == "no_git"
    assert r["branch"] is None
    assert r["message"] is None


def test_clean_branch_self_recursive(tmp_path):
    wd = _mkrepo(tmp_path)
    r = wbe.detect(wd, self_recursive=True)
    assert r["branch"] == "main"
    assert r["head_sha"] and len(r["head_sha"]) == 7
    assert r["dirty_files"] == 0
    assert "main" in r["message"]
    assert "0 dirty files" in r["message"]
    assert r["skip_reason"] is None


def test_dirty_working_copy(tmp_path):
    wd = _mkrepo(tmp_path)
    (wd / "new.txt").write_text("x")
    (wd / "another.txt").write_text("y")
    r = wbe.detect(wd, self_recursive=True)
    assert r["dirty_files"] == 2
    assert "2 dirty files" in r["message"]


def test_singular_dirty_grammar(tmp_path):
    wd = _mkrepo(tmp_path)
    (wd / "single.txt").write_text("x")
    r = wbe.detect(wd, self_recursive=True)
    assert r["dirty_files"] == 1
    assert "1 dirty file" in r["message"]
    assert "1 dirty files" not in r["message"]


def test_detached_head(tmp_path):
    wd = _mkrepo(tmp_path)
    sha = _g(wd, "rev-parse", "HEAD")
    _g(wd, "checkout", "-q", sha)
    r = wbe.detect(wd, self_recursive=True)
    assert r["branch"] is None
    assert r["head_sha"] is not None
    assert "detached HEAD" in r["message"]


def test_not_self_recursive(tmp_path):
    wd = _mkrepo(tmp_path)
    r = wbe.detect(wd, self_recursive=False)
    assert r["skip_reason"] == "not_self_recursive"
    assert r["message"] is None
    # Branch info still populated for diagnostics.
    assert r["branch"] == "main"
    assert r["head_sha"] is not None


def test_cli_not_self_recursive_flag(tmp_path, capsys):
    wd = _mkrepo(tmp_path)
    rc = wbe.main(["--workdir", str(wd), "--json", "--not-self-recursive"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["skip_reason"] == "not_self_recursive"
    assert payload["message"] is None
    assert rc == 0


def test_cli_human_readable(tmp_path, capsys):
    wd = _mkrepo(tmp_path)
    rc = wbe.main(["--workdir", str(wd)])
    out = capsys.readouterr().out
    assert "branch: main" in out
    assert "Self-recursive runtime" in out
    assert rc == 0


def test_subprocess_invocation_emits_valid_json_schema(tmp_path):
    wd = _mkrepo(tmp_path)
    out = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "working_branch_echo.py"),
                          "--workdir", str(wd), "--json"],
                         capture_output=True, text=True, check=True)
    payload = json.loads(out.stdout)
    expected = {"branch", "head_sha", "dirty_files", "message", "skip_reason"}
    assert set(payload) == expected


def test_message_format_matches_spec(tmp_path):
    """Spec sample: '🔁 Self-recursive runtime — working copy on `<branch>` @ <sha>, 0 dirty files'."""
    wd = _mkrepo(tmp_path)
    r = wbe.detect(wd, self_recursive=True)
    assert r["message"].startswith("🔁 Self-recursive runtime — working copy on `main` @ ")
    assert r["message"].endswith(", 0 dirty files")


def test_no_git_via_cli(tmp_path, capsys):
    nongit = tmp_path / "no"
    nongit.mkdir()
    rc = wbe.main(["--workdir", str(nongit), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["skip_reason"] == "no_git"
    assert rc == 0
