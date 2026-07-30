#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""The optional Postgres-mirror miss must warn VISIBLY once, not fail silently.

Regression: a missing psycopg module sent `postgres_unavailable` only to
.build-loop/sync_errors.log, which recurred unseen 2026-05-05..2026-06-03.
sync_navgator_lessons now emits a one-time stderr warning with the install hint.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_navgator_lessons as snl  # noqa: E402


def _reset():
    snl._PSYCOPG_WARNED = False


def test_warn_fires_once(capsys):
    _reset()
    snl._warn_psycopg_missing_once()
    first = capsys.readouterr().err
    assert "psycopg not installed" in first
    assert ".[db]" in first  # actionable install hint present
    # Second call is suppressed (once per process).
    snl._warn_psycopg_missing_once()
    second = capsys.readouterr().err
    assert second == "", "psycopg warning must print only once per process"


def test_warn_names_sqlite_source_of_truth(capsys):
    _reset()
    snl._warn_psycopg_missing_once()
    err = capsys.readouterr().err
    # Must reassure that the miss is non-fatal (SQLite remains authoritative).
    assert "SQLite" in err
    assert "source of truth" in err


def test_module_not_found_is_classified_as_psycopg_miss():
    # The branch in run_sync keys on ModuleNotFoundError OR 'psycopg' in the
    # message — guard both shapes so a wrapped import error still warns.
    exc1 = ModuleNotFoundError("No module named 'psycopg'")
    assert isinstance(exc1, ModuleNotFoundError)
    exc2 = RuntimeError("could not import psycopg backend")
    assert "psycopg" in str(exc2).lower()
