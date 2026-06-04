#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""intent_confidence.py — score whether a build-loop goal text is concrete enough
to skip intent-exploration, or ambiguous enough to warrant a brief exploration pass.

Default verdict is ``high`` (auto-execute, no exploration). The script flips to
``medium`` or ``low`` ONLY when at least one explicit ambiguity signal fires.
This keeps the auto-execute fast path free of friction; intent-explorer fires
only on genuinely ambiguous or creative-open goals.

CLI::

    python3 scripts/intent_confidence.py \\
        --goal "<text>" \\
        [--workdir <path>] \\
        --json

Output JSON::

    {
      "confidence":     "high" | "medium" | "low",
      "signals":        [str, ...],   # explicit ambiguity signals that fired
      "should_explore": bool,         # true iff confidence in {"medium", "low"}
      "reason":         str           # one-line summary
    }

Exit code is always 0 (advisory script; never blocks).

Detection rules (each fires at most once):

- ``short_goal``                : tokenized goal has fewer than 8 words
- ``vague_verb``                : leading verb is one of {explore, figure, see if,
                                  play with, look into, think about, mess with,
                                  noodle on, kick around}
- ``branching_or``              : goal contains " or " between two candidate paths
- ``question_mark``             : "?" present in goal
- ``hedge_phrase``              : "something like", "kind of", "some kind of",
                                  "sort of", "maybe", "not sure"
- ``creative_open``             : "brainstorm", "design from scratch", "greenfield",
                                  "open-ended", "play around"
- ``no_deliverable_noun``       : goal has none of {file, function, class, page,
                                  endpoint, route, script, test, skill, agent,
                                  command, hook, config, package, dependency,
                                  manifest, schema, table, field, model, plugin,
                                  bug, error, typo, version, line, commit}

A goal scores ``low`` when 3+ signals fire, ``medium`` on 1-2, ``high`` on 0.
The default is biased toward ``high`` so the orchestrator's auto-execute path
stays the common case.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VAGUE_VERB_PATTERNS = (
    r"\bexplore\b",
    r"\bfigure\s+out\b",
    r"\bsee\s+if\b",
    r"\bplay\s+with\b",
    r"\blook\s+into\b",
    r"\bthink\s+about\b",
    r"\bmess\s+with\b",
    r"\bnoodle\s+on\b",
    r"\bkick\s+around\b",
)

HEDGE_PATTERNS = (
    r"\bsomething\s+like\b",
    r"\bkind\s+of\b",
    r"\bsome\s+kind\s+of\b",
    r"\bsort\s+of\b",
    r"\bmaybe\b",
    r"\bnot\s+sure\b",
)

CREATIVE_OPEN_PATTERNS = (
    r"\bbrainstorm\b",
    r"\bdesign\s+from\s+scratch\b",
    r"\bgreenfield\b",
    r"\bopen-?ended\b",
    r"\bplay\s+around\b",
)

# Concrete deliverable nouns — presence of ANY one signals concreteness.
# Kept broad but specific; matched as whole words case-insensitive.
DELIVERABLE_NOUNS = frozenset((
    "file", "function", "class", "page", "endpoint", "route", "script", "test",
    "skill", "agent", "command", "hook", "config", "package", "dependency",
    "manifest", "schema", "table", "field", "model", "plugin", "bug", "error",
    "typo", "version", "line", "commit", "method", "import", "module", "type",
    "interface", "component", "header", "footer", "button", "form", "query",
    "migration", "branch", "remote", "tag", "release", "deploy", "build",
))

_WORD = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_-]*\b")


def _fire_pattern(goal: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, goal, re.IGNORECASE) for p in patterns)


def _has_deliverable_noun(goal: str) -> bool:
    tokens = {tok.lower() for tok in _WORD.findall(goal)}
    return bool(tokens & DELIVERABLE_NOUNS)


def _has_explicit_path(goal: str) -> bool:
    """A file path, function reference, or named symbol is strong concreteness signal."""
    # foo/bar.py, foo.bar(), Foo.bar, /abs/path
    return bool(
        re.search(r"[A-Za-z0-9_./-]+\.(py|ts|tsx|js|jsx|json|md|sh|toml|yaml|yml)\b", goal)
        or re.search(r"\b[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*\b", goal)
        or re.search(r"\b[a-zA-Z_][a-zA-Z0-9_]*\(\)", goal)
        or "/" in goal and not goal.strip().startswith("/")
    )


def score(goal: str) -> dict:
    """Score a goal string. Returns the public JSON envelope (sans `should_explore`).

    Empty-goal edge case: returns confidence=low with reason='empty_goal'. The
    orchestrator should always populate goal before calling, but we treat the
    null case as low confidence rather than crashing.
    """
    g = (goal or "").strip()
    if not g:
        return {
            "confidence": "low",
            "signals": ["empty_goal"],
            "reason": "no goal text provided",
        }

    signals: list[str] = []
    tokens = _WORD.findall(g)
    has_deliverable = _has_deliverable_noun(g)
    has_path = _has_explicit_path(g)

    # short_goal fires only when the goal is BOTH short AND lacks any
    # concreteness anchor (path or deliverable noun). "Fix typo in README.md
    # line 47" is short but anchored; "fix it" is short and unanchored.
    if len(tokens) < 8 and not (has_deliverable or has_path):
        signals.append("short_goal")

    if _fire_pattern(g, VAGUE_VERB_PATTERNS):
        signals.append("vague_verb")

    # branching_or fires only when " or " appears between substantive tokens
    # ("X or Y") not e.g. "X, or do Y after"
    if re.search(r"\b\w+\s+or\s+\w+\b", g, re.IGNORECASE):
        signals.append("branching_or")

    if "?" in g:
        signals.append("question_mark")

    if _fire_pattern(g, HEDGE_PATTERNS):
        signals.append("hedge_phrase")

    if _fire_pattern(g, CREATIVE_OPEN_PATTERNS):
        signals.append("creative_open")

    # no_deliverable_noun fires only when explicit-path is ALSO absent —
    # a file path or `foo.bar` reference counts as concrete even without a
    # deliverable noun in the vocabulary list.
    if not has_deliverable and not has_path:
        signals.append("no_deliverable_noun")

    n = len(signals)
    if n >= 3:
        confidence = "low"
        reason = f"{n} ambiguity signals: {', '.join(signals)}"
    elif n >= 1:
        confidence = "medium"
        reason = f"{n} ambiguity signal(s): {', '.join(signals)}"
    else:
        confidence = "high"
        reason = "no ambiguity signals; concrete goal"

    return {"confidence": confidence, "signals": signals, "reason": reason}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--goal", required=True, help="goal text to score")
    ap.add_argument("--workdir", default=".", help="(reserved; not currently used)")
    ap.add_argument("--json", action="store_true", help="emit JSON envelope")
    args = ap.parse_args()

    result = score(args.goal)
    result["should_explore"] = result["confidence"] in {"medium", "low"}

    if args.json:
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(
            f"{result['confidence']} (should_explore={result['should_explore']}): "
            f"{result['reason']}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
