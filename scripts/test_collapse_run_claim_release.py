#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Exact-session Rally claim cleanup tests for collapse_run.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import collapse_run  # noqa: E402


SESSION = "session-a"
NATIVE_TOOL = f"claude_code:{SESSION}"
PROTOCOL_SESSION = f"sess:managed:{SESSION}#live"


class _FakeNativeRally:
    """Typed backend stand-in; no real Rally or Git process is invoked."""

    def __init__(
        self,
        claims: list[dict],
        *,
        native: bool = True,
        room_status: str = "ok",
        release_status: str = "ok",
        protocol_session_id: str = PROTOCOL_SESSION,
        release_protocol_session_id: str | None = None,
        native_tool: str = NATIVE_TOOL,
        raw_session_id: str = SESSION,
    ) -> None:
        self.claims = list(claims)
        self.context = SimpleNamespace(native=native)
        self.room_status = room_status
        self.release_status = release_status
        self.protocol_session_id = protocol_session_id
        self.release_protocol_session_id = (
            release_protocol_session_id or protocol_session_id
        )
        self.native_tool = native_tool
        self.raw_session_id = raw_session_id
        self.room_actors: list[str] = []
        self.whoami_calls: list[list[str]] = []
        self.release_calls: list[list[str]] = []

    def resolve_context(self, _workdir: Path):
        return self.context

    def room_snapshot(self, context, *, actor: str):
        assert context is self.context
        self.room_actors.append(actor)
        return collapse_run.rally_backend.NativeResult(self.room_status)

    def room_summary(self, _result):
        return {"active_claims": list(self.claims)}

    def invoke_native(self, context, argv, **kwargs):
        assert context is self.context
        assert kwargs["tool"] == self.native_tool
        assert kwargs["session_id"] == self.raw_session_id
        if argv[0] == "whoami":
            assert kwargs["expected_schema"] == "agent-rally.command.whoami.v1"
            assert kwargs.get("mutating", False) is False
            self.whoami_calls.append(list(argv))
            return collapse_run.rally_backend.NativeResult(
                "ok",
                payload={
                    "data": {
                        "whoami": {
                            "whoami": {
                                "session_identity": {
                                    "session_id": self.protocol_session_id,
                                }
                            }
                        }
                    }
                },
            )
        assert kwargs["expected_schema"] == "agent-rally.command.say.v1"
        assert kwargs["mutating"] is True
        self.release_calls.append(list(argv))
        if self.release_status != "ok":
            return collapse_run.rally_backend.NativeResult(self.release_status)
        event_id = argv[argv.index("--ref") + 1]
        self.claims = [claim for claim in self.claims if claim.get("event_id") != event_id]
        return collapse_run.rally_backend.NativeResult(
            "ok",
            payload={
                "data": {
                    "say": {
                        "fact": {
                            "seq": 1,
                            "kind": "release",
                            "tool": self.native_tool,
                            "from_session_id": self.release_protocol_session_id,
                            "ref": event_id,
                        }
                    }
                }
            },
            revision=1,
        )


def _claim(
    event_id: str,
    path: str,
    *,
    tool: str = NATIVE_TOOL,
    session_id: str = PROTOCOL_SESSION,
) -> dict:
    return {
        "kind": "claim",
        "tool": tool,
        "from_session_id": session_id,
        "scope": [f"file:{path}"],
        "event_id": event_id,
    }


def _install_fake(monkeypatch, fake: _FakeNativeRally) -> None:
    for name in (
        "RALLY_POINT_TOOL",
        "APP_PULSE_TOOL",
        "BUILD_LOOP_RALLY_SESSION_ID",
        "CLAUDE_SESSION_ID",
        "RALLY_AGENT_ID",
        "RALLY_SESSION_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(collapse_run.rally_backend, "resolve_context", fake.resolve_context)
    monkeypatch.setattr(collapse_run.rally_backend, "room_snapshot", fake.room_snapshot)
    monkeypatch.setattr(collapse_run.rally_backend, "native_room_summary", fake.room_summary)
    monkeypatch.setattr(collapse_run.rally_backend, "invoke_native", fake.invoke_native)
    monkeypatch.setattr(
        collapse_run,
        "_git",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )


def _make_worktree_dir(workdir: Path) -> tuple[Path, str]:
    wt_dir = workdir / ".claude" / "worktrees" / "agent-abc123"
    wt_dir.mkdir(parents=True)
    relpath = wt_dir.resolve().relative_to(workdir.resolve()).as_posix()
    return wt_dir, relpath


def _released_refs(fake: _FakeNativeRally) -> list[str]:
    return [call[call.index("--ref") + 1] for call in fake.release_calls]


def test_successful_removal_releases_only_exact_session_path_claim(
    tmp_path,
    monkeypatch,
):
    wt_dir, relpath = _make_worktree_dir(tmp_path)
    fake = _FakeNativeRally(
        [
            _claim(
                "fact_match",
                f"{relpath}/scripts/foo.py",
            ),
            _claim("fact_other_path", "scripts/unrelated.py"),
            _claim(
                "fact_sibling_actor",
                f"{relpath}/scripts/sibling.py",
                tool="claude_code:session-b",
                session_id="sess:managed:session-b#live",
            ),
            _claim(
                "fact_old_generation",
                f"{relpath}/scripts/old.py",
                session_id=f"sess:managed:{SESSION}#old",
            ),
        ]
    )
    _install_fake(monkeypatch, fake)

    assert collapse_run._remove_worktree(
        tmp_path,
        str(wt_dir),
        session_id=SESSION,
        tool="claude_code",
    ) is None

    assert fake.whoami_calls == [["whoami", "--json", "--tool", NATIVE_TOOL]]
    assert fake.room_actors == [NATIVE_TOOL]
    assert _released_refs(fake) == ["fact_match"]
    assert {claim["event_id"] for claim in fake.claims} >= {
        "fact_old_generation",
        "fact_sibling_actor",
    }
    call = fake.release_calls[0]
    assert call[call.index("--tool") + 1] == NATIVE_TOOL
    assert "--reap-stale" not in call


def test_sibling_worktree_name_prefix_is_not_released(tmp_path, monkeypatch):
    wt_dir, relpath = _make_worktree_dir(tmp_path)
    fake = _FakeNativeRally(
        [
            _claim("fact_match", f"{relpath}/scripts/foo.py"),
            _claim("fact_sibling", f"{relpath}-extra/scripts/foo.py"),
        ]
    )
    _install_fake(monkeypatch, fake)

    assert collapse_run._remove_worktree(
        tmp_path,
        str(wt_dir),
        session_id=SESSION,
        tool="claude_code",
    ) is None
    assert _released_refs(fake) == ["fact_match"]


def test_room_failure_does_not_break_teardown(tmp_path, monkeypatch):
    wt_dir, _relpath = _make_worktree_dir(tmp_path)
    fake = _FakeNativeRally([], room_status="failed")
    _install_fake(monkeypatch, fake)

    assert collapse_run._remove_worktree(
        tmp_path,
        str(wt_dir),
        session_id=SESSION,
        tool="claude_code",
    ) is None
    assert fake.release_calls == []


def test_local_fallback_does_not_attempt_native_cleanup(tmp_path, monkeypatch):
    wt_dir, _relpath = _make_worktree_dir(tmp_path)
    fake = _FakeNativeRally([], native=False)
    _install_fake(monkeypatch, fake)

    assert collapse_run._remove_worktree(
        tmp_path,
        str(wt_dir),
        session_id=SESSION,
        tool="claude_code",
    ) is None
    assert fake.room_actors == []
    assert fake.release_calls == []


def test_unknown_release_outcome_stops_without_retry_or_next_claim(
    tmp_path,
    monkeypatch,
):
    wt_dir, relpath = _make_worktree_dir(tmp_path)
    fake = _FakeNativeRally(
        [
            _claim("fact_one", f"{relpath}/one.py"),
            _claim("fact_two", f"{relpath}/two.py"),
        ],
        release_status="outcome_unknown",
    )
    _install_fake(monkeypatch, fake)

    assert collapse_run._remove_worktree(
        tmp_path,
        str(wt_dir),
        session_id=SESSION,
        tool="claude_code",
    ) is None
    assert len(fake.release_calls) == 1
    assert _released_refs(fake) == ["fact_one"]


def test_sibling_generation_release_receipt_is_not_accepted(
    tmp_path,
    monkeypatch,
):
    wt_dir, relpath = _make_worktree_dir(tmp_path)
    fake = _FakeNativeRally(
        [_claim("fact_live", f"{relpath}/live.py")],
        release_protocol_session_id=f"sess:managed:{SESSION}#old",
    )
    _install_fake(monkeypatch, fake)

    released = collapse_run._release_worktree_claims(
        tmp_path,
        str(wt_dir),
        session_id=SESSION,
        tool="claude_code",
    )

    assert released == 0
    assert _released_refs(fake) == ["fact_live"]


def test_already_gone_worktree_still_reconciles_exact_claims(tmp_path, monkeypatch):
    wt_dir = tmp_path / ".claude" / "worktrees" / "agent-already-gone"
    relpath = wt_dir.resolve().relative_to(tmp_path.resolve()).as_posix()
    fake = _FakeNativeRally([_claim("fact_orphan", f"{relpath}/old.py")])
    _install_fake(monkeypatch, fake)
    git_calls: list[tuple] = []
    monkeypatch.setattr(
        collapse_run,
        "_git",
        lambda *args, **kwargs: git_calls.append(args),
    )

    assert collapse_run._remove_worktree(
        tmp_path,
        str(wt_dir),
        session_id=SESSION,
        tool="claude_code",
    ) is None
    assert _released_refs(fake) == ["fact_orphan"]
    assert git_calls == []


def test_missing_explicit_or_selected_session_never_uses_environment(
    tmp_path,
    monkeypatch,
):
    wt_dir, relpath = _make_worktree_dir(tmp_path)
    fake = _FakeNativeRally([_claim("fact_must_survive", f"{relpath}/old.py")])
    _install_fake(monkeypatch, fake)
    monkeypatch.setenv("BUILD_LOOP_RALLY_SESSION_ID", "wrong-environment-session")

    assert collapse_run._remove_worktree(tmp_path, str(wt_dir)) is None
    assert fake.whoami_calls == []
    assert fake.room_actors == []
    assert fake.release_calls == []


def test_selected_execution_session_wins_without_session_environment(monkeypatch):
    for name in (
        "BUILD_LOOP_RALLY_SESSION_ID",
        "CLAUDE_SESSION_ID",
        "RALLY_AGENT_ID",
        "RALLY_SESSION_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    execution = {"current_session_id": SESSION, "started_by_tool": "cursor"}

    assert collapse_run._claim_release_session_id(execution, None) == (
        SESSION,
        "execution.current_session_id",
    )
    assert collapse_run._claim_release_session_id(execution, "explicit-owner") == (
        "explicit-owner",
        "cli",
    )
    assert collapse_run._claim_release_tool(execution, None) == (
        "cursor",
        "execution.started_by_tool",
    )
    assert collapse_run._claim_release_tool(execution, "codex:caller") == (
        "codex",
        "cli",
    )


def test_cross_host_cleanup_uses_selected_owner_tool_not_integrator_env(
    tmp_path,
    monkeypatch,
):
    cursor_session = "cursor-owner"
    cursor_tool = f"cursor:{cursor_session}"
    cursor_protocol = f"sess:managed:{cursor_session}#live"
    wt_dir, relpath = _make_worktree_dir(tmp_path)
    fake = _FakeNativeRally(
        [
            _claim(
                "fact_cursor",
                f"{relpath}/cursor.py",
                tool=cursor_tool,
                session_id=cursor_protocol,
            )
        ],
        protocol_session_id=cursor_protocol,
        native_tool=cursor_tool,
        raw_session_id=cursor_session,
    )
    _install_fake(monkeypatch, fake)
    monkeypatch.setenv("RALLY_POINT_TOOL", "codex")
    monkeypatch.setenv("BUILD_LOOP_RALLY_SESSION_ID", "codex-integrator")

    released = collapse_run._release_worktree_claims(
        tmp_path,
        str(wt_dir),
        session_id=cursor_session,
        tool="cursor",
    )

    assert released == 1
    assert fake.room_actors == [cursor_tool]
