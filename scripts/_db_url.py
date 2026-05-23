#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Shared Postgres DB-URL resolver. Stdlib-only, import-safe.

Single source of truth for connection-string resolution across the repo's
DB-touching scripts. Deliberately imports only ``os`` and ``pathlib`` so it
is safe to import from modules that must stay stdlib-only at import time
(e.g. ``memory_facade.py``, which cannot import ``db.py`` because ``db.py``
imports ``psycopg`` at module top).

Resolution order (precedence is intentional):
  1. ``$BUILD_LOOP_DATABASE_URL``  — build-loop-namespaced override, wins
     when set so the in-progress env-var rename is forward-compatible.
  2. ``$DATABASE_URL``             — legacy/default var; existing setups
     keep working.
  3. ``~/.config/agent-memory/connection.env`` line ``DATABASE_URL=...``
     — file fallback for machines that configure the DSN there.

Returns ``""`` (never raises) when none are configured. Callers that need
raise-on-missing semantics (e.g. ``db.py:_read_db_url``) wrap the empty
return themselves.
"""
from __future__ import annotations

import os
from pathlib import Path

CONNECTION_ENV = Path(".config") / "agent-memory" / "connection.env"

# Human-readable cause used by non-raising callers when resolution fails.
NO_URL_REASON = (
    "no DB URL (BUILD_LOOP_DATABASE_URL / DATABASE_URL / connection.env all unset)"
)


def resolve_db_url() -> str:
    """Return a DSN per the documented precedence, or ``""`` if none set.

    Never raises. Honors ``$HOME`` (via ``Path.home()``) so test fixtures
    can isolate the ``connection.env`` probe with a temporary HOME.
    """
    url = os.environ.get("BUILD_LOOP_DATABASE_URL", "").strip()
    if url:
        return url

    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url

    conn_env = Path.home() / CONNECTION_ENV
    try:
        if conn_env.exists():
            for line in conn_env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        return ""

    return ""
