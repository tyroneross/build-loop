# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import io
import json
import sys

import pytest

from post_push_target import main, push_target


@pytest.mark.parametrize("command", [
    "git push origin main", "command git push", "/usr/bin/git push",
    "git push && git push backup", "git push; git push backup",
    "git add a.py && git commit -m fix && git push", "git status && git push",
])
def test_local_target(command, tmp_path):
    assert push_target(command, tmp_path) == tmp_path


def test_quoted_relative_and_chained_directories(tmp_path):
    other = tmp_path / "other repo"
    sub = other / "sub"
    sub.mkdir(parents=True)
    assert push_target('git -C "other repo" push', tmp_path) == other
    assert push_target('git -C "other repo" -C sub push', tmp_path) == sub
    assert push_target('cd "other repo" && git push', tmp_path) == other
    assert push_target(f'git -C "{other}" push', tmp_path) == other


@pytest.mark.parametrize("command", [
    'echo "git push"', 'git -C "$OTHER" push', 'git --git-dir=/other push',
    'git -c alias.push="!evil" push', 'git push || git -C other push',
    'git push; git -C other push', 'git push &', 'git push | cat',
    'cd missing && git push', 'cd /tmp; git push', 'bash -c "git push"',
    'GIT_WORK_TREE=/other git push', 'git push --dry-run', 'git push -n', 'git push -nv', 'git push -vn',
    'git -C other* push', 'cd {a,b} && git push',
    'git push > out', 'git -C "unterminated push', 'git -C ~/other push',
    'cat <<EOF\ngit push\nEOF', 'git unknown-alias && git push',
    'git -C a push origin b#c && cd ../b && git push',
])
def test_ambiguous_or_non_effectful_commands_do_not_guess(command, tmp_path):
    assert push_target(command, tmp_path) is None


def test_cd_preserves_shell_logical_symlink_parent(tmp_path):
    root = tmp_path / "root"
    other = tmp_path / "other"
    root.mkdir()
    (other / "deep").mkdir(parents=True)
    (root / "link").symlink_to(other / "deep", target_is_directory=True)
    assert push_target("cd link && cd .. && git push", root) == root
    assert push_target("git -C link -C .. push", root) == other


@pytest.mark.parametrize("response", [{"exit_code": 1}, {"is_error": True},
                                      {"returncode": 128}, "Process exited with code 1"])
def test_codex_failed_command_does_not_trigger_closeout(response, tmp_path, monkeypatch, capsys):
    event = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "turn_id": "turn",
             "cwd": str(tmp_path), "tool_input": {"command": "git commit -m fix && git push"},
             "tool_response": response}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    main()
    assert capsys.readouterr().out == ""
