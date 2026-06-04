# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tier-1 deterministic correction + lesson detector.

ZERO LLM dependency. Pure regex + heuristics over Claude Code transcript JSONL.

Captures three signal classes from USER turns (the high-signal turns):

1. **Correction-of-just-taken-action** — user pushes back on the assistant's
   immediately-preceding action ("revert that", "undo", "don't do X").
   Confidence: CONFIRMED (highest). Because the assistant just acted and
   the user just said no, the textual evidence is unambiguous.

2. **Preference / convention** — user states a project-scoped or global
   preference ("always X", "never Y", "we use X for Z", "default to X").
   Confidence: CONFIRMED.

3. **Tradeoff / instead-of** — user names the right choice over the wrong
   one ("X instead of Y", "actually X not Y", "X because Z, not Y").
   Confidence: CONFIRMED.

Detection runs over the LAST user turn first (highest signal), then walks
back through the transcript for additional patterns. Returns Candidate
records with: kind, signal_type, quote (verbatim user span), context
(±200 char window), confidence, scope (project|global), and a stable hash
for dedup.

Anti-false-positive: only fires when the user turn carries an imperative
or declarative pattern AND no question mark dominates. Questions are NOT
captures.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# Pattern library — case-insensitive, anchored where meaningful.
# ---------------------------------------------------------------------------

# Correction-of-just-taken-action.
# The signature pattern is short imperative user turns immediately after
# an assistant action turn. Patterns capture the spans we want to quote.
CORRECTION_PATTERNS = [
    (re.compile(r"\b(revert\s+(?:that|this|it)|undo\s+(?:that|this|it)?)\b", re.I), "revert"),
    (re.compile(r"\bdon[''’]t\s+(?:do|use|add|change|touch|run)\s+([^.,;!?\n]{2,80})", re.I), "negative_directive"),
    (re.compile(r"\b(?:no,?\s+)?(?:that[''’]s|that\s+is|it[''’]s)\s+wrong\b", re.I), "wrong"),
    (re.compile(r"\b(?:stop|cease)\s+(?:doing\s+)?([^.,;!?\n]{2,80})", re.I), "stop_directive"),
    (re.compile(r"\bback\s+(?:that|it|those)\s+out\b", re.I), "back_out"),
    (re.compile(r"\b(?:not\s+(?:what|how)\s+i\s+(?:want|asked)|wrong\s+approach)\b", re.I), "wrong_approach"),
]

# Preference / convention. These can be standalone (no prior action needed).
PREFERENCE_PATTERNS = [
    (re.compile(r"\balways\s+([^.,;!?\n]{3,120})", re.I), "always"),
    (re.compile(r"\bnever\s+([^.,;!?\n]{3,120})", re.I), "never"),
    (re.compile(r"\b(?:must|need\s+to|should)\s+(?:always\s+)?([^.,;!?\n]{3,120})", re.I), "must"),
    (re.compile(r"\bdefault\s+(?:to|is)\s+([^.,;!?\n]{2,120})", re.I), "default"),
    (re.compile(r"\bwe\s+use\s+([^.,;!?\n]{2,120})\s+(?:for|to)\s+([^.,;!?\n]{2,120})", re.I), "we_use_for"),
    (re.compile(r"\bprefer\s+([^.,;!?\n]{2,120})", re.I), "prefer"),
]

# Tradeoff / instead-of.
# Patterns use lazy quantifiers + the explicit "stop word" so the first
# capture group doesn't swallow the keyword itself.
TRADEOFF_PATTERNS = [
    (re.compile(r"\b([^.,;!?\n]{2,80}?)\s+instead\s+of\s+([^.,;!?\n]{2,80})", re.I), "instead_of"),
    (re.compile(r"\bactually\s+([^.,;!?\n]{2,80}?)\s+not\s+([^.,;!?\n]{2,80})", re.I), "actually_not"),
    (re.compile(r"\b(\S[^.,;!?\n]{1,80}?)\s+over\s+(\S[^.,;!?\n]{1,80}?)\s+because\s+([^.,;!?\n]{3,120})", re.I), "over_because"),
]

# Global-scope hints — when present, route to cross-project lesson lane.
# Keyword set is intentionally narrow: only universal-language signals.
GLOBAL_SCOPE_HINTS = re.compile(
    r"\b(?:always|never|for\s+all\s+projects|globally|across\s+projects|"
    r"every\s+project|in\s+general|as\s+a\s+rule|standing\s+(?:rule|preference))\b",
    re.I,
)

# Hard skip patterns — user turn is clearly conversational, not directive.
# These match the WHOLE turn and short-circuit before any pattern scan.
HARD_SKIP_PATTERNS = [
    re.compile(r"^\s*(?:hi|hello|hey|thanks|ok(?:ay)?|nice|cool|got\s+it)\s*[.!?]?\s*$", re.I),
    re.compile(r"^\s*(?:what|how|why|when|where|who|which|can|could|would|should)\b.{0,200}\?\s*$", re.I | re.DOTALL),
]


@dataclass
class Candidate:
    """One pending lesson candidate.

    `id_hash` is a stable SHA1 of (kind, signal_type, quote_normalized) so
    re-running the scanner on the same transcript produces idempotent
    candidate IDs (dedup-friendly downstream).
    """

    kind: str  # "correction" | "preference" | "tradeoff"
    signal_type: str  # the named pattern that fired (e.g. "always", "instead_of")
    quote: str  # verbatim user span
    context: str  # ±N char window around the quote
    confidence: str  # "confirmed" (tier-1 only fires on high-signal patterns)
    scope: str  # "project" | "global"
    turn_index: int  # 0-based index into the user-turn stream
    captured_chars: int  # length of the quote
    id_hash: str = ""
    extras: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id_hash:
            self.id_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        norm = " ".join((self.kind, self.signal_type, self.quote.lower().strip()))
        return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)


class CorrectionDetector:
    """Scans an iterable of user turns and yields Candidates.

    Stateful only across one scan invocation. Safe to instantiate per call.
    """

    def __init__(self, *, context_window: int = 200, max_candidates_per_turn: int = 4) -> None:
        self.context_window = context_window
        self.max_candidates_per_turn = max_candidates_per_turn

    def scan_turn(self, turn_text: str, turn_index: int, *, prior_assistant_acted: bool = False) -> list[Candidate]:
        """Scan one user turn and return Candidates.

        `prior_assistant_acted` raises confidence for the correction class
        (matters when the assistant just took an action and the user is
        pushing back on it).
        """
        text = (turn_text or "").strip()
        if not text:
            return []

        # Hard skip: pure conversational / question turns.
        # HARD_SKIP_PATTERNS covers wh-question turns ending in ?. Longer
        # mixed turns (directive + trailing clarifying question) are NOT
        # skipped — the directive substring carries real signal.
        for sp in HARD_SKIP_PATTERNS:
            if sp.match(text):
                return []

        candidates: list[Candidate] = []
        scope = "global" if GLOBAL_SCOPE_HINTS.search(text) else "project"

        # Class 1: correction-of-just-taken-action.
        for pat, sig in CORRECTION_PATTERNS:
            for m in pat.finditer(text):
                if len(candidates) >= self.max_candidates_per_turn:
                    break
                quote = m.group(0).strip()
                ctx = self._window(text, m.start(), m.end())
                candidates.append(
                    Candidate(
                        kind="correction",
                        signal_type=sig,
                        quote=quote,
                        context=ctx,
                        confidence="confirmed",
                        scope=scope,
                        turn_index=turn_index,
                        captured_chars=len(quote),
                        extras={"prior_assistant_acted": bool(prior_assistant_acted)},
                    )
                )

        # Class 2: preference / convention.
        for pat, sig in PREFERENCE_PATTERNS:
            for m in pat.finditer(text):
                if len(candidates) >= self.max_candidates_per_turn:
                    break
                quote = m.group(0).strip()
                ctx = self._window(text, m.start(), m.end())
                candidates.append(
                    Candidate(
                        kind="preference",
                        signal_type=sig,
                        quote=quote,
                        context=ctx,
                        confidence="confirmed",
                        scope=scope,
                        turn_index=turn_index,
                        captured_chars=len(quote),
                    )
                )

        # Class 3: tradeoff / instead-of.
        for pat, sig in TRADEOFF_PATTERNS:
            for m in pat.finditer(text):
                if len(candidates) >= self.max_candidates_per_turn:
                    break
                quote = m.group(0).strip()
                ctx = self._window(text, m.start(), m.end())
                candidates.append(
                    Candidate(
                        kind="tradeoff",
                        signal_type=sig,
                        quote=quote,
                        context=ctx,
                        confidence="confirmed",
                        scope=scope,
                        turn_index=turn_index,
                        captured_chars=len(quote),
                    )
                )

        return candidates

    def _window(self, text: str, start: int, end: int) -> str:
        lo = max(0, start - self.context_window)
        hi = min(len(text), end + self.context_window)
        return text[lo:hi].strip()


# ---------------------------------------------------------------------------
# Transcript parsing — Claude Code JSONL format
# ---------------------------------------------------------------------------


def iter_user_turns_from_jsonl(transcript_path: Path) -> Iterable[tuple[int, str, bool]]:
    """Yield (turn_index, user_text, prior_assistant_acted_signal) tuples.

    `prior_assistant_acted` is True when the most recent assistant message
    BEFORE this user turn used tool calls (Edit/Write/Bash/etc), signalling
    that the user is reacting to an action.

    Best-effort: ignores malformed JSONL lines, supports both `message.role`
    + `message.content` and flat `{type:user, content:...}` shapes.
    """
    if not transcript_path or not transcript_path.exists():
        return

    user_turn_idx = -1
    prior_assistant_acted = False
    try:
        text = transcript_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        role = _extract_role(obj)
        content = _extract_text(obj)

        if role == "assistant":
            # Detect tool-use signals (set the flag for the NEXT user turn).
            prior_assistant_acted = _assistant_used_tools(obj)
            continue
        if role == "user":
            user_turn_idx += 1
            yield user_turn_idx, content, prior_assistant_acted
            # Reset after consumption.
            prior_assistant_acted = False


def _extract_role(obj: dict) -> str | None:
    """Pull role from either flat or nested message shapes."""
    if isinstance(obj.get("role"), str):
        return obj["role"]
    msg = obj.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("role"), str):
        return msg["role"]
    # Claude Code event-stream shape carries `type: user|assistant` at top level.
    t = obj.get("type")
    if t in ("user", "assistant"):
        return t
    return None


def _extract_text(obj: dict) -> str:
    """Extract a plain-text content string from a transcript record."""
    msg = obj.get("message", obj)
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("content"), str):
                    parts.append(block["content"])
        return "\n".join(parts)
    return ""


def _assistant_used_tools(obj: dict) -> bool:
    """Heuristic: did the assistant turn carry a tool_use content block?"""
    msg = obj.get("message", obj)
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_call"):
                return True
    return False


# ---------------------------------------------------------------------------
# Top-level API
# ---------------------------------------------------------------------------


def detect_candidates(transcript_path: Path | str | None = None, *, text_turns: list[str] | None = None) -> list[Candidate]:
    """High-level entry point.

    Either `transcript_path` (JSONL on disk) or `text_turns` (plain list of
    user-turn strings) — the latter is the testing surface.
    """
    detector = CorrectionDetector()
    results: list[Candidate] = []

    if text_turns is not None:
        for idx, t in enumerate(text_turns):
            results.extend(detector.scan_turn(t, idx, prior_assistant_acted=False))
        return _dedup(results)

    if transcript_path is None:
        return []

    p = Path(transcript_path) if not isinstance(transcript_path, Path) else transcript_path
    for idx, content, prior in iter_user_turns_from_jsonl(p):
        results.extend(detector.scan_turn(content, idx, prior_assistant_acted=prior))
    return _dedup(results)


def _dedup(candidates: list[Candidate]) -> list[Candidate]:
    """Drop duplicate Candidates by id_hash, keeping the earliest occurrence."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for c in candidates:
        if c.id_hash in seen:
            continue
        seen.add(c.id_hash)
        out.append(c)
    return out
