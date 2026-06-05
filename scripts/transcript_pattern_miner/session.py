#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""session — SessionAggregate dataclass, per-session JSONL streaming, outcome classification.

The key decomposition: process_session_file (was cyclomatic=47, cognitive=206,
11-deep nesting) is split into focused helpers — one per concern — so each has
early returns and a flat structure.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from .io_cache import iter_jsonl, parse_ts
from .textproc import (
    ACCEPT_PIVOT_RE,
    ACCEPT_TOKEN_RE,
    CORRECTION_RE,
    message_text,
    is_real_user_text,
    normalize_bash,
    project_from_cwd,
    truncate,
)
from .secrets_scan import scan_secrets

# ---------------------------------------------------------------------------
# Test-pattern detection constants (Section 6)
# ---------------------------------------------------------------------------

IBR_TOOL_PREFIX = "mcp__plugin_ibr_ibr__"
IBR_SLASH_RE = re.compile(r"/ibr:[a-z0-9\-_]+", re.IGNORECASE)

TEST_RUNNER_RE = re.compile(
    r"(?:^|\s|;|&&|\|)("
    r"pytest(?:\s|$)|"
    r"npm\s+(?:run\s+)?test(?:\s|$)|"
    r"npm\s+t(?:\s|$)|"
    r"yarn\s+test(?:\s|$)|"
    r"pnpm\s+test(?:\s|$)|"
    r"vitest(?:\s|$)|"
    r"jest(?:\s|$)|"
    r"go\s+test(?:\s|$)|"
    r"cargo\s+test(?:\s|$)|"
    r"swift\s+test(?:\s|$)"
    r")"
)

VALIDATION_GATE_RE = re.compile(
    r"(?:^|\n|\.)\s*"
    r"(?:Validation:|Verified by:|✅\s*verified by|How tested:|Tested by:|Verified:)",
    re.IGNORECASE,
)

SYNTHETIC_FIXTURE_RE = re.compile(
    r"mktemp\b|"
    r"<<\s*['\"]?BAD['\"]?|"
    r"<<\s*['\"]?EMPTY['\"]?|"
    r"echo\s+['\"]?(?:bad|invalid|malformed|broken)\b",
    re.IGNORECASE,
)

SMOKE_CURL_RE = re.compile(r"\bcurl\s+-[a-zA-Z]*[sS]")

REAL_DATA_REGRESSION_RE = re.compile(
    r"\b(must catch|should catch|catches|regress(?:ion)?)\b.*"
    r"(past issues?|past bugs?|prior runs?|existing repo|existing data|real data)|"
    r"\b(real(?:-|\s)data)\b.*\b(scan|test|regress|check)\b",
    re.IGNORECASE,
)

MANUAL_VISUAL_RE = re.compile(
    r"\bopen\s+(?:https?://|http://|localhost|-a\s+['\"]?(?:Safari|Chrome|Firefox))|"
    r"\bscreencapture\b|\bcmd\+shift\+[345]|\bScreenshot\b",
    re.IGNORECASE,
)

TYPECHECK_RE = re.compile(
    r"(?:^|\s|;|&&|\|)("
    r"tsc(?:\s+--noEmit|\s|$)|"
    r"mypy(?:\s|$)|"
    r"pyright(?:\s|$)|"
    r"eslint(?:\s|$)|"
    r"ruff(?:\s+(?:check|format))?(?:\s|$)|"
    r"swiftlint(?:\s|$)"
    r")"
)

TEST_CATEGORIES = (
    "A_ibr", "B_runner", "C_validation_gate", "D_synthetic",
    "E_smoke", "F_real_regression", "G_manual_visual", "H_typecheck",
)

# Tool calls we treat as "file-touching".
FILE_TOUCH_TOOLS = {"Read", "Edit", "Write", "Glob", "MultiEdit", "NotebookEdit"}

# Tools we omit from "manual ritual" detection (not user-issued shells).
RITUAL_SKIP = {
    "Read", "Edit", "Write", "Glob", "Grep", "Task", "TodoWrite",
    "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    "WebFetch", "WebSearch", "Skill", "SlashCommand", "AskUserQuestion",
    "NotebookEdit", "MultiEdit", "BashOutput", "KillShell", "Monitor",
    "ExitPlanMode",
}


# ---------------------------------------------------------------------------
# SessionAggregate
# ---------------------------------------------------------------------------

class SessionAggregate:
    """Per-session aggregation while streaming the JSONL."""

    __slots__ = (
        "session_id", "first_ts", "last_ts", "cwds",
        "user_messages", "tool_sequence", "files_touched",
        "bash_commands", "secret_hits", "_prev_was_assistant",
        "events", "test_invocations",
    )

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.first_ts: dt.datetime | None = None
        self.last_ts: dt.datetime | None = None
        self.cwds: set[str] = set()
        # list of (timestamp, text, proj) for real user messages (post-assistant)
        self.user_messages: list[tuple[dt.datetime | None, str, str]] = []
        # tool sequence within session: list of tool names (with first-arg key)
        self.tool_sequence: list[str] = []
        # (project, abs_path) tuples
        self.files_touched: list[tuple[str, str]] = []
        # normalized bash command shapes
        self.bash_commands: list[str] = []
        # (kind, value, project, ts) secret observations
        self.secret_hits: list[tuple[str, str, str, dt.datetime | None]] = []
        # whether the previous non-meta event was an assistant message
        self._prev_was_assistant = False
        # ordered timeline for outcome correlation
        self.events: list[dict[str, Any]] = []
        # detected test invocations
        self.test_invocations: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Helpers extracted from process_session_file
# ---------------------------------------------------------------------------

def _record_user_prose(
    agg: SessionAggregate,
    content: Any,
    ts: dt.datetime | None,
    proj: str,
) -> None:
    """Capture a real user prose message when it follows an assistant turn."""
    if not isinstance(content, str):
        return
    is_real, cleaned = is_real_user_text(content)
    if not (is_real and agg._prev_was_assistant):
        return
    agg.user_messages.append((ts, cleaned, proj))
    agg.events.append({
        "idx": len(agg.events),
        "kind": "user_real",
        "ts": ts,
        "text": cleaned,
        "tool_name": None, "tool_input": None,
        "tool_use_id": None, "is_error": None, "proj": proj,
    })


def _record_tool_results(
    agg: SessionAggregate,
    content: Any,
    ts: dt.datetime | None,
    proj: str,
) -> None:
    """Capture tool_result is_error status for outcome inference."""
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "tool_result":
            continue
        agg.events.append({
            "idx": len(agg.events),
            "kind": "tool_result",
            "ts": ts,
            "text": "",
            "tool_name": None, "tool_input": None,
            "tool_use_id": item.get("tool_use_id"),
            "is_error": bool(item.get("is_error")),
            "proj": proj,
        })


def _process_user_record(
    agg: SessionAggregate,
    obj: dict[str, Any],
    ts: dt.datetime | None,
    proj: str,
) -> bool:
    """Handle a 'user' type record. Returns True if any content was found."""
    msg = obj.get("message") or {}
    content = msg.get("content")
    text = message_text(content)
    if not text:
        return False
    for kind, val in scan_secrets(text):
        agg.secret_hits.append((kind, val, proj, ts))
    _record_user_prose(agg, content, ts, proj)
    _record_tool_results(agg, content, ts, proj)
    return True


def _record_assistant_text_item(
    agg: SessionAggregate,
    item: dict[str, Any],
    ts: dt.datetime | None,
    proj: str,
) -> None:
    """Handle a text content item from an assistant message."""
    txt = str(item.get("text", ""))
    if not txt:
        return
    agg.events.append({
        "idx": len(agg.events),
        "kind": "assistant_text",
        "ts": ts, "text": txt,
        "tool_name": None, "tool_input": None,
        "tool_use_id": None, "is_error": None, "proj": proj,
    })
    if VALIDATION_GATE_RE.search(txt):
        agg.test_invocations.append({
            "category": "C_validation_gate",
            "subtype": "verified-by-text",
            "evidence": truncate(txt, 300),
            "event_idx": len(agg.events) - 1,
            "ts": ts, "proj": proj,
            "tool_use_id": None,
        })


def _record_file_touch(
    agg: SessionAggregate,
    name: str,
    inp: dict[str, Any],
    proj: str,
) -> None:
    """Record a file path for cross-project / churn analysis."""
    fp = inp.get("file_path") or inp.get("path") or inp.get("pattern")
    if isinstance(fp, str) and fp:
        agg.files_touched.append((proj, fp))


def _record_bash_command(
    agg: SessionAggregate,
    inp: dict[str, Any],
    event_idx: int,
    ts: dt.datetime | None,
    proj: str,
    tool_use_id: str | None,
) -> None:
    """Normalize a bash command and detect test categories."""
    cmd = inp.get("command") if isinstance(inp, dict) else None
    if not isinstance(cmd, str):
        return
    shape = normalize_bash(cmd)
    if shape:
        agg.bash_commands.append(shape)
    _detect_bash_test_category(agg, cmd, event_idx, ts, proj, tool_use_id)


def _record_ibr_tool_call(
    agg: SessionAggregate,
    name: str,
    inp: Any,
    event_idx: int,
    ts: dt.datetime | None,
    proj: str,
    tool_use_id: str | None,
) -> None:
    """Detect IBR MCP tool calls (category A)."""
    if not (isinstance(name, str) and name.startswith(IBR_TOOL_PREFIX)):
        return
    agg.test_invocations.append({
        "category": "A_ibr",
        "subtype": name[len(IBR_TOOL_PREFIX):] or "ibr",
        "evidence": truncate(
            f"{name} {json.dumps(inp)[:200] if isinstance(inp, dict) else ''}",
            300,
        ),
        "event_idx": event_idx,
        "ts": ts, "proj": proj,
        "tool_use_id": tool_use_id,
    })


def _record_ibr_slash_command(
    agg: SessionAggregate,
    name: str,
    inp: Any,
    event_idx: int,
    ts: dt.datetime | None,
    proj: str,
    tool_use_id: str | None,
) -> None:
    """Detect /ibr:* SlashCommand invocations (category A)."""
    if name != "SlashCommand" or not isinstance(inp, dict):
        return
    cmd_str = str(inp.get("command", ""))
    m = IBR_SLASH_RE.search(cmd_str)
    if not m:
        return
    agg.test_invocations.append({
        "category": "A_ibr",
        "subtype": "slash-" + m.group(0),
        "evidence": truncate(cmd_str, 300),
        "event_idx": event_idx,
        "ts": ts, "proj": proj,
        "tool_use_id": tool_use_id,
    })


def _process_assistant_tool_use_item(
    agg: SessionAggregate,
    item: dict[str, Any],
    ts: dt.datetime | None,
    proj: str,
) -> None:
    """Handle a single tool_use content item from an assistant message."""
    name = item.get("name") or "?"
    inp = item.get("input") or {}
    tool_use_id = item.get("id")
    first_key = next(iter(inp.keys()), None) if isinstance(inp, dict) else None
    agg.tool_sequence.append(f"{name}:{first_key}" if first_key else name)
    agg.events.append({
        "idx": len(agg.events),
        "kind": "assistant_tool",
        "ts": ts, "text": "",
        "tool_name": name,
        "tool_input": inp if isinstance(inp, dict) else {},
        "tool_use_id": tool_use_id,
        "is_error": None, "proj": proj,
    })
    event_idx = len(agg.events) - 1
    if name in FILE_TOUCH_TOOLS:
        _record_file_touch(agg, name, inp if isinstance(inp, dict) else {}, proj)
    elif name == "Bash":
        _record_bash_command(agg, inp if isinstance(inp, dict) else {}, event_idx, ts, proj, tool_use_id)
    _record_ibr_tool_call(agg, name, inp, event_idx, ts, proj, tool_use_id)
    _record_ibr_slash_command(agg, name, inp, event_idx, ts, proj, tool_use_id)


def _process_assistant_record(
    agg: SessionAggregate,
    obj: dict[str, Any],
    ts: dt.datetime | None,
    proj: str,
) -> bool:
    """Handle an 'assistant' type record. Returns True to signal has_any."""
    msg = obj.get("message") or {}
    content = msg.get("content")
    text_for_secrets = message_text(content)
    if text_for_secrets:
        for kind, val in scan_secrets(text_for_secrets):
            agg.secret_hits.append((kind, val, proj, ts))
    if not isinstance(content, list):
        return True
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text":
            _record_assistant_text_item(agg, item, ts, proj)
        elif item_type == "tool_use":
            _process_assistant_tool_use_item(agg, item, ts, proj)
    return True


# ---------------------------------------------------------------------------
# Test category detection (Bash)
# ---------------------------------------------------------------------------

def _detect_bash_test_category(
    agg: SessionAggregate,
    cmd: str,
    event_idx: int,
    ts: dt.datetime | None,
    proj: str,
    tool_use_id: str | None,
) -> None:
    """Inspect a Bash command for test categories B/D/E/F/G/H. Append matches."""
    if not cmd:
        return
    m = TEST_RUNNER_RE.search(cmd)
    if m:
        agg.test_invocations.append({
            "category": "B_runner", "subtype": m.group(1).strip().split()[0],
            "evidence": truncate(cmd, 300), "event_idx": event_idx,
            "ts": ts, "proj": proj, "tool_use_id": tool_use_id,
        })
    if SYNTHETIC_FIXTURE_RE.search(cmd):
        agg.test_invocations.append({
            "category": "D_synthetic", "subtype": "mktemp-or-bad-payload",
            "evidence": truncate(cmd, 300), "event_idx": event_idx,
            "ts": ts, "proj": proj, "tool_use_id": tool_use_id,
        })
    if SMOKE_CURL_RE.search(cmd):
        agg.test_invocations.append({
            "category": "E_smoke", "subtype": "curl-s",
            "evidence": truncate(cmd, 300), "event_idx": event_idx,
            "ts": ts, "proj": proj, "tool_use_id": tool_use_id,
        })
    if REAL_DATA_REGRESSION_RE.search(cmd):
        agg.test_invocations.append({
            "category": "F_real_regression", "subtype": "real-data-language",
            "evidence": truncate(cmd, 300), "event_idx": event_idx,
            "ts": ts, "proj": proj, "tool_use_id": tool_use_id,
        })
    if MANUAL_VISUAL_RE.search(cmd):
        agg.test_invocations.append({
            "category": "G_manual_visual", "subtype": "open-or-screencapture",
            "evidence": truncate(cmd, 300), "event_idx": event_idx,
            "ts": ts, "proj": proj, "tool_use_id": tool_use_id,
        })
    m = TYPECHECK_RE.search(cmd)
    if m:
        agg.test_invocations.append({
            "category": "H_typecheck", "subtype": m.group(1).strip().split()[0],
            "evidence": truncate(cmd, 300), "event_idx": event_idx,
            "ts": ts, "proj": proj, "tool_use_id": tool_use_id,
        })


# ---------------------------------------------------------------------------
# Outcome classification
# ---------------------------------------------------------------------------

def classify_outcome(
    agg: SessionAggregate,
    invocation: dict[str, Any],
) -> tuple[str, str, bool]:
    """
    Combine strict tool-result status + soft user signals for one test invocation.
    Returns (outcome_class, evidence_quote, directional_only).

    Classes: POSITIVE | MIXED | REWORK | NO_SIGNAL.

    Trap 3 guard — soft signals are DIRECTIONAL only:
    - When outcome is derived purely from next-user-message text (no is_error basis),
      directional_only=True is returned so callers can label the row accordingly.
    - When outcome is grounded in tool_result.is_error (the strict signal), it is NOT
      directional.  This prevents soft correction/acceptance text from being treated as
      a hard metric.
    """
    idx = invocation["event_idx"]
    tool_use_id = invocation.get("tool_use_id")
    events = agg.events
    if idx >= len(events):
        return "NO_SIGNAL", "", False

    tool_result_err = _find_tool_result_error(events, idx, tool_use_id)
    rework, accept, evidence = _scan_next_user_signals(events, idx)

    if tool_result_err is True and rework:
        return "REWORK", evidence or "tool errored + user corrected", False
    if tool_result_err is True and not accept:
        return "REWORK", evidence or "tool returned is_error=true", False
    if rework:
        # Only user-text evidence — directional
        return "REWORK", evidence, True
    if tool_result_err is False and accept:
        return "POSITIVE", evidence, False
    if accept:
        # Only user-text evidence — directional
        return "POSITIVE", evidence, True
    if tool_result_err is False and not rework and not accept:
        return "MIXED", "tool ok, no follow-up signal", False
    return "NO_SIGNAL", "", False


def _find_tool_result_error(
    events: list[dict[str, Any]],
    idx: int,
    tool_use_id: str | None,
) -> bool | None:
    """Return is_error from the matching tool_result event, or None if not found."""
    if not tool_use_id:
        return None
    for ev in events[idx + 1: idx + 8]:
        if ev["kind"] == "tool_result" and ev.get("tool_use_id") == tool_use_id:
            return ev.get("is_error")
    return None


def _scan_next_user_signals(
    events: list[dict[str, Any]],
    idx: int,
) -> tuple[bool, bool, str]:
    """Scan up to 3 user_real events after idx for rework/accept signals.
    Returns (rework, accept, evidence_quote).
    """
    rework = False
    accept = False
    evidence = ""
    count = 0
    for ev in events[idx + 1:]:
        if ev["kind"] != "user_real":
            continue
        txt = ev.get("text") or ""
        count += 1
        if txt and CORRECTION_RE.search(txt):
            rework = True
            evidence = truncate(txt, 300)
            break
        if txt and (ACCEPT_PIVOT_RE.search(txt) or ACCEPT_TOKEN_RE.search(txt)):
            accept = True
            evidence = truncate(txt, 300)
            break
        if count >= 3:
            break
    return rework, accept, evidence


# ---------------------------------------------------------------------------
# Main session processor
# ---------------------------------------------------------------------------

def process_session_file(path: Path, cutoff: dt.datetime | None) -> SessionAggregate | None:
    """Stream a session JSONL and return an aggregate, or None if outside the window.

    Trap 2 guard — content dedup: resumed/sidechain transcripts re-log records that
    already appeared in an earlier session file.  We dedup by the record's `uuid` field
    (unique per Claude Code message) so duplicated records never inflate counts.
    Records without a `uuid` field are processed unconditionally (they cannot be
    distinguished and are rare; the main duplication vector always carries a uuid).
    """
    sess_id = path.stem
    agg = SessionAggregate(sess_id)
    has_any = False
    in_window = cutoff is None
    seen_uuids: set[str] = set()

    for obj in iter_jsonl(path):
        # Skip hook-injected / command-scaffolding / skill-load records.
        # Claude Code marks these with top-level isMeta=true even though
        # type stays "user"/"assistant" (Stop-hook feedback, slash-command
        # templates, SessionStart hook output, skill-load bodies). These
        # records pollute every downstream signal: secret-scanning runs on
        # injected diffs, user_messages count hook text as human prompts,
        # and tool-sequence captures hook-rendered assistant tool calls.
        # The text-level META_PREFIXES allowlist in textproc.py is brittle
        # (misses isMeta records whose text doesn't start with a known
        # prefix — SPDX headers, skill base-dir scaffolding, future hook
        # shapes). The structural isMeta flag is canonical and future-proof.
        # Mirrors the v0.29.1 retrospective sections.py fix on the same
        # record-shape gap (evidence transcript dfe491e3-…: 4× Stop-hook +
        # 1 SPDX skill body + 1 skill base-dir leaked through text filters).
        if obj.get("isMeta"):
            continue
        uid = obj.get("uuid")
        if uid:
            if uid in seen_uuids:
                continue
            seen_uuids.add(uid)
        t = obj.get("type")
        ts = parse_ts(obj.get("timestamp"))
        if ts is not None:
            if agg.first_ts is None:
                agg.first_ts = ts
            agg.last_ts = ts
            if cutoff is not None and ts >= cutoff:
                in_window = True
        cwd = obj.get("cwd")
        if cwd:
            agg.cwds.add(cwd)
        proj = project_from_cwd(cwd) if cwd else "(unknown)"

        if t == "user":
            if _process_user_record(agg, obj, ts, proj):
                has_any = True
            agg._prev_was_assistant = False

        elif t == "assistant":
            if _process_assistant_record(agg, obj, ts, proj):
                has_any = True
            agg._prev_was_assistant = True

    if not has_any:
        return None
    if not in_window:
        return None
    return agg
