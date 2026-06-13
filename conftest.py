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
import sys

import pytest


# Modules that some fixtures intentionally ``del sys.modules[...]`` so a new
# env var (BUILD_LOOP_MEMORY_ROOT / _STORE_ROOT) takes effect on re-import
# (e.g. tests/test_migrate_project_memory.py, tests/test_memory_consolidation_pr1.py).
# Those deletions are NOT restored by ``monkeypatch`` — monkeypatch tracks env
# and attributes, never ``sys.modules`` membership. The leak corrupts module
# IDENTITY for any LATER test that holds a module-global reference or calls
# ``importlib.reload`` on one of these (the symptom: scripts/test_memory_writer.py
# P2 tests + scripts/semantic_index/test_hybrid.py pass in isolation but fail in
# the full run with "module memory_writer not in sys.modules"). Restoring these
# entries after every test makes the deletion self-contained — one source of
# truth instead of patching each polluting fixture.
_SHARED_MUTABLE_MODULES = (
    "_paths",
    "memory_writer",
    "memory_facade",
    "project_resolver",
    "audit_memory_invocation",
    "semantic_index",
    "recall",
)


@pytest.fixture(autouse=True)
def _restore_shared_module_identity():
    """Snapshot + restore ``sys.modules`` for shared memory modules per test.

    Cheap: captures at most a handful of dict entries. If a test (or its
    fixtures) deletes or re-imports one of these, the original object is put
    back so the next test sees a consistent module identity. Modules absent
    before the test are removed again if a test imported them.
    """
    before = {name: sys.modules.get(name) for name in _SHARED_MUTABLE_MODULES}
    try:
        yield
    finally:
        for name, original in before.items():
            current = sys.modules.get(name)
            if original is None:
                # Was absent before; drop anything the test imported so a later
                # test re-imports fresh rather than inheriting test-scoped env.
                if current is not None:
                    sys.modules.pop(name, None)
            elif current is not original:
                # Deleted or replaced during the test — restore the original.
                sys.modules[name] = original


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


def _postgres_reachable() -> bool:
    """Fast TCP probe: True if a Postgres server is reachable per the resolved DSN.

    SQLite is the default semantic backend (see ``scripts/semantic_index/``);
    Postgres is an optional fallback. A ``db``-marked test needs BOTH psycopg
    AND a live server — so a machine with psycopg installed but no PG running
    (or no DSN configured) must skip, not error. Resolves the DSN via the
    stdlib-only ``_db_url.resolve_db_url`` and probes host:port (default
    127.0.0.1:5432). Timeout 0.5s; never raises.
    """
    import sys
    from pathlib import Path
    from urllib.parse import urlparse

    scripts_dir = Path(__file__).resolve().parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from _db_url import resolve_db_url  # type: ignore
        dsn = resolve_db_url()
    except Exception:
        return False
    if not dsn:
        return False  # no DB configured → treat as unreachable (skip)
    host, port = "127.0.0.1", 5432
    try:
        parsed = urlparse(dsn)
        host = parsed.hostname or host
        port = parsed.port or port
    except Exception:
        pass
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
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
    # A `db`-marked test needs BOTH psycopg AND a live Postgres server. Postgres
    # is an optional fallback backend (SQLite is the default), so when either is
    # absent the test skips rather than erroring.
    db_up = _psycopg_available() and _postgres_reachable()

    skip_live = pytest.mark.skip(
        reason="live service (Ollama/qwen on 127.0.0.1:11434) unreachable"
    )
    skip_db = pytest.mark.skip(
        reason="Postgres backend unavailable (psycopg missing or no server reachable); "
        "SQLite is the default — install .[db] + run Postgres to exercise the fallback"
    )

    for item in items:
        if not ollama_up and item.get_closest_marker("live") is not None:
            item.add_marker(skip_live, append=False)
        if not db_up and item.get_closest_marker("db") is not None:
            item.add_marker(skip_db, append=False)
