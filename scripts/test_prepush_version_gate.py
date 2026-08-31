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
