# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Staleness computation for captured references.

A reference is stale once ``retrieved_at + refresh_after`` days is on or before
today. These helpers do pure date arithmetic over the reference frontmatter so
the read path (context bootstrap) can flag stale references without re-deriving
horizons. Fail-soft: a reference with missing/garbled dates is reported as
``unknown`` (never stale, never crashes a scan).

Stdlib only.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

# Reuse the writer's lightweight frontmatter parser so we read references the
# same way they are written (no second YAML dialect). Imported lazily inside the
# scan to keep this module import-cheap and dependency-light.


def _parse_date(raw: Any) -> date | None:
    """Parse an ISO date (``YYYY-MM-DD`` or full ISO datetime) into a date.

    Returns None for empty/unparseable input. Never raises.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Take the date portion of a datetime if present.
    s = s.split("T", 1)[0]
    try:
        return date.fromisoformat(s)
    except ValueError:
        # Last resort: try full ISO parse then take .date().
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
        except ValueError:
            return None


def _coerce_days(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def days_until_refresh(
    frontmatter: dict[str, Any], today: date | None = None
) -> int | None:
    """Days remaining until the reference goes stale.

    Positive = fresh with N days to go; zero or negative = stale (overdue by
    ``-N`` days). Returns None when ``retrieved_at`` or ``refresh_after`` is
    missing/unparseable.
    """
    today = today or date.today()
    retrieved = _parse_date(frontmatter.get("retrieved_at"))
    horizon_days = _coerce_days(frontmatter.get("refresh_after"))
    if retrieved is None or horizon_days is None:
        return None
    refresh_on = retrieved.toordinal() + horizon_days
    return refresh_on - today.toordinal()


def is_stale(frontmatter: dict[str, Any], today: date | None = None) -> bool:
    """True iff the reference is past its staleness horizon today.

    A reference with unknown dates is treated as NOT stale (fail-soft — we never
    fabricate a refresh demand from missing data).
    """
    remaining = days_until_refresh(frontmatter, today=today)
    if remaining is None:
        return False
    return remaining <= 0


def scan_reference_lane(
    lane_dir: Path, today: date | None = None
) -> list[dict[str, Any]]:
    """Scan a reference lane directory and return one record per reference file.

    Each record: {file, name, content_class, retrieved_at, refresh_after,
    days_remaining, stale, status}. ``status`` is one of ``"stale"``,
    ``"fresh"``, or ``"unknown"`` (missing dates). Only files matching the
    ``reference-*.md`` / ``reference_*.md`` naming class are scanned. Fail-soft:
    an unreadable file is skipped; the scan never raises.
    """
    today = today or date.today()
    lane = Path(lane_dir)
    if not lane.is_dir():
        return []

    # Lazy import to avoid a hard import cost / cycle at module load.
    import sys

    here = Path(__file__).resolve().parent.parent  # scripts/
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        from memory_writer import _split_frontmatter  # type: ignore
    except Exception:  # noqa: BLE001 — degrade rather than crash the read path
        _split_frontmatter = None  # type: ignore

    records: list[dict[str, Any]] = []
    # Files are named ``<YYYY-MM-DD>-reference-<slug>.md`` (date-prefixed by the
    # canonical writer), so match ``reference`` anywhere, not just at the start.
    # Both ``-reference-`` and ``_reference_`` separator styles are recognized.
    patterns = ("*reference-*.md", "*reference_*.md")
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(lane.glob(pattern)):
            if path in seen:
                continue
            seen.add(path)
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if _split_frontmatter is not None:
                fm, _ = _split_frontmatter(text)
            else:
                fm = {}
            remaining = days_until_refresh(fm, today=today)
            if remaining is None:
                status = "unknown"
                stale = False
            elif remaining <= 0:
                status = "stale"
                stale = True
            else:
                status = "fresh"
                stale = False
            records.append({
                "file": path.name,
                "path": str(path),
                "name": fm.get("name") or path.stem,
                "content_class": fm.get("content_class"),
                "retrieved_at": fm.get("retrieved_at"),
                "refresh_after": fm.get("refresh_after"),
                "days_remaining": remaining,
                "stale": stale,
                "status": status,
            })
    return records
