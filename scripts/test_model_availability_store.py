#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for model_availability_store.py — TTL expiry, legacy self-heal, prune."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import model_availability_store as store  # noqa: E402


def _write(workdir: Path, data: dict) -> None:
    bl = workdir / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    (bl / "model-availability.json").write_text(json.dumps(data), encoding="utf-8")


def _read(workdir: Path) -> dict:
    p = workdir / ".build-loop" / "model-availability.json"
    return json.loads(p.read_text()) if p.exists() else {}


class TtlResolutionTests(unittest.TestCase):
    def test_default_when_nothing_set(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(store.resolve_ttl(Path(td)), store.DEFAULT_TTL_SECONDS)

    def test_explicit_wins(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(store.resolve_ttl(Path(td), explicit=42), 42)

    def test_env_over_config_and_default(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as td:
            _config(Path(td), 100)
            old = os.environ.get(store.ENV_TTL)
            os.environ[store.ENV_TTL] = "55"
            try:
                self.assertEqual(store.resolve_ttl(Path(td)), 55)
            finally:
                if old is None:
                    os.environ.pop(store.ENV_TTL, None)
                else:
                    os.environ[store.ENV_TTL] = old

    def test_config_over_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _config(Path(td), 123)
            self.assertEqual(store.resolve_ttl(Path(td)), 123)

    def test_non_positive_falls_through(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                store.resolve_ttl(Path(td), explicit=0), store.DEFAULT_TTL_SECONDS
            )


class ExpiryTests(unittest.TestCase):
    def test_object_within_ttl_is_live(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write(Path(td), {"unavailable": [
                {"id": "fable", "recorded_at": 1000.0, "ttl": 100}
            ]})
            live = store.prune_on_read(Path(td), at=1050.0)  # 50s in, ttl 100
            self.assertEqual(live, {"fable"})

    def test_object_past_ttl_is_expired_and_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write(Path(td), {"unavailable": [
                {"id": "fable", "recorded_at": 1000.0, "ttl": 100}
            ], "hostProviders": ["anthropic"]})
            live = store.prune_on_read(Path(td), at=1200.0)  # 200s in, ttl 100
            self.assertEqual(live, set())
            after = _read(Path(td))
            self.assertEqual(after["unavailable"], [])  # pruned from disk
            self.assertEqual(after["hostProviders"], ["anthropic"])  # preserved

    def test_legacy_bare_string_treated_expired_and_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write(Path(td), {"unavailable": ["fable"]})
            live = store.prune_on_read(Path(td))
            self.assertEqual(live, set())
            self.assertEqual(_read(Path(td))["unavailable"], [])

    def test_object_missing_recorded_at_is_expired(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write(Path(td), {"unavailable": [{"id": "fable", "ttl": 100}]})
            self.assertEqual(store.prune_on_read(Path(td)), set())

    def test_mixed_records_only_live_kept(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write(Path(td), {"unavailable": [
                "legacy-down",  # legacy -> expired
                {"id": "still-down", "recorded_at": 1000.0, "ttl": 1000},  # live
                {"id": "expired", "recorded_at": 1000.0, "ttl": 10},  # expired
            ]})
            live = store.prune_on_read(Path(td), at=1500.0)
            self.assertEqual(live, {"still-down"})
            kept_ids = {store._record_id(r) for r in _read(Path(td))["unavailable"]}
            self.assertEqual(kept_ids, {"still-down"})

    def test_no_change_no_rewrite_needed(self) -> None:
        # All live -> nothing pruned -> changed False (store untouched).
        with tempfile.TemporaryDirectory() as td:
            _write(Path(td), {"unavailable": [
                {"id": "fable", "recorded_at": 1000.0, "ttl": 1000}
            ]})
            _live, _data, changed = store.live_unavailable(Path(td), at=1100.0)
            self.assertFalse(changed)

    def test_missing_file_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(store.prune_on_read(Path(td)), set())


def _config(workdir: Path, ttl: int) -> None:
    bl = workdir / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    (bl / "config.json").write_text(json.dumps({"outageTtlSeconds": ttl}), encoding="utf-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
