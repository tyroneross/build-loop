#!/usr/bin/env python3
"""
transcript-pattern-miner.py

Mines local Claude Code session transcripts for recurring patterns that may be
worth promoting to skills, agents, hooks, or feedback notes. Pure stdlib + regex.
No network calls. No LLM calls. Output is a markdown report and a candidates JSON.

Verified data layout (2026-05-01):
  Sessions live at  ~/.claude/projects/<project-slug>/<session-uuid>.jsonl
  (NOT in a `sessions/` subdirectory.) Each line is a JSON object with a
  `type` field. Relevant types:

    user         — message from user OR carrying tool_result blocks back to model
                   message.role == "user"
                   message.content: str | list[ {type: "text"|"tool_result", ...} ]
                   tool_result.content: str | list[ {type: "text", text: str} ]
    assistant    — model output
                   message.content: list[ {type: "text"|"thinking"|"tool_use", ...} ]
                   tool_use: {name, input}
    system       — tool/CLI system events
    attachment, file-history-snapshot, queue-operation, last-prompt,
    permission-mode  — meta-events, mostly ignored by this miner

  Common top-level fields on user/assistant: timestamp (ISO8601), cwd, sessionId,
  uuid, parentUuid, gitBranch, version.

This miner reads JSONL line-by-line, never holds a whole session in memory beyond
small extracted aggregates, and writes only to ~/.build-loop/transcript-patterns/.

Categories:
  1. Recurring user corrections   (heuristic + n-gram clustering, 3+ occurrences)
  2. Repeated tool sequences      (length 3-6 sequences, 3+ sessions)
  3. Cross-project file patterns  (files touched in 3+ projects, or churn within one)
  4. Manual command rituals       (Bash invocations normalized, 5+ across sessions)
  5. Secrets observed             (rotation tracker — full values surfaced)

CLI:
  python3 transcript-pattern-miner.py            # last 7 days
  python3 transcript-pattern-miner.py --days 30
  python3 transcript-pattern-miner.py --all
  python3 transcript-pattern-miner.py --force    # ignore .processed.json cache

Privacy (single-user context, see feedback_single_user_transcripts.md):
  Quotes are capped at 300 chars (raised from 80; thin previews under-judged
  intent). Secrets are surfaced in full so the user can rotate them. Output
  remains local-only — no network egress, no auto-publish. Do NOT adapt this
  miner for multi-user environments without restoring the privacy caps.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator

# --- Constants -------------------------------------------------------------

HOME = Path.home()
SESSIONS_DIR = HOME / ".claude" / "projects" / "-Users-tyroneross"
OUT_DIR = HOME / ".build-loop" / "transcript-patterns"
PROCESSED_FILE = OUT_DIR / ".processed.json"
CANDIDATES_FILE = OUT_DIR / ".candidates.json"

# Correction signal phrases. Lowercase substrings; word-boundary checked at use.
CORRECTION_SIGNALS = [
    r"\bno\b",
    r"\bstop\b",
    r"\bdon't\b",
    r"\bdont\b",
    r"\bactually\b",
    r"\bwrong\b",
    r"\binstead\b",
    r"\bnot that\b",
    r"\byou should have\b",
    r"\bwhy didn'?t you\b",
    r"\bi told you\b",
    r"\bagain\b",
    r"\bas i said\b",
    r"\bnot what i\b",
    r"\bthat'?s not\b",
    r"\bdoesn'?t work\b",
    r"\bthis doesn'?t\b",
]
CORRECTION_RE = re.compile("|".join(CORRECTION_SIGNALS), re.IGNORECASE)

# Local-command and meta noise we should skip when looking for "real" user voice.
META_PREFIXES = (
    "<local-command-",
    "<command-",
    "<system-reminder>",
    "<task-notification>",
    "<task-result>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
    "[Request interrupted",
    "Caveat:",
    "Stop hook feedback:",
    "PostToolUse:",
    "PreToolUse:",
    "Notification:",
    "SessionStart:",
    "UserPromptSubmit:",
    "PreCompact:",
)


def _strip_meta_block(s: str) -> str:
    """Strip a leading <tag>...</tag> meta block to expose the user prose."""
    s = s.lstrip()
    if s.startswith("<") and ">" in s[:200]:
        end = s.find(">")
        if end > 0:
            tag = s[1:end].split()[0] if s[1:end] else ""
            close = f"</{tag}>"
            close_idx = s.find(close)
            if close_idx > 0:
                return s[close_idx + len(close):].lstrip()
    return s

# Secret regexes. Order matters: more-specific first.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_\-]{40,}")),
    ("openai", re.compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}")),
    ("github_classic", re.compile(r"gh[ps]_[A-Za-z0-9]{36,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("github_oauth", re.compile(r"gho_[A-Za-z0-9]{36,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("pem", re.compile(r"-----BEGIN [A-Z ]+-----")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}")),
]
# Generic high-entropy: only flag if a credential keyword precedes within 30 chars.
GENERIC_SECRET_RE = re.compile(
    r"(?i)(token|secret|key|password|credential|api[_\-]?key|bearer)"
    r"[^A-Za-z0-9]{1,30}([A-Za-z0-9_\-]{40,})"
)

# Tool calls we treat as "file-touching".
FILE_TOUCH_TOOLS = {"Read", "Edit", "Write", "Glob", "MultiEdit", "NotebookEdit"}

# --- Test pattern detection (Section 6) ----------------------------------
# 8 categories per research entry tools.testing-prioritization-llm-and-ui-2026-05-01.md §4.2.

# Category A: IBR — MCP tool names + slash commands
IBR_TOOL_PREFIX = "mcp__plugin_ibr_ibr__"
IBR_SLASH_RE = re.compile(r"/ibr:[a-z0-9\-_]+", re.IGNORECASE)

# Category B: pytest / jest / vitest shell invocations
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

# Category C: validation gate language — assistant text after work claims
VALIDATION_GATE_RE = re.compile(
    r"(?:^|\n|\.)\s*"
    r"(?:Validation:|Verified by:|✅\s*verified by|How tested:|Tested by:|Verified:)",
    re.IGNORECASE,
)

# Category D: synthetic fixture pattern — mktemp + bad/empty payload
SYNTHETIC_FIXTURE_RE = re.compile(
    r"mktemp\b|"
    r"<<\s*['\"]?BAD['\"]?|"
    r"<<\s*['\"]?EMPTY['\"]?|"
    r"echo\s+['\"]?(?:bad|invalid|malformed|broken)\b",
    re.IGNORECASE,
)

# Category E: smoke tests — single-line curl
SMOKE_CURL_RE = re.compile(r"\bcurl\s+-[a-zA-Z]*[sS]")

# Category F: real-data regression — language about catching past issues
REAL_DATA_REGRESSION_RE = re.compile(
    r"\b(must catch|should catch|catches|regress(?:ion)?)\b.*"
    r"(past issues?|past bugs?|prior runs?|existing repo|existing data|real data)|"
    r"\b(real(?:-|\s)data)\b.*\b(scan|test|regress|check)\b",
    re.IGNORECASE,
)

# Category G: manual visual checks — open browser, take screenshot
MANUAL_VISUAL_RE = re.compile(
    r"\bopen\s+(?:https?://|http://|localhost|-a\s+['\"]?(?:Safari|Chrome|Firefox))|"
    r"\bscreencapture\b|\bcmd\+shift\+[345]|\bScreenshot\b",
    re.IGNORECASE,
)

# Category H: type-checking / lint
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

TEST_CATEGORIES = ("A_ibr", "B_runner", "C_validation_gate", "D_synthetic", "E_smoke", "F_real_regression", "G_manual_visual", "H_typecheck")

# Implicit acceptance signals on the next 1-3 user messages.
ACCEPT_PIVOT_RE = re.compile(
    r"^\s*(?:now\s+let'?s|next|moving on|what about|let'?s\s+(?:now|move|switch)|on to|"
    r"how about|alright,?\s+now)\b",
    re.IGNORECASE,
)
ACCEPT_TOKEN_RE = re.compile(
    r"\b(?:looks good|good|perfect|great|nice|excellent|ship it|works|"
    r"ok thanks|thanks|thank you|cool|awesome|much better|that works)\b[\s\.\!]",
    re.IGNORECASE,
)

# Tools we omit from "manual ritual" detection (not user-issued shells).
RITUAL_SKIP = {
    "Read", "Edit", "Write", "Glob", "Grep", "Task", "TodoWrite",
    "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    "WebFetch", "WebSearch", "Skill", "SlashCommand", "AskUserQuestion",
    "NotebookEdit", "MultiEdit", "BashOutput", "KillShell", "Monitor",
    "ExitPlanMode",
}

PROJECT_RE = re.compile(r"/dev/git-folder/([^/]+)")


# --- Utilities -------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=7, help="Window in days (default 7)")
    p.add_argument("--all", action="store_true", help="Scan full history")
    p.add_argument("--force", action="store_true", help="Reprocess files in cache")
    p.add_argument(
        "--sessions-dir",
        default=str(SESSIONS_DIR),
        help="Override sessions root (for testing)",
    )
    p.add_argument(
        "--out-dir",
        default=str(OUT_DIR),
        help="Override output dir (for testing)",
    )
    return p.parse_args(argv)


def load_processed(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def save_processed(path: Path, processed: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(processed, indent=2, sort_keys=True))
    tmp.replace(path)


def file_signature(path: Path) -> str:
    """Cheap signature: size + mtime. Avoid hashing GB of JSONL."""
    st = path.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def parse_ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return dt.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def project_from_cwd(cwd: str | None) -> str:
    if not cwd:
        return "(unknown)"
    m = PROJECT_RE.search(cwd)
    return m.group(1) if m else "(other)"


def truncate(s: str, n: int = 300) -> str:
    s = s.replace("\n", " ").replace("\r", " ").strip()
    s = re.sub(r"\s+", " ", s)
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def truncate_secret(s: str, n: int = 12) -> str:
    return s[:n] + "…" if len(s) > n else s


def message_text(msg_content: Any) -> str:
    """Coerce a message content (str or list of parts) into a single string for scanning."""
    if isinstance(msg_content, str):
        return msg_content
    if isinstance(msg_content, list):
        parts: list[str] = []
        for item in msg_content:
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            if t == "text":
                parts.append(str(item.get("text", "")))
            elif t == "tool_result":
                cnt = item.get("content")
                if isinstance(cnt, str):
                    parts.append(cnt)
                elif isinstance(cnt, list):
                    for cc in cnt:
                        if isinstance(cc, dict) and cc.get("type") == "text":
                            parts.append(str(cc.get("text", "")))
        return "\n".join(parts)
    return ""


def is_real_user_text(text: str) -> tuple[bool, str]:
    """
    Filter out tool_result echoes and system-injected meta blocks.

    Returns (is_real, cleaned_text). cleaned_text strips a leading meta block
    when one is present but real prose follows.
    """
    if not text:
        return False, ""
    head = text.lstrip()
    if any(head.startswith(p) for p in META_PREFIXES):
        # Try to strip the leading meta block and see if real prose follows.
        rest = _strip_meta_block(head)
        if rest and rest != head and not any(rest.startswith(p) for p in META_PREFIXES):
            # Recursively strip in case multiple meta blocks are stacked.
            for _ in range(3):
                if any(rest.startswith(p) for p in META_PREFIXES):
                    rest = _strip_meta_block(rest)
                else:
                    break
            if rest and not any(rest.startswith(p) for p in META_PREFIXES) and len(rest) >= 8:
                return True, rest
        return False, ""
    return True, head


# --- Scanning core --------------------------------------------------------


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
        # list of (timestamp, text) for real user messages (post-assistant)
        self.user_messages: list[tuple[dt.datetime | None, str, str]] = []
        # tool sequence within session: list of tool names (with first-arg key when distinguishing)
        self.tool_sequence: list[str] = []
        # (project, abs_path) tuples
        self.files_touched: list[tuple[str, str]] = []
        # normalized bash command shapes
        self.bash_commands: list[str] = []
        # observations of secrets: list of (kind, value, project, ts)
        self.secret_hits: list[tuple[str, str, str, dt.datetime | None]] = []
        # whether the previous non-meta event was an assistant message — used
        # to distinguish reactive corrections from session-opening prompts.
        self._prev_was_assistant = False
        # ordered timeline for outcome correlation. each entry is a dict with
        # keys: idx (int), kind ("assistant_tool"|"assistant_text"|"user_real"|"tool_result"),
        # ts (datetime|None), text (str), tool_name (str|None), tool_input (dict|None),
        # tool_use_id (str|None), is_error (bool|None), proj (str).
        self.events: list[dict[str, Any]] = []
        # detected test invocations: list of dicts with category, subtype, evidence, event_idx, ts, proj
        self.test_invocations: list[dict[str, Any]] = []


def _detect_bash_test_category(
    agg: "SessionAggregate",
    cmd: str,
    event_idx: int,
    ts: dt.datetime | None,
    proj: str,
    tool_use_id: str | None,
) -> None:
    """Inspect a Bash command for test categories B/D/E/F/G/H. Append matches."""
    if not cmd:
        return
    # B: pytest/jest/vitest
    m = TEST_RUNNER_RE.search(cmd)
    if m:
        agg.test_invocations.append({
            "category": "B_runner", "subtype": m.group(1).strip().split()[0],
            "evidence": truncate(cmd, 300), "event_idx": event_idx,
            "ts": ts, "proj": proj, "tool_use_id": tool_use_id,
        })
    # D: synthetic fixture
    if SYNTHETIC_FIXTURE_RE.search(cmd):
        agg.test_invocations.append({
            "category": "D_synthetic", "subtype": "mktemp-or-bad-payload",
            "evidence": truncate(cmd, 300), "event_idx": event_idx,
            "ts": ts, "proj": proj, "tool_use_id": tool_use_id,
        })
    # E: smoke curl
    if SMOKE_CURL_RE.search(cmd):
        agg.test_invocations.append({
            "category": "E_smoke", "subtype": "curl-s",
            "evidence": truncate(cmd, 300), "event_idx": event_idx,
            "ts": ts, "proj": proj, "tool_use_id": tool_use_id,
        })
    # F: real-data regression (language)
    if REAL_DATA_REGRESSION_RE.search(cmd):
        agg.test_invocations.append({
            "category": "F_real_regression", "subtype": "real-data-language",
            "evidence": truncate(cmd, 300), "event_idx": event_idx,
            "ts": ts, "proj": proj, "tool_use_id": tool_use_id,
        })
    # G: manual visual
    if MANUAL_VISUAL_RE.search(cmd):
        agg.test_invocations.append({
            "category": "G_manual_visual", "subtype": "open-or-screencapture",
            "evidence": truncate(cmd, 300), "event_idx": event_idx,
            "ts": ts, "proj": proj, "tool_use_id": tool_use_id,
        })
    # H: type-checking / lint
    m = TYPECHECK_RE.search(cmd)
    if m:
        agg.test_invocations.append({
            "category": "H_typecheck", "subtype": m.group(1).strip().split()[0],
            "evidence": truncate(cmd, 300), "event_idx": event_idx,
            "ts": ts, "proj": proj, "tool_use_id": tool_use_id,
        })


def classify_outcome(
    agg: "SessionAggregate",
    invocation: dict[str, Any],
) -> tuple[str, str]:
    """
    Combine implicit acceptance + tool-result inference for one test invocation.
    Returns (outcome_class, evidence_quote).
    Classes: POSITIVE | MIXED | REWORK | NO_SIGNAL.
    """
    idx = invocation["event_idx"]
    tool_use_id = invocation.get("tool_use_id")
    events = agg.events
    if idx >= len(events):
        return "NO_SIGNAL", ""

    # Signal A — tool_result is_error for this tool_use_id.
    tool_result_err: bool | None = None
    if tool_use_id:
        for ev in events[idx + 1: idx + 8]:  # adjacent tool_result usually within a few events
            if ev["kind"] == "tool_result" and ev.get("tool_use_id") == tool_use_id:
                tool_result_err = ev.get("is_error")
                break

    # Signal B — next 1-3 user_real messages.
    next_user: list[dict[str, Any]] = []
    for ev in events[idx + 1:]:
        if ev["kind"] == "user_real":
            next_user.append(ev)
            if len(next_user) >= 3:
                break

    rework = False
    accept = False
    evidence = ""
    for ev in next_user:
        txt = ev.get("text") or ""
        if not txt:
            continue
        if CORRECTION_RE.search(txt):
            rework = True
            evidence = truncate(txt, 300)
            break
        if ACCEPT_PIVOT_RE.search(txt) or ACCEPT_TOKEN_RE.search(txt):
            accept = True
            evidence = truncate(txt, 300)
            break

    # Combine
    if tool_result_err is True and rework:
        return "REWORK", evidence or "tool errored + user corrected"
    if tool_result_err is True and not accept:
        return "REWORK", evidence or "tool returned is_error=true"
    if rework:
        return "REWORK", evidence
    if tool_result_err is False and accept:
        return "POSITIVE", evidence
    if accept:
        return "POSITIVE", evidence
    if tool_result_err is False and not rework and not accept:
        # Tool succeeded, no further user signal. Treat as MIXED — silent on next-user
        # could mean acceptance OR session ended. Conservative.
        return "MIXED", "tool ok, no follow-up signal"
    return "NO_SIGNAL", ""


def normalize_bash(cmd: str) -> str:
    """Reduce a shell command to a stable shape: keep program + flag NAMES, drop values."""
    if not cmd:
        return ""
    cmd = cmd.strip()
    # take first ~3 words for program + subcommand
    # but preserve flag names (--foo, -f) without their values
    tokens = cmd.split()
    out: list[str] = []
    skip_next = False
    for i, tok in enumerate(tokens[:30]):  # cap to avoid huge lines
        if skip_next:
            skip_next = False
            continue
        if tok.startswith("--"):
            # --flag=value -> --flag
            name = tok.split("=", 1)[0]
            out.append(name)
        elif tok.startswith("-") and len(tok) > 1 and not tok[1].isdigit():
            out.append(tok)
            # next token is likely its value if it doesn't itself start with -
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                skip_next = True
        elif i < 2:
            # program name + subcommand: keep
            out.append(tok)
        else:
            # arg/path: replace
            out.append("<arg>")
    # collapse repeated <arg>
    cleaned: list[str] = []
    for tok in out:
        if tok == "<arg>" and cleaned and cleaned[-1] == "<arg>":
            continue
        cleaned.append(tok)
    return " ".join(cleaned)


def scan_secrets(text: str) -> list[tuple[str, str]]:
    """Return list of (kind, value) found. Dedupes within one text."""
    if not text:
        return []
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, rx in SECRET_PATTERNS:
        for m in rx.finditer(text):
            v = m.group(0)
            key = (kind, v)
            if key not in seen:
                seen.add(key)
                found.append(key)
    for m in GENERIC_SECRET_RE.finditer(text):
        v = m.group(2)
        # skip if already caught by a specific pattern
        if any(v == fv for _, fv in found):
            continue
        key = ("generic", v)
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


def process_session_file(path: Path, cutoff: dt.datetime | None) -> SessionAggregate | None:
    """Stream a session JSONL and return an aggregate, or None if outside the window."""
    sess_id = path.stem
    agg = SessionAggregate(sess_id)
    has_any = False
    in_window = cutoff is None  # if no cutoff, all in window

    for obj in iter_jsonl(path):
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
            msg = obj.get("message") or {}
            content = msg.get("content")
            text = message_text(content)
            if text:
                # Secrets can be in tool_result strings too — scan whole payload.
                for kind, val in scan_secrets(text):
                    agg.secret_hits.append((kind, val, proj, ts))
                # Real user prose only (not tool_result, not local-command meta).
                if isinstance(content, str):
                    is_real, cleaned = is_real_user_text(content)
                    if is_real and agg._prev_was_assistant:
                        agg.user_messages.append((ts, cleaned, proj))
                        agg.events.append({
                            "idx": len(agg.events),
                            "kind": "user_real",
                            "ts": ts,
                            "text": cleaned,
                            "tool_name": None, "tool_input": None,
                            "tool_use_id": None, "is_error": None, "proj": proj,
                        })
                    has_any = True
                # tool_result events — capture is_error + tool_use_id for outcome inference
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "tool_result":
                            agg.events.append({
                                "idx": len(agg.events),
                                "kind": "tool_result",
                                "ts": ts,
                                "text": "",  # body not needed for outcome inference
                                "tool_name": None, "tool_input": None,
                                "tool_use_id": item.get("tool_use_id"),
                                "is_error": bool(item.get("is_error")),
                                "proj": proj,
                            })
            agg._prev_was_assistant = False

        elif t == "assistant":
            msg = obj.get("message") or {}
            content = msg.get("content")
            text_for_secrets = message_text(content)
            if text_for_secrets:
                for kind, val in scan_secrets(text_for_secrets):
                    agg.secret_hits.append((kind, val, proj, ts))
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = item.get("type")
                    if item_type == "text":
                        txt = str(item.get("text", ""))
                        if txt:
                            agg.events.append({
                                "idx": len(agg.events),
                                "kind": "assistant_text",
                                "ts": ts, "text": txt,
                                "tool_name": None, "tool_input": None,
                                "tool_use_id": None, "is_error": None, "proj": proj,
                            })
                            # Category C: validation gate language in assistant text
                            if VALIDATION_GATE_RE.search(txt):
                                agg.test_invocations.append({
                                    "category": "C_validation_gate",
                                    "subtype": "verified-by-text",
                                    "evidence": truncate(txt, 300),
                                    "event_idx": len(agg.events) - 1,
                                    "ts": ts, "proj": proj,
                                    "tool_use_id": None,
                                })
                    elif item_type == "tool_use":
                        name = item.get("name") or "?"
                        inp = item.get("input") or {}
                        tool_use_id = item.get("id")
                        # discriminate Bash/Read/Edit/Write further for sequence keys
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
                        if name in FILE_TOUCH_TOOLS:
                            fp = inp.get("file_path") or inp.get("path") or inp.get("pattern")
                            if isinstance(fp, str) and fp:
                                agg.files_touched.append((proj, fp))
                        elif name == "Bash":
                            cmd = inp.get("command") if isinstance(inp, dict) else None
                            if isinstance(cmd, str):
                                shape = normalize_bash(cmd)
                                if shape:
                                    agg.bash_commands.append(shape)
                                # Test category detection on bash commands
                                _detect_bash_test_category(agg, cmd, len(agg.events) - 1, ts, proj, tool_use_id)
                        # Category A: IBR MCP tool calls
                        if isinstance(name, str) and name.startswith(IBR_TOOL_PREFIX):
                            agg.test_invocations.append({
                                "category": "A_ibr",
                                "subtype": name[len(IBR_TOOL_PREFIX):] or "ibr",
                                "evidence": truncate(f"{name} {json.dumps(inp)[:200] if isinstance(inp, dict) else ''}", 300),
                                "event_idx": len(agg.events) - 1,
                                "ts": ts, "proj": proj,
                                "tool_use_id": tool_use_id,
                            })
                        # SlashCommand: /ibr:* counts as Category A too
                        if name == "SlashCommand" and isinstance(inp, dict):
                            cmd_str = str(inp.get("command", ""))
                            if IBR_SLASH_RE.search(cmd_str):
                                agg.test_invocations.append({
                                    "category": "A_ibr",
                                    "subtype": "slash-" + (IBR_SLASH_RE.search(cmd_str).group(0)),
                                    "evidence": truncate(cmd_str, 300),
                                    "event_idx": len(agg.events) - 1,
                                    "ts": ts, "proj": proj,
                                    "tool_use_id": tool_use_id,
                                })
            agg._prev_was_assistant = True
            has_any = True

    if not has_any:
        return None
    if not in_window:
        return None
    return agg


# --- Pattern detectors ----------------------------------------------------


def cluster_corrections(
    aggs: list[SessionAggregate],
) -> list[dict[str, Any]]:
    """Cluster user corrections by 3-gram overlap. Surface clusters with 3+ members."""
    candidates: list[dict[str, Any]] = []
    for agg in aggs:
        for ts, text, proj in agg.user_messages:
            if CORRECTION_RE.search(text):
                # take a representative quote: first sentence, capped at 300
                quote = truncate(text, 300)
                # 3-gram signature on lowercased alphanumeric tokens
                tokens = re.findall(r"[a-z0-9']+", text.lower())
                if len(tokens) < 3:
                    continue
                grams = {tuple(tokens[i : i + 3]) for i in range(len(tokens) - 2)}
                candidates.append({
                    "ts": ts,
                    "quote": quote,
                    "grams": grams,
                    "session": agg.session_id,
                    "project": proj,
                })

    # union-find-ish clustering by shared 3-grams (>=2 shared)
    clusters: list[list[dict[str, Any]]] = []
    for c in candidates:
        placed = False
        for cl in clusters:
            # compare to representative (first member)
            rep = cl[0]
            if len(c["grams"] & rep["grams"]) >= 2:
                cl.append(c)
                placed = True
                break
        if not placed:
            clusters.append([c])

    out: list[dict[str, Any]] = []
    for cl in clusters:
        if len(cl) < 3:
            continue
        timestamps = [c["ts"] for c in cl if c["ts"]]
        first_seen = min(timestamps) if timestamps else None
        last_seen = max(timestamps) if timestamps else None
        projects = sorted({c["project"] for c in cl})
        sessions = sorted({c["session"] for c in cl})
        # rep quote = shortest representative-ish
        rep_quote = sorted({c["quote"] for c in cl}, key=len)[0]
        out.append({
            "count": len(cl),
            "first_seen": first_seen.isoformat() if first_seen else None,
            "last_seen": last_seen.isoformat() if last_seen else None,
            "representative_quote": rep_quote,
            "projects": projects,
            "session_count": len(sessions),
        })
    out.sort(key=lambda d: (-d["count"], d.get("last_seen") or ""))
    return out


def repeated_tool_sequences(
    aggs: list[SessionAggregate],
) -> list[dict[str, Any]]:
    """Find length-3..6 sub-sequences that recur across 3+ sessions."""
    counts: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for agg in aggs:
        seq = agg.tool_sequence
        # cap per-session contribution to avoid one huge session dominating
        for length in (3, 4, 5, 6):
            seen_in_session: set[tuple[str, ...]] = set()
            for i in range(len(seq) - length + 1):
                window = tuple(seq[i : i + length])
                # collapse runs of identical tool ops to avoid trivial Read*N matches
                if len(set(window)) == 1:
                    continue
                if window in seen_in_session:
                    continue
                seen_in_session.add(window)
                counts[window].add(agg.session_id)

    out: list[dict[str, Any]] = []
    for window, sessions in counts.items():
        if len(sessions) < 3:
            continue
        out.append({
            "sequence": list(window),
            "session_count": len(sessions),
            "sample_sessions": sorted(sessions)[:3],
        })
    # prefer longer + more sessions
    out.sort(key=lambda d: (-d["session_count"], -len(d["sequence"])))
    return out[:20]  # cap output to keep report readable


def cross_project_files(
    aggs: list[SessionAggregate],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (cross-project files in 3+ projects, churn files inside one project)."""
    file_to_projects: dict[str, set[str]] = defaultdict(set)
    file_count_per_project: dict[tuple[str, str], int] = Counter()
    for agg in aggs:
        local_seen: set[tuple[str, str]] = set()
        for proj, fp in agg.files_touched:
            file_to_projects[fp].add(proj)
            file_count_per_project[(proj, fp)] += 1

    cross: list[dict[str, Any]] = []
    for fp, projects in file_to_projects.items():
        if len(projects) >= 3:
            cross.append({
                "file": fp,
                "projects": sorted(projects),
                "project_count": len(projects),
            })
    cross.sort(key=lambda d: -d["project_count"])

    churn: list[dict[str, Any]] = []
    for (proj, fp), n in file_count_per_project.items():
        if n >= 5:
            churn.append({"project": proj, "file": fp, "touches": n})
    churn.sort(key=lambda d: -d["touches"])
    return cross[:15], churn[:15]


def manual_command_rituals(
    aggs: list[SessionAggregate],
) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    sessions_for_shape: dict[str, set[str]] = defaultdict(set)
    for agg in aggs:
        for shape in agg.bash_commands:
            counter[shape] += 1
            sessions_for_shape[shape].add(agg.session_id)
    out: list[dict[str, Any]] = []
    for shape, n in counter.items():
        if n < 5:
            continue
        out.append({
            "command_shape": shape,
            "count": n,
            "session_count": len(sessions_for_shape[shape]),
        })
    out.sort(key=lambda d: -d["count"])
    return out[:20]


def test_pattern_outcomes(
    aggs: list[SessionAggregate],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Returns (per_invocation_log, aggregate_table).
    per_invocation_log entries are the rows we persist to .outcomes.jsonl.
    aggregate_table is one row per category for the markdown report.
    """
    per_invocation: list[dict[str, Any]] = []
    cat_counts: dict[str, dict[str, Any]] = {
        c: {"count": 0, "POSITIVE": 0, "MIXED": 0, "REWORK": 0, "NO_SIGNAL": 0,
            "projects": Counter()} for c in TEST_CATEGORIES
    }
    for agg in aggs:
        for inv in agg.test_invocations:
            outcome, ev_quote = classify_outcome(agg, inv)
            cat = inv["category"]
            cat_counts[cat]["count"] += 1
            cat_counts[cat][outcome] += 1
            cat_counts[cat]["projects"][inv["proj"]] += 1
            per_invocation.append({
                "timestamp": inv["ts"].isoformat() if inv["ts"] else None,
                "session_id": agg.session_id,
                "test_category": cat,
                "pattern_subtype": inv["subtype"],
                "outcome_class": outcome,
                "evidence_quote": ev_quote[:300],
                "project": inv["proj"],
            })
    table: list[dict[str, Any]] = []
    for cat in TEST_CATEGORIES:
        d = cat_counts[cat]
        if d["count"] == 0:
            continue
        top_proj = d["projects"].most_common(1)[0][0] if d["projects"] else "?"
        table.append({
            "category": cat, "count": d["count"],
            "POSITIVE": d["POSITIVE"], "MIXED": d["MIXED"],
            "REWORK": d["REWORK"], "NO_SIGNAL": d["NO_SIGNAL"],
            "top_project": top_proj,
        })
    table.sort(key=lambda r: -r["count"])
    return per_invocation, table


def append_outcomes_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    """Append outcome rows. Idempotency: dedupe within this run on
    (timestamp, session_id, test_category, event_idx) by way of the caller
    only running once per scan. We do NOT cross-check the file for
    pre-existing rows since scans of overlapping windows are expected."""
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            written += 1
    return written


def secrets_observed(
    aggs: list[SessionAggregate],
) -> list[dict[str, Any]]:
    """Aggregate unique secret values into a rotation tracker."""
    by_value: dict[tuple[str, str], dict[str, Any]] = {}
    for agg in aggs:
        for kind, val, proj, ts in agg.secret_hits:
            key = (kind, val)
            entry = by_value.setdefault(key, {
                "kind": kind,
                "preview": val,  # full value — single-user rotation tracking, see feedback_single_user_transcripts.md
                "first_seen": ts,
                "last_seen": ts,
                "session_ids": set(),
                "projects": set(),
            })
            if ts is not None:
                if entry["first_seen"] is None or ts < entry["first_seen"]:
                    entry["first_seen"] = ts
                if entry["last_seen"] is None or ts > entry["last_seen"]:
                    entry["last_seen"] = ts
            entry["session_ids"].add(agg.session_id)
            entry["projects"].add(proj)

    out: list[dict[str, Any]] = []
    for entry in by_value.values():
        out.append({
            "kind": entry["kind"],
            "preview": entry["preview"],
            "first_seen": entry["first_seen"].isoformat() if entry["first_seen"] else None,
            "last_seen": entry["last_seen"].isoformat() if entry["last_seen"] else None,
            "session_count": len(entry["session_ids"]),
            "projects": sorted(entry["projects"]),
        })
    out.sort(key=lambda d: (d.get("last_seen") or "", d["kind"]), reverse=True)
    return out


# --- Report rendering ----------------------------------------------------


def render_report(
    *,
    window_label: str,
    sessions_scanned: int,
    sessions_in_window: int,
    corrections: list[dict[str, Any]],
    sequences: list[dict[str, Any]],
    cross_files: list[dict[str, Any]],
    churn_files: list[dict[str, Any]],
    rituals: list[dict[str, Any]],
    secrets: list[dict[str, Any]],
    test_table: list[dict[str, Any]],
    test_invocation_count: int,
    test_outcomes_jsonl_path: Path,
    generated_at: dt.datetime,
) -> str:
    lines: list[str] = []
    lines.append(f"# Transcript Pattern Miner — {generated_at.date().isoformat()}")
    lines.append("")
    lines.append(f"- Window: **{window_label}**")
    lines.append(f"- Session files scanned: {sessions_scanned}")
    lines.append(f"- Session files in window: {sessions_in_window}")
    lines.append(f"- Generated: {generated_at.isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("> Pure-stdlib pattern miner. Single-user context: full quotes (≤300 chars) and full secret values surfaced for rotation tracking.")
    lines.append("> Output remains local-only — no network egress, no auto-publish.")
    lines.append("")

    # 1. Recurring user corrections
    lines.append("## 1. Recurring user corrections")
    lines.append("")
    if not corrections:
        lines.append("_No clusters with 3+ occurrences found._")
    else:
        lines.append(f"_{len(corrections)} cluster(s) with 3+ occurrences. Highest-signal candidate for `feedback_*.md`._")
        lines.append("")
        lines.append("| Count | First seen | Last seen | Projects | Representative quote |")
        lines.append("|---:|---|---|---|---|")
        for c in corrections[:15]:
            projs = ", ".join(c["projects"][:3])
            if len(c["projects"]) > 3:
                projs += f", +{len(c['projects']) - 3}"
            quote = c["representative_quote"].replace("|", "\\|")
            lines.append(f"| {c['count']} | {c['first_seen'] or '?'} | {c['last_seen'] or '?'} | {projs} | {quote} |")
    lines.append("")

    # 2. Repeated tool sequences
    lines.append("## 2. Repeated tool sequences")
    lines.append("")
    if not sequences:
        lines.append("_No sequences (length 3-6) recurring in 3+ sessions._")
    else:
        lines.append(f"_Top {min(len(sequences), 10)} sequences. Candidate workflow/skill targets._")
        lines.append("")
        lines.append("| Sessions | Length | Sequence |")
        lines.append("|---:|---:|---|")
        for s in sequences[:10]:
            seq = " → ".join(s["sequence"])
            lines.append(f"| {s['session_count']} | {len(s['sequence'])} | `{seq}` |")
    lines.append("")

    # 3. Cross-project file patterns
    lines.append("## 3. Cross-project file patterns")
    lines.append("")
    if cross_files:
        lines.append("_Files touched in 3+ projects (shared template / utility)._")
        lines.append("")
        lines.append("| Projects | File |")
        lines.append("|---:|---|")
        for f in cross_files[:10]:
            lines.append(f"| {f['project_count']} | `{f['file']}` |")
    else:
        lines.append("_No cross-project file patterns found._")
    lines.append("")
    if churn_files:
        lines.append("_Within-project churn (5+ touches in one project)._")
        lines.append("")
        lines.append("| Touches | Project | File |")
        lines.append("|---:|---|---|")
        for f in churn_files[:10]:
            lines.append(f"| {f['touches']} | {f['project']} | `{f['file']}` |")
        lines.append("")

    # 4. Manual command rituals
    lines.append("## 4. Manual command rituals")
    lines.append("")
    if not rituals:
        lines.append("_No bash command shapes repeating 5+ times._")
    else:
        lines.append(f"_Top {min(len(rituals), 10)} repeating shell shapes. Candidates for `/schedule` or scripts._")
        lines.append("")
        lines.append("| Count | Sessions | Shape |")
        lines.append("|---:|---:|---|")
        for r in rituals[:10]:
            shape = r["command_shape"].replace("|", "\\|")
            lines.append(f"| {r['count']} | {r['session_count']} | `{shape}` |")
    lines.append("")

    # 5. Secrets observed
    lines.append("## 5. Secrets observed (rotation tracker)")
    lines.append("")
    if not secrets:
        lines.append("_No secrets matched the detector regex set in this window._")
    else:
        lines.append(f"_{len(secrets)} distinct secret value(s) observed — full values surfaced for rotation. **Rotate any real keys before doing anything else with this report.**_")
        lines.append("")
        lines.append("| Kind | Preview | First seen | Last seen | Sessions | Projects |")
        lines.append("|---|---|---|---|---:|---|")
        for s in secrets[:50]:
            projs = ", ".join(s["projects"][:3])
            if len(s["projects"]) > 3:
                projs += f", +{len(s['projects']) - 3}"
            lines.append(
                f"| {s['kind']} | `{s['preview']}` | {s['first_seen'] or '?'} | "
                f"{s['last_seen'] or '?'} | {s['session_count']} | {projs} |"
            )
    lines.append("")
    lines.append(
        "> Full secret values are NOT in this report. To verify rotation, "
        "re-find the value in the source JSONL by `session_id` + date. "
        "Report safety: previews here are 12 chars and not sufficient to authenticate."
    )
    lines.append("")

    # 6. Test patterns + outcomes
    lines.append("## 6. Test patterns + outcomes")
    lines.append("")
    lines.append(
        "_Per research entry tools.testing-prioritization-llm-and-ui-2026-05-01.md §4.2._"
    )
    lines.append(
        f"_Total test invocations detected: {test_invocation_count}. "
        f"Per-invocation log appended to `{test_outcomes_jsonl_path}`._"
    )
    lines.append("")
    if not test_table:
        lines.append("_No test invocations matched the 8 detector categories in this window._")
    else:
        lines.append("Categories: A=IBR · B=runner (pytest/jest/vitest) · C=validation-gate prose · D=synthetic fixture · E=smoke (curl -s) · F=real-data regression · G=manual visual · H=type/lint")
        lines.append("")
        lines.append("| Category | Count | POSITIVE | MIXED | REWORK | NO_SIGNAL | Top project |")
        lines.append("|---|---:|---:|---:|---:|---:|---|")
        for r in test_table:
            lines.append(
                f"| {r['category']} | {r['count']} | {r['POSITIVE']} | {r['MIXED']} | "
                f"{r['REWORK']} | {r['NO_SIGNAL']} | {r['top_project']} |"
            )
        lines.append("")
        lines.append(
            "> Outcome inference combines (a) tool_result is_error status when the test was a tool call "
            "and (b) implicit acceptance/correction signals on the next 1-3 user messages. "
            "MIXED = tool succeeded but no follow-up user signal (could be silent acceptance OR session ended). "
            "NO_SIGNAL = neither tool-result nor next-user signal was conclusive."
        )
    lines.append("")
    lines.append("---")
    lines.append("Generated by `transcript-pattern-miner.py`. No network. No LLM. Local stdlib only.")
    return "\n".join(lines)


def build_candidates(
    corrections: list[dict[str, Any]],
    sequences: list[dict[str, Any]],
    rituals: list[dict[str, Any]],
    cross_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Top-5 ranked candidate proposals for self-improvement-architect to consume."""
    candidates: list[dict[str, Any]] = []
    for c in corrections[:3]:
        candidates.append({
            "kind": "feedback_candidate",
            "shape": "user_correction_cluster",
            "count": c["count"],
            "last_seen": c["last_seen"],
            "representative_quote": c["representative_quote"],
            "projects": c["projects"],
            "rationale": "Repeated user correction — highest signal for a feedback_*.md note.",
        })
    for s in sequences[:2]:
        candidates.append({
            "kind": "skill_or_workflow_candidate",
            "shape": "repeated_tool_sequence",
            "session_count": s["session_count"],
            "sequence": s["sequence"],
            "rationale": "Tool sequence recurring across 3+ sessions — candidate workflow/skill.",
        })
    for r in rituals[:1]:
        candidates.append({
            "kind": "automation_candidate",
            "shape": "bash_ritual",
            "count": r["count"],
            "command_shape": r["command_shape"],
            "rationale": "Shell shape repeated 5+ times — candidate for /schedule or script.",
        })
    for f in cross_files[:1]:
        candidates.append({
            "kind": "shared_utility_candidate",
            "shape": "cross_project_file",
            "project_count": f["project_count"],
            "file": f["file"],
            "rationale": "File touched in 3+ projects — candidate for shared template or utility.",
        })
    return candidates[:5]


# --- Main ---------------------------------------------------------------


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    sessions_dir = Path(args.sessions_dir).expanduser()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    processed_path = out_dir / ".processed.json"
    candidates_path = out_dir / ".candidates.json"

    if not sessions_dir.exists():
        print(f"sessions dir not found: {sessions_dir}", file=sys.stderr)
        return 2

    now = dt.datetime.now(dt.timezone.utc)
    if args.all:
        cutoff: dt.datetime | None = None
        window_label = "all history"
    else:
        cutoff = now - dt.timedelta(days=args.days)
        window_label = f"last {args.days} day(s)"

    processed = {} if args.force else load_processed(processed_path)

    jsonl_files = sorted(sessions_dir.glob("*.jsonl"))
    aggs: list[SessionAggregate] = []
    sessions_scanned = 0
    sessions_in_window = 0

    for path in jsonl_files:
        sig = file_signature(path)
        # mtime-based skip: if cutoff exists and file's mtime is older than cutoff,
        # the file cannot contain in-window events.
        st = path.stat()
        mtime = dt.datetime.fromtimestamp(st.st_mtime, tz=dt.timezone.utc)
        if cutoff is not None and mtime < cutoff:
            processed[str(path)] = sig
            continue
        # idempotent skip — but re-process in-window files since later messages may arrive
        # (Claude Code appends within the day). We still record the sig once we process it.
        sessions_scanned += 1
        agg = process_session_file(path, cutoff)
        if agg is None:
            processed[str(path)] = sig
            continue
        sessions_in_window += 1
        aggs.append(agg)
        processed[str(path)] = sig

    save_processed(processed_path, processed)

    corrections = cluster_corrections(aggs)
    sequences = repeated_tool_sequences(aggs)
    cross_files, churn_files = cross_project_files(aggs)
    rituals = manual_command_rituals(aggs)
    secrets = secrets_observed(aggs)
    per_invocation, test_table = test_pattern_outcomes(aggs)
    outcomes_jsonl = out_dir / ".outcomes.jsonl"
    rows_written = append_outcomes_jsonl(outcomes_jsonl, per_invocation)

    report = render_report(
        window_label=window_label,
        sessions_scanned=sessions_scanned,
        sessions_in_window=sessions_in_window,
        corrections=corrections,
        sequences=sequences,
        cross_files=cross_files,
        churn_files=churn_files,
        rituals=rituals,
        secrets=secrets,
        test_table=test_table,
        test_invocation_count=len(per_invocation),
        test_outcomes_jsonl_path=outcomes_jsonl,
        generated_at=now,
    )

    today = now.date().isoformat()
    report_path = out_dir / f"{today}.md"
    report_path.write_text(report)

    candidates = build_candidates(corrections, sequences, rituals, cross_files)
    candidates_path.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "window_label": window_label,
        "candidates": candidates,
    }, indent=2))

    # Brief stdout summary so caller (cron, agent, human) sees something useful.
    print(f"transcript-pattern-miner: window={window_label}")
    print(f"  sessions scanned: {sessions_scanned} (in window: {sessions_in_window})")
    print(f"  correction clusters: {len(corrections)}")
    print(f"  repeated sequences: {len(sequences)}")
    print(f"  cross-project files: {len(cross_files)}, churn files: {len(churn_files)}")
    print(f"  command rituals: {len(rituals)}")
    print(f"  distinct secrets observed: {len(secrets)}")
    print(f"  test invocations detected: {len(per_invocation)} ({rows_written} rows appended to .outcomes.jsonl)")
    if test_table:
        for r in test_table[:3]:
            print(f"    {r['category']}: count={r['count']} POS={r['POSITIVE']} MIX={r['MIXED']} REW={r['REWORK']} NS={r['NO_SIGNAL']}")
    print(f"  report: {report_path}")
    print(f"  candidates: {candidates_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
