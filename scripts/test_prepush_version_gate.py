#!/usr/bin/env python3
"""Regression tests for the protected-branch plugin version gate."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import prepush_version_gate as gate

ZERO = "0" * 40


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _commit_version(repo: Path, version: str, message: str) -> str:
    manifest = repo / gate.MANIFEST
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"name": "build-loop", "version": version}) + "\n")
    _git(repo, "add", gate.MANIFEST)
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, published: str = "1.2.3", current: str = "1.2.3"):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.com")
    remote_sha = _commit_version(tmp_path, published, "published")
    if current != published:
        _commit_version(tmp_path, current, "local bump")
    local_sha = _git(tmp_path, "rev-parse", "HEAD")
    line = f"refs/heads/main {local_sha} refs/heads/main {remote_sha}\n"
    return tmp_path, line


def test_blocks_unchanged_or_behind_version(tmp_path: Path):
    repo, line = _repo(tmp_path)
    verdict = gate.evaluate(repo, [line], {})
    assert verdict["action"] == "block"
    assert verdict["current"] == verdict["published"] == "1.2.3"


def test_allows_version_advanced_past_remote(tmp_path: Path):
    repo, line = _repo(tmp_path, current="1.2.4")
    assert gate.evaluate(repo, [line], {})["action"] == "allow"


def test_skips_new_branch_tag_delete_and_dedicated_bypass(tmp_path: Path):
    repo, line = _repo(tmp_path)
    local_sha = line.split()[1]
    cases = [
        f"refs/heads/topic {local_sha} refs/heads/topic {ZERO}\n",
        f"refs/tags/v1.2.3 {local_sha} refs/tags/v1.2.3 {ZERO}\n",
        f"(delete) {ZERO} refs/heads/main {line.split()[3]}\n",
    ]
    assert gate.evaluate(repo, cases, {})["action"] == "allow"
    assert gate.evaluate(repo, [line], {"BUILD_LOOP_SKIP_VERSION_GATE": "1"})["action"] == "allow"


def test_unreadable_manifest_fails_open(tmp_path: Path):
    repo, line = _repo(tmp_path)
    (repo / gate.MANIFEST).write_text("{bad json\n")
    assert gate.evaluate(repo, [line], {})["action"] == "allow"


def test_block_message_keeps_tagging_separate_from_bump():
    message = gate.format_block_message({
        "current": "1.2.3", "published": "1.2.3", "remote_ref": "refs/heads/main"
    })
    assert "bump_version.py --patch" in message
    assert "bump_version.py --tag" in message
    assert "--patch --tag" not in message


# ---------------------------------------------------------------------------
# release-please mode. Added 2026-09-02 with the release automation. Before it,
# this gate demanded a manual version bump on every push — correct under the old
# "a push is one release" model, and wrong the moment release-please took
# ownership of the number: it would force a hand-edit of a field an automation
# owns and desync the very manifest release-please reads to pick the next
# version. The rule flipped; the stage stayed.
# ---------------------------------------------------------------------------

def _release_please_repo(tmp_path: Path, manifest_v: str, package_v: str, plugin_v: str):
    repo = tmp_path / "rp"
    (repo / ".claude-plugin").mkdir(parents=True)
    (repo / gate.RP_CONFIG).write_text(json.dumps({"packages": {".": {}}}))
    (repo / gate.RP_MANIFEST).write_text(json.dumps({".": manifest_v}))
    (repo / gate.PACKAGE_JSON).write_text(json.dumps({"version": package_v}))
    (repo / gate.MANIFEST).write_text(json.dumps({"version": plugin_v}))
    return repo


def test_release_please_mode_allows_a_push_with_no_bump(tmp_path: Path):
    """The whole point. Under release-please a push is a commit, not a release."""
    repo = _release_please_repo(tmp_path, "0.42.5", "0.42.5", "0.42.5")
    verdict = gate.evaluate(repo, ["refs/heads/main a refs/heads/main b\n"], {})
    assert verdict["action"] == "allow"
    assert "release-please owns the version" in verdict["reason"]


def test_release_please_mode_blocks_a_desynced_manifest(tmp_path: Path):
    """A manifest behind package.json makes release-please propose a version npm
    already has, and the publish is rejected with nothing shipped."""
    repo = _release_please_repo(tmp_path, "0.41.0", "0.42.5", "0.42.5")
    verdict = gate.evaluate(repo, ["refs/heads/main a refs/heads/main b\n"], {})
    assert verdict["action"] == "block"
    assert verdict["mode"] == "release-please"


def test_release_please_block_message_names_every_field_and_its_value(tmp_path: Path):
    """A gate that blocks without saying which of three files is wrong costs more
    time than it saves."""
    repo = _release_please_repo(tmp_path, "0.41.0", "0.42.5", "0.42.5")
    message = gate.format_block_message(
        gate.evaluate(repo, ["refs/heads/main a refs/heads/main b\n"], {})
    )
    for token in (gate.RP_MANIFEST, gate.PACKAGE_JSON, gate.MANIFEST, "0.41.0", "0.42.5"):
        assert token in message
    assert "--patch" not in message, "the manual-bump instruction does not apply here"


def test_legacy_mode_survives_for_repos_without_release_please(tmp_path: Path):
    """This file ships to other repos. Removing release-please's config must
    restore the one-push-one-bump rule exactly."""
    repo, line = _repo(tmp_path)
    assert not gate.release_please_owns_versioning(repo)
    assert gate.evaluate(repo, [line], {})["action"] == "block"


def test_the_bypass_still_wins_in_release_please_mode(tmp_path: Path):
    repo = _release_please_repo(tmp_path, "0.41.0", "0.42.5", "0.42.5")
    verdict = gate.evaluate(
        repo, ["refs/heads/main a refs/heads/main b\n"],
        {"BUILD_LOOP_SKIP_VERSION_GATE": "1"},
    )
    assert verdict["action"] == "allow"
