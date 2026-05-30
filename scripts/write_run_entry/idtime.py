#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""idtime.py — timestamp formatting and run-ID computation for write_run_entry."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def iso_basic_utc(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def iso_utc(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compute_run_id(goal: str, now: datetime | None = None) -> str:
    goal_hash = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:8]
    return f"run_{iso_basic_utc(now)}_{goal_hash}"
