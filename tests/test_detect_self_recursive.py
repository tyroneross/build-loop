"""Tests for scripts/detect_self_recursive.py.

Uses real tmpdirs, real symlinks, real ``git init`` — no mocking magic.
Each test builds a synthetic plugin layout under tmp_path so we exercise
the full detect() path end-to-end.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import detect_self_recursive as dsr  # noqa: E402


def _make_workdir(tmp_path: Path, *, with_manifest=True, with_git=True,
                  plugin_name="myplugin") -> Path:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    if with_manifest:
        (workdir / ".claude-plugin").mkdir()
        (workdir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": plugin_name, "version": "0.1.0"}))
    if with_git:
        subprocess.run(["git", "init", "-q", "-b", "main", str(workdir)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(workdir), "config", "user.email", "t@e"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(workdir), "config", "user.name", "t"],
                       check=True, capture_output=True)
        (workdir / "README").write_text("hi")
        subprocess.run(["git", "-C", str(workdir), "add", "."],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(workdir), "commit", "-qm", "init"],
                       check=True, capture_output=True)
    return workdir


def _make_plugins_root(tmp_path: Path) -> Path:
    root = tmp_path / "plugins_root"
    root.mkdir()
    return root


def _link_direct(plugins_root: Path, name: str, target: Path) -> Path:
    link = plugins_root / name
    link.symlink_to(target)
    return link


def _link_cache(plugins_root: Path, marketplace: str, name: str,
                version: str, target: Path) -> Path:
    base = plugins_root / "cache" / marketplace / name
    base.mkdir(parents=True)
    link = base / version
    link.symlink_to(target)
    return link


# ---- True cases ---------------------------------------------------------

def test_true_with_direct_symlink(tmp_path):
    workdir = _make_workdir(tmp_path)
    root = _make_plugins_root(tmp_path)
    _link_direct(root, "myplugin", workdir)
    r = dsr.detect(workdir, plugins_root=root, env={})
    assert r["self_recursive"] is True
    assert r["plugin_name"] == "myplugin"
    assert r["runtime_symlink_path"] == str(root / "myplugin")
    assert r["working_copy_branch"] == "main"
    assert r["working_copy_sha"] and len(r["working_copy_sha"]) == 40
    assert r["reason_if_false"] is None
    assert r["detection_method"] == "cache_symlink"


def test_true_with_cache_subdir_symlink(tmp_path):
    """Production case: ``cache/<marketplace>/<name>/<version>`` symlink."""
    workdir = _make_workdir(tmp_path)
    root = _make_plugins_root(tmp_path)
    link = _link_cache(root, "some-mkt", "myplugin", "0.10.0", workdir)
    r = dsr.detect(workdir, plugins_root=root, env={})
    assert r["self_recursive"] is True
    assert r["runtime_symlink_path"] == str(link)
    assert r["detection_method"] == "cache_symlink"


# ---- Runtime-root precedence (arg > env > symlink walk) -----------------

def test_runtime_root_arg_matches_workdir_returns_true(tmp_path):
    """The reliable signal under `claude --plugin-dir <path>`: no symlink, arg matches."""
    workdir = _make_workdir(tmp_path)
    root = _make_plugins_root(tmp_path)  # intentionally empty
    r = dsr.detect(workdir, plugins_root=root, runtime_root=workdir, env={})
    assert r["self_recursive"] is True
    assert r["detection_method"] == "runtime_root_arg"
    assert r["runtime_symlink_path"] == str(workdir)
    assert r["reason_if_false"] is None


def test_runtime_root_arg_mismatch_returns_false_no_link(tmp_path):
    """Explicit arg that doesn't match → cache is loaded copy, not this checkout."""
    workdir = _make_workdir(tmp_path)
    other = tmp_path / "loaded-elsewhere"
    other.mkdir()
    root = _make_plugins_root(tmp_path)
    r = dsr.detect(workdir, plugins_root=root, runtime_root=other, env={})
    assert r["self_recursive"] is False
    assert r["reason_if_false"] == "no_runtime_link"
    assert r["detection_method"] == "none"


def test_runtime_root_arg_beats_env_var(tmp_path):
    """Explicit arg wins even when env var would say otherwise."""
    workdir = _make_workdir(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    root = _make_plugins_root(tmp_path)
    r = dsr.detect(workdir, plugins_root=root, runtime_root=workdir,
                   env={"CLAUDE_PLUGIN_ROOT": str(other)})
    assert r["self_recursive"] is True
    assert r["detection_method"] == "runtime_root_arg"


def test_env_var_matches_workdir_returns_true(tmp_path):
    """No arg, but CLAUDE_PLUGIN_ROOT env points at workdir."""
    workdir = _make_workdir(tmp_path)
    root = _make_plugins_root(tmp_path)
    r = dsr.detect(workdir, plugins_root=root,
                   env={"CLAUDE_PLUGIN_ROOT": str(workdir)})
    assert r["self_recursive"] is True
    assert r["detection_method"] == "plugin_root_env"
    assert r["runtime_symlink_path"] == str(workdir)


def test_env_var_mismatch_returns_false(tmp_path):
    """Env points at a real-but-different plugin cache → not self-recursive."""
    workdir = _make_workdir(tmp_path)
    other = tmp_path / "other-plugin"
    other.mkdir()
    root = _make_plugins_root(tmp_path)
    r = dsr.detect(workdir, plugins_root=root,
                   env={"CLAUDE_PLUGIN_ROOT": str(other)})
    assert r["self_recursive"] is False
    assert r["reason_if_false"] == "no_runtime_link"


def test_runtime_root_normalizes_symlink(tmp_path):
    """realpath/symlink normalization: arg via a symlinked path still matches."""
    workdir = _make_workdir(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(workdir)
    root = _make_plugins_root(tmp_path)
    r = dsr.detect(workdir, plugins_root=root, runtime_root=alias, env={})
    assert r["self_recursive"] is True
    assert r["detection_method"] == "runtime_root_arg"


def test_runtime_root_arg_present_but_no_git_returns_not_a_git_repo(tmp_path):
    """Arg matched, but .git is missing → not_a_git_repo (preserves taxonomy)."""
    workdir = _make_workdir(tmp_path, with_git=False)
    root = _make_plugins_root(tmp_path)
    r = dsr.detect(workdir, plugins_root=root, runtime_root=workdir, env={})
    assert r["self_recursive"] is False
    assert r["reason_if_false"] == "not_a_git_repo"


def test_no_signal_falls_through_to_existing_symlink_behavior(tmp_path):
    """No arg, no env, no symlink → exactly the prior no_runtime_link result."""
    workdir = _make_workdir(tmp_path)
    root = _make_plugins_root(tmp_path)
    r = dsr.detect(workdir, plugins_root=root, env={})
    assert r["self_recursive"] is False
    assert r["reason_if_false"] == "no_runtime_link"
    assert r["detection_method"] == "none"


def test_empty_string_runtime_root_treated_as_not_provided(tmp_path):
    """Shell expansion of an unset CLAUDE_PLUGIN_ROOT produces an empty string;
    that must NOT be treated as 'matches CWD' (Path('').resolve() == CWD).
    Falls through to env then symlink walk."""
    workdir = _make_workdir(tmp_path)
    root = _make_plugins_root(tmp_path)
    _link_direct(root, "myplugin", workdir)
    r = dsr.detect(workdir, plugins_root=root, runtime_root=Path(""), env={})
    # Falls through to symlink walk and finds the link.
    assert r["self_recursive"] is True
    assert r["detection_method"] == "cache_symlink"


def test_empty_string_env_var_treated_as_unset(tmp_path):
    """Empty CLAUDE_PLUGIN_ROOT env var must not short-circuit detection."""
    workdir = _make_workdir(tmp_path)
    root = _make_plugins_root(tmp_path)
    _link_direct(root, "myplugin", workdir)
    r = dsr.detect(workdir, plugins_root=root, env={"CLAUDE_PLUGIN_ROOT": ""})
    assert r["self_recursive"] is True
    assert r["detection_method"] == "cache_symlink"


# ---- False cases --------------------------------------------------------

def test_false_no_manifest(tmp_path):
    workdir = _make_workdir(tmp_path, with_manifest=False)
    root = _make_plugins_root(tmp_path)
    r = dsr.detect(workdir, plugins_root=root, env={})
    assert r["self_recursive"] is False
    assert r["reason_if_false"] == "not_a_plugin"
    assert r["plugin_name"] is None


def test_false_manifest_missing_name_field(tmp_path):
    workdir = tmp_path / "wd"
    (workdir / ".claude-plugin").mkdir(parents=True)
    (workdir / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "1"}))
    r = dsr.detect(workdir, plugins_root=_make_plugins_root(tmp_path), env={})
    assert r["self_recursive"] is False
    assert r["reason_if_false"] == "not_a_plugin"


def test_false_no_runtime_link(tmp_path):
    workdir = _make_workdir(tmp_path)
    root = _make_plugins_root(tmp_path)  # no symlinks created
    r = dsr.detect(workdir, plugins_root=root, env={})
    assert r["self_recursive"] is False
    assert r["reason_if_false"] == "no_runtime_link"
    assert r["plugin_name"] == "myplugin"


def test_false_no_git(tmp_path):
    workdir = _make_workdir(tmp_path, with_git=False)
    root = _make_plugins_root(tmp_path)
    _link_direct(root, "myplugin", workdir)
    r = dsr.detect(workdir, plugins_root=root, env={})
    assert r["self_recursive"] is False
    assert r["reason_if_false"] == "not_a_git_repo"
    assert r["runtime_symlink_path"] is not None


# ---- Git edge cases -----------------------------------------------------

def test_detached_head_returns_null_branch(tmp_path):
    workdir = _make_workdir(tmp_path)
    sha = subprocess.run(["git", "-C", str(workdir), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "-C", str(workdir), "checkout", "-q", sha],
                   check=True, capture_output=True)
    root = _make_plugins_root(tmp_path)
    _link_direct(root, "myplugin", workdir)
    r = dsr.detect(workdir, plugins_root=root, env={})
    assert r["self_recursive"] is True
    assert r["working_copy_branch"] is None
    assert r["working_copy_sha"] == sha


def test_shallow_clone_returns_true(tmp_path):
    upstream = _make_workdir(tmp_path)
    # Add a second commit so depth=1 has something to clone.
    (upstream / "more").write_text("x")
    subprocess.run(["git", "-C", str(upstream), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(upstream), "commit", "-qm", "two"],
                   check=True, capture_output=True)
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1",
                    f"file://{upstream}", str(shallow)], check=True, capture_output=True)
    # Manifest was already cloned from upstream; verify and proceed.
    assert (shallow / ".claude-plugin" / "plugin.json").exists()
    root = _make_plugins_root(tmp_path)
    _link_direct(root, "myplugin", shallow)
    r = dsr.detect(shallow, plugins_root=root, env={})
    assert r["self_recursive"] is True
    assert (shallow / ".git" / "shallow").exists()  # confirm it's actually shallow


# ---- Failure / degradation ----------------------------------------------

def test_workdir_resolve_oserror_returns_symlink_check_failed(tmp_path):
    """Outer try/except catches OSError from workdir.resolve()."""
    workdir = _make_workdir(tmp_path)
    root = _make_plugins_root(tmp_path)
    _link_direct(root, "myplugin", workdir)
    real_resolve = Path.resolve

    def boom(self, *a, **kw):
        if self == workdir:
            raise OSError("simulated workdir failure")
        return real_resolve(self, *a, **kw)

    with patch.object(Path, "resolve", boom):
        r = dsr.detect(workdir, plugins_root=root, env={})
    assert r["self_recursive"] is False
    assert r["reason_if_false"] == "symlink_check_failed"


# ---- I/O surface --------------------------------------------------------

def test_human_readable_output(tmp_path, capsys):
    workdir = _make_workdir(tmp_path, with_manifest=False)
    rc = dsr.main(["--workdir", str(workdir)])
    out = capsys.readouterr().out
    assert "self_recursive: no" in out
    assert "reason_if_false: not_a_plugin" in out
    assert rc == 0


def test_subprocess_invocation_emits_valid_json_schema(tmp_path):
    """End-to-end: script invoked as subprocess emits the documented schema."""
    workdir = _make_workdir(tmp_path)
    # Strip CLAUDE_PLUGIN_ROOT so the subprocess test is independent of the
    # caller's environment (would otherwise short-circuit the symlink walk).
    sub_env = {k: v for k, v in __import__("os").environ.items()
               if k != "CLAUDE_PLUGIN_ROOT"}
    out = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "detect_self_recursive.py"),
                          "--workdir", str(workdir), "--json"],
                         capture_output=True, text=True, check=True, env=sub_env)
    payload = json.loads(out.stdout)
    expected = {"self_recursive", "plugin_name", "runtime_symlink_path",
                "working_copy_branch", "working_copy_sha", "reason_if_false",
                "detection_method"}
    assert set(payload) == expected


def test_subprocess_runtime_root_arg_returns_true(tmp_path):
    """End-to-end: --runtime-root flag is accepted and produces detection_method=runtime_root_arg."""
    workdir = _make_workdir(tmp_path)
    sub_env = {k: v for k, v in __import__("os").environ.items()
               if k != "CLAUDE_PLUGIN_ROOT"}
    out = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "detect_self_recursive.py"),
                          "--workdir", str(workdir),
                          "--runtime-root", str(workdir),
                          "--json"],
                         capture_output=True, text=True, check=True, env=sub_env)
    payload = json.loads(out.stdout)
    assert payload["self_recursive"] is True
    assert payload["detection_method"] == "runtime_root_arg"


def test_subprocess_env_var_returns_true(tmp_path):
    """End-to-end: CLAUDE_PLUGIN_ROOT env propagates to subprocess detection."""
    workdir = _make_workdir(tmp_path)
    sub_env = dict(__import__("os").environ)
    sub_env["CLAUDE_PLUGIN_ROOT"] = str(workdir)
    out = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "detect_self_recursive.py"),
                          "--workdir", str(workdir), "--json"],
                         capture_output=True, text=True, check=True, env=sub_env)
    payload = json.loads(out.stdout)
    assert payload["self_recursive"] is True
    assert payload["detection_method"] == "plugin_root_env"


def test_backward_compatible_no_runtime_args(tmp_path):
    """Existing callers passing only --workdir still work; legacy symlink path preserved."""
    workdir = _make_workdir(tmp_path)
    root = _make_plugins_root(tmp_path)
    _link_direct(root, "myplugin", workdir)
    # Skip the subprocess CLI here — exercise via detect() with explicit empty env
    # to simulate "old caller, no env" without depending on the test runner's env.
    r = dsr.detect(workdir, plugins_root=root, env={})
    assert r["self_recursive"] is True
    assert r["detection_method"] == "cache_symlink"
