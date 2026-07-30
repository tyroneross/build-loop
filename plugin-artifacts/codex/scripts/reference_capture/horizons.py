# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Content-class staleness horizons for captured references.

Different kinds of retrieved knowledge age at different rates: an API reference
or a price card goes stale in days, an ecosystem survey holds for a quarter, a
ratified standard for half a year. The horizon is therefore a per-reference
field (``refresh_after`` days) the writer sets — never a single global constant.

``classify_content_class`` infers the class from the topic + source URLs when a
caller does not name one; ``default_refresh_days`` maps a class to its default
horizon. Callers may always override the horizon explicitly.

Pure functions, stdlib only.
"""
from __future__ import annotations

import re

# Default staleness horizon (in days) per content class. Tuned to how fast each
# class actually changes — these are defaults, not locks; any caller can pass an
# explicit ``refresh_after_days`` to override.
CONTENT_CLASS_DEFAULT_DAYS: dict[str, int] = {
    "api-docs": 7,          # method signatures, config keys, endpoints — change fast
    "pricing": 7,           # rate cards, plan tiers, quotas — change fast
    "model-info": 14,       # model IDs, context windows, capabilities
    "library-syntax": 30,   # imports, API shape of a pinned-ish library
    "version-release": 14,  # changelogs, release notes, deprecation timelines
    "ecosystem-survey": 90, # "what's the landscape of X" — moves slowly
    "standard-spec": 180,   # ratified specs / RFCs / regulations — slow
    "general": 30,          # default bucket when nothing more specific matches
}

DEFAULT_CONTENT_CLASS = "general"

# Keyword → class signals, checked in priority order (first match wins). Each
# entry is (class, compiled-regex). Order matters: more specific/faster-aging
# classes are checked before slower, broader ones so an "api pricing" topic
# lands on the faster horizon.
_CLASS_SIGNALS: list[tuple[str, re.Pattern[str]]] = [
    ("pricing", re.compile(r"\b(pricing|price|cost|rate\s*card|plan\s*tier|"
                           r"quota|billing|per\s*(?:m?tok|token|request|seat))\b", re.I)),
    ("api-docs", re.compile(r"\b(api|sdk|endpoint|method\s*signature|"
                           r"config\s*key|cli\s*flag|schema\s*field|reference\s*docs?)\b", re.I)),
    ("model-info", re.compile(r"\b(model\s*id|context\s*window|model\s*card|"
                             r"token\s*limit|gpt-|claude-|gemini-|llama-|qwen)\b", re.I)),
    ("version-release", re.compile(r"\b(changelog|release\s*notes?|deprecat(?:e|ed|ion)|"
                                  r"migration\s*guide|breaking\s*change|version\s*\d)\b", re.I)),
    ("library-syntax", re.compile(r"\b(library|framework|package|import|"
                                 r"usage\s*example|how\s*to\s*use|syntax)\b", re.I)),
    ("standard-spec", re.compile(r"\b(standard|specification|spec\b|rfc\s*\d|"
                                r"regulation|compliance|protocol\s*spec|w3c|ietf|iso\s*\d)\b", re.I)),
    ("ecosystem-survey", re.compile(r"\b(landscape|survey|overview|comparison|"
                                   r"alternatives|ecosystem|state\s*of\s*the|options\s*for)\b", re.I)),
]


def default_refresh_days(content_class: str | None) -> int:
    """Return the default staleness horizon in days for ``content_class``.

    Unknown or empty classes fall back to the ``general`` bucket so the function
    never raises — a horizon is always available.
    """
    if not content_class:
        return CONTENT_CLASS_DEFAULT_DAYS[DEFAULT_CONTENT_CLASS]
    return CONTENT_CLASS_DEFAULT_DAYS.get(
        content_class, CONTENT_CLASS_DEFAULT_DAYS[DEFAULT_CONTENT_CLASS]
    )


def classify_content_class(topic: str = "", source_urls: list[str] | None = None) -> str:
    """Infer a content class from the topic text and source URLs.

    Checks topic + joined URLs against ``_CLASS_SIGNALS`` in priority order and
    returns the first matching class, else ``"general"``. Deterministic and
    side-effect-free.
    """
    haystack = topic or ""
    if source_urls:
        haystack = haystack + " " + " ".join(u for u in source_urls if u)
    for content_class, pattern in _CLASS_SIGNALS:
        if pattern.search(haystack):
            return content_class
    return DEFAULT_CONTENT_CLASS
