#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Exact-session safety tests for native Rally stop and deregistration."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import agent_rally  # noqa: E402
from rally_point import agent_autoreg  # noqa: E402
from rally_point.backend_adapter import BackendContext, NativeResult  # noqa: E402
from rally_point.discovery_bridge import DiscoveryEnvelope  # noqa: E402


def _context(workdir: Path) -> BackendContext:
    return BackendContext(
        workdir=workdir,
        envelope=DiscoveryEnvelope(
            channel_dir=str(workdir / ".rally"),
            app_slug="stop-contract",
            repo_id="stop-contract",
            channel_layout="repo-local-rally",
            policy="canonical",
            protocol_version="1.0",
            last_resolved_at="2026-08-14T00:00:00Z",
            resolved_via="repo-local-rally-cli",
        ),
        local_channel_dir=workdir / ".build-loop" / "local-rally",
    )


def _local_context(workdir: Path) -> BackendContext:
    channel = workdir / ".build-loop" / "local-rally"
    return BackendContext(
        workdir=workdir,
        envelope=DiscoveryEnvelope(
            channel_dir=str(channel),
            app_slug="stop-contract",
            repo_id=None,
            channel_layout="legacy",
            policy="legacy-only",
            protocol_version="1.0",
            last_resolved_at="2026-08-14T00:00:00Z",
            resolved_via="build-loop-internal",
        ),
        local_channel_dir=channel,
    )


def _fact(
    *,
    seq: int,
    tool: str,
    session_id: str,
    subject: str,
) -> dict:
    return {
        "seq": seq,
        "kind": "presence",
        "tool": tool,
        "from_session_id": session_id,
        "subject": subject,
    }


def _recent_result(facts: list[dict], *, limit: int = 500) -> NativeResult:
    return NativeResult(
        "ok",
        payload={
            "data": {
                "recent": {
                    "limit": limit,
                    "rows": [{"fact": fact} for fact in reversed(facts)],
                    "warnings": [],
                }
            }
        },
    )


def _whoami_result(session_id: str) -> NativeResult:
    return NativeResult(
        "ok",
        payload={
            "data": {
                "whoami": {
                    "whoami": {
                        "session_identity": {"session_id": session_id},
                    }
                }
            }
        },
    )


def _committed_session_result(
    *,
    schema: str,
    container: str,
    fact: dict,
) -> NativeResult:
    return NativeResult(
        "invalid",
        payload={
            "ok": True,
            "product": "rally",
            "schema": schema,
            "data": {container: {"fact": fact}},
        },
        returncode=0,
        reason="Rally mutation success omitted a positive fact sequence",
    )


@pytest.fixture(autouse=True)
def _passthrough_protocol_session_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resolve_identity(*_args, **kwargs):
        return _whoami_result(str(kwargs["session_id"]))

    monkeypatch.setattr(agent_rally, "invoke_native", resolve_identity)
    monkeypatch.setattr(agent_autoreg, "invoke_native", resolve_identity)


def _args(tmp_path: Path, *, tool: str = "cursor", session_id: str = "session-a"):
    return SimpleNamespace(
        workdir=str(tmp_path),
        tool=tool,
        session_id=session_id,
        reason="agent stopped",
        keep_claims=False,
    )


def _native_actor(session_id: str) -> str:
    return f"cursor:{session_id}"


def test_native_stop_matches_rally_canonical_protocol_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    canonical = "sess:managed:cursor-session-a#live"
    identity = Mock(return_value=_whoami_result(canonical))
    monkeypatch.setattr(agent_rally, "invoke_native", identity)
    monkeypatch.setattr(
        agent_rally,
        "native_recent",
        lambda *_a, **_kw: _recent_result(
            [
                _fact(
                    seq=1,
                    tool="cursor",
                    session_id=canonical,
                    subject="agent presence: cursor",
                )
            ]
        ),
    )

    state, _reason, result, protocol_session_id = (
        agent_rally._native_session_presence_state(
            context,
            tool="cursor",
            session_id="cursor-session-a",
        )
    )

    assert state == "active"
    assert result.ok
    assert protocol_session_id == canonical
    assert identity.call_args.kwargs["session_id"] == "cursor-session-a"


def test_native_claim_release_filters_canonical_id_and_authorizes_with_raw_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    canonical_a = "sess:managed:cursor-session-a#live"
    canonical_b = "sess:managed:cursor-session-b#live"
    monkeypatch.setattr(
        agent_rally,
        "room_snapshot",
        lambda *_a, **_kw: NativeResult(
            "ok",
            payload={
                "data": {
                    "room": {
                        "room": {
                            "active_claims": [
                                {
                                    "event_id": "claim-a",
                                    "kind": "claim",
                                    "tool": "cursor",
                                    "from_session_id": canonical_a,
                                },
                                {
                                    "event_id": "claim-b",
                                    "kind": "claim",
                                    "tool": "cursor",
                                    "from_session_id": canonical_b,
                                },
                            ]
                        }
                    }
                }
            },
        ),
    )
    release = Mock(
        return_value=_committed_session_result(
            schema="agent-rally.command.say.v1",
            container="say",
            fact={
                "event_id": "release-a",
                "seq": 10,
                "kind": "release",
                "tool": "cursor",
                "from_session_id": canonical_a,
                "subject": "agent stopped",
                "ref": "claim-a",
            },
        )
    )
    monkeypatch.setattr(agent_rally, "invoke_native", release)

    released, error = agent_rally._release_native_session_claims(
        context,
        tool="cursor",
        session_id="cursor-session-a",
        protocol_session_id=canonical_a,
        reason="agent stopped",
    )

    assert error is None
    assert released == ["claim-a"]
    assert release.call_count == 1
    assert release.call_args.kwargs["session_id"] == "cursor-session-a"
    assert release.call_args.args[1][-1] == "claim-a"


def test_native_stop_accepts_exact_canonical_done_receipt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    raw_session_id = "cursor-session-a"
    canonical = "sess:managed:cursor-session-a#live"
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(
        agent_rally,
        "invoke_native",
        lambda *_a, **_kw: _whoami_result(canonical),
    )
    monkeypatch.setattr(
        agent_rally,
        "native_recent",
        lambda *_a, **_kw: _recent_result(
            [
                _fact(
                    seq=1,
                    tool=_native_actor("cursor-session-a"),
                    session_id=canonical,
                    subject="agent presence: cursor",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        agent_rally,
        "_release_native_session_claims",
        lambda *_a, **_kw: ([], None),
    )
    status = Mock(
        return_value=_committed_session_result(
            schema="agent-rally.command.status_post.v1",
            container="status_post",
            fact={
                "event_id": "done-a",
                "seq": 2,
                "kind": "presence",
                "tool": _native_actor("cursor-session-a"),
                "from_session_id": canonical,
                "subject": "state=done | committed_sha=abc | worktree_branch=feature",
            },
        )
    )
    monkeypatch.setattr(agent_rally, "native_status_post", status)
    monkeypatch.setattr(agent_rally, "_git_value", lambda *_a, **_kw: "git-value")

    assert agent_rally.cmd_stop(
        _args(tmp_path, session_id=raw_session_id)
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is True
    assert payload["action"] == "presence-stopped"
    assert "exact canonical session fact" in payload["reason"]


def test_native_stop_rejects_other_session_done_receipt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    canonical_a = "sess:managed:cursor-session-a#live"
    canonical_b = "sess:managed:cursor-session-b#live"
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(
        agent_rally,
        "invoke_native",
        lambda *_a, **_kw: _whoami_result(canonical_a),
    )
    monkeypatch.setattr(
        agent_rally,
        "native_recent",
        lambda *_a, **_kw: _recent_result(
            [
                _fact(
                    seq=1,
                    tool=_native_actor("cursor-session-a"),
                    session_id=canonical_a,
                    subject="agent presence: cursor",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        agent_rally,
        "_release_native_session_claims",
        lambda *_a, **_kw: ([], None),
    )
    monkeypatch.setattr(
        agent_rally,
        "native_status_post",
        lambda *_a, **_kw: _committed_session_result(
            schema="agent-rally.command.status_post.v1",
            container="status_post",
            fact={
                "event_id": "done-b",
                "seq": 2,
                "kind": "presence",
                "tool": _native_actor("cursor-session-a"),
                "from_session_id": canonical_b,
                "subject": "state=done",
            },
        ),
    )
    monkeypatch.setattr(agent_rally, "_git_value", lambda *_a, **_kw: "git-value")

    assert agent_rally.cmd_stop(
        _args(tmp_path, session_id="cursor-session-a")
    ) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is False
    assert payload["action"] == "presence-stop-failed"
    assert payload["reason"] == "Rally mutation success omitted a positive fact sequence"


def test_native_stop_latest_exact_done_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    release = Mock(side_effect=AssertionError("already-done stop released claims"))
    status = Mock(side_effect=AssertionError("already-done stop emitted duplicate done"))
    lead = Mock(side_effect=AssertionError("session stop changed tool-level lead"))
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(
        agent_rally,
        "native_recent",
        lambda *_a, **_kw: _recent_result(
            [
                _fact(
                    seq=1,
                    tool=_native_actor("session-a"),
                    session_id="session-a",
                    subject="state=working | file=src/a.py | intent=edit",
                ),
                _fact(
                    seq=4,
                    tool=_native_actor("session-a"),
                    session_id="session-a",
                    subject="state=done | committed_sha=abc | worktree_branch=feature",
                ),
            ]
        ),
    )
    monkeypatch.setattr(agent_rally, "_release_native_session_claims", release)
    monkeypatch.setattr(agent_rally, "native_status_post", status)
    monkeypatch.setattr(agent_rally, "lead_command", lead)

    assert agent_rally.cmd_stop(_args(tmp_path)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is True
    assert payload["action"] == "presence-stop-already-done"
    assert payload["idempotent"] is True
    release.assert_not_called()
    status.assert_not_called()
    lead.assert_not_called()


def test_native_stop_releases_only_exact_session_and_preserves_tool_lead(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    release = Mock(return_value=(["claim-session-a"], None))
    status = Mock(return_value=NativeResult("ok", backend="rally"))
    lead = Mock(side_effect=AssertionError("session stop changed tool-level lead"))
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(
        agent_rally,
        "native_recent",
        lambda *_a, **_kw: _recent_result(
            [
                _fact(
                    seq=7,
                    tool=_native_actor("session-a"),
                    session_id="session-a",
                    subject="state=working | file=src/a.py | intent=edit",
                ),
                _fact(
                    seq=8,
                    tool=_native_actor("session-b"),
                    session_id="session-b",
                    subject="state=working | file=src/b.py | intent=edit",
                ),
            ]
        ),
    )
    monkeypatch.setattr(agent_rally, "_release_native_session_claims", release)
    monkeypatch.setattr(agent_rally, "native_status_post", status)
    monkeypatch.setattr(agent_rally, "lead_command", lead)
    monkeypatch.setattr(agent_rally, "_git_value", lambda *_a, **_kw: "git-value")

    assert agent_rally.cmd_stop(_args(tmp_path)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is True
    assert payload["claims_released"] == ["claim-session-a"]
    assert payload["lead_relinquished"] is False
    release.assert_called_once_with(
        context,
        tool=_native_actor("session-a"),
        session_id="session-a",
        protocol_session_id="session-a",
        reason="agent stopped",
    )
    status.assert_called_once_with(
        context,
        tool=_native_actor("session-a"),
        session_id="session-a",
        state="done",
        committed_sha="git-value",
        worktree_branch="git-value",
    )
    lead.assert_not_called()


def test_native_stop_saturated_recent_view_fails_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    mutation = Mock(side_effect=AssertionError("bounded visibility allowed mutation"))
    facts = [
        _fact(
            seq=seq,
            tool="cursor",
            session_id="session-a" if seq == 1 else f"other-{seq}",
            subject="state=working | file=. | intent=test",
        )
        for seq in range(1, 501)
    ]
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(
        agent_rally,
        "native_recent",
        lambda *_a, **_kw: _recent_result(facts),
    )
    monkeypatch.setattr(agent_rally, "_release_native_session_claims", mutation)
    monkeypatch.setattr(agent_rally, "native_status_post", mutation)
    monkeypatch.setattr(agent_rally, "lead_command", mutation)

    assert agent_rally.cmd_stop(_args(tmp_path)) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is False
    assert payload["visibility"] == "bounded"
    assert "500-row visibility bound" in payload["reason"]
    mutation.assert_not_called()


def test_native_stop_ambiguous_session_tool_fails_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    mutation = Mock(side_effect=AssertionError("ambiguous session allowed mutation"))
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(
        agent_rally,
        "native_recent",
        lambda *_a, **_kw: _recent_result(
            [
                _fact(
                    seq=1,
                    tool=_native_actor("session-a"),
                    session_id="session-a",
                    subject="state=working | file=. | intent=test",
                ),
                _fact(
                    seq=2,
                    tool="codex:session-a",
                    session_id="session-a",
                    subject="state=working | file=. | intent=test",
                ),
            ]
        ),
    )
    monkeypatch.setattr(agent_rally, "_release_native_session_claims", mutation)
    monkeypatch.setattr(agent_rally, "native_status_post", mutation)

    assert agent_rally.cmd_stop(_args(tmp_path)) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is False
    assert payload["status"] == "unproven"
    assert "ambiguous" in payload["reason"]
    mutation.assert_not_called()


@pytest.mark.parametrize("session_id", ("*", "../victim", "session[ab]", "session?"))
def test_local_stop_rejects_glob_and_traversal_without_deleting(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    session_id: str,
) -> None:
    context = _local_context(tmp_path)
    sessions = context.local_channel_dir / "sessions"
    sessions.mkdir(parents=True)
    victim = sessions / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    outside = context.local_channel_dir / "victim.json"
    outside.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)

    assert agent_rally.cmd_stop(_args(tmp_path, session_id=session_id)) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "presence-stop-refused"
    assert payload["accepted"] is False
    assert victim.is_file()
    assert outside.is_file()


def test_local_stop_deletes_one_exact_valid_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _local_context(tmp_path)
    sessions = context.local_channel_dir / "sessions"
    sessions.mkdir(parents=True)
    target = sessions / "agent:code_review-01.json"
    sibling = sessions / "agent:code_review-02.json"
    target.write_text("{}", encoding="utf-8")
    sibling.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)

    assert agent_rally.cmd_stop(
        _args(tmp_path, session_id="agent:code_review-01")
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is True
    assert payload["presence_removed"] == [str(target.resolve())]
    assert not target.exists()
    assert sibling.is_file()


def test_native_autoreg_repeated_deregister_does_not_duplicate_done(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    session_id = "agent:code-review-abc123"
    status = Mock(side_effect=AssertionError("repeated deregister emitted duplicate done"))
    monkeypatch.setattr(agent_autoreg, "_resolve_channel", lambda _workdir: context)
    monkeypatch.setattr(
        agent_autoreg,
        "recent",
        lambda *_a, **_kw: _recent_result(
            [
                _fact(
                    seq=1,
                    tool="agent:code-review",
                    session_id=session_id,
                    subject="agent presence: agent:code-review",
                ),
                _fact(
                    seq=2,
                    tool="agent:code-review",
                    session_id=session_id,
                    subject="state=done | committed_sha=abc | worktree_branch=feature",
                ),
            ]
        ),
    )
    monkeypatch.setattr(agent_autoreg, "status_post", status)

    assert agent_autoreg.deregister(session_id, workdir=tmp_path) is True
    status.assert_not_called()


def test_native_autoreg_saturated_recent_view_fails_before_done(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    session_id = "agent:coder-abc123"
    status = Mock(side_effect=AssertionError("bounded deregister emitted done"))
    facts = [
        _fact(
            seq=seq,
            tool="agent:coder",
            session_id=(
                session_id if seq == 1 else f"agent:coder-other-{seq}"
            ),
            subject="state=working | file=. | intent=test",
        )
        for seq in range(1, 501)
    ]
    monkeypatch.setattr(agent_autoreg, "_resolve_channel", lambda _workdir: context)
    monkeypatch.setattr(
        agent_autoreg,
        "recent",
        lambda *_a, **_kw: _recent_result(facts),
    )
    monkeypatch.setattr(agent_autoreg, "status_post", status)

    assert agent_autoreg.deregister(session_id, workdir=tmp_path) is False
    status.assert_not_called()


def test_native_autoreg_ambiguous_session_tool_fails_before_done(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    session_id = "explicit-child-session"
    status = Mock(side_effect=AssertionError("ambiguous deregister emitted done"))
    monkeypatch.setattr(agent_autoreg, "_resolve_channel", lambda _workdir: context)
    monkeypatch.setattr(
        agent_autoreg,
        "recent",
        lambda *_a, **_kw: _recent_result(
            [
                _fact(
                    seq=1,
                    tool="agent:coder",
                    session_id=session_id,
                    subject="state=working | file=. | intent=test",
                ),
                _fact(
                    seq=2,
                    tool="agent:reviewer",
                    session_id=session_id,
                    subject="state=working | file=. | intent=test",
                ),
            ]
        ),
    )
    monkeypatch.setattr(agent_autoreg, "status_post", status)

    assert agent_autoreg.deregister(session_id, workdir=tmp_path) is False
    status.assert_not_called()
