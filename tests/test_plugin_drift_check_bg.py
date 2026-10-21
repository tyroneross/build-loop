# SPDX-License-Identifier: Apache-2.0
"""Plugin drift check: anonymous ls-remote, orphan-marker cleanup, no semver false drift."""
from __future__ import annotations
import importlib.util, json, os, shlex, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import credential_policy  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("drift_bg", ROOT / "hooks" / "_plugin_drift_check_bg.py")
assert _SPEC and _SPEC.loader
drift = importlib.util.module_from_spec(_SPEC); _SPEC.loader.exec_module(drift)

MAIN = "a" * 40


def _setup(tmp: Path, installed: dict[str, str]) -> Path:
    mkt = tmp / "mkt"; (mkt / ".claude-plugin").mkdir(parents=True)
    (mkt / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"plugins": [
        {"name": n, "source": {"source": "github", "repo": f"o/{n}"}} for n in installed]}))
    (tmp / "known_marketplaces.json").write_text(json.dumps({"m": {"installLocation": str(mkt)}}))
    plugins = {}
    for n, ver in installed.items():
        ip = tmp / "cache" / n; ip.mkdir(parents=True)
        plugins[f"{n}@m"] = [{"installPath": str(ip), "version": ver}]
    (tmp / "installed_plugins.json").write_text(json.dumps({"plugins": plugins}))
    return tmp / ".drift-markers"


def test_ls_remote_bypasses_all_credential_helpers(monkeypatch):
    seen = {}
    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"])
        return subprocess.CompletedProcess(cmd, 0, f"{MAIN}\trefs/heads/main\n", "")
    monkeypatch.setattr(drift.subprocess, "run", fake_run)
    assert drift._sha("https://github.com/o/p") == MAIN
    assert seen["cmd"][:5] == ["git", "-c", "credential.helper=",
                               "-c", "credential.https://github.com.helper="]
    assert seen["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
    assert seen["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    monkeypatch.undo()  # the policy reads real git config
    assert credential_policy.command_reason(shlex.join(seen["cmd"]), platform="darwin") is None


def test_orphan_and_semver_markers_cleared_real_drift_kept(tmp_path, monkeypatch):
    md = _setup(tmp_path, {"semver": "0.31.0", "behind": "b" * 12})
    md.mkdir()
    for stale in ("gone__m", "semver__m"):
        (md / f"{stale}.json").write_text("{}")
    monkeypatch.setattr(drift, "_sha", lambda url, t=8: MAIN)
    assert drift.main(["--workdir", str(tmp_path), "--force"]) == 0
    assert sorted(p.name for p in md.glob("*.json")) == ["behind__m.json"]
