# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for prepush_dist_gate.py — proves the dist auto-rebuild gate green.

Rigs a failing case (stale dist) and shows the gate rebuilds, auto-commits, and
BLOCKS; then a passing case (fresh dist) allows. The tsc build is stubbed (the gate
just needs a build step that mutates dist/) so the test is hermetic.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import prepush_dist_gate as g  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "proj"
    (r / "src").mkdir(parents=True)
    (r / "dist" / "src").mkdir(parents=True)
    (r / "tsconfig.json").write_text("{}")
    (r / "src" / "a.ts").write_text("export const a = 1;\n")
    (r / "dist" / "src" / "a.js").write_text("export const a = 1;\n")
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


def test_fresh_dist_allows(repo):
    # dist built after src → fresh.
    v = g.evaluate(repo)
    assert v["action"] == "allow"
    assert "fresh" in v["reason"]


def test_skip_env_allows(repo):
    v = g.evaluate(repo, env={"BUILDLOOP_DIST_GATE_SKIP": "1"})
    assert v["action"] == "allow" and "skipped" in v["reason"]


def test_no_ts_project_allows(tmp_path):
    r = tmp_path / "empty"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(r), check=True, capture_output=True)
    v = g.evaluate(r)
    assert v["action"] == "allow" and "no TS project" in v["reason"]


def test_stale_dist_rebuilds_commits_and_blocks(repo, monkeypatch):
    # RIG THE FAILURE: edit src so it is newer than dist → stale.
    time.sleep(0.01)
    (repo / "src" / "a.ts").write_text("export const a = 2;\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "edit src only (dist now stale)")
    assert g._needs_build(repo) is True

    # STUB the build: emulate tsc regenerating dist from the new src.
    def fake_tsc_cmd(_repo):
        return ["true"]  # a no-op command; the "build" is the side effect below

    real_run = g._run

    def fake_run(cmd, cwd, timeout=180):
        if cmd == ["true"]:
            (Path(cwd) / "dist" / "src" / "a.js").write_text("export const a = 2;\n")
            return 0, "built"
        return real_run(cmd, cwd, timeout)

    monkeypatch.setattr(g, "_tsc_cmd", fake_tsc_cmd)
    monkeypatch.setattr(g, "_run", fake_run)

    v = g.evaluate(repo)
    assert v["action"] == "block", v
    assert v["exit_code"] == 1
    # The rebuilt dist was auto-committed (working tree clean, HEAD is the build commit).
    status = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo),
                            capture_output=True, text=True).stdout
    assert status.strip() == "", f"expected clean tree, got: {status!r}"
    subject = subprocess.run(["git", "log", "-1", "--pretty=%s"], cwd=str(repo),
                             capture_output=True, text=True).stdout.strip()
    assert subject == "build: rebuild dist [pre-push auto]"
    # Message renders the commit for the re-push instruction.
    msg = g.format_block_message(v)
    assert "re-run your push" in msg


def test_stale_but_tsc_unavailable_fails_open(repo, monkeypatch):
    time.sleep(0.01)
    (repo / "src" / "a.ts").write_text("export const a = 3;\n")
    monkeypatch.setattr(g, "_tsc_cmd", lambda _r: None)
    v = g.evaluate(repo)
    assert v["action"] == "allow" and "fail-open" in v["reason"]
