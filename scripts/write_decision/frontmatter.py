#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tiny YAML-subset parser + emitter for decision frontmatter.

Handles only the value shapes this writer emits. Byte-for-byte identical to
the historical flat-module implementation — the parse/emit round trip is the
schema contract that decision files depend on, so any drift here corrupts the
versioned (v2/v3) frontmatter.
"""
from __future__ import annotations

import re
from typing import Any

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_frontmatter(text: str) -> dict | None:
    """Tiny YAML-subset parser. Handles only what this writer emits.

    Supported value shapes:
      key: scalar
      key: 'quoted scalar'
      key: null
      key: [item, 'item with spaces', null]
    """
    m = _FM_RE.match(text)
    if not m:
        return None
    body = m.group(1)
    out: dict[str, Any] = {}
    for line in body.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        out[key] = _parse_yaml_value(val)
    return out


def _split_list_items(inner: str) -> list[str]:
    """Split a bracketed YAML-subset list body on top-level commas.

    Quoted segments (``'…'``) may contain commas; those are preserved. Returns
    the raw (still-trimmed) item strings for ``_parse_yaml_value`` to coerce.
    """
    items: list[str] = []
    # naive split on commas; values may be 'quoted, with comma'
    depth = 0
    cur = ""
    in_quote = False
    for ch in inner:
        if ch == "'" and not in_quote:
            in_quote = True
            cur += ch
        elif ch == "'" and in_quote:
            in_quote = False
            cur += ch
        elif ch == "," and not in_quote and depth == 0:
            items.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        items.append(cur.strip())
    return items


# Sentinel: a scalar coercion that did not apply (so the caller falls through
# to the list / bare-string branches, exactly as the chained ``if``s did).
_NOT_SCALAR = object()


def _parse_yaml_scalar(val: str) -> Any:
    """Coerce the scalar value shapes (null / quoted / bare int).

    Returns ``_NOT_SCALAR`` when none apply — including the case where the
    bare-integer regex matches but ``int()`` fails, preserving the original
    fall-through to the list / bare-string handling.
    """
    if val == "" or val == "null":
        return None
    if val.startswith("'") and val.endswith("'"):
        return val[1:-1]
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]
    # Bare integer (positive or negative). Floats are intentionally NOT coerced
    # — none of the v1/v2/v3 fields are floats and matching ".5" eagerly would
    # collide with date-like or version-like strings.
    if re.match(r"^-?\d+$", val):
        try:
            return int(val)
        except ValueError:
            pass
    return _NOT_SCALAR


def _parse_yaml_value(val: str) -> Any:
    scalar = _parse_yaml_scalar(val)
    if scalar is not _NOT_SCALAR:
        return scalar
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_value(item) for item in _split_list_items(inner)]
    return val


def emit_frontmatter(fm: dict[str, Any]) -> str:
    """Emit the small YAML subset above. Order-preserving (python ≥3.7)."""
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {_yaml_emit_value(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _yaml_emit_value(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[" + ", ".join(_yaml_emit_scalar_for_list(x) for x in v) + "]"
    return _yaml_emit_scalar(v)


def _yaml_emit_scalar(v: Any) -> str:
    s = str(v)
    # Quote when leading char is YAML-special, or value would otherwise look like null/number/bool/list,
    # or contains chars that confuse the tiny parser.
    needs_quote = (
        s == ""
        or s.lower() in {"null", "true", "false", "yes", "no"}
        or s[0].isdigit()
        or any(c in s for c in [":", "#", "[", "]", "{", "}", "'", '"'])
    )
    if needs_quote:
        return "'" + s.replace("'", "''") + "'"
    return s


def _yaml_emit_scalar_for_list(v: Any) -> str:
    if v is None:
        return "null"
    return _yaml_emit_scalar(v)
