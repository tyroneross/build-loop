# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for stable, session-unique native Rally actors."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from rally_point import actor_identity


def test_host_session_env_precedes_generic_rally_identity() -> None:
    identity = actor_identity.resolve_identity(
        "codex",
        environ={
            "CODEX_THREAD_ID": "thread-one",
            "RALLY_SESSION_ID": "generic-rally",
        },
        observer_pid=42,
    )

    assert identity.base_tool == "codex"
    assert identity.session_id == "thread-one"
    assert identity.native_tool == "codex:thread-one"


def test_same_host_sessions_have_distinct_actors() -> None:
    first = actor_identity.resolve_identity(
        "cursor", environ={"CURSOR_SESSION_ID": "cursor-a"}, observer_pid=42
    )
    second = actor_identity.resolve_identity(
        "cursor", environ={"CURSOR_SESSION_ID": "cursor-b"}, observer_pid=42
    )

    assert first.base_tool == second.base_tool == "cursor"
    assert first.native_tool == "cursor:cursor-a"
    assert second.native_tool == "cursor:cursor-b"
    assert first.native_tool != second.native_tool


def test_explicit_actor_is_preserved() -> None:
    identity = actor_identity.resolve_identity(
        "claude_code:reviewer-01",
        "ignored-session",
        environ={},
        observer_pid=42,
    )

    assert identity.base_tool == "claude_code"
    assert identity.session_id == "ignored-session"
    assert identity.native_tool == "claude_code:reviewer-01"


def test_unsafe_or_long_session_cannot_escape_actor_segment() -> None:
    actor = actor_identity.native_tool_id("codex", "../../bad:" + ("x" * 200))

    assert actor.startswith("codex:")
    assert "/" not in actor
    assert ".." not in actor
    assert len(actor.encode("ascii")) <= actor_identity.MAX_NATIVE_TOOL_BYTES


def test_long_explicit_actor_is_compacted_to_native_boundary() -> None:
    actor = actor_identity.native_tool_id("claude_code:" + ("x" * 200))

    assert actor.startswith("claude_code:")
    assert len(actor.encode("ascii")) <= actor_identity.MAX_NATIVE_TOOL_BYTES
    assert actor == actor_identity.native_tool_id("claude_code:" + ("x" * 200))


def test_cli_prints_the_same_native_actor() -> None:
    script = Path(actor_identity.__file__).resolve()
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--tool",
            "codex",
            "--session-id",
            "thread-one",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "codex:thread-one"
