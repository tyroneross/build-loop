#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Portability regression tests: no maintainer-machine paths in resolvers.

build-loop is installed by other people. Three modules used to hardcode the
maintainer's laptop layout, so on any other machine they either wrote to a
phantom tree or went dead:

  * ``append_milestone.py``      — literal personal memory-root default, and it
                                   ``mkdir(parents=True)``s that tree at Review-G
                                   on every run.
  * ``transcript_pattern_miner`` — sessions dir pinned to the maintainer's
                                   project slug (their ``$HOME`` with ``/`` -> ``-``).
  * ``prior_art.py``             — same literal memory-root fallback.

Every test here runs under a synthetic ``$HOME`` so a machine-specific default
is observable as a wrong path, not masked by the maintainer's real disk.

Run with: uv run pytest scripts/test_portable_paths.py -q
"""
from __future__ import annotations

import io
import json
import re
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent

_MEMORY_ROOT_ENV = (
    "BUILD_LOOP_MEMORY_STORE_ROOT",
    "BUILD_LOOP_MEMORY_ROOT",
    "AGENT_MEMORY_ROOT",
)

# Split so this test file does not itself trip a repo-wide grep for the literal.
_MAINTAINER_HOME_SLUG = "-Users-" + "tyroneross"
_MAINTAINER_MEMORY_PATH = re.compile(r"dev/git-folder/build-loop-memory")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_home(tmp_path, monkeypatch) -> Path:
    """Point ``$HOME`` at an empty tmp dir with no memory-root env override."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in _MEMORY_ROOT_ENV:
        monkeypatch.delenv(var, raising=False)
    return home


def _make_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    (repo / "README.md").write_text("hi")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True)
    return repo


def _run_milestone(argv: list[str]) -> dict:
    import append_milestone as am  # noqa: PLC0415 — needs pytest's sys.path tweak

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = am.main(argv)
    out = buf.getvalue().strip()
    assert out, f"no output from main(); rc={rc}"
    return json.loads(out)


# ---------------------------------------------------------------------------
# append_milestone.py
# ---------------------------------------------------------------------------

class TestAppendMilestoneRoot:
    def test_defaults_to_neutral_root_under_synthetic_home(self, tmp_path, synthetic_home):
        """No --memory-root => `_paths.memory_store_root()` neutral default."""
        repo = _make_git_repo(tmp_path)

        result = _run_milestone([
            "--workdir", str(repo),
            "--summary", "portability check",
            "--project", "proj",
        ])

        assert result["appended"] is True, result
        expected = synthetic_home / ".build-loop-memory" / "projects" / "proj" / "milestones.jsonl"
        assert Path(result["path"]) == expected, result["path"]
        assert expected.exists()

    def test_creates_no_phantom_dev_git_folder_tree(self, tmp_path, synthetic_home):
        """The Review-G mkdir must never materialize ~/dev/git-folder on a user's disk."""
        repo = _make_git_repo(tmp_path)

        _run_milestone([
            "--workdir", str(repo),
            "--summary", "portability check",
            "--project", "proj",
        ])

        assert not (synthetic_home / "dev").exists(), (
            "append_milestone created a phantom ~/dev tree under the user's home"
        )

    def test_env_override_still_wins(self, tmp_path, synthetic_home, monkeypatch):
        repo = _make_git_repo(tmp_path)
        override = tmp_path / "env-memory"
        monkeypatch.setenv("BUILD_LOOP_MEMORY_STORE_ROOT", str(override))

        result = _run_milestone([
            "--workdir", str(repo),
            "--summary", "env override",
            "--project", "proj",
        ])

        assert result["appended"] is True, result
        assert Path(result["path"]) == override / "projects" / "proj" / "milestones.jsonl"

    def test_explicit_memory_root_flag_still_wins(self, tmp_path, synthetic_home):
        """The explicit parameter override is preserved, and beats the resolver."""
        repo = _make_git_repo(tmp_path)
        explicit = tmp_path / "explicit-memory"

        result = _run_milestone([
            "--workdir", str(repo),
            "--summary", "explicit root",
            "--project", "proj",
            "--memory-root", str(explicit),
        ])

        assert result["appended"] is True, result
        assert Path(result["path"]) == explicit / "projects" / "proj" / "milestones.jsonl"
        assert not (synthetic_home / ".build-loop-memory").exists()

    def test_unwritable_root_still_fails_soft(self, tmp_path, synthetic_home):
        """Fail-soft OSError behavior survives the resolver swap."""
        repo = _make_git_repo(tmp_path)
        blocked = tmp_path / "blocked"
        blocked.write_text("not a directory")  # mkdir(parents=True) -> OSError

        result = _run_milestone([
            "--workdir", str(repo),
            "--summary", "fail soft",
            "--project", "proj",
            "--memory-root", str(blocked),
        ])

        assert result["appended"] is False
        assert "reason" in result


# ---------------------------------------------------------------------------
# transcript_pattern_miner
# ---------------------------------------------------------------------------

class TestMinerSessionsDir:
    def test_sessions_dir_derived_from_live_home(self, synthetic_home):
        from transcript_pattern_miner.__main__ import default_sessions_dir  # noqa: PLC0415

        resolved = default_sessions_dir()
        slug = str(synthetic_home).replace("/", "-")

        assert resolved == synthetic_home / ".claude" / "projects" / slug, resolved
        assert _MAINTAINER_HOME_SLUG not in str(resolved)

    def test_slug_encoding_matches_claude_code_layout(self):
        """`/` -> `-` only; `.` and `_` are preserved (verified on-disk layout)."""
        from transcript_pattern_miner.__main__ import project_slug  # noqa: PLC0415

        assert project_slug(Path("/Users/alice")) == "-Users-alice"
        assert project_slug(Path("/home/bob_dev/.config")) == "-home-bob_dev-.config"

    def test_miner_runs_against_home_derived_dir_without_override(
        self, tmp_path, synthetic_home
    ):
        """End-to-end: no --sessions-dir, and the miner finds the user's own sessions."""
        from transcript_pattern_miner.__main__ import main  # noqa: PLC0415

        slug = str(synthetic_home).replace("/", "-")
        sessions = synthetic_home / ".claude" / "projects" / slug
        sessions.mkdir(parents=True)
        record = {
            "type": "user",
            "uuid": "u1",
            "parentUuid": None,
            "timestamp": "2026-01-15T10:00:00.000Z",
            "cwd": str(tmp_path / "someproject"),
            "sessionId": "s1",
            "message": {"role": "user", "content": "hello"},
        }
        (sessions / "s1.jsonl").write_text(json.dumps(record) + "\n")

        out_dir = tmp_path / "out"
        rc = main(["--all", "--force", "--out-dir", str(out_dir)])

        assert rc == 0, "miner could not resolve a sessions dir from the live $HOME"
        assert list(out_dir.glob("*.md")), "no report written"


# ---------------------------------------------------------------------------
# prior_art.py
# ---------------------------------------------------------------------------

class TestPriorArtRoot:
    def test_memory_root_defaults_to_neutral_root(self, synthetic_home):
        import prior_art  # noqa: PLC0415

        root = prior_art._memory_root()

        assert root == synthetic_home / ".build-loop-memory", root
        assert "dev/git-folder" not in str(root)

    def test_env_override_still_honoured(self, tmp_path, synthetic_home, monkeypatch):
        import prior_art  # noqa: PLC0415

        override = tmp_path / "env-memory"
        monkeypatch.setenv("AGENT_MEMORY_ROOT", str(override))

        assert prior_art._memory_root() == override

    def test_explicit_override_still_wins(self, tmp_path, synthetic_home):
        import prior_art  # noqa: PLC0415

        explicit = tmp_path / "explicit"
        assert prior_art._memory_root(explicit) == explicit


# ---------------------------------------------------------------------------
# Source-level guard — keeps the literals from creeping back in
# ---------------------------------------------------------------------------

_GUARDED_SOURCES = (
    "append_milestone.py",
    "prior_art.py",
    "transcript_pattern_miner/__main__.py",
)

@pytest.mark.parametrize("rel", _GUARDED_SOURCES)
def test_no_maintainer_machine_paths_in_source(rel):
    text = (_HERE / rel).read_text(encoding="utf-8")

    assert _MAINTAINER_HOME_SLUG not in text, f"{rel} hardcodes the maintainer's home slug"
    assert not _MAINTAINER_MEMORY_PATH.search(text), (
        f"{rel} hardcodes the maintainer's memory-root path; "
        "route through _paths.memory_store_root() instead"
    )
