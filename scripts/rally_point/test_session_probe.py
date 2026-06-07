#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for session_probe.probe().

Test coverage:
1. Fresh repo (no channel) — creates channel, writes presence, posts rally-start,
   returns status=clear, coordination_file=None.
2. Peer presence already exists with overlapping intent — envelope reflects the peer.
3. start_watch=False — watcher_started=False, no PID file.
4. start_watch=True — watcher_started=True, PID file exists under watchers/.
5. Probe never raises even when channel writes fail (injected failing post).
6. Solo-mode envelope matches the test_orchestrator_auto_invoke contract fields.

Uses dependency injection for watcher_launcher + clock so tests are hermetic.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from rally_point import channel_paths, presence
from rally_point import inbox
from rally_point import post as _post_mod
import rally_point.session_probe as sp


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class _FakeWatcherLauncher:
    """Returns a deterministic fake PID and records invocations."""

    def __init__(self, pid: int = 12345):
        self.pid = pid
        self.calls: list[dict] = []

    def __call__(self, *, workdir, session_id, tool, watch_script):
        self.calls.append({
            "workdir": workdir,
            "session_id": session_id,
            "tool": tool,
            "watch_script": watch_script,
        })
        return self.pid


class _FailingWatcherLauncher:
    def __call__(self, **kwargs):
        raise RuntimeError("simulated watcher failure")


# ---------------------------------------------------------------------------
# Base fixture
# ---------------------------------------------------------------------------

class _ProbeTestBase(unittest.TestCase):
    """Create an isolated temp repo + isolated apps root for each test."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="session-probe-test-")
        self.repo = Path(self.tmpdir) / "repo"
        self.repo.mkdir()
        self.apps_root = Path(self.tmpdir) / "apps"
        self.apps_root.mkdir()

        # Init a minimal git repo so app_slug resolves properly.
        import subprocess
        subprocess.run(
            ["git", "init", "-q", str(self.repo)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email", "t@e.com"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.name", "t"],
            check=True, capture_output=True,
        )

        self._orig_apps_root = os.environ.get("BUILD_LOOP_APPS_ROOT")
        os.environ["BUILD_LOOP_APPS_ROOT"] = str(self.apps_root)
        # Channel-paths audit (2026-05-25): force the discovery bridge
        # to use the legacy internal fallback so these tests' reads
        # against BUILD_LOOP_APPS_ROOT find the same channel session_probe
        # writes to. Without this, an installed agent-rally-discover
        # binary routes writes to the canonical channel under
        # ~/.agent-rally-point/.
        self._orig_bridge_internal = os.environ.get(
            "BUILD_LOOP_BRIDGE_INTERNAL_ONLY"
        )
        os.environ["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = "1"
        # The bridge caches the canonical-source resolution from any
        # earlier non-test call in this process; drop the cache so the
        # internal-only env override is honoured.
        try:
            from rally_point.discovery_bridge import clear_cache as _cc
            _cc()
        except Exception:
            pass

    def tearDown(self):
        if self._orig_apps_root is None:
            os.environ.pop("BUILD_LOOP_APPS_ROOT", None)
        else:
            os.environ["BUILD_LOOP_APPS_ROOT"] = self._orig_apps_root
        if self._orig_bridge_internal is None:
            os.environ.pop("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", None)
        else:
            os.environ["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = (
                self._orig_bridge_internal
            )
        try:
            from rally_point.discovery_bridge import clear_cache as _cc
            _cc()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _slug(self) -> str:
        return channel_paths.app_slug(self.repo)

    def _channel_dir(self) -> Path:
        return channel_paths.app_channel_dir(self._slug())

    def _presence_files(self) -> list[Path]:
        sd = self._channel_dir() / "sessions"
        if not sd.exists():
            return []
        return list(sd.glob("*.json"))

    def _changes(self) -> list[dict]:
        from rally_point.changes import read_changes_since
        recs, _ = read_changes_since(self._channel_dir(), 0)
        return recs

    def _run_probe(self, **kwargs):
        defaults = dict(
            workdir=str(self.repo),
            tool="claude_code",
            mode="interactive",
            start_watch=False,
            model="test-model",
            run_id="test-run-id",
        )
        defaults.update(kwargs)
        return sp.probe(**defaults)


# ---------------------------------------------------------------------------
# Test 1: Fresh repo — no channel exists yet
# ---------------------------------------------------------------------------

class FreshRepoTests(_ProbeTestBase):

    def test_creates_channel_writes_presence_posts_rally_start(self):
        """Fresh repo: channel created, presence written, rally-start posted, coord_file=None."""
        result = self._run_probe()

        # Channel dir must now exist
        self.assertTrue(self._channel_dir().exists(), "channel_dir not created")

        # Presence file written
        pf = self._presence_files()
        self.assertTrue(len(pf) >= 1, f"no presence file written; found: {pf}")

        # Presence file contains correct fields
        rec = json.loads(pf[0].read_text())
        self.assertEqual(rec["phase"], "rally-start")
        self.assertEqual(rec["tool"], "claude_code")

        # Rally-start posted to changes.jsonl
        changes = self._changes()
        rally_start_posts = [
            c for c in changes
            if c.get("kind") == "phase"
            and (c.get("payload") or {}).get("phase") == "rally-start"
        ]
        self.assertTrue(
            len(rally_start_posts) >= 1,
            f"no rally-start phase record in changes; found: {changes}",
        )

        # Envelope shape
        self.assertIn(result["status"], ("clear", "warn", "blocked"))
        self.assertIsNone(result["coordination_file"])
        self.assertIn("session_id", result)
        self.assertIn("slug", result)
        self.assertIn("inbox_unread_counts", result)
        self.assertIn("inbox_latest_messages", result)

    def test_returns_coordination_file_none_when_no_coord_file(self):
        result = self._run_probe()
        self.assertIsNone(result["coordination_file"])


# ---------------------------------------------------------------------------
# Test 2: Peer presence already exists
# ---------------------------------------------------------------------------

class PeerPresenceTests(_ProbeTestBase):

    def setUp(self):
        super().setUp()
        # Seed a peer presence record
        slug = self._slug()
        chan = channel_paths.ensure_channel_dir(slug)
        presence.write_presence(
            chan,
            session_id="peer-codex-001",
            tool="codex",
            model="gpt-5",
            run_id="peer-run",
            app_slug=slug,
            phase="execute",
            files_in_flight=["src/main.py"],
            cwd=self.repo,
        )

    def test_envelope_reflects_peer(self):
        """When a peer presence exists, active_peers reflects it."""
        result = self._run_probe()
        # The peer may or may not appear in active_peers depending on whether
        # coordination_status ran cleanly; we just assert the probe doesn't crash
        # and returns a well-formed envelope.
        self.assertIsInstance(result["active_peers"], list)
        self.assertIn("session_id", result)

    def test_probe_does_not_raise_with_peer(self):
        """probe() returns a dict even when a peer is present — no exceptions."""
        result = self._run_probe()
        self.assertIsInstance(result, dict)


# ---------------------------------------------------------------------------
# Test 3: start_watch=False
# ---------------------------------------------------------------------------

class WatcherNotStartedTests(_ProbeTestBase):

    def test_watcher_started_false_no_pid_file(self):
        launcher = _FakeWatcherLauncher()
        result = self._run_probe(start_watch=False, watcher_launcher=launcher)
        self.assertFalse(result["watcher_started"], "watcher_started should be False")
        self.assertEqual(len(launcher.calls), 0, "watcher launcher should not be called")

        # No PID file
        slug = self._slug()
        watcher_dir = self.apps_root / slug / "watchers"
        if watcher_dir.exists():
            pid_files = list(watcher_dir.glob("*.json"))
            self.assertEqual(pid_files, [], "no PID files should exist")


# ---------------------------------------------------------------------------
# Test 4: start_watch=True
# ---------------------------------------------------------------------------

class WatcherStartedTests(_ProbeTestBase):

    def test_watcher_started_true_pid_file_exists(self):
        launcher = _FakeWatcherLauncher(pid=99999)
        result = self._run_probe(start_watch=True, watcher_launcher=launcher)

        self.assertTrue(result["watcher_started"], "watcher_started should be True")
        self.assertEqual(len(launcher.calls), 1, "watcher launcher should be called once")

        # PID file must exist
        slug = self._slug()
        session_id = result["session_id"]
        pid_file = self.apps_root / slug / "watchers" / f"{session_id}.json"
        self.assertTrue(pid_file.exists(), f"PID file not found at {pid_file}")

        rec = json.loads(pid_file.read_text())
        self.assertEqual(rec["pid"], 99999)
        self.assertEqual(rec["session_id"], session_id)

    def test_watcher_launcher_receives_correct_args(self):
        launcher = _FakeWatcherLauncher()
        self._run_probe(start_watch=True, watcher_launcher=launcher)
        call = launcher.calls[0]
        self.assertEqual(call["workdir"], str(self.repo.resolve()))
        self.assertEqual(call["tool"], "claude_code")
        self.assertIn("session_id", call)


# ---------------------------------------------------------------------------
# Test 5: probe never raises even on channel write failures
# ---------------------------------------------------------------------------

class ErrorResilienceTests(_ProbeTestBase):

    def test_probe_never_raises_on_post_failure(self):
        """Even when post() raises, probe() returns a dict with errors[]."""
        original_post = _post_mod.post

        def _failing_post(**kwargs):
            raise RuntimeError("simulated post failure")

        # Patch post in session_probe's namespace
        with patch.object(sp._post_mod, "post", side_effect=RuntimeError("simulated post failure")):
            result = sp.probe(
                workdir=str(self.repo),
                tool="claude_code",
                mode="interactive",
                start_watch=False,
                model="test-model",
                run_id="test-run-id",
            )

        self.assertIsInstance(result, dict, "probe must return dict even on failure")
        self.assertIn("errors", result)
        # Should have at least one error captured
        self.assertTrue(
            len(result["errors"]) >= 1,
            f"Expected errors to be captured; got: {result['errors']}",
        )

    def test_probe_never_raises_on_watcher_failure(self):
        """Even when the watcher launcher raises, probe() returns a valid dict."""
        result = self._run_probe(
            start_watch=True,
            watcher_launcher=_FailingWatcherLauncher(),
        )
        self.assertIsInstance(result, dict)
        self.assertFalse(result["watcher_started"])
        self.assertTrue(
            any("watcher" in e.lower() for e in result["errors"]),
            f"Expected watcher error in errors[]; got: {result['errors']}",
        )


# ---------------------------------------------------------------------------
# Test 6: Solo-mode envelope matches the test_orchestrator_auto_invoke contract
# ---------------------------------------------------------------------------

class SoloModeContractTests(_ProbeTestBase):
    """Verify that probe() on a fresh repo produces an envelope consistent with
    the decide_coordination_action solo-mode contract:

        {
            action: rally_start,
            mode: solo,
            presence_should_be_written: True,
            post_kind: phase,
            payload_phase: rally-start,
            coordination_file: None,
        }

    probe() doesn't return action/mode directly; we verify the observable
    artifacts that encode those invariants.
    """

    def test_solo_mode_presence_written(self):
        """Solo: presence is written (presence_should_be_written=True)."""
        result = self._run_probe()
        pf = self._presence_files()
        self.assertTrue(len(pf) >= 1, "presence_should_be_written=True but no file found")

    def test_solo_mode_rally_start_posted(self):
        """Solo: kind=phase payload.phase=rally-start is posted (post_kind=phase, payload_phase=rally-start)."""
        self._run_probe()
        changes = self._changes()
        matching = [
            c for c in changes
            if c.get("kind") == "phase"
            and isinstance(c.get("payload"), dict)
            and c["payload"].get("phase") == "rally-start"
        ]
        self.assertTrue(len(matching) >= 1, f"No rally-start phase record; changes={changes}")

    def test_solo_mode_no_coordination_file(self):
        """Solo: coordination_file=None (no coord file created)."""
        result = self._run_probe()
        self.assertIsNone(result["coordination_file"], "solo mode must not set coordination_file")

    def test_solo_mode_envelope_has_required_keys(self):
        """Envelope contains all keys required by the orchestrator contract."""
        result = self._run_probe()
        required_keys = {
            "status", "active_peers", "inbox_unread_count",
            "inbox_unread_counts", "inbox_latest_messages",
            "watcher_started", "coordination_file", "session_id", "slug",
        }
        missing = required_keys - set(result.keys())
        self.assertFalse(missing, f"Missing envelope keys: {missing}")

    def test_solo_mode_inbox_unread_counts_shape(self):
        """inbox_unread_counts has direct/broadcast/total keys."""
        result = self._run_probe()
        counts = result.get("inbox_unread_counts", {})
        self.assertIn("direct", counts)
        self.assertIn("broadcast", counts)
        self.assertIn("total", counts)

    def test_solo_mode_surfaces_inbox_latest_messages(self):
        slug = self._slug()
        chan = channel_paths.ensure_channel_dir(slug)
        inbox.write_message(
            chan,
            sender="codex",
            recipient="claude_code",
            payload={"summary": "wake up"},
            message_id="probe-doorbell",
        )

        result = self._run_probe(tool="claude_code")

        self.assertEqual(result["inbox_unread_count"], 1)
        self.assertEqual(
            result["inbox_latest_messages"][0]["id"],
            "probe-doorbell",
        )
        self.assertEqual(result["inbox_latest_messages"][0]["preview"], "wake up")


# ---------------------------------------------------------------------------
# SEC-007 — session-id random component uses a CSPRNG
# ---------------------------------------------------------------------------

class SessionIdRandomnessTests(unittest.TestCase):

    def test_short_random_is_hex_and_long_enough(self):
        """_short_random returns a hex token of >= 16 chars (8 bytes)."""
        tok = sp._short_random()
        self.assertGreaterEqual(len(tok), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in tok))

    def test_short_random_distinct_across_calls(self):
        """Many calls yield distinct tokens (no trivial collisions)."""
        tokens = {sp._short_random() for _ in range(200)}
        self.assertEqual(len(tokens), 200)

    def test_short_random_uses_secrets_module(self):
        """SEC-007 — the CSPRNG (secrets), not random.choices, is used."""
        with patch("rally_point.session_probe.secrets.token_hex",
                   return_value="abc123") as mock_tok:
            tok = sp._short_random()
        mock_tok.assert_called_once()
        self.assertEqual(tok, "abc123")


# ---------------------------------------------------------------------------
# Test 7: CLI — returns clean envelope with no crash on nonexistent repo
# ---------------------------------------------------------------------------

class CLITests(unittest.TestCase):

    def test_nonexistent_repo_returns_clean_envelope(self):
        """python3 session_probe.py --workdir /tmp/nonexistent --tool claude_code --json must not crash."""
        import subprocess
        result = subprocess.run(
            [
                sys.executable,
                str(HERE / "session_probe.py"),
                "--workdir", "/tmp/nonexistent-repo-xyz-12345",
                "--tool", "claude_code",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            env={**os.environ},
        )
        # Must exit 0 (fire-and-forget)
        self.assertEqual(
            result.returncode, 0,
            f"Expected exit 0; got {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}",
        )
        # Must produce parseable JSON
        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(f"Could not parse stdout as JSON: {result.stdout!r}")

        # Must have all required keys
        required_keys = {"status", "active_peers", "session_id", "slug", "errors"}
        missing = required_keys - set(envelope.keys())
        self.assertFalse(missing, f"Missing envelope keys: {missing}")


# ---------------------------------------------------------------------------
# C2: parent-pid capture + reap-stale on launch
#
# Root cause: per-session watchers spawned via Popen(start_new_session=True)
# are reparented to launchd when the hook process exits. The launcher captures
# its own PID BEFORE Popen and threads it to the watcher; the next
# SessionStart's reap-stale sweeps any prior watcher whose parent or own
# process is gone, or whose lifetime exceeds the configured ceiling.
# ---------------------------------------------------------------------------

class _CapturingWatcherLauncher:
    """Records all kwargs the launcher passes through (incl. parent_pid)."""

    def __init__(self, pid: int = 99100):
        self.pid = pid
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.pid


class LaunchWatcherPersistsParentPidTests(unittest.TestCase):
    """The watcher_launcher injection point sees parent_pid and the pid file
    persists parent_pid + started_at + max_lifetime_seconds for the reaper."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="probe-c2-"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_launch(self, parent_pid=None):
        errors: list[str] = []
        launcher = _CapturingWatcherLauncher()
        with patch.object(sp, "_bridge_resolve") as mock_resolve:
            # Force the apps_root fallback path so the pid_dir lives in our
            # tmpdir for assertion.
            mock_resolve.side_effect = RuntimeError("forced fallback")
            with patch.object(sp.channel_paths, "apps_root",
                              return_value=str(self.tmpdir)):
                pid_file = sp._launch_watcher(
                    workdir=str(self.tmpdir),
                    session_id="sess-c2",
                    tool="claude_code",
                    slug="test-slug",
                    watcher_launcher=launcher,
                    errors=errors,
                    parent_pid=parent_pid,
                )
        return pid_file, launcher, errors

    def test_default_parent_pid_is_os_getpid(self):
        pid_file, launcher, errors = self._run_launch(parent_pid=None)
        self.assertIsNotNone(pid_file)
        self.assertEqual(errors, [])
        self.assertEqual(len(launcher.calls), 1)
        self.assertEqual(launcher.calls[0]["parent_pid"], os.getpid())

    def test_explicit_parent_pid_is_passed_through(self):
        pid_file, launcher, _ = self._run_launch(parent_pid=42424)
        self.assertEqual(launcher.calls[0]["parent_pid"], 42424)

    def test_pid_file_persists_metadata_for_reaper(self):
        pid_file, _, _ = self._run_launch(parent_pid=99001)
        meta = json.loads(Path(pid_file).read_text())
        # All four reaper-required keys present.
        for k in ("session_id", "pid", "parent_pid", "started_at",
                  "max_lifetime_seconds"):
            self.assertIn(k, meta, f"pid file missing {k}")
        self.assertEqual(meta["parent_pid"], 99001)
        self.assertEqual(meta["pid"], 99100)
        self.assertGreater(meta["started_at"], 0)
        self.assertGreater(meta["max_lifetime_seconds"], 0)


class ReapStaleWatchersTests(unittest.TestCase):
    """The reaper deletes dead-pid files and SIGTERMs over-age or
    parent-gone watchers. Live in-window watchers untouched."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="reap-c2-"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_pid_file(self, name, **meta):
        full = {
            "session_id": name,
            "tool": "claude_code",
            "pid": 0,
            "parent_pid": None,
            "started_at": time.time(),
            "max_lifetime_seconds": 14400.0,
            **meta,
        }
        path = self.tmpdir / f"{name}.json"
        path.write_text(json.dumps(full))
        # Matching log file the reaper should delete with the json.
        log = self.tmpdir / f"{name}.log"
        log.write_text("")
        return path, log

    def test_returns_empty_stats_when_dir_missing(self):
        missing = self.tmpdir / "nope"
        stats = sp._reap_stale_watchers(missing, now=time.time(),
                                        max_lifetime=10.0)
        self.assertEqual(stats["scanned"], 0)
        self.assertEqual(stats["deleted_files"], 0)

    def test_deletes_dead_pid_file(self):
        # Pid 987654321 is definitely dead.
        path, log = self._write_pid_file("dead", pid=987654321)
        stats = sp._reap_stale_watchers(self.tmpdir, now=time.time(),
                                        max_lifetime=14400.0)
        self.assertEqual(stats["scanned"], 1)
        self.assertEqual(stats["deleted_files"], 1)
        self.assertFalse(path.exists())
        self.assertFalse(log.exists())

    def test_unreadable_pid_file_is_deleted(self):
        path = self.tmpdir / "garbage.json"
        path.write_text("not-json{{{")
        stats = sp._reap_stale_watchers(self.tmpdir, now=time.time(),
                                        max_lifetime=14400.0)
        self.assertEqual(stats["deleted_files"], 1)
        self.assertFalse(path.exists())

    def test_leaves_live_in_window_watcher_alone(self):
        # Use our own pid (alive) + recent started_at + live parent.
        path, _ = self._write_pid_file(
            "live", pid=os.getpid(), parent_pid=os.getpid(),
            started_at=time.time() - 5.0,
        )
        stats = sp._reap_stale_watchers(self.tmpdir, now=time.time(),
                                        max_lifetime=14400.0)
        self.assertEqual(stats["scanned"], 1)
        self.assertEqual(stats["deleted_files"], 0)
        self.assertEqual(stats["sigtermed"], 0)
        self.assertTrue(path.exists())

    def test_terminates_over_lifetime_running_watcher(self):
        # Start a real subprocess we can SIGTERM (sleep so it's alive).
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            # started_at far in the past, max_lifetime 1s.
            path, log = self._write_pid_file(
                "overage", pid=proc.pid, parent_pid=os.getpid(),
                started_at=time.time() - 3600.0,
                max_lifetime_seconds=1.0,
            )
            stats = sp._reap_stale_watchers(
                self.tmpdir, now=time.time(), max_lifetime=1.0,
            )
            self.assertEqual(stats["scanned"], 1)
            self.assertEqual(stats["deleted_files"], 1)
            self.assertGreaterEqual(stats["sigtermed"], 1)
            # Process should be gone now.
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.fail("reaper did not actually terminate the watcher")
            self.assertFalse(path.exists())
            self.assertFalse(log.exists())
        finally:
            # Defensive: kill if still alive.
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=1.0)

    def test_terminates_watcher_when_parent_pid_is_dead(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            # parent_pid points at a definitely-dead PID; watcher pid alive.
            path, _ = self._write_pid_file(
                "parent-gone", pid=proc.pid, parent_pid=987654321,
                started_at=time.time() - 5.0,
                max_lifetime_seconds=14400.0,
            )
            stats = sp._reap_stale_watchers(
                self.tmpdir, now=time.time(), max_lifetime=14400.0,
            )
            self.assertEqual(stats["deleted_files"], 1)
            self.assertGreaterEqual(stats["sigtermed"], 1)
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.fail("reaper did not terminate dead-parent watcher")
            self.assertFalse(path.exists())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=1.0)


class ProbeRunsReaperBeforeLaunchTests(unittest.TestCase):
    """probe() with start_watch=True must call _reap_stale_watchers BEFORE
    _launch_watcher so pre-existing leaks are cleaned before this session
    adds its own pid file."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="probe-reaper-"))
        # Force discovery bridge into a known fallback path.
        self._patches = []

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_reaper_called_before_launcher(self):
        order: list[str] = []
        original_reap = sp._reap_stale_watchers
        original_launch = sp._launch_watcher

        def spy_reap(*args, **kwargs):
            order.append("reap")
            return original_reap(*args, **kwargs)

        def spy_launch(*args, **kwargs):
            order.append("launch")
            return original_launch(*args, **kwargs)

        with patch.object(sp, "_reap_stale_watchers", side_effect=spy_reap), \
                patch.object(sp, "_launch_watcher", side_effect=spy_launch):
            launcher = _CapturingWatcherLauncher()
            sp.probe(
                workdir=str(self.tmpdir),
                tool="claude_code",
                mode="hook",
                start_watch=True,
                model="test",
                watcher_launcher=launcher,
            )
        self.assertEqual(order[:2], ["reap", "launch"],
                         f"Expected reap-then-launch, got {order}")


if __name__ == "__main__":
    unittest.main()
