# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the fallback→ARP auto-migrate seam (discovery_bridge.maybe_auto_migrate).

  - non-full envelope -> returns None (not applicable)
  - full (repo-local-rally-cli) + no stranded store -> None
  - full + stranded store with a fact.v1 line -> invokes `rally migrate-legacy` (argv asserted)
  - binary absent -> None, no crash
  - per-process marker / .migrated file -> skips re-invocation
  - LOSSLESS ROUND-TRIP (gated on a real rally binary): fact.v1 store -> migrate-legacy ->
    facts_read == facts_migrated + facts_skipped_existing, key fields preserved. Uses a throwaway
    slug + temp HOME so the live ~/.agent-rally-point and the live .rally room are untouched.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import capability as _cap  # noqa: E402
import discovery_bridge as db  # noqa: E402
import fact_v1 as fv  # noqa: E402
import payload_codec as codec  # noqa: E402
from post import post  # noqa: E402


class _Env:
    """Minimal stand-in for DiscoveryEnvelope (resolved_via + capability_level).

    maybe_auto_migrate now gates on capability_level (full = a real binary owns
    the channel), so the stub derives it the same way the real envelope does.
    """

    def __init__(self, resolved_via: str, coordination_unavailable: str | None = None):
        self.resolved_via = resolved_via
        self.coordination_unavailable = coordination_unavailable
        self.raw = {"rally_binary": "/fake/rally"}

    @property
    def capability_level(self) -> str:
        return _cap.level_for_resolved_via(
            self.resolved_via, self.coordination_unavailable
        )


def _legacy_v025_record(*, revision: int = 1, subject: str = "legacy") -> dict:
    """Match the 14-field schema-less shape found in the real v0.25 store."""
    return {
        "ts": 1786694400.125 + revision,
        "kind": "phase",
        "tool": "claude_code",
        "model": "opus",
        "run_id": "legacy-run",
        "app_slug": "throwaway",
        "payload": {"phase": "review", "subject": subject, "nullable": None},
        "revision": revision,
        "producer_name": "build-loop",
        "producer_version": "0.25.0",
        "producer_commit_sha": None,
        "producer_runtime_path": "/opt/build-loop",
        "producer_runtime_surface": "plugin",
        "producer_protocol_version": "1.0",
    }


def _successful_migration_proc(
    kwargs: dict,
    *,
    facts_read: int | None = None,
    facts_migrated: int | None = None,
    facts_skipped_existing: int = 0,
) -> SimpleNamespace:
    bridge_root = Path(kwargs["env"]["HOME"]) / ".agent-rally-point" / "apps"
    dirs = [entry for entry in bridge_root.iterdir() if entry.is_dir()]
    slugs = [entry.name for entry in dirs]
    if facts_read is None:
        facts_read = sum(
            1
            for entry in dirs
            for line in (entry / "changes.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("schema") == fv.FACT_SCHEMA
        )
    if facts_migrated is None:
        facts_migrated = facts_read - facts_skipped_existing
    return SimpleNamespace(
        returncode=0,
        stdout=json.dumps(
            {
                "ok": True,
                "product": "rally",
                "schema": "agent-rally.command.migrate-legacy.v1",
                "data": {
                    "migrate-legacy": {
                        "facts_read": facts_read,
                        "facts_migrated": facts_migrated,
                        "facts_skipped_existing": facts_skipped_existing,
                        "slugs_found": slugs,
                        "warnings": [],
                    }
                },
            }
        ),
    )


@pytest.fixture(autouse=True)
def _reset_process_marker(monkeypatch):
    db._MIGRATED_THIS_PROCESS.clear()
    monkeypatch.delenv("BUILD_LOOP_LEGACY_MIGRATION_SOURCE", raising=False)
    yield
    db._MIGRATED_THIS_PROCESS.clear()


def test_non_full_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "resolve", lambda _w: _Env("build-loop-internal"))
    assert db.maybe_auto_migrate(tmp_path, _Env("build-loop-internal")) is None


def test_migration_receipt_must_cover_every_staged_fact() -> None:
    receipt = {
        "ok": True,
        "product": "rally",
        "schema": "agent-rally.command.migrate-legacy.v1",
        "data": {
            "migrate-legacy": {
                "facts_read": 1,
                "facts_migrated": 1,
                "facts_skipped_existing": 0,
                "slugs_found": ["repo-12345678"],
                "warnings": [],
                "outcome_unknowns": [],
            }
        },
    }

    assert db._migration_result(
        receipt,
        expected_slug="repo-12345678",
        expected_fact_count=2,
    ) is None


def test_full_no_stranded_store_returns_none(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(db.channel_paths, "app_channel_dir", lambda _s: fallback)
    monkeypatch.setattr(db.channel_paths, "fallback_channel_dir", lambda *_a: fallback)
    # No changes.jsonl in fallback -> None
    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is None


def test_full_stranded_store_invokes_migrate(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    f = fv.to_fact_v1(kind="handoff", tool="claude", model="m", run_id="r",
                      app_slug="throwaway", payload={"subject": "x"}, revision=1)
    fv.write_fact_v1_line(fallback, f)
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(db.channel_paths, "app_channel_dir", lambda _s: fallback)
    monkeypatch.setattr(db.channel_paths, "fallback_channel_dir", lambda *_a: fallback)
    monkeypatch.setattr(db.channel_paths, "canonical_workdir", lambda _w: Path("/tmp/throwaway"))
    monkeypatch.setattr(db, "rust_rally_binary", lambda _w: "/fake/rally")

    captured = {}

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _successful_migration_proc(kw)

    monkeypatch.setattr(db.subprocess, "run", _fake_run)
    result = db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli"))
    assert result is not None
    assert result["facts_read"] == 1
    assert captured["cmd"][1:3] == ["migrate-legacy", "--json"]
    assert (fallback / ".migrated").exists()


def test_binary_absent_returns_none_no_crash(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    fv.write_fact_v1_line(fallback, fv.to_fact_v1(
        kind="handoff", tool="t", model="m", run_id="r", app_slug="s",
        payload={"subject": "x"}, revision=1))
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(db.channel_paths, "app_channel_dir", lambda _s: fallback)
    monkeypatch.setattr(db.channel_paths, "fallback_channel_dir", lambda *_a: fallback)
    monkeypatch.setattr(db, "rust_rally_binary", lambda _w: None)
    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is None


def test_marker_skips_reinvocation(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    fv.write_fact_v1_line(fallback, fv.to_fact_v1(
        kind="handoff", tool="t", model="m", run_id="r", app_slug="s",
        payload={"subject": "x"}, revision=1))
    fingerprint = db._store_fingerprint(fallback / "changes.jsonl")
    assert fingerprint is not None
    (fallback / ".migrated").write_text(json.dumps(fingerprint))
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(db.channel_paths, "app_channel_dir", lambda _s: fallback)
    monkeypatch.setattr(db.channel_paths, "fallback_channel_dir", lambda *_a: fallback)
    monkeypatch.setattr(db, "rust_rally_binary", lambda _w: "/fake/rally")

    def _fail_run(cmd, **kw):
        raise AssertionError("migrate-legacy must not be invoked when marker present")

    monkeypatch.setattr(db.subprocess, "run", _fail_run)
    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is None


def test_unchanged_marker_skips_all_full_history_reads(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    fact = fv.to_fact_v1(
        kind="artifact",
        tool="codex",
        model="m",
        run_id="fast-marker",
        app_slug="s",
        payload={"subject": "already migrated"},
        revision=1,
    )
    assert fv.write_fact_v1_line(fallback, fact)
    fingerprint = db._store_fingerprint(fallback / "changes.jsonl")
    assert fingerprint is not None
    assert db._publish_marker_atomic(
        fallback / ".migrated", {**fingerprint, "synced_at": "now"}
    )
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(
        db.channel_paths, "fallback_channel_dir", lambda *_a: fallback
    )
    monkeypatch.setattr(
        db,
        "_backfill_legacy_fact_rows",
        lambda _store: (_ for _ in ()).throw(
            AssertionError("unchanged source must not be backfilled")
        ),
    )
    monkeypatch.setattr(
        db,
        "_store_fingerprint",
        lambda _store: (_ for _ in ()).throw(
            AssertionError("unchanged source must not be hashed")
        ),
    )

    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is None


def test_migration_refuses_symlink_store(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text(
        json.dumps(_legacy_v025_record(), separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (fallback / "changes.jsonl").symlink_to(outside)
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(
        db.channel_paths, "fallback_channel_dir", lambda *_a: fallback
    )
    monkeypatch.setattr(
        db.subprocess,
        "run",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("symlink store must not be migrated")
        ),
    )

    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is None
    assert outside.read_text(encoding="utf-8").startswith("{")


def test_migration_refuses_symlink_marker(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    fact = fv.to_fact_v1(
        kind="artifact",
        tool="codex",
        model="m",
        run_id="unsafe-marker",
        app_slug="s",
        payload={"subject": "do not follow marker"},
        revision=1,
    )
    assert fv.write_fact_v1_line(fallback, fact)
    outside = tmp_path / "outside-marker"
    outside.write_text("untouched", encoding="utf-8")
    (fallback / ".migrated").symlink_to(outside)
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(
        db.channel_paths, "fallback_channel_dir", lambda *_a: fallback
    )
    monkeypatch.setattr(
        db.subprocess,
        "run",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("unsafe marker must refuse migration")
        ),
    )

    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is None
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_oversize_marker_is_rejected_with_bounded_read(tmp_path):
    marker = tmp_path / ".migrated"
    marker.write_text(
        "x" * (db._MAX_MIGRATION_MARKER_BYTES + 1), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="byte ceiling"):
        db._read_regular_json(marker)


def test_source_growth_during_migration_prevents_marker(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    store = fallback / "changes.jsonl"
    first = fv.to_fact_v1(
        kind="artifact",
        tool="codex",
        model="m",
        run_id="growth-race",
        app_slug="s",
        payload={"subject": "first"},
        revision=1,
    )
    second = fv.to_fact_v1(
        kind="artifact",
        tool="codex",
        model="m",
        run_id="growth-race",
        app_slug="s",
        payload={"subject": "second"},
        revision=2,
    )
    assert fv.write_fact_v1_line(fallback, first)
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(
        db.channel_paths, "fallback_channel_dir", lambda *_a: fallback
    )
    monkeypatch.setattr(
        db.channel_paths,
        "canonical_workdir",
        lambda _w: Path("/tmp/throwaway"),
    )

    def migrate_then_grow(**_kwargs):
        assert fv.write_fact_v1_line(fallback, second)
        return {
            "facts_read": 1,
            "facts_migrated": 1,
            "facts_skipped_existing": 0,
            "slugs_found": ["throwaway"],
            "warnings": [],
        }

    monkeypatch.setattr(db, "_migrate_explicit_store", migrate_then_grow)
    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is None
    assert not (fallback / ".migrated").exists()
    assert db._fact_v1_count(store) == 2


def test_atomic_marker_failure_leaves_no_marker_or_temp(monkeypatch, tmp_path):
    marker = tmp_path / ".migrated"
    real_replace = db.os.replace

    def fail_marker_replace(source, target):
        if Path(target) == marker:
            raise OSError("simulated marker publish failure")
        return real_replace(source, target)

    monkeypatch.setattr(db.os, "replace", fail_marker_replace)
    assert not db._publish_marker_atomic(
        marker,
        {
            "sha256": "a" * 64,
            "size": 1,
            "device": 1,
            "inode": 1,
            "mtime_ns": 1,
            "ctime_ns": 1,
        },
    )
    assert not marker.exists()
    assert list(tmp_path.glob("..migrated.*.tmp")) == []


def test_failed_migration_writes_no_marker_and_retries(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    fv.write_fact_v1_line(fallback, fv.to_fact_v1(
        kind="artifact", tool="cursor", model="m", run_id="r", app_slug="s",
        payload={"subject": "offline"}, revision=1))
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(db.channel_paths, "app_channel_dir", lambda _s: fallback)
    monkeypatch.setattr(db.channel_paths, "fallback_channel_dir", lambda *_a: fallback)
    monkeypatch.setattr(db.channel_paths, "canonical_workdir", lambda _w: Path("/tmp/throwaway"))
    monkeypatch.setattr(db, "rust_rally_binary", lambda _w: "/fake/rally")
    calls = []

    class _Proc:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout

    def _fake_run(*_a, **kw):
        calls.append(1)
        if len(calls) == 1:
            return _Proc(1, json.dumps({"ok": False}))
        return _successful_migration_proc(kw)

    monkeypatch.setattr(db.subprocess, "run", _fake_run)
    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is None
    assert not (fallback / ".migrated").exists()
    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is not None
    assert len(calls) == 2
    assert (fallback / ".migrated").exists()


def test_appended_fact_changes_watermark_and_resyncs(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    fv.write_fact_v1_line(fallback, fv.to_fact_v1(
        kind="artifact", tool="codex", model="m", run_id="r", app_slug="s",
        payload={"subject": "first"}, revision=1))
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(db.channel_paths, "app_channel_dir", lambda _s: fallback)
    monkeypatch.setattr(db.channel_paths, "fallback_channel_dir", lambda *_a: fallback)
    monkeypatch.setattr(db.channel_paths, "canonical_workdir", lambda _w: Path("/tmp/throwaway"))
    monkeypatch.setattr(db, "rust_rally_binary", lambda _w: "/fake/rally")
    calls = []

    def _fake_success(*_args, **kwargs):
        calls.append(1)
        return _successful_migration_proc(kwargs)

    monkeypatch.setattr(db.subprocess, "run", _fake_success)
    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is not None
    first_marker = (fallback / ".migrated").read_text()
    fv.write_fact_v1_line(fallback, fv.to_fact_v1(
        kind="artifact", tool="claude_code", model="m", run_id="r", app_slug="s",
        payload={"subject": "second"}, revision=2))
    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is not None
    assert len(calls) == 2
    assert (fallback / ".migrated").read_text() != first_marker


def test_incomplete_receipt_writes_no_marker(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    fv.write_fact_v1_line(fallback, fv.to_fact_v1(
        kind="artifact", tool="codex", model="m", run_id="r", app_slug="s",
        payload={"subject": "x"}, revision=1))
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(db.channel_paths, "app_channel_dir", lambda _s: fallback)
    monkeypatch.setattr(db, "rust_rally_binary", lambda _w: "/fake/rally")

    class _Proc:
        returncode = 0
        stdout = json.dumps({"ok": True, "data": {"migrate-legacy": {
            "facts_read": 2, "facts_migrated": 1, "facts_skipped_existing": 0,
        }}})

    monkeypatch.setattr(db.subprocess, "run", lambda *_a, **_kw: _Proc())
    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is None
    assert not (fallback / ".migrated").exists()


def test_nonstandard_schema_less_store_not_migrated(monkeypatch, tmp_path):
    # Arbitrary schema-less JSON is not the historical Build Loop contract and
    # must not be guessed into a Rally fact.
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    (fallback / "changes.jsonl").write_text(
        json.dumps({"ts": 1, "kind": "commit", "tool": "t", "payload": {}}) + "\n")
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(db.channel_paths, "app_channel_dir", lambda _s: fallback)
    monkeypatch.setattr(db, "rust_rally_binary", lambda _w: "/fake/rally")
    monkeypatch.setattr(db.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run")))
    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is None


def test_v025_legacy_store_backfills_append_only_and_migrates(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    source = _legacy_v025_record()
    store = fallback / "changes.jsonl"
    original = json.dumps(source, separators=(",", ":")) + "\n"
    store.write_text(original, encoding="utf-8")
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(db.channel_paths, "app_channel_dir", lambda _s: fallback)
    monkeypatch.setattr(db.channel_paths, "fallback_channel_dir", lambda *_a: fallback)
    monkeypatch.setattr(db.channel_paths, "canonical_workdir", lambda _w: Path("/tmp/throwaway"))
    monkeypatch.setattr(db, "rust_rally_binary", lambda _w: "/fake/rally")
    calls = []

    def _fake_success(*_args, **kwargs):
        calls.append(1)
        return _successful_migration_proc(kwargs)

    monkeypatch.setattr(db.subprocess, "run", _fake_success)

    result = db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli"))
    assert result is not None
    contents = store.read_text(encoding="utf-8")
    assert contents.startswith(original)
    rows = [json.loads(line) for line in contents.splitlines()]
    assert rows[0] == source
    assert rows[1]["schema"] == fv.FACT_SCHEMA
    event = codec.decode_event(rows[1]["evidence"])
    assert event is not None
    assert event["source_record"] == source
    assert db._store_is_losslessly_migratable(store)

    # Marker + deterministic companion make a retry a read-only no-op.
    snapshot = store.read_bytes()
    assert db.maybe_auto_migrate(tmp_path, _Env("repo-local-rally-cli")) is None
    assert store.read_bytes() == snapshot
    assert calls == [1]


def test_mixed_store_requires_a_companion_for_every_legacy_row(tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    store = fallback / "changes.jsonl"
    first = _legacy_v025_record(revision=1, subject="first")
    second = _legacy_v025_record(revision=2, subject="second")
    normal = fv.to_fact_v1(
        kind="artifact",
        tool="cursor",
        model="cursor-agent",
        run_id="new-run",
        app_slug="throwaway",
        payload={"subject": "native-shape"},
        revision=3,
        created_at="2026-08-14T08:00:00Z",
    )
    store.write_text(
        "".join(
            json.dumps(row, separators=(",", ":")) + "\n"
            for row in (first, second, normal)
        ),
        encoding="utf-8",
    )
    first_companion = db._legacy_fact(first)
    assert first_companion is not None
    fv.write_fact_v1_line(fallback, first_companion)

    assert not db._store_is_losslessly_migratable(store)
    before = store.read_bytes()
    assert db._backfill_legacy_fact_rows(store)
    after = store.read_bytes()
    assert after.startswith(before)
    assert db._store_is_losslessly_migratable(store)

    source_records = []
    for line in store.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("schema") != fv.FACT_SCHEMA:
            continue
        event = codec.decode_event(row.get("evidence"))
        if isinstance(event, dict) and isinstance(event.get("source_record"), dict):
            source_records.append(event["source_record"])
    assert first in source_records
    assert second in source_records


def test_backfill_preserves_source_order_when_event_ids_sort_reverse(tmp_path):
    records = [
        _legacy_v025_record(revision=index, subject=f"order-{index}")
        for index in range(1, 80)
    ]
    pair = None
    for left, right in zip(records, records[1:]):
        left_fact = db._legacy_fact(left)
        right_fact = db._legacy_fact(right)
        assert left_fact is not None and right_fact is not None
        if left_fact["event_id"] > right_fact["event_id"]:
            pair = (left, right, left_fact, right_fact)
            break
    assert pair is not None, "fixture must include reverse-lexical adjacent ids"
    left, right, left_fact, right_fact = pair
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    store = fallback / "changes.jsonl"
    store.write_text(
        "".join(
            json.dumps(record, separators=(",", ":")) + "\n"
            for record in (left, right)
        ),
        encoding="utf-8",
    )

    assert db._backfill_legacy_fact_rows(store)
    companion_ids = [
        row["event_id"]
        for row in map(json.loads, store.read_text(encoding="utf-8").splitlines())
        if row.get("schema") == fv.FACT_SCHEMA
    ]
    assert companion_ids == [left_fact["event_id"], right_fact["event_id"]]


def test_migration_process_guard_is_bounded():
    for index in range(db._MIGRATED_THIS_PROCESS_MAX_ENTRIES + 25):
        db._remember_migrated(f"store:{index}")
    assert len(db._MIGRATED_THIS_PROCESS) == db._MIGRATED_THIS_PROCESS_MAX_ENTRIES


def test_migration_sources_do_not_scan_ambiguous_legacy_basenames(
    monkeypatch, tmp_path
):
    current = tmp_path / "private" / "repo-identity"
    ambiguous = tmp_path / "legacy" / "same-name"
    current.mkdir(parents=True)
    ambiguous.mkdir(parents=True)
    monkeypatch.setattr(
        db.channel_paths, "fallback_channel_dir", lambda *_a: current
    )
    monkeypatch.setattr(db.channel_paths, "LEGACY_APPS_ROOT", ambiguous.parent)

    assert db._migration_source_dirs(tmp_path, "same-name") == [current]


def test_exact_legacy_migration_source_requires_explicit_non_symlink_path(
    monkeypatch, tmp_path
):
    current = tmp_path / "private" / "repo-identity"
    legacy = tmp_path / "legacy" / "same-name"
    current.mkdir(parents=True)
    legacy.mkdir(parents=True)
    monkeypatch.setattr(
        db.channel_paths, "fallback_channel_dir", lambda *_a: current
    )
    monkeypatch.setenv("BUILD_LOOP_LEGACY_MIGRATION_SOURCE", str(legacy))

    assert db._migration_source_dirs(tmp_path, "same-name") == [
        current,
        legacy.resolve(),
    ]

    link = tmp_path / "legacy-link"
    link.symlink_to(legacy, target_is_directory=True)
    monkeypatch.setenv("BUILD_LOOP_LEGACY_MIGRATION_SOURCE", str(link))
    assert db._migration_source_dirs(tmp_path, "same-name") == [current]


def test_legacy_backfill_is_multiprocess_idempotent(tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    store = fallback / "changes.jsonl"
    source_rows = [
        _legacy_v025_record(revision=1, subject="first"),
        _legacy_v025_record(revision=2, subject="second"),
    ]
    store.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in source_rows),
        encoding="utf-8",
    )
    code = (
        "import sys;from pathlib import Path;"
        f"sys.path.insert(0,{str(_HERE)!r});"
        "import discovery_bridge as db;"
        f"raise SystemExit(0 if db._backfill_legacy_fact_rows(Path({str(store)!r})) else 1)"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    results = [proc.communicate(timeout=10) for proc in procs]
    assert [proc.returncode for proc in procs] == [0] * len(procs), results

    facts = [
        row
        for row in map(json.loads, store.read_text(encoding="utf-8").splitlines())
        if row.get("schema") == fv.FACT_SCHEMA
    ]
    assert len(facts) == 2
    assert len({fact["event_id"] for fact in facts}) == 2
    assert db._store_is_losslessly_migratable(store)


def test_large_legacy_backfill_uses_disk_backed_indexes(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    store = fallback / "changes.jsonl"
    with store.open("w", encoding="utf-8") as stream:
        for revision in range(1, 1_501):
            stream.write(
                json.dumps(
                    _legacy_v025_record(
                        revision=revision, subject=f"legacy-{revision}"
                    ),
                    separators=(",", ":"),
                )
                + "\n"
            )

    real_connect = db.sqlite3.connect
    index_paths: list[str] = []

    def tracked_connect(path, *args, **kwargs):
        index_paths.append(str(path))
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(db.sqlite3, "connect", tracked_connect)
    assert db._backfill_legacy_fact_rows(store)
    assert len(index_paths) >= 3
    assert all(path != ":memory:" and path.endswith(".sqlite3") for path in index_paths)
    assert sum(
        1
        for line in store.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("schema") == fv.FACT_SCHEMA
    ) == 1_500


# --------------------------------------------------------------------------
# Lossless round-trip against a REAL rally binary (skipped when absent).
# --------------------------------------------------------------------------

def _rally_binary() -> str | None:
    return shutil.which("rally")


@pytest.mark.skipif(_rally_binary() is None, reason="rally binary not installed")
def test_lossless_round_trip(tmp_path):
    """fact.v1 store -> rally migrate-legacy -> zero loss, key fields preserved.

    Isolated: temp HOME so the live ~/.agent-rally-point is untouched, and a
    throwaway repo basename so the live .rally room is never selected.
    """
    rally = _rally_binary()
    home = tmp_path / "home"
    home.mkdir()
    # A throwaway git repo whose basename is the migration slug.
    repo = tmp_path / "rp-roundtrip-throwaway"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)

    slug = repo.name
    apps = home / ".agent-rally-point" / "apps" / slug
    apps.mkdir(parents=True)

    facts = [
        fv.to_fact_v1(kind="handoff", tool="claude", model="m", run_id="rt1",
                      app_slug=slug, payload={"subject": "first", "to": "codex"}, revision=1),
        fv.to_fact_v1(kind="decision", tool="claude", model="m", run_id="rt2",
                      app_slug=slug, payload={"subject": "second"}, revision=2),
        fv.to_fact_v1(kind="lesson", tool="claude", model="m", run_id="rt3",
                      app_slug=slug, payload={"subject": "third"}, revision=3),
    ]
    for f in facts:
        fv.write_fact_v1_line(apps, f)

    env = dict(os.environ)
    env["HOME"] = str(home)

    proc = subprocess.run(
        [rally, "migrate-legacy", "--json"],
        cwd=str(repo), env=env, capture_output=True, text=True, timeout=20,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    out = json.loads(proc.stdout)
    data = out["data"]["migrate-legacy"]
    fr, fm, fs = data["facts_read"], data["facts_migrated"], data["facts_skipped_existing"]

    # Zero-loss invariant.
    assert fr == fm + fs, f"loss: read={fr} migrated={fm} skipped={fs}"
    assert fr == len(facts), f"expected {len(facts)} facts read, got {fr}"
    assert slug in data["slugs_found"]

    # Idempotency: a second run migrates zero new, skips all by event_id.
    proc2 = subprocess.run(
        [rally, "migrate-legacy", "--json"],
        cwd=str(repo), env=env, capture_output=True, text=True, timeout=20,
    )
    data2 = json.loads(proc2.stdout)["data"]["migrate-legacy"]
    assert data2["facts_read"] == len(facts)
    assert data2["facts_skipped_existing"] == len(facts)
    assert data2["facts_migrated"] == 0


@pytest.mark.skipif(_rally_binary() is None, reason="rally binary not installed")
def test_real_v025_append_only_backfill_round_trip(tmp_path):
    """The real CLI imports a schema-less v0.25 row through its companion fact."""
    rally = _rally_binary()
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "rp-v025-roundtrip"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    slug = repo.name
    apps = home / ".agent-rally-point" / "apps" / slug
    apps.mkdir(parents=True)
    source = _legacy_v025_record()
    source["app_slug"] = slug
    original = json.dumps(source, separators=(",", ":")) + "\n"
    store = apps / "changes.jsonl"
    store.write_text(original, encoding="utf-8")

    assert db._backfill_legacy_fact_rows(store)
    assert store.read_text(encoding="utf-8").startswith(original)
    facts = [
        row
        for row in map(json.loads, store.read_text(encoding="utf-8").splitlines())
        if row.get("schema") == fv.FACT_SCHEMA
    ]
    assert len(facts) == 1
    event = codec.decode_event(facts[0]["evidence"])
    assert event is not None and event["source_record"] == source

    env = dict(os.environ)
    env["HOME"] = str(home)
    proc = subprocess.run(
        [rally, "migrate-legacy", "--json"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)["data"]["migrate-legacy"]
    assert result["facts_read"] == 1
    assert result["facts_migrated"] == 1
    assert result["facts_skipped_existing"] == 0
    assert slug in result["slugs_found"]


@pytest.mark.skipif(_rally_binary() is None, reason="rally binary not installed")
def test_real_build_loop_failover_and_incremental_recovery(monkeypatch, tmp_path):
    """Three hosts spool locally, then Rally imports once and catches later growth."""
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "host-matrix-recovery"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AGENT_RALLY_BINARY", raising=False)
    monkeypatch.delenv("AGENT_RALLY_DISCOVER", raising=False)
    monkeypatch.delenv("BUILD_LOOP_APPS_ROOT", raising=False)
    monkeypatch.delenv("AGENT_RALLY_APPS_ROOT", raising=False)
    monkeypatch.setenv("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", "1")
    db.clear_cache()

    local = db.resolve(repo)
    assert local.backend == "build-loop-local"
    for revision, tool in enumerate(("codex", "claude_code", "cursor"), start=1):
        assert post(
            channel_dir=Path(local.channel_dir),
            kind="artifact",
            tool=tool,
            model=f"{tool}-model",
            run_id="host-matrix",
            app_slug=local.app_slug,
            payload={"subject": f"{tool} offline"},
            workdir=repo,
        ) == revision

    monkeypatch.delenv("BUILD_LOOP_BRIDGE_INTERNAL_ONLY")
    db.clear_cache()
    native = db.resolve(repo)
    assert native.backend == "rally"
    assert native.transport == "rally-cli"
    first = db.maybe_auto_migrate(repo, native)
    assert first is not None
    assert first["facts_read"] == 3
    assert first["facts_migrated"] == 3
    assert first["facts_skipped_existing"] == 0
    assert not (repo / ".rally" / "changes.jsonl").exists()
    assert db.maybe_auto_migrate(repo, native) is None

    monkeypatch.setenv("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", "1")
    db.clear_cache()
    local_again = db.resolve(repo)
    assert post(
        channel_dir=Path(local_again.channel_dir),
        kind="artifact",
        tool="cursor",
        model="cursor-agent",
        run_id="host-matrix",
        app_slug=local_again.app_slug,
        payload={"subject": "cursor follow-up"},
        workdir=repo,
    ) == 4

    monkeypatch.delenv("BUILD_LOOP_BRIDGE_INTERNAL_ONLY")
    db.clear_cache()
    native_again = db.resolve(repo)
    second = db.maybe_auto_migrate(repo, native_again)
    assert second is not None
    assert second["facts_read"] == 4
    assert second["facts_migrated"] == 1
    assert second["facts_skipped_existing"] == 3


@pytest.mark.skipif(_rally_binary() is None, reason="rally binary not installed")
def test_wrong_schema_silently_skipped(tmp_path):
    """SILENT-SKIP CONTRACT TRIPWIRE.

    migrate-legacy SILENTLY skips any JSONL line whose ``schema`` != the upstream
    ``FACT_SCHEMA`` (discovery.rs:712-714: ``if schema != FACT_SCHEMA { continue; }``
    with no warning, and ``facts_read`` NOT incremented). That means a future drift
    between build-loop's emitter constant and the real wire contract = silent data
    loss (facts written but never migrated, no error surfaced).

    This test pins the contract from the REAL binary's perspective: a store whose
    ONLY line carries a deliberately-wrong schema migrates ZERO facts and reads
    ZERO. It pairs with ``fact_v1.write_fact_v1_line`` emitting ``fact_v1.FACT_SCHEMA``
    (now the single source of truth, deduped into changes.py) and the provenance
    drift-detector watching ``lib.rs``: if the emitter ever stops matching the wire
    contract, the round-trip test reads zero facts and this tripwire documents WHY.

    Isolated: temp HOME + throwaway repo basename so the live store/room are untouched.
    """
    rally = _rally_binary()
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "rp-wrongschema-throwaway"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)

    slug = repo.name
    apps = home / ".agent-rally-point" / "apps" / slug
    apps.mkdir(parents=True)

    # A line that is byte-for-byte a valid fact EXCEPT its schema is wrong — exactly
    # the shape a drifted emitter constant would produce.
    good = fv.to_fact_v1(kind="handoff", tool="claude", model="m", run_id="ws1",
                         app_slug=slug, payload={"subject": "wrong-schema"}, revision=1)
    assert good["schema"] == fv.FACT_SCHEMA  # emitter still matches the source of truth
    wrong = dict(good)
    wrong["schema"] = "agent-rally.fact.v0-DRIFTED"
    (apps / "changes.jsonl").write_text(
        json.dumps(wrong, separators=(",", ":")) + "\n", encoding="utf-8")

    env = dict(os.environ)
    env["HOME"] = str(home)
    proc = subprocess.run(
        [rally, "migrate-legacy", "--json"],
        cwd=str(repo), env=env, capture_output=True, text=True, timeout=20,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    data = json.loads(proc.stdout)["data"]["migrate-legacy"]
    # The wrong-schema line is silently skipped: read 0, migrated 0, skipped 0.
    assert data["facts_read"] == 0, (
        f"wrong-schema line was NOT silently skipped (facts_read={data['facts_read']}); "
        "the silent-skip contract this tripwire pins has changed"
    )
    assert data["facts_migrated"] == 0
    assert data["facts_skipped_existing"] == 0
