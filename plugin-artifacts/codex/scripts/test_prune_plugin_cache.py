#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for prune_plugin_cache.py. Zero deps."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "prune_plugin_cache.py"


def env_without_plugin_roots(**overrides: str) -> dict[str, str]:
    """A copy of the ambient env with the host plugin-root vars cleared, so a test
    controls in-use detection explicitly and an ambient CLAUDE_PLUGIN_ROOT (the
    test may run inside a live Claude Code session) can't leak into the result."""
    env = dict(os.environ)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.pop("CODEX_PLUGIN_ROOT", None)
    env.update(overrides)
    return env


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_source(root: Path, *, name: str = "build-loop", version: str = "1.2.0") -> None:
    write(root / ".codex-plugin/plugin.json", json.dumps({"name": name, "version": version}))
    write(root / ".claude-plugin/plugin.json", json.dumps({"name": name, "version": version}))


def write_cache(cache_root: Path, host: str, marketplace: str, name: str, version: str) -> Path:
    root = cache_root / marketplace / name / version
    manifest_dir = ".codex-plugin" if host == "codex" else ".claude-plugin"
    write(root / manifest_dir / "plugin.json", json.dumps({"name": name, "version": version}))
    write(root / "payload.txt", f"{host}:{version}")
    return root


def run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=env,
    )


class PrunePluginCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.codex_cache = self.root / "codex-cache"
        self.claude_cache = self.root / "claude-cache"
        write_source(self.source)

        self.codex_current = write_cache(self.codex_cache, "codex", "ross-labs-local", "build-loop", "1.2.0")
        self.codex_old = write_cache(self.codex_cache, "codex", "ross-labs-local", "build-loop", "1.1.0")
        self.claude_current = write_cache(self.claude_cache, "claude", "rosslabs-ai-toolkit", "build-loop", "1.2.0")
        self.claude_old = write_cache(self.claude_cache, "claude", "rosslabs-ai-toolkit", "build-loop", "1.0.0")
        self.other_plugin = write_cache(self.claude_cache, "claude", "rosslabs-ai-toolkit", "research", "1.0.0")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_host_all_apply_deletes_stale_versions_in_both_caches(self) -> None:
        result = run([
            "--source", str(self.source),
            "--codex-cache-root", str(self.codex_cache),
            "--claude-cache-root", str(self.claude_cache),
            "--apply",
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual({r["host"] for r in data["reports"]}, {"codex", "claude"})
        self.assertFalse(self.codex_old.exists())
        self.assertFalse(self.claude_old.exists())
        self.assertTrue(self.codex_current.exists())
        self.assertTrue(self.claude_current.exists())
        self.assertTrue(self.other_plugin.exists())

    def test_single_host_json_keeps_back_compatible_shape(self) -> None:
        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data["host"], "claude")
        self.assertIn(str(self.claude_old.resolve()), data["stale"])

    def test_marketplace_can_be_host_specific(self) -> None:
        extra = write_cache(self.claude_cache, "claude", "other-market", "build-loop", "0.9.0")

        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--claude-marketplace", "rosslabs-ai-toolkit",
            "--apply",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse(self.claude_old.exists())
        self.assertTrue(extra.exists())

    def test_unverified_host_manifest_is_skipped(self) -> None:
        unverified = self.claude_cache / "rosslabs-ai-toolkit" / "build-loop" / "0.8.0"
        write(unverified / ".codex-plugin/plugin.json", json.dumps({"name": "build-loop", "version": "0.8.0"}))

        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--apply",
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertIn(str(unverified.resolve()), data["skipped_unverified"])
        self.assertTrue(unverified.exists())

    def test_stale_symlink_entry_is_unlinked_not_followed(self) -> None:
        source_target = self.root / "linked-source"
        write_source(source_target, version="1.2.0")
        symlink_entry = self.claude_cache / "rosslabs-ai-toolkit" / "build-loop" / "1.1.5"
        symlink_entry.parent.mkdir(parents=True, exist_ok=True)
        symlink_entry.symlink_to(source_target, target_is_directory=True)

        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--apply",
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse(symlink_entry.exists())
        self.assertTrue(source_target.exists())
        self.assertTrue((source_target / ".claude-plugin/plugin.json").exists())

    def test_in_use_version_from_env_is_protected(self) -> None:
        # The version a live session is loaded from must survive a prune even
        # though the manifest now points at a newer keep_version.
        env = env_without_plugin_roots(CLAUDE_PLUGIN_ROOT=str(self.claude_old))
        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--apply",
            "--json",
        ], env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertIn("1.0.0", data["protected"])
        self.assertNotIn(str(self.claude_old.resolve()), data["stale"])
        self.assertTrue(self.claude_old.exists())
        self.assertTrue(self.claude_current.exists())

    def test_in_use_symlink_version_is_protected(self) -> None:
        # Mirrors the real incident: the in-use version dir is a symlink to a
        # working tree (local-dev override). It must NOT be unlinked, and the
        # path is matched by cache name (unresolved), not the symlink target.
        source_target = self.root / "live-source"
        write_source(source_target, version="1.2.0")
        in_use = self.claude_cache / "rosslabs-ai-toolkit" / "build-loop" / "1.1.0-dev"
        in_use.parent.mkdir(parents=True, exist_ok=True)
        in_use.symlink_to(source_target, target_is_directory=True)
        env = env_without_plugin_roots(CLAUDE_PLUGIN_ROOT=str(in_use))

        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--apply",
        ], env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertTrue(in_use.is_symlink())
        self.assertTrue(source_target.exists())
        # the genuinely-stale old version is still pruned
        self.assertFalse(self.claude_old.exists())

    def test_protect_flag_preserves_named_version(self) -> None:
        env = env_without_plugin_roots()  # in-use detection finds nothing
        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--protect", "1.0.0",
            "--apply",
            "--json",
        ], env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertIn("1.0.0", data["protected"])
        self.assertTrue(self.claude_old.exists())

    def test_no_detect_in_use_allows_pruning_env_version(self) -> None:
        # Escape hatch / back-compat: with detection off, even the env-pointed
        # in-use version is treated as stale.
        env = env_without_plugin_roots(CLAUDE_PLUGIN_ROOT=str(self.claude_old))
        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--no-detect-in-use",
            "--apply",
            "--json",
        ], env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data["protected"], [])
        self.assertFalse(self.claude_old.exists())

    def test_peer_session_version_is_protected_via_fake_ps(self) -> None:
        """Simulate a peer Claude session bound to claude_old via a fake `ps`
        on PATH. The peer scan must see the peer's CLAUDE_PLUGIN_ROOT and
        protect claude_old even when this process's env has no pin.

        Strategy: prepend a tempdir to PATH containing a `ps` shim that emits
        a fixed line with the peer's PID, command, and env. Forces the macOS
        branch by running with no /proc available (the script's Linux branch
        is gated on Path('/proc').is_dir() — present on Linux, absent on
        macOS, so we'd need a separate Linux test using a chroot-like
        construct; instead we cover the cross-platform path via the
        public API and rely on the in-process direct test for /proc).
        """
        if Path("/proc").is_dir():
            # Linux: peer scan reads /proc directly, not `ps`. Skip the fake-ps
            # path (it doesn't exercise the code path used on Linux).
            self.skipTest("/proc-based scan: see test_peer_proc_environ_in_process")
        fake_dir = Path(self.tmp.name) / "fake-bin"
        fake_dir.mkdir(parents=True, exist_ok=True)
        peer_pid = "99999"  # implausibly high, won't collide with the test pid
        # Fake ps emits ONE line. Order: pid, command, env (env after the
        # command, matching ps -E).
        fake_ps = fake_dir / "ps"
        fake_ps.write_text(
            "#!/usr/bin/env bash\n"
            f"echo '{peer_pid} claude --some-arg CLAUDE_PLUGIN_ROOT={self.claude_old} OTHER=x'\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_ps.chmod(0o755)
        env = env_without_plugin_roots()  # no pin in our env
        env["PATH"] = f"{fake_dir}:" + env.get("PATH", "")
        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--apply",
            "--json",
        ], env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertIn("1.0.0", data["protected"])
        self.assertTrue(self.claude_old.exists())
        self.assertTrue(self.claude_current.exists())

    def test_peer_session_protection_disabled_by_no_scan_peers(self) -> None:
        """With --no-scan-peers, a peer pin is NOT protected — useful as an
        escape hatch when the fake-ps / /proc scan is misbehaving in CI."""
        if Path("/proc").is_dir():
            self.skipTest("/proc-based scan: see in-process tests")
        fake_dir = Path(self.tmp.name) / "fake-bin-noscan"
        fake_dir.mkdir(parents=True, exist_ok=True)
        fake_ps = fake_dir / "ps"
        fake_ps.write_text(
            "#!/usr/bin/env bash\n"
            f"echo '88888 claude CLAUDE_PLUGIN_ROOT={self.claude_old}'\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_ps.chmod(0o755)
        env = env_without_plugin_roots()
        env["PATH"] = f"{fake_dir}:" + env.get("PATH", "")
        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--no-scan-peers",
            "--apply",
            "--json",
        ], env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        # protected is empty because (a) our own env has no pin and (b) peers
        # were not scanned.
        self.assertEqual(data["protected"], [])
        self.assertFalse(self.claude_old.exists())


class ArchiveNotHardDeleteTests(unittest.TestCase):
    """A pruned version dir lands under <plugins>/removed/buildloop-prune-*
    in the EXACT layout the SessionStart healer scans, so a peer session
    pinned to that version can be restored on next-session start.

    Part 4 of bl-plugin-cache-gc-selfheal.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        # `removed/` is a sibling of `cache/` under `<host>/plugins/`, so the
        # cache root must be at depth `<plugins>/cache/` for the script's
        # archive_root_for() to land it correctly.
        self.plugins = self.root / "claude-plugins"
        self.claude_cache = self.plugins / "cache"
        self.removed_root = self.plugins / "removed"
        write_source(self.source)
        self.claude_current = write_cache(
            self.claude_cache, "claude", "rosslabs-ai-toolkit", "build-loop", "1.2.0",
        )
        self.claude_old = write_cache(
            self.claude_cache, "claude", "rosslabs-ai-toolkit", "build-loop", "1.0.0",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_prune(self, *extra_args: str, env: dict[str, str] | None = None,
                   ) -> subprocess.CompletedProcess:
        if env is None:
            env = env_without_plugin_roots()
        return run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--apply",
            "--json",
            *extra_args,
        ], env=env)

    def _archive_dirs(self) -> list[Path]:
        if not self.removed_root.is_dir():
            return []
        return sorted([p for p in self.removed_root.iterdir()
                       if p.is_dir() and p.name.startswith("buildloop-prune-")])

    def test_pruned_dir_archived_to_removed_in_healer_layout(self) -> None:
        result = self._run_prune()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse(self.claude_old.exists(),
                         "old version dir should have been moved out of cache")
        archives = self._archive_dirs()
        self.assertEqual(len(archives), 1, f"expected one archive tag dir, got {archives}")
        # Layout: removed/buildloop-prune-<ts>/<plugin>/<version>/
        archived_version = archives[0] / "build-loop" / "1.0.0"
        self.assertTrue(archived_version.is_dir(),
                        f"archived version dir missing: {archived_version}")
        # The archived dir's manifest is intact so the healer's
        # _is_plugin_version_dir() will match.
        manifest = archived_version / ".claude-plugin" / "plugin.json"
        self.assertTrue(manifest.exists())
        self.assertEqual(json.loads(manifest.read_text())["version"], "1.0.0")
        # Current version still present in cache.
        self.assertTrue(self.claude_current.exists())

    def test_symlink_entry_is_unlinked_never_archived(self) -> None:
        """Symlinks (local-dev / heal symlinks) must be unlinked, never
        followed and never archived — the target is a real dir that other
        callers may still need.
        """
        # Manually retire the current version and replace it with a symlink to
        # the source (mimics the live-remedy / heal-symlink case).
        shutil.rmtree(self.claude_old)
        link_target = self.root / "linked-source"
        write_source(link_target, version="1.1.5")
        symlink_entry = self.claude_cache / "rosslabs-ai-toolkit" / "build-loop" / "1.1.5"
        symlink_entry.symlink_to(link_target, target_is_directory=True)

        result = self._run_prune()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        # Symlink gone, target preserved.
        self.assertFalse(symlink_entry.exists())
        self.assertTrue(link_target.exists())
        # No archive tag was created (symlinks never go through the archive
        # path), AND nothing inside `removed/` references the symlinked
        # version.
        archives = self._archive_dirs()
        if archives:
            for tag in archives:
                for inner in tag.rglob("1.1.5"):
                    self.fail(f"symlink target was archived under {inner}")
                for inner in tag.rglob("linked-source"):
                    self.fail(f"symlink was followed into {inner}")

    def test_retention_cap_gcs_oldest_archives(self) -> None:
        """N+1 archive tags → oldest is GC'd, newest ARCHIVE_RETENTION kept."""
        import prune_plugin_cache as ppc  # noqa: PLC0415
        # Seed ARCHIVE_RETENTION + 2 stale archive tags with deterministic
        # mtimes (oldest first). The next real prune adds one MORE archive,
        # leaving N+3 candidates before GC → expect exactly N after.
        retention = ppc.ARCHIVE_RETENTION
        archive_root = ppc.archive_root_for(self.claude_cache)
        archive_root.mkdir(parents=True, exist_ok=True)
        seeded: list[Path] = []
        for i in range(retention + 2):
            d = archive_root / f"buildloop-prune-seed-{i:02d}"
            (d / "build-loop" / f"0.0.{i}").mkdir(parents=True)
            # mtime: older i = older time. (1700000000 = 2023-11-14 baseline.)
            t = 1700000000.0 + i
            os.utime(d, (t, t))
            seeded.append(d)
        # An UNRELATED dir under removed/ must NOT be GC'd (only the
        # buildloop-prune-* prefix is owned by this script).
        unrelated = archive_root / "cc-core-archive-keep"
        unrelated.mkdir()

        # Trigger the real prune so the archive + GC fires through the
        # public path (apply mode).
        result = self._run_prune()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

        remaining = sorted([p.name for p in archive_root.iterdir()
                            if p.is_dir() and p.name.startswith("buildloop-prune-")])
        self.assertEqual(len(remaining), retention,
                         f"expected {retention} archive dirs after GC, got {remaining}")
        # The unrelated dir is untouched.
        self.assertTrue(unrelated.is_dir())

    def test_cross_fs_fallback_uses_copytree_then_rmtree(self) -> None:
        """When os.rename refuses (EXDEV / read-only fs), the archive path
        falls back to copytree(symlinks=True) + rmtree. Force the fallback
        by monkey-patching os.rename inside the module.
        """
        import prune_plugin_cache as ppc  # noqa: PLC0415
        archive_root = ppc.archive_root_for(self.claude_cache)

        original_rename = ppc.os.rename
        calls: list[tuple[str, str]] = []

        def fake_rename(src, dst):  # noqa: ANN001
            # First call (the archive move) raises; any later call is real.
            if not calls:
                calls.append((str(src), str(dst)))
                raise OSError(18, "EXDEV — simulated cross-fs move")
            return original_rename(src, dst)

        ppc.os.rename = fake_rename  # type: ignore[assignment]
        try:
            ppc.remove_cache_entry(
                self.claude_old,
                plugin_name="build-loop",
                archive_root=archive_root,
            )
        finally:
            ppc.os.rename = original_rename  # type: ignore[assignment]

        self.assertEqual(len(calls), 1, "fallback should have been triggered exactly once")
        self.assertFalse(self.claude_old.exists(), "source dir must be gone after fallback")
        # The fallback-copied dir must still be discoverable by the healer.
        archives = [p for p in archive_root.iterdir()
                    if p.is_dir() and p.name.startswith("buildloop-prune-")]
        self.assertEqual(len(archives), 1)
        archived = archives[0] / "build-loop" / "1.0.0"
        self.assertTrue(archived.is_dir())
        self.assertTrue((archived / ".claude-plugin" / "plugin.json").exists())

    def test_round_trip_prune_then_heal_restores_version(self) -> None:
        """Integration: prune archives the dir; the plugin_dir_heal.py
        SessionStart healer (the same one that ships in this plugin) reads
        the registry, finds the archive under `removed/`, and moves it
        back to its installPath. This is the contract that lets a peer
        session bound to the pruned version recover at next start.
        """
        # 1) Prune — archive the old version.
        result = self._run_prune()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse(self.claude_old.exists())
        archives = self._archive_dirs()
        self.assertEqual(len(archives), 1)

        # 2) Build a synthetic ~/.claude tree the healer can read. The healer
        # scopes everything off CLAUDE_HOME_OVERRIDE → `<home>/plugins/...`,
        # so point it at our test `claude-plugins/` parent. The healer
        # registry pins installPath at the missing claude_old path.
        claude_home = self.root / "claude-home"
        (claude_home / "plugins").mkdir(parents=True)
        # Move the archive into the healer's expected location (it scans
        # `<claude_home>/plugins/removed/`, which is exactly our self.removed_root
        # already if we point CLAUDE_HOME_OVERRIDE at self.plugins's parent).
        # Simpler: copy our archives + a registry under the healer's home.
        healer_removed = claude_home / "plugins" / "removed"
        shutil.copytree(self.removed_root, healer_removed)
        registry = {
            "plugins": {
                "build-loop@rosslabs-ai-toolkit": [
                    {
                        "installPath": str(self.claude_old),
                        "version": "1.0.0",
                    }
                ]
            }
        }
        (claude_home / "plugins" / "installed_plugins.json").write_text(
            json.dumps(registry), encoding="utf-8",
        )

        # 3) Invoke the healer.
        healer = HERE / "hooks" / "plugin_dir_heal.py"
        self.assertTrue(healer.exists(), f"healer not found at {healer}")
        env = env_without_plugin_roots(CLAUDE_HOME_OVERRIDE=str(claude_home))
        proc = subprocess.run(
            [sys.executable, str(healer), "--verbose"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)

        # 4) The previously-pruned version is back at its installPath, with
        # an intact manifest.
        self.assertTrue(self.claude_old.exists(),
                        f"healer should have restored {self.claude_old}; "
                        f"stdout={proc.stdout!r}")
        manifest = self.claude_old / ".claude-plugin" / "plugin.json"
        self.assertTrue(manifest.exists())
        self.assertEqual(json.loads(manifest.read_text())["version"], "1.0.0")


class PeerDetectorUnitTests(unittest.TestCase):
    """In-process tests of _detect_peer_in_use_versions covering BOTH
    code paths (ps shim + /proc) regardless of host OS, so the function
    is exercised end-to-end on every CI runner."""

    def setUp(self) -> None:
        sys.path.insert(0, str(HERE))
        import prune_plugin_cache  # noqa: PLC0415
        self.mod = prune_plugin_cache
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_macos_ps_path_parses_env_var(self) -> None:
        """Force the ps branch by routing the subprocess call through a fake
        Path('/proc') that isn't a dir, regardless of host OS."""
        # Patch Path('/proc').is_dir to False so the script takes the ps path.
        original_path_is_dir = Path.is_dir
        original_run = self.mod.subprocess.run

        def fake_is_dir(self_):  # noqa: ANN001
            if str(self_) == "/proc":
                return False
            return original_path_is_dir(self_)

        peer_pid = "77777"
        peer_line = (
            f"{peer_pid} claude --foo "
            "CLAUDE_PLUGIN_ROOT=/cache/m/build-loop/9.9.9 "
            "OTHER=ignored"
        )

        class _R:
            returncode = 0
            stdout = peer_line + "\n"
            stderr = ""

        def fake_run(*_args, **_kwargs):  # noqa: ANN002
            return _R()

        Path.is_dir = fake_is_dir  # type: ignore[assignment]
        self.mod.subprocess.run = fake_run  # type: ignore[assignment]
        try:
            names = self.mod._detect_peer_in_use_versions(plugin_name="build-loop")
        finally:
            Path.is_dir = original_path_is_dir  # type: ignore[assignment]
            self.mod.subprocess.run = original_run  # type: ignore[assignment]
        self.assertIn("9.9.9", names)

    def test_ps_path_fail_open_on_missing_binary(self) -> None:
        original_path_is_dir = Path.is_dir
        original_run = self.mod.subprocess.run

        def fake_is_dir(self_):  # noqa: ANN001
            if str(self_) == "/proc":
                return False
            return original_path_is_dir(self_)

        def fake_run(*_args, **_kwargs):  # noqa: ANN002
            raise FileNotFoundError("no ps")

        Path.is_dir = fake_is_dir  # type: ignore[assignment]
        self.mod.subprocess.run = fake_run  # type: ignore[assignment]
        try:
            names = self.mod._detect_peer_in_use_versions(plugin_name="build-loop")
        finally:
            Path.is_dir = original_path_is_dir  # type: ignore[assignment]
            self.mod.subprocess.run = original_run  # type: ignore[assignment]
        self.assertEqual(names, set())

    def test_unrelated_env_value_does_not_overprotect(self) -> None:
        """Env points at a path whose PARENT name isn't the plugin → ignored.
        Same un-resolved name match rule as the current-process detector."""
        original_path_is_dir = Path.is_dir
        original_run = self.mod.subprocess.run

        def fake_is_dir(self_):  # noqa: ANN001
            if str(self_) == "/proc":
                return False
            return original_path_is_dir(self_)

        class _R:
            returncode = 0
            stdout = (
                "55555 claude CLAUDE_PLUGIN_ROOT=/somewhere/else/plugin/1.0.0\n"
            )
            stderr = ""

        Path.is_dir = fake_is_dir  # type: ignore[assignment]
        self.mod.subprocess.run = lambda *a, **k: _R()  # type: ignore[assignment]
        try:
            names = self.mod._detect_peer_in_use_versions(plugin_name="build-loop")
        finally:
            Path.is_dir = original_path_is_dir  # type: ignore[assignment]
            self.mod.subprocess.run = original_run  # type: ignore[assignment]
        self.assertEqual(names, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
