#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""textproc — message text extraction, meta-block stripping, bash normalization."""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Correction signal phrases (lowercase substrings; word-boundary checked).
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

# Local-command and meta noise to skip when looking for "real" user voice.
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

PROJECT_RE = re.compile(r"/dev/git-folder/([^/]+)")

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


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

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


def message_text(msg_content: Any) -> str:
    """Coerce message content (str or list of parts) into a single string for scanning."""
    if isinstance(msg_content, str):
        return msg_content
    if not isinstance(msg_content, list):
        return ""
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


def is_real_user_text(text: str) -> tuple[bool, str]:
    """
    Filter out tool_result echoes and system-injected meta blocks.

    Returns (is_real, cleaned_text). cleaned_text strips a leading meta block
    when one is present but real prose follows.
    """
    if not text:
        return False, ""
    head = text.lstrip()
    if not any(head.startswith(p) for p in META_PREFIXES):
        return True, head
    # Try to strip the leading meta block and see if real prose follows.
    rest = _strip_meta_block(head)
    if not rest or rest == head:
        return False, ""
    if any(rest.startswith(p) for p in META_PREFIXES):
        return False, ""
    # Recursively strip in case multiple meta blocks are stacked.
    for _ in range(3):
        if not any(rest.startswith(p) for p in META_PREFIXES):
            break
        rest = _strip_meta_block(rest)
    if rest and not any(rest.startswith(p) for p in META_PREFIXES) and len(rest) >= 8:
        return True, rest
    return False, ""


def truncate(s: str, n: int = 300) -> str:
    s = s.replace("\n", " ").replace("\r", " ").strip()
    s = re.sub(r"\s+", " ", s)
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def project_from_cwd(cwd: str | None) -> str:
    if not cwd:
        return "(unknown)"
    m = PROJECT_RE.search(cwd)
    return m.group(1) if m else "(other)"


def normalize_bash(cmd: str) -> str:
    """Reduce a shell command to a stable shape: keep program + flag NAMES, drop values."""
    if not cmd:
        return ""
    cmd = cmd.strip()
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
