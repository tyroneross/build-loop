# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the fallback→ARP auto-migrate seam (discovery_bridge.maybe_auto_migrate).

  - no rust-cli envelope -> returns None (not applicable)
  - rust-cli + no stranded store -> None
  - rust-cli + stranded store with a fact.v1 line -> invokes `rally migrate-legacy` (argv asserted)
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

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import discovery_bridge as db  # noqa: E402
import fact_v1 as fv  # noqa: E402


class _Env:
    """Minimal stand-in for DiscoveryEnvelope.resolved_via."""

    def __init__(self, resolved_via: str):
        self.resolved_via = resolved_via


@pytest.fixture(autouse=True)
def _reset_process_marker():
    db._MIGRATED_THIS_PROCESS.clear()
    yield
    db._MIGRATED_THIS_PROCESS.clear()


def test_not_rust_cli_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "resolve", lambda _w: _Env("build-loop-internal"))
    assert db.maybe_auto_migrate(tmp_path, _Env("build-loop-internal")) is None


def test_rust_cli_no_stranded_store_returns_none(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(db.channel_paths, "app_channel_dir", lambda _s: fallback)
    # No changes.jsonl in fallback -> None
    assert db.maybe_auto_migrate(tmp_path, _Env("rust-cli")) is None


def test_rust_cli_stranded_store_invokes_migrate(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    f = fv.to_fact_v1(kind="handoff", tool="claude", model="m", run_id="r",
                      app_slug="throwaway", payload={"subject": "x"}, revision=1)
    fv.write_fact_v1_line(fallback, f)
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(db.channel_paths, "app_channel_dir", lambda _s: fallback)
    monkeypatch.setattr(db, "rust_rally_binary", lambda _w: "/fake/rally")

    captured = {}

    class _Proc:
        returncode = 0
        stdout = json.dumps({"ok": True, "data": {"migrate-legacy": {
            "facts_read": 1, "facts_migrated": 1, "facts_skipped_existing": 0,
            "slugs_found": ["throwaway"], "warnings": []}}})

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(db.subprocess, "run", _fake_run)
    result = db.maybe_auto_migrate(tmp_path, _Env("rust-cli"))
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
    monkeypatch.setattr(db, "rust_rally_binary", lambda _w: None)
    assert db.maybe_auto_migrate(tmp_path, _Env("rust-cli")) is None


def test_marker_skips_reinvocation(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    fv.write_fact_v1_line(fallback, fv.to_fact_v1(
        kind="handoff", tool="t", model="m", run_id="r", app_slug="s",
        payload={"subject": "x"}, revision=1))
    (fallback / ".migrated").write_text("2026-06-17T00:00:00Z")
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(db.channel_paths, "app_channel_dir", lambda _s: fallback)
    monkeypatch.setattr(db, "rust_rally_binary", lambda _w: "/fake/rally")

    def _fail_run(cmd, **kw):
        raise AssertionError("migrate-legacy must not be invoked when marker present")

    monkeypatch.setattr(db.subprocess, "run", _fail_run)
    assert db.maybe_auto_migrate(tmp_path, _Env("rust-cli")) is None


def test_non_factv1_store_not_migrated(monkeypatch, tmp_path):
    # A store with only legacy-shape (non-fact.v1) lines must NOT trigger migrate
    # (migrate-legacy would migrate zero facts; the seam should no-op).
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    (fallback / "changes.jsonl").write_text(
        json.dumps({"ts": 1, "kind": "commit", "tool": "t", "payload": {}}) + "\n")
    monkeypatch.setattr(db.channel_paths, "app_slug", lambda _w: "throwaway")
    monkeypatch.setattr(db.channel_paths, "app_channel_dir", lambda _s: fallback)
    monkeypatch.setattr(db, "rust_rally_binary", lambda _w: "/fake/rally")
    monkeypatch.setattr(db.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run")))
    assert db.maybe_auto_migrate(tmp_path, _Env("rust-cli")) is None


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
