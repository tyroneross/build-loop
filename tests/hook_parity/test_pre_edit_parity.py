# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Parity cases for the Rally pre-edit hook.

``test_shell_contract`` checks each case's expected outcome on the shell hook
alone (the oracle). ``test_bl_matches_shell`` runs ``bl hook pre-edit`` on the
same case and requires identical results. ``test_launcher_matches_shell``
does the same through the shell launcher when ``bin/native/bl`` exists.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.hook_parity import harness
from tests.hook_parity.harness import Case

STAMP_DIR = "repo/.build-loop/rally-hook-throttle"


def _edit(path: str, session: str | None = None) -> bytes:
    event: dict = {"tool_name": "Edit", "tool_input": {"file_path": path}}
    if session is not None:
        event["session_id"] = session
    return json.dumps(event).encode()


def _bash(command: str, session: str | None = None) -> bytes:
    event: dict = {"tool_name": "Bash", "tool_input": {"command": command}}
    if session is not None:
        event["session_id"] = session
    return json.dumps(event).encode()


def _joins(snap):
    return [s for s in snap.spawns if len(s) > 2 and s[2] == "pre-edit-join-bg"]


def _refreshes(snap):
    return [s for s in snap.spawns if "--refresh" in s]


def _stamps(snap, prefix=STAMP_DIR):
    return {k: v for k, v in snap.files.items() if k.startswith(prefix)}


def _silent(snap):
    assert snap.rc == 0
    assert snap.stdout == b""
    assert snap.stderr == b""


def expect_join(name: str, *, refresh: int = 0, label: str = "repo"):
    def check(snap, world):
        _silent(snap)
        stamp = f"{label}/.build-loop/rally-hook-throttle/{name}.stamp"
        assert snap.files.get(stamp, "").split(":")[0] in ("new", "touched"), snap.files
        assert snap.files[stamp].endswith(":0")
        joins = _joins(snap)
        assert len(joins) == 1, snap.spawns
        argv = joins[0]
        assert argv[0] == "<python-stub>"
        assert argv[1].endswith("/scripts/rally_point/hooks.py")
        assert argv[3:] == (
            "--workdir", f"<root>/{label}", "--tool", argv[6], "--session-id", name,
            "--model", "unknown",
        )
        assert len(_refreshes(snap)) == refresh, snap.spawns
    return check


def expect_nothing(*, refresh: int = 0):
    def check(snap, world):
        _silent(snap)
        assert not [k for k, v in snap.files.items() if v.startswith("new") or v.startswith("touched")
                    if "rally-hook-throttle" in k], snap.files
        assert not _joins(snap), snap.spawns
        assert len(_refreshes(snap)) == refresh, snap.spawns
    return check


def expect_refresh_only(n: int = 1):
    def check(snap, world):
        _silent(snap)
        assert not _stamps(snap), snap.files
        assert not _joins(snap)
        assert len(_refreshes(snap)) == n, snap.spawns
        for argv in _refreshes(snap):
            assert argv[1].endswith("/scripts/rally_point/discovery_bridge.py")
            assert argv[2] == "--workdir" and argv[4] == "--refresh"
            assert argv[5].endswith(".refresh")
        assert any(k.endswith(".refresh") and v.startswith(("new", "touched")) for k, v in snap.files.items()) or n == 0
    return check


def expect_deadline(snap, world):
    assert snap.rc == 0
    assert snap.stdout == b"{}"
    assert snap.stderr == b""


def expect_prune(snap, world):
    _silent(snap)
    names = sorted(k.rsplit("/", 1)[1] for k in _stamps(snap))
    # >24h stamps deleted; the oldest non-fresh entries trimmed to 128 total.
    assert "ancient-0.stamp" not in names
    assert "sess-parity.stamp" in names
    assert len(names) == 128, len(names)
    assert "fresh-0.stamp" in names


def expect_unscoped(snap, world):
    _silent(snap)
    assert not _stamps(snap)
    assert not snap.spawns


FRESH = 5.0
HOUR = 3600.0

big_padding = " " * 70000
CASES = [
    Case("native_first_edit", _edit("{root}/repo/file.py"), cache={"repo": FRESH},
         expect=expect_join("sess-parity")),
    Case("fresh_stamp_short_circuits", _edit("{root}/repo/file.py"), cache={"repo": FRESH},
         stamps={"repo/sess-parity.stamp": 10}, expect=expect_nothing()),
    Case("stale_stamp_rejoins", _edit("{root}/repo/file.py"), cache={"repo": FRESH},
         stamps={"repo/sess-parity.stamp": 120}, expect=expect_join("sess-parity")),
    Case("missing_cache_no_stamp", _edit("{root}/repo/file.py"),
         expect=expect_refresh_only(1)),
    Case("stale_cache_used_and_refreshed", _edit("{root}/repo/file.py"), cache={"repo": HOUR},
         expect=expect_join("sess-parity", refresh=1)),
    Case("expired_cache_is_missing", _edit("{root}/repo/file.py"), cache={"repo": 25 * HOUR},
         expect=expect_refresh_only(1)),
    Case("young_lock_blocks_refresh", _edit("{root}/repo/file.py"), locks={"l:repo": 5},
         expect=expect_refresh_only(0)),
    Case("old_lock_replaced", _edit("{root}/repo/file.py"), locks={"l:repo": 60},
         expect=expect_refresh_only(1)),
    Case("future_lock_blocks", _edit("{root}/repo/file.py"), locks={"l:repo": -600},
         expect=expect_refresh_only(0)),
    Case("nogit_workdir_bash_no_cd", _bash("ls"), workdir="nogit"),
    Case("nogit_workdir_with_cache", _bash("ls"), workdir="nogit", cache={"nogit": FRESH},
         expect=expect_nothing()),
    Case("bash_cd_into_repo_from_nogit", _bash("cd {root}/repo && ls"), workdir="nogit",
         cache={"repo": FRESH, "nogit": FRESH}, expect=expect_join("sess-parity")),
    Case("bash_cd_semicolon_greedy", _bash("cd {root}/repo;ls"), workdir="nogit",
         cache={"repo": FRESH, "nogit": FRESH}),
    Case("bash_cd_relative", _bash("cd ../repo && ls"), workdir="nogit",
         cache={"repo": FRESH, "nogit": FRESH}),
    Case("bash_cd_tilde", _bash("cd ~ && ls"), cache={"repo": FRESH}),
    Case("bash_cd_quoted", _bash("cd '{root}/other' && ls"), cache={"repo": FRESH, "other": FRESH}),
    Case("bash_no_cd_uses_workdir", _bash("git status"), cache={"repo": FRESH},
         expect=expect_join("sess-parity")),
    Case("edit_other_repo", _edit("{root}/other/x.py"), cache={"repo": FRESH, "other": FRESH},
         expect=expect_join("sess-parity", label="other")),
    Case("edit_other_repo_missing_cache", _edit("{root}/other/x.py"), cache={"repo": FRESH},
         expect=expect_refresh_only(1)),
    Case("edit_other_repo_fresh_stamp", _edit("{root}/other/x.py"), cache={"repo": FRESH},
         stamps={"other/sess-parity.stamp": 3}, expect=expect_nothing()),
    Case("edit_relative_path", _edit("sub/new.py"), cache={"repo": FRESH},
         expect=expect_join("sess-parity")),
    Case("edit_dotdot_path", _edit("{root}/repo/missing/../sub/x.py"), cache={"repo": FRESH},
         expect=expect_join("sess-parity")),
    Case("edit_outside_any_repo", _edit("{root}/nogit/x.py"), workdir="nogit",
         cache={"nogit": FRESH}, expect=expect_nothing()),
    Case("unscoped_slug", _edit("{root}/repo/file.py"), cache={"repo": FRESH},
         cache_slug={"repo": "_unscoped"}, expect=expect_unscoped),
    Case("empty_slug_is_missing", _edit("{root}/repo/file.py"), cache={"repo": FRESH},
         cache_slug={"repo": ""}, expect=expect_refresh_only(1)),
    Case("bad_json", b"{not json", cache={"repo": FRESH}, expect=expect_join("sess-parity")),
    Case("empty_stdin", b"", cache={"repo": FRESH}, expect=expect_join("sess-parity")),
    Case("non_object_json", b"[1,2,3]", cache={"repo": FRESH}, expect=expect_join("sess-parity")),
    Case("invalid_utf8", b'{"session_id": "a\xff"}', cache={"repo": FRESH}),
    Case("nan_json", b'{"tool_input": {"file_path": "{root}/other/x"}, "n": NaN}',
         cache={"repo": FRESH, "other": FRESH}),
    Case("over_64k_cap",
         json.dumps({"tool_input": {"file_path": "{root}/other/x.py"}, "pad": big_padding}).encode(),
         cache={"repo": FRESH, "other": FRESH}, expect=expect_join("sess-parity")),
    Case("exactly_64k_valid",
         (json.dumps({"tool_input": {"file_path": "{root}/other/x.py"}}) + " " * 70000).encode()[:65536],
         cache={"repo": FRESH, "other": FRESH}),
    Case("event_session_id_wins", _edit("{root}/repo/a.py", session="Event-Session_1"),
         cache={"repo": FRESH}),
    Case("build_loop_session_env", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"BUILD_LOOP_RALLY_SESSION_ID": "bl-sess"}, expect=expect_join("bl-sess")),
    Case("codex_thread_id", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"CLAUDE_SESSION_ID": None, "CODEX_THREAD_ID": "thread-9", "CODEX_SESSION_ID": "s-1"},
         expect=expect_join("thread-9")),
    Case("codex_session_id", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"CLAUDE_SESSION_ID": None, "CODEX_SESSION_ID": "s-1"}, expect=expect_join("s-1")),
    Case("cursor_family", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"RALLY_TOOL_ID": "cursor", "CURSOR_SESSION_ID": "cur-1"}, expect=expect_join("cur-1")),
    Case("rally_agent_id", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"CLAUDE_SESSION_ID": None, "RALLY_AGENT_ID": "agent-7", "RALLY_SESSION_ID": "rs"},
         expect=expect_join("agent-7")),
    Case("term_session_id", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"CLAUDE_SESSION_ID": None, "TERM_SESSION_ID": "w0t0p0:ABC"}),
    Case("observer_pid", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"CLAUDE_SESSION_ID": None, "RALLY_OBSERVER_PID": "4242"},
         expect=expect_join("claude_code-proc-4242")),
    Case("parent_pid_fallback", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"CLAUDE_SESSION_ID": None}),
    Case("unsafe_session_hashed", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"CLAUDE_SESSION_ID": "  My Session/ID!  "}),
    Case("long_session_hashed", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"CLAUDE_SESSION_ID": "x" * 100}),
    Case("tool_with_actor", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"RALLY_TOOL_ID": "claude_code:named-actor"}),
    Case("throttle_zero", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         stamps={"repo/sess-parity.stamp": 2},
         env={"BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS": "0"}, expect=expect_join("sess-parity")),
    Case("throttle_negative", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         stamps={"repo/sess-parity.stamp": 30},
         env={"BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS": "-5"}, expect=expect_nothing()),
    Case("throttle_invalid", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         stamps={"repo/sess-parity.stamp": 30},
         env={"BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS": "abc"}, expect=expect_nothing()),
    Case("throttle_ten", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         stamps={"repo/sess-parity.stamp": 30},
         env={"BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS": "10"}, expect=expect_join("sess-parity")),
    Case("prune_stamps", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         stamps={
             **{f"repo/ancient-{i}.stamp": 25 * HOUR for i in range(3)},
             **{f"repo/old-{i:03d}.stamp": 200 + i for i in range(130)},
             **{f"repo/fresh-{i}.stamp": 1 for i in range(2)},
         },
         expect=expect_prune),
    Case("deadline_slow_rally", _edit("{root}/repo/a.py"), cache={"repo": FRESH}, rally="slow", supervised=True,
         env={"BUILD_LOOP_RALLY_PRE_EDIT_DEADLINE_MS": "300"}, expect=expect_deadline),
    Case("deadline_invalid_budget", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"BUILD_LOOP_RALLY_PRE_EDIT_DEADLINE_MS": "50"}, expect=expect_join("sess-parity")),
    Case("missing_workdir", _bash("ls"), workdir="missing"),
    Case("no_rally_binary_local_fallback", _edit("{root}/repo/a.py"), rally="none"),
    Case("debug_env", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"BUILD_LOOP_RALLY_DEBUG": "1"}),
    Case("internal_only", _edit("{root}/repo/a.py"), cache={"repo": FRESH},
         env={"BUILD_LOOP_BRIDGE_INTERNAL_ONLY": "1", "BUILD_LOOP_APPS_ROOT": "{root}/home/apps"}),
    Case("apply_patch_payload",
         json.dumps({"tool_name": "apply_patch", "tool_input": {
             "command": "*** Begin Patch\n*** Update File: {root}/other/p.py\n@@\n*** End Patch"}}).encode(),
         cache={"repo": FRESH, "other": FRESH}, expect=expect_join("sess-parity", label="other")),
    Case("multi_edit_payload",
         json.dumps({"tool_input": {"edits": [{"path": ""}, {"file_path": "{root}/other/q.py"}]}}).encode(),
         cache={"repo": FRESH, "other": FRESH}, expect=expect_join("sess-parity", label="other")),
    Case("filename_payload", json.dumps({"tool_input": {"filename": "{root}/other/f.py"}}).encode(),
         cache={"repo": FRESH, "other": FRESH}, expect=expect_join("sess-parity", label="other")),
    Case("newline_path_filtered",
         json.dumps({"tool_input": {"file_path": "{root}/other/a\nb", "path": "{root}/other/c"}}).encode(),
         cache={"repo": FRESH, "other": FRESH}, expect=expect_join("sess-parity", label="other")),
]


def _materialize(case: Case, root: Path) -> Case:
    return Case(**{**case.__dict__, "stdin": case.stdin.replace(b"{root}", str(root).encode())})


@pytest.fixture
def root(tmp_path):
    return tmp_path.resolve() / "w"


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_shell_contract(case, root):
    case = _materialize(case, root)
    snap, world = harness.run("shell", case, root)
    assert snap.rc == 0
    assert snap.stdout in (b"", b"{}") or case.name.startswith(("no_rally", "internal", "debug"))
    if case.expect:
        case.expect(snap, world)


def _compare(impl: str, case: Case, root: Path):
    case = _materialize(case, root)
    shell, _ = harness.run("shell", case, root)
    other, _ = harness.run(impl, case, root)
    assert other.comparable() == shell.comparable()
    if case.expect:
        case.expect(other, None)


@pytest.mark.skipif(not harness.BL.exists(), reason="bl not built (cargo build --release)")
@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_bl_matches_shell(case, root):
    _compare("bl", case, root)


@pytest.mark.skipif(
    not (harness.REPO_ROOT / "bin" / "native" / "bl").exists(),
    reason="bin/native/bl not installed",
)
@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_launcher_matches_shell(case, root):
    _compare("launcher", case, root)
