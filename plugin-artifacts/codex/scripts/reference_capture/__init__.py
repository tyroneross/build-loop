# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Reference-capture capability: persist web/doc research findings as
date-stamped reference files with per-content-class staleness horizons.

Public API:
  classify_content_class / default_refresh_days  (horizons)
  capture_reference                              (capture)
  is_stale / scan_reference_lane                 (staleness)
"""
from __future__ import annotations

from .horizons import (
    CONTENT_CLASS_DEFAULT_DAYS,
    classify_content_class,
    default_refresh_days,
)
from .capture import capture_reference, build_reference_body
from .staleness import is_stale, scan_reference_lane, days_until_refresh

__all__ = [
    "CONTENT_CLASS_DEFAULT_DAYS",
    "classify_content_class",
    "default_refresh_days",
    "capture_reference",
    "build_reference_body",
    "is_stale",
    "scan_reference_lane",
    "days_until_refresh",
]
