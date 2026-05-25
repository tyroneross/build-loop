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
        if k not in {"PYTHONPATH", "AGENT_RALLY_DISCOVER"}
    }
    # Use a synthetic minimal PATH so we control which binaries exist.
    env["PATH"] = "/usr/bin:/bin"
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
        """Canonical source reports protocol 2.0 → coordination_unavailable.

        The bridge MUST NOT silently fall back to internal — that's the
        v0.12.16 defect class. Caller sees the loud envelope and decides.
        """
        canonical_dir = str(self.tmp / "mismatch-channel")
        env_script = self._write_fake_discover_script(
            channel_dir=canonical_dir,
            app_slug="mismatch-slug",
            protocol_version="2.0",  # outside pinned [1.0, 2.0)
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
        """When ``agent-rally-discover`` is on the system PATH (it is in
        this repo's test environment via pipx install of agent-rally-point),
        the bridge resolves canonical via ``path-binary``. Skipped when
        the binary is not present so the test is locally portable.
        """
        if not shutil.which("agent-rally-discover"):
            self.skipTest("agent-rally-discover not on PATH; α not installed")
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
        self.assertEqual(result["resolved_via"], "path-binary")
        # Canonical apps root is ~/.agent-rally-point/apps/ per α design.
        self.assertIn(".agent-rally-point", result["channel_dir"])


if __name__ == "__main__":
    unittest.main()
