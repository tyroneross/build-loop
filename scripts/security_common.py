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
# or `// nosec: <reason>` (JS/TS) on the flagged line. Two spellings are
# recognized:
#   - `# nosec: <prose>`  — this scanner's own form
#   - `# nosec B608`      — bandit's form, naming a bandit test ID
# The bandit form is accepted because it is what every Python codebase that has
# ever run bandit already writes. Named failure (2026-09-05): two f-strings in
# RossLabs Ambient Agent's Scripts/ledger_composition.py carried `# nosec B608`,
# were HIGH-flagged anyway, and helped hard-block a push whose delta was clean.
#
# WHAT SUPPRESSION ACTUALLY DOES — read this before adding a marker.
# `suppressed()` receives ONLY the line, never a check id, so EITHER spelling
# silences EVERY check on that line, not just the one it names. A bandit ID here
# is a marker, not a scope. Two consequences, both verified live 2026-09-05
# after an earlier version of this comment claimed otherwise:
#   - `AWS_KEY = "AKIA…"  # nosec B608` silences the hardcoded-secret finding,
#     even though B608 is bandit's SQL-injection test.
#   - `# nosec:` with nothing after the colon suppresses too, so the reason is a
#     convention, not something the pattern enforces.
# A bare `# nosec` (no colon, no ID) does NOT suppress — that much is enforced,
# and is pinned by test_bare_nosec_without_colon_does_NOT_suppress.
#
# The ID range is `B[1-7]\d\d` because bandit's own tests live in B1xx–B7xx.
# Accepting any three digits made `# nosec B000` a plausible-looking universal
# bypass that no bandit run would ever have produced.
NOSEC_RE = re.compile(r"(#|//)\s*nosec\b\s*(?::|B[1-7]\d{2}\b)", re.IGNORECASE)


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
    """True when the line carries an inline `nosec: <reason>` / `nosec B###` suppression."""
    return bool(NOSEC_RE.search(line))
