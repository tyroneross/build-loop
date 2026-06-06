# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Root conftest.py — covers both scripts/ and tests/.

Marker auto-skip rules (both probed once per collection pass):

  `live`  — skipped when Ollama (127.0.0.1:11434) is unreachable.
             Load-bearing fix for the full-scope gate hang on live-qwen
             tests (test_stop_hook_integration, etc.).

  `db`    — skipped when psycopg is not installed (.[db] extra absent).
             Makes `pytest scripts/ tests/` collect and run clean on a
             fresh `uv sync` without the optional Postgres extra, honouring
             the contract stated in pyproject.toml:
             "Sync scripts gracefully degrade when these aren't installed;
              tests stub psycopg out entirely."
             PG-requiring tests that are already marked `live` inherit
             the `live` skip; `db` is for tests that need psycopg but
             don't need a live Ollama service.

Both probes use stdlib only (no extra deps).  Timeout for the TCP probe
is 0.5s.
"""
from __future__ import annotations

import socket

import pytest


def _ollama_reachable() -> bool:
    """Fast TCP probe: True if Ollama is listening on 127.0.0.1:11434."""
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=0.5):
            return True
    except OSError:
        return False


def _psycopg_available() -> bool:
    """True if psycopg (the .[db] extra) is importable."""
    try:
        import psycopg  # noqa: F401
        return True
    except ImportError:
        return False


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Auto-skip `live`- and `db`-marked tests based on one-shot probes.

    Both probes run once per collection pass; total overhead is ≤0.5s
    regardless of how many marked tests exist.
    """
    ollama_up = _ollama_reachable()
    psycopg_up = _psycopg_available()

    skip_live = pytest.mark.skip(
        reason="live service (Ollama/qwen on 127.0.0.1:11434) unreachable"
    )
    skip_db = pytest.mark.skip(
        reason="psycopg not installed — install .[db] extra to run Postgres tests"
    )

    for item in items:
        if not ollama_up and item.get_closest_marker("live") is not None:
            item.add_marker(skip_live, append=False)
        if not psycopg_up and item.get_closest_marker("db") is not None:
            item.add_marker(skip_db, append=False)
