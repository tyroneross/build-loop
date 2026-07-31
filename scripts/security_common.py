#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
security_common.py — primitives shared by the deterministic security scanner
and its check modules.

Exists to break the circular import that would otherwise arise from
``security_scan.py`` importing check modules that need ``security_scan``'s
finding constructor. Both sides import from here; neither imports the other.

Stdlib only. No LLM. No network.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SEVERITY_ORDER: dict[str, int] = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

# A confirmed false positive is silenced with `# nosec: <reason>` (Python/shell)
# or `// nosec: <reason>` (JS/TS) on the flagged line. The reason is mandatory by
# convention — a bare `nosec` still matches, but review should reject it.
NOSEC_RE = re.compile(r"(#|//)\s*nosec\s*:", re.IGNORECASE)


def finding(
    severity: str,
    owasp_ids: str,
    file_path: Path,
    line_no: int,
    message: str,
    snippet: str,
    fix: str,
    check_id: str,
) -> dict[str, Any]:
    """Construct one finding record. The single shape every check emits."""
    return {
        "severity": severity,
        "owasp_ids": owasp_ids,
        "file": str(file_path),
        "line": line_no,
        "message": message,
        "snippet": snippet.rstrip(),
        "fix": fix,
        "check_id": check_id,
    }


_DQUOTE_RE = re.compile(r'"[^"\n\\]*(?:\\.[^"\n\\]*)*"')
_SQUOTE_RE = re.compile(r"'[^'\n\\]*(?:\\.[^'\n\\]*)*'")


def strip_string_literals(line: str) -> str:
    """Blank out quoted string bodies so a pattern can't match inside a literal."""
    line = _DQUOTE_RE.sub('""', line)
    line = _SQUOTE_RE.sub("''", line)
    return line


# Files that cannot be reached by any runtime. Framework routers load exact
# filenames (route.ts, handler.py, +server.ts); a sibling with a backup or
# disabled suffix is inert no matter what it contains. Reporting a finding in
# one costs a reviewer the same attention as a live route and buys nothing,
# and these files skew a deploy gate's totals badly because they are usually
# stale copies of code whose live version has already been fixed.
_INERT_SUFFIXES = (
    ".bak",
    ".old",
    ".orig",
    ".disabled",
    ".backup",
    ".save",
    ".tmp",
    ".example",
    ".sample",
    ".template",
)

_INERT_MARKERS = (
    "phase1-backup",
    "integration_example",
    "_archive",
    "node_modules",
)


def is_inert_file(path: Path) -> bool:
    """True when no runtime can load this file, so its contents are not surface.

    Checked before any API check emits. A finding here is never actionable —
    the correct response is deletion, which is a hygiene task, not a security
    gate's business.
    """
    name = path.name.lower()
    if name.endswith(_INERT_SUFFIXES):
        return True
    # Timestamped backups: route.ts.phase1-backup-20251010-232611
    lowered = str(path).lower()
    return any(marker in lowered for marker in _INERT_MARKERS)


def is_api_path(path: Path) -> bool:
    """True when the file sits on a conventional server route/handler path.

    Deliberately broad: an API-only check that misses a route is worse than one
    that reads an extra file, because every check here also requires a positive
    handler match before it emits.
    """
    if is_inert_file(path):
        return False
    parts_lower = [p.lower() for p in path.parts]
    return (
        "api" in parts_lower
        or "functions" in parts_lower
        or "routes" in parts_lower
        or "handlers" in parts_lower
        or "endpoints" in parts_lower
        or "controllers" in parts_lower
        or "server" in parts_lower
        or "trpc" in parts_lower
    )


def first_match_line(lines: list[str], pattern: re.Pattern[str]) -> tuple[int, str]:
    """Return (1-indexed line number, stripped text) of the first pattern hit.

    Falls back to (1, "") when nothing matches, so a caller that already knows a
    file-level match exists still reports a usable location.
    """
    for i, line in enumerate(lines, 1):
        if pattern.search(line):
            return i, line.strip()
    return 1, ""


def suppressed(line: str) -> bool:
    """True when the line carries an inline `nosec:` suppression."""
    return bool(NOSEC_RE.search(line))
