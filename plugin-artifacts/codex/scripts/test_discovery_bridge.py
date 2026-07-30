#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Adversarial tests for ``scripts/rally_point/discovery_bridge.resolve``.

β1 verification standard (see ``smoke-test-environment-rigging`` memory):
each test runs WITHOUT the convenience that would hide the absence the
test is supposed to detect. Specifically:

- ``PYTHONPATH`` is stripped from every subprocess invocation so a
  passing test cannot rely on a sibling-repo install being implicitly
  on sys.path.
- The ``agent-rally-discover`` binary is shadowed via a fresh ``PATH``
  in each test where it should be absent.
- The Python import probe is killed by running the bridge under
  ``python3 -I`` (isolated mode) where required.
- ``AGENT_RALLY_DISCOVER`` overrides are exercised via fake scripts on
  disk; no real package is required.

The bridge MUST NOT silently fall back to internal when a canonical
source is reachable but reports an incompatible protocol version.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rally_point import discovery_bridge as bridge  # noqa: E402
from rally_point import channel_paths  # noqa: E402


def _clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build a subprocess env with PYTHONPATH and any rally-discover
    overrides stripped. Tests then add only what they explicitly want.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {
            "PYTHONPATH",
            "AGENT_RALLY_BINARY",
            "AGENT_RALLY_DISCOVER",
        }
    }
    # Use a synthetic minimal PATH so we control which binaries exist.
    env["PATH"] = "/usr/bin:/bin"
    # Keep bridge-order tests about the declared source under test; the
    # adjacent local agent-rally-point checkout may contain an uninstalled
    # Rust binary that would otherwise intentionally win.
    env["BUILD_LOOP_DISABLE_SIBLING_RALLY"] = "1"
    # Likewise exclude the fetch-on-install tier: these tests pin the order of
    # the LIVE source tiers, and a previously-cached fetched binary would
    # otherwise resolve as repo-local before the lower live tiers.
    env["BUILD_LOOP_DISABLE_BINARY_FETCH"] = "1"
    if extra:
        env.update(extra)
    return env


class DiscoveryBridgeResolutionOrderTests(unittest.TestCase):
    """Verify the env > PATH > import > internal priority order."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bridge-resolve-"))
        self.workdir = self.tmp / "repo"
        self.workdir.mkdir()
        self._old_apps_root = os.environ.get("BUILD_LOOP_APPS_ROOT")
        os.environ["BUILD_LOOP_APPS_ROOT"] = str(self.tmp / "apps")
        # β1 follow-up: other test classes may have set BUILD_LOOP_BRIDGE_
        # INTERNAL_ONLY=1 in setUp without tearDown-restoring it. The
        # discovery_bridge tests exercise the canonical sources, so we
        # must explicitly clear that env var here.
        self._old_internal_only = os.environ.pop(
            "BUILD_LOOP_BRIDGE_INTERNAL_ONLY", None
        )
        subprocess.run(
            ["git", "init"], cwd=self.workdir, check=True, capture_output=True
        )
        bridge.clear_cache()

    def tearDown(self) -> None:
        if self._old_apps_root is None:
            os.environ.pop("BUILD_LOOP_APPS_ROOT", None)
        else:
            os.environ["BUILD_LOOP_APPS_ROOT"] = self._old_apps_root
        if self._old_internal_only is not None:
            os.environ["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = self._old_internal_only
        shutil.rmtree(self.tmp, ignore_errors=True)
        bridge.clear_cache()

    def _write_fake_discover_script(
        self,
        *,
        channel_dir: str,
        app_slug: str,
        protocol_version: str = "1.0",
        policy: str = "canonical",
        extra_payload: dict | None = None,
        name: str = "fake-discover.sh",
    ) -> Path:
        """Write an executable script that emits a discover JSON envelope.

        Pass a unique ``name`` per call when a single test creates multiple
        scripts (e.g. env override + PATH binary), else later calls
        overwrite earlier ones.
        """
        path = self.tmp / name
        payload = {
            "installed": True,
            "channel_dir": channel_dir,
            "app_slug": app_slug,
            "protocol_version": protocol_version,
            "policy": policy,
            "channel_layout": "canonical",
            "repo_id": app_slug,
            "last_resolved_at": "2026-05-24T00:00:00Z",
        }
        if extra_payload:
            payload.update(extra_payload)
        body = json.dumps(payload)
        path.write_text(
            f'#!/bin/sh\nprintf %s {json.dumps(body)}\n', encoding="utf-8"
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        return path

    def test_path_binary_wins_when_available(self) -> None:
        """When ``agent-rally-discover`` resolves on PATH, the bridge
        consumes its envelope and reports ``path-binary``."""
        canonical_dir = str(self.tmp / "canonical-channel")
        fake = self._write_fake_discover_script(
            channel_dir=canonical_dir, app_slug="fake-slug"
        )
        # Place the fake under a PATH-style dir named so shutil.which finds it.
        path_dir = self.tmp / "bin"
        path_dir.mkdir()
        link = path_dir / "agent-rally-discover"
        shutil.copy(fake, link)
        link.chmod(link.stat().st_mode | stat.S_IXUSR)

        env = _clean_env({"PATH": f"{path_dir}:/usr/bin:/bin"})
        with self._patched_env(env):
            envelope = bridge.resolve(self.workdir)
        self.assertEqual(envelope.resolved_via, "path-binary")
        self.assertEqual(envelope.channel_dir, canonical_dir)
        self.assertEqual(envelope.app_slug, "fake-slug")
        self.assertEqual(envelope.policy, "canonical")
        self.assertIsNone(envelope.coordination_unavailable)

    def test_env_override_beats_path_binary(self) -> None:
        """``$AGENT_RALLY_DISCOVER`` wins even when PATH has a binary."""
        canonical_dir = str(self.tmp / "env-channel")
        path_canonical = str(self.tmp / "path-channel")
        env_script = self._write_fake_discover_script(
            channel_dir=canonical_dir, app_slug="env-slug",
            name="env-discover.sh",
        )
        path_script = self._write_fake_discover_script(
            channel_dir=path_canonical, app_slug="path-slug",
            name="path-discover.sh",
        )
        # Rename so shutil.copy can sit next to env_script.
        path_dir = self.tmp / "bin"
        path_dir.mkdir()
        link = path_dir / "agent-rally-discover"
        shutil.copy(path_script, link)
        link.chmod(link.stat().st_mode | stat.S_IXUSR)

        env = _clean_env({
            "PATH": f"{path_dir}:/usr/bin:/bin",
            "AGENT_RALLY_DISCOVER": str(env_script),
        })
        with self._patched_env(env):
            envelope = bridge.resolve(self.workdir)
        self.assertEqual(envelope.resolved_via, "env-override")
        self.assertEqual(envelope.app_slug, "env-slug")

    def test_internal_fallback_when_nothing_available(self) -> None:
        """No env, no PATH binary, no importable package → internal fallback."""
        env = _clean_env({})  # PATH=/usr/bin:/bin (no rally-discover there)
        # Subprocess form because clearing PYTHONPATH inside the parent
        # would not affect this already-loaded process. We invoke the
        # bridge module via -m and stripped env.
        cmd = [
            sys.executable, "-I",
            "-c",
            textwrap.dedent(
                f"""
                import sys
                sys.path.insert(0, {str(HERE)!r})
                from rally_point import discovery_bridge as bridge
                envelope = bridge.resolve({str(self.workdir)!r})
                import json
                print(json.dumps(envelope.to_dict()))
                """
            ).strip(),
        ]
        env["BUILD_LOOP_APPS_ROOT"] = os.environ["BUILD_LOOP_APPS_ROOT"]
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=10
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["resolved_via"], "build-loop-internal")
        self.assertEqual(result["policy"], "legacy-only")

    def test_protocol_mismatch_returns_unavailable_no_silent_fallback(self) -> None:
        """Canonical source reports protocol 3.0 → coordination_unavailable.

        The bridge MUST NOT silently fall back to internal — that's the
        v0.12.16 defect class. Caller sees the loud envelope and decides.
        """
        canonical_dir = str(self.tmp / "mismatch-channel")
        env_script = self._write_fake_discover_script(
            channel_dir=canonical_dir,
            app_slug="mismatch-slug",
            protocol_version="3.0",  # outside pinned [1.0, 3.0)
        )
        env = _clean_env({"AGENT_RALLY_DISCOVER": str(env_script)})
        with self._patched_env(env):
            envelope = bridge.resolve(self.workdir)
        self.assertEqual(envelope.resolved_via, "env-override")
        self.assertEqual(
            envelope.coordination_unavailable, "incompatible_protocol"
        )
        # The bridge surfaces the envelope; it did NOT silently flip to
        # internal-fallback (which would have policy="legacy-only").
        self.assertNotEqual(envelope.policy, "legacy-only")

    def test_protocol_above_lower_bound_inclusive(self) -> None:
        """Protocol 1.5 (inside the pinned band) is accepted normally."""
        canonical_dir = str(self.tmp / "ok-channel")
        env_script = self._write_fake_discover_script(
            channel_dir=canonical_dir,
            app_slug="ok-slug",
            protocol_version="1.5",
        )
        env = _clean_env({"AGENT_RALLY_DISCOVER": str(env_script)})
        with self._patched_env(env):
            envelope = bridge.resolve(self.workdir)
        self.assertIsNone(envelope.coordination_unavailable)
        self.assertEqual(envelope.protocol_version, "1.5")

    def test_protocol_two_zero_is_accepted_for_rust_hash_chain(self) -> None:
        """Protocol 2.0 is the Rust hash-chain bridge band."""
        canonical_dir = str(self.tmp / "rust-channel")
        env_script = self._write_fake_discover_script(
            channel_dir=canonical_dir,
            app_slug="rust-slug",
            protocol_version="2.0",
            extra_payload={"channel_layout": "hash-chain"},
        )
        env = _clean_env({"AGENT_RALLY_DISCOVER": str(env_script)})
        with self._patched_env(env):
            envelope = bridge.resolve(self.workdir)
        self.assertIsNone(envelope.coordination_unavailable)
        self.assertEqual(envelope.protocol_version, "2.0")
        self.assertEqual(envelope.channel_layout, "hash-chain")

    def test_stale_rally_binary_is_skipped_for_current_surface(self) -> None:
        """A binary missing a real surface fragment must not be accepted.

        The stale binary's help lacks ``rally whoami`` (rally's real surface);
        the current binary exposes all three real fragments. Discovery skips
        the stale override and accepts the current one.
        """
        stale = self.tmp / "stale-rally"
        stale.write_text(
            "#!/bin/sh\n"
            "echo 'usage: rally enter --tool <tool>'\n"
            "echo '       rally say <kind> --tool <tool>'\n"
            "exit 2\n",
            encoding="utf-8",
        )
        stale.chmod(stale.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        current = self.tmp / "current-rally"
        current.write_text(
            "#!/bin/sh\n"
            "echo 'usage: rally enter --tool <tool>'\n"
            "echo '       rally say <kind> --tool <tool> --subject <subject>'\n"
            "echo '       rally whoami [--tool <id>] [--json]'\n"
            "exit 2\n",
            encoding="utf-8",
        )
        current.chmod(current.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

        env = _clean_env({
            "BUILD_LOOP_DISABLE_SIBLING_RALLY": "1",
            "AGENT_RALLY_BINARY": str(stale),
        })
        with self._patched_env(env):
            with mock.patch.object(bridge.shutil, "which", return_value=str(current)):
                self.assertEqual(bridge.rust_rally_binary(), str(current))

    def test_path_rally_beats_sibling_dev_rally(self) -> None:
        """A PATH Rally beats a sibling dev build when binary fetch is disabled."""
        path_dir = self.tmp / "bin"
        path_rally = path_dir / "rally"
        self._write_fake_rally(path_rally, self.tmp / "path-channel")

        sibling_rally = (
            self.workdir.parent
            / "agent-rally-point"
            / "target"
            / "release"
            / "rally"
        )
        self._write_fake_rally(sibling_rally, self.tmp / "sibling-channel")

        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {
                "AGENT_RALLY_BINARY",
                "BUILD_LOOP_DISABLE_BINARY_FETCH",
                "BUILD_LOOP_APPS_ROOT",
                "BUILD_LOOP_DISABLE_SIBLING_RALLY",
            }
        }
        env["BUILD_LOOP_DISABLE_BINARY_FETCH"] = "1"
        env["PATH"] = f"{path_dir}:/usr/bin:/bin"
        with self._patched_env(env):
            self.assertEqual(
                Path(str(bridge.rust_rally_binary(self.workdir))).resolve(),
                path_rally.resolve(),
            )

    def test_repo_local_enter_say_rally_resolves_to_dot_rally(self) -> None:
        """Older native Rally uses repo-local .rally and must beat fallback."""
        rally = self.tmp / "bin" / "rally"
        self._write_fake_repo_local_rally(rally)

        env = _clean_env({
            "PATH": f"{rally.parent}:/usr/bin:/bin",
            "BUILD_LOOP_DISABLE_SIBLING_RALLY": "1",
        })
        with self._patched_env(env):
            envelope = bridge.resolve(self.workdir)

        self.assertEqual(envelope.resolved_via, "repo-local-rally-cli")
        self.assertEqual(
            Path(envelope.channel_dir).resolve(),
            (self.workdir / ".rally").resolve(),
        )
        self.assertEqual(envelope.policy, "repo-local")

    def test_rust_and_repo_local_resolvers_are_the_same_real_surface(self) -> None:
        """The phantom ``setup`` tier is gone: ``rust_rally_binary`` and
        ``repo_local_rally_binary`` now resolve rally's single real surface and
        return the identical binary (they are the same function)."""
        rally = self.tmp / "bin" / "rally"
        self._write_fake_repo_local_rally(rally)

        self.assertIs(bridge.repo_local_rally_binary, bridge.rust_rally_binary)

        env = _clean_env({
            "PATH": f"{rally.parent}:/usr/bin:/bin",
            "BUILD_LOOP_DISABLE_SIBLING_RALLY": "1",
        })
        with self._patched_env(env):
            self.assertEqual(bridge.rust_rally_binary(self.workdir), str(rally))
            self.assertEqual(bridge.repo_local_rally_binary(self.workdir), str(rally))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _write_fake_rally(self, path: Path, channel_dir: Path) -> None:
        """Write a fake ``rally`` exposing rally's REAL surface.

        ``channel_dir`` is retained for signature compatibility; the real
        repo-local resolver derives the channel from ``whoami``'s ``repo_root``
        (the ``.rally`` ledger), so the whoami payload reports the binary's own
        parent repo. The binary's identity (which path resolved) is what the
        sibling/stale/PATH-priority tests assert.
        """
        whoami_payload = json.dumps({
            "ok": True,
            "data": {"whoami": {
                "repo_root": str(channel_dir.parent),
                "repo_id": channel_dir.parent.name,
                "worktree": str(channel_dir.parent),
                "cwd": str(channel_dir.parent),
                "build_id": "test-fake",
            }},
        })
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "args = sys.argv[1:]\n"
            "if not args:\n"
            "    print('usage: rally enter --tool <tool>')\n"
            "    print('       rally say <kind> --tool <tool> --subject <subject>')\n"
            "    print('       rally whoami [--tool <id>] [--json]')\n"
            "    raise SystemExit(2)\n"
            "if args == ['whoami', '--json']:\n"
            f"    print({whoami_payload!r})\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(2)\n",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

    def _write_fake_repo_local_rally(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "if not args:\n"
            "    print('Usage: rally enter --tool <tool>')\n"
            "    print('       rally say <kind> --tool <tool> --subject <subject>')\n"
            "    print('       rally whoami [--tool <id>] [--json]')\n"
            "    raise SystemExit(0)\n"
            "repo = pathlib.Path.cwd()\n"
            "if args == ['whoami', '--json']:\n"
            "    print(json.dumps({'ok': True, 'data': {'whoami': {\n"
            "        'repo_root': str(repo), 'repo_id': repo.name,\n"
            "        'worktree': str(repo), 'cwd': str(repo),\n"
            "        'build_id': 'test-local'}}}))\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(2)\n",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

    def _patched_env(self, env: dict[str, str]):
        """Context manager that swaps os.environ + os.environb in-process."""
        class _Ctx:
            def __init__(self, new_env):
                self.new_env = new_env
                self.old_env: dict[str, str] = {}

            def __enter__(self_inner):
                self_inner.old_env = dict(os.environ)
                os.environ.clear()
                os.environ.update(self_inner.new_env)
                bridge.clear_cache()
                return None

            def __exit__(self_inner, *exc):
                os.environ.clear()
                os.environ.update(self_inner.old_env)
                bridge.clear_cache()
                return False

        return _Ctx(env)


class DiscoveryBridgeUserShellEquivalentTests(unittest.TestCase):
    """Run the bridge under the exact env a fresh user shell carries.

    The smoke-test-environment-rigging memory note: the test environment
    must NOT contain the convenience (PYTHONPATH inject) that hides the
    absence the test should detect. These tests use ``env -u PYTHONPATH``
    via subprocess so a passing result reflects real-world default.
    """

    def test_user_default_env_resolves_canonical_when_binary_installed(self) -> None:
        """When a native Rally Point surface is available in a user shell,
        the bridge resolves through it. A native ``rally`` (repo-local real
        surface) wins when present; otherwise the Python ``agent-rally-discover``
        binary is accepted.
        """
        if not shutil.which("agent-rally-discover") and not bridge.rust_rally_binary():
            self.skipTest("no native Rally Point surface available")
        repo_root = HERE.parent
        cmd = [
            # Strip PYTHONPATH (env-rigging avoidance), the test-isolation
            # flag that other test classes leak via os.environ, AND
            # BUILD_LOOP_APPS_ROOT (also leaked by other suite setUps).
            "env", "-u", "PYTHONPATH",
            "-u", "BUILD_LOOP_BRIDGE_INTERNAL_ONLY",
            "-u", "BUILD_LOOP_APPS_ROOT",
            sys.executable, "-c",
            textwrap.dedent(
                f"""
                import sys, json
                sys.path.insert(0, {str(HERE)!r})
                from rally_point import discovery_bridge as bridge
                envelope = bridge.resolve({str(repo_root)!r})
                print(json.dumps(envelope.to_dict()))
                """
            ).strip(),
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn(
            result["resolved_via"],
            {"repo-local-rally-cli", "fetched-binary", "path-binary"},
        )
        # Native rally owns a repo-local ``.rally`` ledger; the Python
        # ``agent-rally-discover`` path resolves the canonical apps root.
        self.assertTrue(
            result["channel_dir"].endswith(".rally")
            or ".agent-rally-point" in result["channel_dir"],
            msg=result["channel_dir"],
        )


class DiscoveryBridgeWorktreeCanonicalizationTests(unittest.TestCase):
    """Channel-split fix: two git worktrees of one repo resolve through
    ``bridge.resolve`` to the SAME channel_dir.

    Exercised via the internal fallback (BUILD_LOOP_BRIDGE_INTERNAL_ONLY)
    so the assertion does not depend on a native rally binary being
    installed. The canonicalization runs at the ENTRY of resolve(), before
    any source is selected, so the internal path is a faithful proxy: if
    the worktree path were not collapsed, the two checkouts would key
    different channels regardless of which resolver wins.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bridge-wt-canon-"))
        self.repo = self.tmp / "split-repo"
        self.repo.mkdir()
        self._old_apps_root = os.environ.get("BUILD_LOOP_APPS_ROOT")
        self._old_internal = os.environ.get("BUILD_LOOP_BRIDGE_INTERNAL_ONLY")
        os.environ["BUILD_LOOP_APPS_ROOT"] = str(self.tmp / "apps")
        os.environ["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = "1"
        for args in (
            ["init", "-q"],
            ["config", "user.email", "t@example.com"],
            ["config", "user.name", "t"],
        ):
            subprocess.run(["git", *args], cwd=self.repo, check=True,
                           capture_output=True)
        (self.repo / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True,
                       capture_output=True)
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
            cwd=self.repo, check=True, capture_output=True,
        )
        bridge.clear_cache()

    def tearDown(self) -> None:
        for key, old in (
            ("BUILD_LOOP_APPS_ROOT", self._old_apps_root),
            ("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", self._old_internal),
        ):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        shutil.rmtree(self.tmp, ignore_errors=True)
        bridge.clear_cache()

    def test_worktree_and_main_resolve_to_same_channel(self) -> None:
        main_env = bridge.resolve(self.repo)
        wt = self.tmp / "wt-room"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(wt), "HEAD"],
            cwd=self.repo, check=True, capture_output=True,
        )
        bridge.clear_cache()  # avoid per-path cache masking the split
        wt_env = bridge.resolve(wt)
        self.assertEqual(main_env.app_slug, wt_env.app_slug)
        self.assertEqual(main_env.channel_dir, wt_env.channel_dir)
        self.assertEqual(main_env.app_slug, "split-repo")


class RequiredSurfacePinnedToRealRallyTests(unittest.TestCase):
    """Pin the surface-acceptance check to a REAL ``rally`` binary's ``--help``.

    The durable anti-recurrence lever for the v0.12.x phantom-surface defect
    class (build-loop gated discovery on ``rally setup``/``rally post``/
    ``rally stop <tool>`` — commands rally never shipped, so a whole resolution
    tier was dead). These tests run a real rally binary's actual top-level usage
    and assert:

      1. build-loop's required-surface check PASSES against it, and
      2. every ``REQUIRED_RALLY_HELP_FRAGMENTS`` fragment is genuinely present
         in that real ``--help`` (so the tuple can never silently drift to a
         surface rally does not expose), and
      3. the historic phantom fragments are ABSENT from real rally — the exact
         assertion that would have caught the original mismatch.

    A real binary is sourced from (in order): the build-loop runtime cache
    (fetched pinned release), ``$AGENT_RALLY_BINARY``, a sibling
    ``agent-rally-point/target/{release,debug}/rally`` checkout, or ``rally`` on
    PATH. When none is available (a clean CI box with no rally), the test
    skips — it never fabricates a binary, because a fabricated help text is
    exactly the phantom this test exists to prevent.
    """

    # Commands rally never shipped; the original phantom gate looked for these.
    PHANTOM_FRAGMENTS = (
        "rally stop <tool>",
        "rally post --kind",
        "rally setup",
    )

    def _real_rally_binary(self) -> str | None:
        # 1. Fetched pinned release in the build-loop runtime cache.
        try:
            from rally_point import binary_fetch
            cached = binary_fetch.cached_binary_path()
            if cached.is_file() and os.access(cached, os.X_OK):
                return str(cached)
        except Exception:  # noqa: BLE001 — fetch module is optional
            pass
        # 2. Operator override.
        override = os.environ.get("AGENT_RALLY_BINARY")
        if override and Path(override).expanduser().is_file():
            return str(Path(override).expanduser())
        # 3. Sibling checkout next to build-loop.
        repo_root = HERE.parent  # scripts/ -> repo root
        sibling = repo_root.parent / "agent-rally-point"
        for sub in ("target/release/rally", "target/debug/rally"):
            cand = sibling / sub
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
        # 4. PATH.
        on_path = shutil.which("rally")
        return on_path

    def _real_help_text(self, binary: str) -> str:
        proc = subprocess.run(
            [binary], capture_output=True, text=True, timeout=10
        )
        return f"{proc.stdout}\n{proc.stderr}"

    def test_real_rally_passes_required_surface_check(self) -> None:
        binary = self._real_rally_binary()
        if not binary:
            self.skipTest("no real rally binary available to pin the surface against")
        self.assertTrue(
            bridge._rally_binary_supports_required_surface(binary),
            msg=(
                "build-loop's required-surface check REJECTED a real rally "
                f"binary ({binary}). The REQUIRED_RALLY_HELP_FRAGMENTS have "
                "drifted away from rally's actual --help — this is the phantom-"
                "surface regression the test exists to catch."
            ),
        )

    def test_required_fragments_are_all_in_real_help(self) -> None:
        binary = self._real_rally_binary()
        if not binary:
            self.skipTest("no real rally binary available to pin the surface against")
        help_text = self._real_help_text(binary)
        for fragment in bridge.REQUIRED_RALLY_HELP_FRAGMENTS:
            self.assertIn(
                fragment, help_text,
                msg=f"required fragment {fragment!r} absent from real rally --help",
            )

    def test_phantom_fragments_are_absent_from_real_help(self) -> None:
        binary = self._real_rally_binary()
        if not binary:
            self.skipTest("no real rally binary available to pin the surface against")
        help_text = self._real_help_text(binary)
        for fragment in self.PHANTOM_FRAGMENTS:
            self.assertNotIn(
                fragment, help_text,
                msg=(
                    f"phantom fragment {fragment!r} unexpectedly present in real "
                    "rally --help. If rally genuinely added this command, move it "
                    "from PHANTOM_FRAGMENTS into REQUIRED_RALLY_HELP_FRAGMENTS."
                ),
            )

    def test_required_fragments_are_not_the_phantom_set(self) -> None:
        # Pure unit guard (no binary needed): the gate must never be the old
        # phantom tuple again.
        self.assertNotIn("rally stop <tool>", bridge.REQUIRED_RALLY_HELP_FRAGMENTS)
        self.assertNotIn("rally post --kind", bridge.REQUIRED_RALLY_HELP_FRAGMENTS)
        self.assertEqual(
            bridge.REQUIRED_RALLY_HELP_FRAGMENTS,
            ("rally enter --tool", "rally say <kind>", "rally whoami"),
        )


if __name__ == "__main__":
    unittest.main()
