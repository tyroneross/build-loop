#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Segment-first capture of decisions, lessons and open questions from a session transcript.

The single-prompt scan asked one model to find "decisions" anywhere in a whole
transcript. Measured on real sessions it produced ~5% usable items: it read harness
text as the owner speaking, never saw messages typed mid-task, and turned every
assistant offer into an open question.

This module splits the transcript by who had authority to say a thing, then asks a
narrow question of each part:

    owner_said      what the owner typed        -> standing rules only
    owner_answered  a steering question + the chosen option -> standing policies only
    agent_reported  the agent's closing report per turn     -> observed lessons only
    harness         reminders, hooks, notifications, subagent reports, pasted text,
                    tool results                            -> never read

Evidence: build-loop-memory/research/2026-09-19-decision-capture-local-models/.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

OWNER_SAID = "owner_said"
OWNER_ANSWERED = "owner_answered"
AGENT_REPORTED = "agent_reported"
HARNESS = "harness"

# Text the harness injects into user-role turns. None of it is the owner speaking.
_HARNESS_RE = re.compile(
    r"<system-reminder>|<task-notification>|Another Claude session sent a message|\[SYSTEM NOTIFICATION|"
    r"<agent-message|hook (success|additional context|blocking)|UserPromptSubmit|Stop hook|"
    r"^Base directory for this skill|SPDX-FileCopyrightText|<command-(name|message|args)>|"
    r"The user sent a new message while you were working|Caveat: The messages below|^\[Request interrupted",
    re.I | re.M,
)
# Material the owner pasted (peer messages, logs). Quoted, not authored by the owner.
_PASTED_RE = re.compile(r"<pasted_content[^>]*>.*?(</pasted_content>|$)", re.S)
_ANSWER_RE = re.compile(r'"([^"]+)"="([^"]+)"')
_MAX_SEGMENT_CHARS = 3000


def segments(path: Path) -> list[tuple[str, str]]:
    """Return (authority, text) pairs for one transcript, oldest first."""
    out: list[tuple[str, str]] = []
    pending_questions: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        # Messages typed while the agent is working are stored as queue records,
        # not as user turns. The previous scanner never saw them.
        if row.get("type") == "queue-operation" and row.get("operation") == "enqueue":
            text = _PASTED_RE.sub("", str(row.get("content") or "")).strip()
            if text:
                out.append((HARNESS if _HARNESS_RE.search(text) else OWNER_SAID, text))
            continue
        message = row.get("message") or {}
        role = str(message.get("role") or row.get("type") or "").lower()
        content = message.get("content")
        parts: Iterable[Any] = [content] if isinstance(content, str) else [p for p in (content or []) if isinstance(p, dict)]
        for part in parts:
            if role == "assistant" and isinstance(part, dict):
                if part.get("type") == "tool_use" and part.get("name") == "AskUserQuestion":
                    pending_questions.add(str(part.get("id")))
                elif part.get("type") == "text" and str(part.get("text") or "").strip():
                    out.append((AGENT_REPORTED, str(part["text"]).strip()))
            elif role == "user":
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    raw = part.get("content")
                    text = raw if isinstance(raw, str) else " ".join(
                        str(x.get("text", "")) for x in raw or [] if isinstance(x, dict))
                    if str(part.get("tool_use_id")) in pending_questions and "answered" in text:
                        out.append((OWNER_ANSWERED, text.strip()))
                    continue
                text = part if isinstance(part, str) else str(part.get("text") or "")
                text = _PASTED_RE.sub("", text).strip()
                if text:
                    out.append((HARNESS if _HARNESS_RE.search(text) else OWNER_SAID, text))
    return _dedupe(out)


def _dedupe(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """One owner message can arrive both as a queue record and as a user turn."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for kind, text in rows:
        if kind == OWNER_SAID:
            key = " ".join(text.split())
            if key in seen:
                continue
            seen.add(key)
        out.append((kind, text))
    return out


def closing_reports(rows: list[tuple[str, str]]) -> list[int]:
    """Indexes of the agent's last message before each owner turn (and at session end).

    Interim assistant messages are progress narration; the closing message is where a
    turn's findings are stated.
    """
    out = []
    for i, (kind, _) in enumerate(rows):
        if kind != AGENT_REPORTED:
            continue
        nxt = next((j for j in range(i + 1, len(rows)) if rows[j][0] in (OWNER_SAID, OWNER_ANSWERED)), len(rows))
        if not any(rows[j][0] == AGENT_REPORTED for j in range(i + 1, nxt)):
            out.append(i)
    return out


RULE_PROMPT = """The owner of a software project typed this message to their coding agent.
Does it state a STANDING rule, preference or constraint that should apply to FUTURE work, beyond the task at hand?
- Task requests ("fix X", "review Y", "merge"), approvals ("go for it", "sure"), questions and complaints are NOT rules: answer null.
- If it is a rule, restate it as one short imperative sentence in plain words.
Examples:
"all tests must work with real data before a feature is declared done" -> "Declare a feature done only after it works on real data."
"Go for it" -> null
"Review uncommitted code and finish if relevant" -> null
"Improve this by exploring local models and prompts" -> null (a task, even though it is phrased as an instruction)
"never push before the app is installed and run" -> "Never push before the installed app has been run."
Message:
{text}"""

ANSWER_PROMPT = """The owner answered a question from their coding agent.
Question: {question}
Answer: {answer}
Does this answer set a STANDING policy that governs future work (yes), or does it only approve or choose a one-off step in the current task (no)?
Examples: "When may a self-written script run? -> Approve once per version" = yes. "Start building stage 2 now? -> Yes" = no.
Reply yes or no."""

LESSON_PROMPT = """A coding agent wrote this report during a session.
Does it state a VERIFIED fact, cause, limit or pitfall about how a tool, system or codebase behaves that would still help in a FUTURE session?
- Plans, progress, status updates, test counts, commit hashes and results of this one task are NOT lessons: answer null.
- Counts, percentages, scores, queue sizes and anything about this session's own progress are NOT lessons.
- Only state behaviour that was observed (command output, tests, docs), never a guess.
- A lesson says what behaves how, for example "Ollama 0.34 auto-sizes context, so a 60k-character prompt is read in full".
Report:
{text}"""


def ask_ollama(prompt: str, key: str, model: str, timeout_s: int = 120, host: str = "http://127.0.0.1:11434") -> str | None:
    """One constrained question to a local model. Returns the string value, or None."""
    schema = {"type": "object", "properties": {key: {"type": ["string", "null"]}}, "required": [key]}
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "think": False,
                       "format": schema, "options": {"temperature": 0, "num_predict": 160}}).encode("utf-8")
    request = urllib.request.Request(f"{host}/api/generate", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
        value = json.loads(payload.get("response") or "{}").get(key)
    except Exception:  # noqa: BLE001 - a hook must never fail the session
        return None
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and value.lower() not in ("none", "null") else None


def capture(path: Path, model: str, ask: Callable[..., str | None] = ask_ollama,
            deadline_s: float = 240.0) -> list[dict]:
    """Extract capture items from one transcript. Each item carries `capture_class`.

    `deadline_s` bounds the model work: owner segments are always read (they are few
    and carry the owner's own words); lesson extraction stops when the deadline passes.
    """
    rows = segments(path)
    items: list[dict] = []
    started = time.monotonic()

    for kind, text in rows:
        if kind != OWNER_ANSWERED:
            continue
        for question, answer in _ANSWER_RE.findall(text):
            verdict = ask(ANSWER_PROMPT.format(question=question, answer=answer), "standing", model)
            standing = bool(verdict) and verdict.strip().lower().startswith("y")
            items.append({
                "capture_class": "decision" if standing else "activity",
                "decision": f"{question.strip()} -> {answer.strip()}",
                "state": "made", "confidence": "explicit", "source_segment": kind,
                "evidence": f"{question.strip()} -> {answer.strip()}"[:200],
                "rationale": "The owner chose this option in a steering question.",
            })

    for kind, text in rows:
        if kind != OWNER_SAID:
            continue
        rule = ask(RULE_PROMPT.format(text=text[:_MAX_SEGMENT_CHARS]), "rule", model)
        if rule:
            items.append({
                "capture_class": "decision", "decision": rule, "state": "made",
                "confidence": "confirmed", "source_segment": kind, "evidence": text[:200],
                "rationale": "Restated from a message the owner typed.",
            })

    for i in closing_reports(rows):
        if time.monotonic() - started > deadline_s:
            break
        text = rows[i][1]
        if len(text) < 200:
            continue
        lesson = ask(LESSON_PROMPT.format(text=text[:_MAX_SEGMENT_CHARS]), "lesson", model)
        if lesson:
            items.append({
                "capture_class": "lesson", "decision": lesson, "state": "made",
                "confidence": "inferred", "source_segment": AGENT_REPORTED, "evidence": text[:200],
                "rationale": "Observed during the session and restated as a reusable fact.",
            })

    question = _closing_question(rows)
    if question:
        items.append({
            "capture_class": "open_question", "decision": question, "state": "needed",
            "confidence": "explicit", "source_segment": AGENT_REPORTED, "evidence": question[:200],
            "rationale": "The agent asked this at the end of the session and the owner did not reply.",
        })
    return items


def _closing_question(rows: list[tuple[str, str]]) -> str | None:
    """The agent's final question when the owner never answered it."""
    last = max((i for i, (kind, _) in enumerate(rows) if kind == AGENT_REPORTED), default=None)
    if last is None or any(kind == OWNER_SAID for kind, _ in rows[last + 1:]):
        return None
    tail = rows[last][1][-600:]
    sentences = [s.strip() for s in re.split(r"(?<=[?.])\s+", tail) if s.strip().endswith("?")]
    return sentences[-1][:240] if sentences else None
