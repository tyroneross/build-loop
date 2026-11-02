#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for sync_host_models.py. Temp dirs only; the real taxonomy is never written."""
from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sync_host_models as shm  # noqa: E402

REAL_TAXONOMY = HERE.parent / "references" / "model-taxonomy.json"

FAKE_IDENTITY = "ident-SECRET-7f3a9c"
FAKE_ETAG = "W/\"etag-SECRET-b81d\""
FAKE_ORG = "org-SECRET-uuid-0042"
TOKENISH_FILENAME = "tokprefix-SECRETPREFIX123_catalog.json"
SECRETS = (FAKE_IDENTITY, "etag-SECRET", FAKE_ORG, "SECRETPREFIX123", "identity", "organizationUuid")


def _codex_fixture() -> dict:
    return {
        "fetched_at": "2026-11-01T10:00:00Z",
        "etag": FAKE_ETAG,
        "client_version": "0.163.0",
        "identity": {"account": FAKE_IDENTITY},
        "models": [
            {"slug": "gpt-6.2-sol", "display_name": "GPT-6.2 Sol", "visibility": "list",
             "supported_reasoning_levels": [{"effort": "low", "description": "x"},
                                            {"effort": "high", "description": "y"}]},
            {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol", "visibility": "list",
             "supported_reasoning_levels": [{"effort": "medium", "description": "z"}]},
            {"slug": "gpt-6-sol", "display_name": "GPT-6 Sol", "visibility": "list",
             "supported_reasoning_levels": []},
            {"slug": "gpt-reserve", "display_name": "Reserve", "visibility": "hide",
             "supported_reasoning_levels": []},
        ],
    }


def _claude_fixture() -> dict:
    return {
        "version": 2,
        "fetchedAt": 1793598051587,
        "staleAt": 1793601320729,
        "organizationUuid": FAKE_ORG,
        "catalog": {"surface": "cc", "config": {"id": "x", "models": [
            {"id": "claude-opus-5-6", "name": "Opus 5.6", "short_name": "Opus", "description": "d"},
            {"id": "claude-fable-5-1", "name": "Fable 5.1"},
            {"id": "claude-opus-4-7", "name": "Opus 4.7"},
        ]}},
    }


CURSOR_OUTPUT = """\x1b[1mAvailable models\x1b[0m

grok-4.7-low - Grok 4.7 Low
grok-4.7-high-fast - Grok 4.7 High Fast
cursor-grok-4.5-high - Grok 4.5
gpt-6.1-sol-medium - GPT-6.1 Sol Medium
claude-opus-5-thinking-high - Claude Opus 5 Thinking
zzzjunk - Junk model
Tip: use --model <id>
"""


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="shm-test-"))
        self.out = self.tmp / "host-models"
        self.codex = self.tmp / "codex" / "models_cache.json"
        self.codex.parent.mkdir()
        self.codex.write_text(json.dumps(_codex_fixture()))
        self.claude_dir = self.tmp / "claude-catalog"
        self.claude_dir.mkdir()
        (self.claude_dir / TOKENISH_FILENAME).write_text(json.dumps(_claude_fixture()))
        self.cursor = self._script("cursor-ok", f"cat <<'EOF'\n{CURSOR_OUTPUT}EOF\n")
        self.tax_path = self.tmp / "model-taxonomy.json"
        shutil.copy(REAL_TAXONOMY, self.tax_path)
        self.env = mock.patch.dict(os.environ, {
            shm.DIR_ENV: str(self.out),
            shm.CODEX_CACHE_ENV: str(self.codex),
            shm.CLAUDE_CATALOG_ENV: str(self.claude_dir),
            shm.CURSOR_BIN_ENV: str(self.cursor),
            shm.TRIGGER_ENV: "1",
        })
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _script(self, name: str, body: str) -> Path:
        path = self.tmp / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    def _collect(self) -> dict:
        return shm.collect(taxonomy_path=self.tax_path)


class CollectTests(Fixture):
    def test_codex_source(self) -> None:
        src = shm.collect_codex(self.codex, shm._models(shm.load_taxonomy(self.tax_path)))
        self.assertEqual(src["status"], "ok")
        slugs = {m["slug"]: m for m in src["models"]}
        self.assertEqual(slugs["gpt-6.2-sol"]["efforts"], ["low", "high"])
        self.assertFalse(slugs["gpt-6.2-sol"]["known"])
        self.assertEqual(slugs["gpt-6.1-sol"]["canonical_family_id"], "gpt-6.1-sol")
        self.assertTrue(slugs["gpt-reserve"]["hidden"])
        self.assertEqual(src["fetched_at"], "2026-11-01T10:00:00Z")

    def test_claude_source_hashes_filename(self) -> None:
        src = shm.collect_claude(self.claude_dir, shm._models(shm.load_taxonomy(self.tax_path)))
        self.assertEqual(src["status"], "ok")
        self.assertEqual({m["slug"] for m in src["models"]},
                         {"claude-opus-5-6", "claude-fable-5-1", "claude-opus-4-7"})
        self.assertNotIn("SECRETPREFIX", json.dumps(src))
        self.assertTrue(src["fetched_at"].startswith("2026-"))

    def test_cursor_collapses_fused_slugs(self) -> None:
        src = shm.collect_cursor(str(self.cursor), shm._models(shm.load_taxonomy(self.tax_path)))
        self.assertEqual(src["status"], "ok")
        rows = {m["slug"]: m for m in src["models"]}
        self.assertIn("grok-4.7", rows)
        self.assertEqual(sorted(rows["grok-4.7"]["efforts"]), ["high", "low"])
        self.assertEqual(rows["grok-4.7"]["variants"], ["fast"])
        self.assertEqual(rows["grok-4.7"]["host_slugs"], ["grok-4.7-low", "grok-4.7-high-fast"])
        self.assertIn("grok-4.5", rows)  # cursor- prefix peeled
        self.assertEqual(rows["gpt-6.1-sol"]["canonical_family_id"], "gpt-6.1-sol")
        self.assertEqual(rows["claude-opus-5"]["canonical_family_id"], "opus")
        self.assertNotIn("tip:", rows)

    def test_cursor_effort_spellings_collapse(self) -> None:
        # Forms measured in the real Cursor list on 2026-11-01.
        body = "\n".join(f"{s} - x" for s in (
            "gpt-5.5-extra-high-fast", "gpt-5.5-none", "gpt-5.6-sol-none-fast",
            "claude-opus-5-5-max-fast", "claude-4.6-opus-high-thinking",
            "claude-haiku-5-5-thinking-max", "gemini-3.6-flash-minimal",
        ))
        src = shm.collect_cursor(str(self._script("cursor-efforts", f"cat <<'EOF'\n{body}\nEOF\n")), {})
        rows = {m["slug"]: m for m in src["models"]}
        self.assertEqual(set(rows), {"gpt-5.5", "gpt-5.6-sol", "claude-opus-5-5", "claude-4.6-opus",
                                     "claude-haiku-5-5", "gemini-3.6-flash"})
        self.assertEqual(sorted(rows["gpt-5.5"]["efforts"]), ["none", "xhigh"])

    def test_cursor_runs_on_a_terminal(self) -> None:
        # cursor-agent prints its full list only to a TTY (15 vs 276 lines measured).
        tty = self._script("cursor-tty", "if [ -t 1 ]; then echo 'grok-4.7 - Grok'; else echo 'gpt-5.2 - GPT'; fi\n")
        src = shm.collect_cursor(str(tty), {})
        self.assertEqual([m["slug"] for m in src["models"]], ["grok-4.7"])

    def test_cursor_timeout_is_unavailable_not_empty(self) -> None:
        slow = self._script("cursor-slow", "sleep 30\n")
        start = time.monotonic()
        src = shm.collect_cursor(str(slow), {}, timeout=1)
        self.assertLess(time.monotonic() - start, 6)
        self.assertEqual(src["status"], "unavailable")
        self.assertIn("timed out", src["reason"])

    def test_cursor_failure_and_missing_are_unavailable(self) -> None:
        bad = self._script("cursor-bad", "echo 'grok-4.7 - Grok'; exit 3\n")
        self.assertEqual(shm.collect_cursor(str(bad), {})["status"], "unavailable")
        empty = self._script("cursor-empty", "echo 'nothing useful'\n")
        src = shm.collect_cursor(str(empty), {})
        self.assertEqual((src["status"], src["reason"]), ("unavailable", "no model lines parsed"))
        src = shm.collect_cursor(str(self.tmp / "does-not-exist"), {})
        self.assertEqual(src["status"], "unavailable")

    def test_collect_writes_snapshot_with_unsupported_hosts(self) -> None:
        snap = self._collect()
        path = self.out / shm.SNAPSHOT_NAME
        self.assertTrue(path.exists())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        by_host = {s["host"]: s for s in snap["sources"]}
        self.assertEqual(by_host["gemini"]["status"], "unsupported")
        self.assertEqual(by_host["opencode"]["reason"], "unsupported source (not checked)")
        self.assertEqual(json.loads(path.read_text())["collected_at"], snap["collected_at"])

    def test_first_seen_carries_forward(self) -> None:
        self._collect()
        path = self.out / shm.SNAPSHOT_NAME
        data = json.loads(path.read_text())
        data["sources"][0]["models"][0]["first_seen"] = "2026-01-01"
        path.write_text(json.dumps(data))
        snap = self._collect()
        self.assertEqual(snap["sources"][0]["models"][0]["first_seen"], "2026-01-01")


class DiffTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.report = shm.diff(self._collect(), shm.load_taxonomy(self.tax_path))

    def ids(self, key: str) -> set[str]:
        return {i["id"] if isinstance(i, dict) else i for i in self.report[key]}

    def test_classification(self) -> None:
        self.assertEqual(self.ids("newer_in_family"), {"gpt-6.2-sol"})
        item = self.report["newer_in_family"][0]
        self.assertEqual(item["predecessor"], "gpt-6.1-sol")
        self.assertIn("grok-4.7", self.ids("new_family"))
        self.assertEqual(self.ids("informational"), {"claude-opus-5-6"})
        self.assertIn("zzzjunk", self.ids("unparseable"))
        self.assertIn("zzzjunk", self.report["unknown"])
        older = self.ids("older_unregistered")
        self.assertTrue({"gpt-6-sol", "grok-4.5", "claude-opus-4-7"} <= older)
        self.assertNotIn("gpt-reserve", self.report["unknown"])  # hidden
        known = {k["id"] for k in self.report["known"]}
        self.assertTrue({"gpt-6.1-sol", "claude-fable-5-1", "claude-opus-5"} <= known)

    def test_cursor_unavailable_reported_not_empty(self) -> None:
        with mock.patch.dict(os.environ, {shm.CURSOR_BIN_ENV: str(self.tmp / "nope")}):
            report = shm.diff(self._collect(), shm.load_taxonomy(self.tax_path))
        cursor = next(s for s in report["sources"] if s["host"] == "cursor")
        self.assertEqual(cursor["status"], "unavailable")
        self.assertIn("unavailable", shm.render_report(report))

    def test_family_versions_numeric(self) -> None:
        self.assertGreater(shm.family_of("gpt-6.1-sol")[1], shm.family_of("gpt-6-sol")[1])
        self.assertGreater(shm.family_of("gpt-6-sol")[1], shm.family_of("gpt-5.6-sol")[1])
        self.assertEqual(shm.family_of("claude-haiku-4-5-20251001"), ("claude-haiku", (4, 5)))
        self.assertIsNone(shm.family_of("qwen3-coder-30b"))


class ApplyTests(Fixture):
    def test_apply_adds_unrouted_candidate_on_temp_copy(self) -> None:
        before = json.loads(self.tax_path.read_text())
        real_before = REAL_TAXONOMY.read_bytes()
        self._collect()
        rc = shm.main(["apply", "--in-family", "--taxonomy", str(self.tax_path)])
        self.assertEqual(rc, 0)
        after = json.loads(self.tax_path.read_text())
        entry = after["models"]["gpt-6.2-sol"]
        self.assertEqual(entry["status"], "candidate-unverified")
        self.assertIsNone(entry["released"])
        pred = before["models"]["gpt-6.1-sol"]
        self.assertEqual((entry["segment"], entry["tier"], entry["tags"]),
                         (pred["segment"], pred["tier"], pred["tags"]))
        self.assertIn("gpt-6.2-sol-medium", entry["aliases"])
        self.assertIn("first seen", entry["evidence"].lower())
        # Routing decision: never routed. Preferred + legacy cells are untouched.
        self.assertEqual(after["preferred"], before["preferred"])
        self.assertEqual(after["legacy_registry"], before["legacy_registry"])
        # Entry sits just before its predecessor; nothing else in models changed.
        keys = list(after["models"])
        self.assertEqual(keys.index("gpt-6.2-sol") + 1, keys.index("gpt-6.1-sol"))
        del after["models"]["gpt-6.2-sol"]
        self.assertEqual(after, before)
        self.assertEqual(REAL_TAXONOMY.read_bytes(), real_before)
        # Idempotent: a second apply adds nothing.
        text = self.tax_path.read_text()
        shm.main(["apply", "--in-family", "--taxonomy", str(self.tax_path)])
        self.assertEqual(self.tax_path.read_text(), text)

    def test_round_trip_is_byte_identical(self) -> None:
        raw = REAL_TAXONOMY.read_text(encoding="utf-8")
        self.assertEqual(shm._taxonomy_text(json.loads(raw)), raw)

    def test_dry_run_writes_nothing(self) -> None:
        self._collect()
        text = self.tax_path.read_text()
        shm.main(["apply", "--in-family", "--taxonomy", str(self.tax_path), "--dry-run"])
        self.assertEqual(self.tax_path.read_text(), text)


class TriggerTests(Fixture):
    def test_at_most_once_per_hour(self) -> None:
        calls: list[int] = []
        now = time.time()
        self.assertTrue(shm.maybe_trigger_refresh(now=now, spawn=lambda: calls.append(1)))
        self.assertFalse(shm.maybe_trigger_refresh(now=now + 60, spawn=lambda: calls.append(1)))
        self.assertFalse(shm.maybe_trigger_refresh(now=now + 3500, spawn=lambda: calls.append(1)))
        self.assertTrue(shm.maybe_trigger_refresh(now=now + 3601, spawn=lambda: calls.append(1)))
        self.assertEqual(len(calls), 2)

    def test_fresh_snapshot_does_not_trigger(self) -> None:
        old = time.time() - 100
        os.utime(self.codex, (old, old))
        for p in self.claude_dir.iterdir():
            os.utime(p, (old, old))
        self._collect()
        calls: list[int] = []
        self.assertFalse(shm.maybe_trigger_refresh(spawn=lambda: calls.append(1)))
        # Codex refreshed its cache after the snapshot -> stale.
        os.utime(self.codex, None)
        later = time.time() + 5
        os.utime(self.codex, (later, later))
        self.assertTrue(shm.maybe_trigger_refresh(spawn=lambda: calls.append(1)))
        # Snapshot older than 7 days -> stale even with no source change.
        snap = json.loads((self.out / shm.SNAPSHOT_NAME).read_text())
        self.assertTrue(shm.is_stale(now=shm._parse_iso(snap["collected_at"]) + shm.STALE_AFTER_S + 10))

    def test_never_raises(self) -> None:
        def boom() -> None:
            raise RuntimeError("spawn failed")
        self.assertFalse(shm.maybe_trigger_refresh(spawn=boom))
        blocker = self.tmp / "file-not-dir"
        blocker.write_text("x")
        with mock.patch.dict(os.environ, {shm.DIR_ENV: str(blocker / "sub")}):
            self.assertFalse(shm.maybe_trigger_refresh(spawn=lambda: None))
            self.assertIsNone(shm.pending_notice())
        with mock.patch.dict(os.environ, {shm.TRIGGER_ENV: "0"}):
            self.assertFalse(shm.maybe_trigger_refresh(spawn=lambda: None))

    def test_notice_and_refresh(self) -> None:
        self.assertIsNone(shm.pending_notice())
        self.assertEqual(shm.main(["refresh"]), 0)
        self.assertTrue((self.out / shm.REPORT_MD_NAME).exists())
        # The real taxonomy is used by refresh: gpt-6.2-sol + grok-4.7 => >= 2.
        notice = shm.pending_notice()
        self.assertRegex(notice or "", r"^\d+ newer models found by host sync: see .*report\.md$")

    def test_model_index_resolve_survives_broken_sync(self) -> None:
        import model_index
        with mock.patch.object(shm, "maybe_trigger_refresh", side_effect=RuntimeError("x")):
            self.assertIsNone(model_index._host_sync_notice())


class SecretTests(Fixture):
    def test_no_secret_fields_in_outputs(self) -> None:
        snap = self._collect()
        report = shm.diff(snap, shm.load_taxonomy(self.tax_path))
        shm.write_report(report, self.out / shm.REPORT_MD_NAME)
        blobs = [
            (self.out / shm.SNAPSHOT_NAME).read_text(),
            (self.out / shm.REPORT_MD_NAME).read_text(),
            (self.out / shm.REPORT_JSON_NAME).read_text(),
            json.dumps(snap), json.dumps(report),
        ]
        for blob in blobs:
            for secret in SECRETS:
                self.assertNotIn(secret, blob)


if __name__ == "__main__":
    unittest.main()


def test_trigger_never_spawns_a_real_refresh_under_pytest(tmp_path, monkeypatch):
    """resolve() is called throughout the test suites; without an injected spawn the
    trigger must not read real caches or call Cursor."""
    import sync_host_models as shm
    monkeypatch.setenv("BUILD_LOOP_HOST_MODELS_DIR", str(tmp_path / "hm"))
    monkeypatch.delenv("BUILD_LOOP_HOST_SYNC", raising=False)
    called = []
    monkeypatch.setattr(shm, "_spawn_refresh", lambda: called.append(1))
    assert shm.maybe_trigger_refresh() is False
    assert called == []
    assert not (tmp_path / "hm").exists()
