#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Probe which fact kinds the resolved ``rally`` binary actually accepts.

``discovery_bridge`` accepts a candidate binary on its *surface* — does it expose
``enter``/``say``/``whoami``? It never asks which KIND values that ``say``
accepts. ``post._native_kind`` then maps a build-loop kind onto a hardcoded
literal set of kinds it *believes* rally supports. When build-loop's belief runs
ahead of the installed binary, ``rally say <kind>`` exits 2 with
``unsupported fact kind`` and the write is rejected after the local ledger append
already succeeded.

A version gate cannot close this. Observed 2026-08-29: the stale PATH install and
the newer sibling build both report ``0.2.5``, differing only in git hash
(``51bd2f9`` vs ``353af12``). Capability has to be probed, not inferred.

This module probes ``<binary> say --help`` once per distinct binary, parses the
kind vocabulary rally itself prints, and lets callers DEMOTE an unsupported kind
to a supported fallback. Demotion is lossless: ``payload_codec.encode_event``
stores the canonical build-loop kind inside the authenticated evidence, so the
native positional is only an indexing projection.

Fail-open by construction. Any probe failure — missing binary, timeout,
unparseable help — returns ``None``, and callers keep today's static behavior.
The probed set may only REMOVE a kind build-loop would have sent; it never adds
one, so no untested kind reaches the wire because of this module.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

try:  # package import
    from . import hook_budget
except ImportError:  # script import
    import hook_budget  # type: ignore

__all__ = ["supported_kinds", "negotiate_kind", "clear_cache"]

# Rally prints the vocabulary inline in its ``say`` usage:
#     KIND    fact kind to post; one of: claim, claim.expired, release, ...
_MARKER = "one of:"
_KIND_RE = re.compile(r"^[a-z][a-z0-9]*(?:[.\-][a-z0-9]+)*$")
# A trustworthy parse names most of rally's vocabulary. A one- or two-token match
# is far more likely to be prose we mis-sliced than a real (tiny) kind set.
_MIN_TRUSTED_KINDS = 3
_CACHE_MAX_ENTRIES = 8

# key: (resolved path, mtime_ns, size) -> frozenset | None
_CACHE: dict[tuple[str, int, int], frozenset[str] | None] = {}


def clear_cache() -> None:
    """Drop the probe cache. Tests own this; production never needs it."""
    _CACHE.clear()


def _cache_key(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _parse_kinds(help_text: str) -> frozenset[str] | None:
    """Return the kind vocabulary rally printed, or None when unparseable."""
    index = help_text.find(_MARKER)
    if index < 0:
        return None
    rest = help_text[index + len(_MARKER) :]

    # The list wraps across indented continuation lines and ends at the first
    # blank line or the next non-indented section header.
    segment: list[str] = []
    for position, line in enumerate(rest.splitlines()):
        if position and (not line.strip() or not line[:1].isspace()):
            break
        segment.append(line.strip())

    kinds: list[str] = []
    for token in " ".join(segment).split(","):
        token = token.strip()
        if not _KIND_RE.fullmatch(token):
            # First token that is not kind-shaped ends the list — trailing prose
            # must not be swallowed as vocabulary.
            break
        kinds.append(token)

    if len(kinds) < _MIN_TRUSTED_KINDS:
        return None
    return frozenset(kinds)


def supported_kinds(binary: str | Path | None) -> frozenset[str] | None:
    """Return the fact kinds ``binary`` accepts, or None when unknown.

    None means "could not determine" — never "supports nothing". Callers must
    treat it as permission to proceed with their static mapping.
    """
    if not binary:
        return None
    path = Path(binary).expanduser()
    key = _cache_key(path)
    if key is None:
        return None
    if key in _CACHE:
        return _CACHE[key]

    result: frozenset[str] | None
    try:
        proc = subprocess.run(
            [str(path), "say", "--help"],
            capture_output=True,
            text=True,
            timeout=hook_budget.inner_timeout_seconds(hook_budget.MARGIN_CHILD),
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        result = None
    else:
        result = _parse_kinds(f"{proc.stdout}\n{proc.stderr}")

    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        _CACHE.clear()
    _CACHE[key] = result
    return result


def negotiate_kind(
    native_kind: str,
    binary: str | Path | None,
    *,
    fallback: str = "artifact",
) -> tuple[str, str | None]:
    """Return ``(kind_to_send, degraded_reason)`` for one native post.

    ``degraded_reason`` is None when nothing changed. When the resolved binary
    does not know ``native_kind`` but does know ``fallback``, the fallback is
    returned with a reason the caller records — a demotion must be visible, not
    silent. When the binary knows neither, the original kind is returned so
    rally's own error text stays the diagnostic rather than a guess of ours.
    """
    kinds = supported_kinds(binary)
    if kinds is None or native_kind in kinds:
        return native_kind, None
    if fallback not in kinds:
        return native_kind, None
    return (
        fallback,
        (
            f"resolved rally binary does not support fact kind "
            f"{native_kind!r}; projected as {fallback!r} "
            f"(canonical kind preserved in event evidence)"
        ),
    )
