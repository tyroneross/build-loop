#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Pinned-binary fetch tests.

Two layers:
  * Unit (always run): host-triple mapping, version-pin parsing, cache path,
    unsupported-host → None.
  * Native integration (run when the pinned asset is reachable for this host):
    fetch the REAL v0.1.3 binary, verify sha256 + version pin, and resolve a
    full-capability channel through discovery_bridge using the fetched binary.
    Skips cleanly (never fails) when offline / asset absent / unsupported host.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from rally_point import binary_fetch as bf  # noqa: E402
from rally_point import capability  # noqa: E402
from rally_point import discovery_bridge  # noqa: E402


class FetchUnitTests(unittest.TestCase):
    def test_host_triple_supported(self) -> None:
        with mock.patch("platform.system", return_value="Darwin"), \
             mock.patch("platform.machine", return_value="arm64"):
            self.assertEqual(bf.host_triple(), "aarch64-apple-darwin")
        with mock.patch("platform.system", return_value="Linux"), \
             mock.patch("platform.machine", return_value="x86_64"):
            self.assertEqual(bf.host_triple(), "x86_64-unknown-linux-gnu")

    def test_unsupported_host_returns_none(self) -> None:
        # Intel macOS has NO published v0.1.3 asset → unsupported → loud upstream.
        with mock.patch("platform.system", return_value="Darwin"), \
             mock.patch("platform.machine", return_value="x86_64"):
            self.assertIsNone(bf.host_triple())
            self.assertIsNone(bf.ensure_binary())
        # musl/Alpine, exotic arch → unsupported.
        with mock.patch("platform.system", return_value="Linux"), \
             mock.patch("platform.machine", return_value="riscv64"):
            self.assertIsNone(bf.host_triple())

    def test_cache_path_is_pinned_and_namespaced(self) -> None:
        p = bf.cached_binary_path()
        self.assertIn("build-loop", str(p))   # namespaced away from ARP's ~/.cache/rally
        self.assertTrue(p.name.endswith(bf.PINNED_TAG))

    def test_pin_constants_consistent(self) -> None:
        self.assertEqual(bf.PINNED_TAG, f"v{bf.PINNED_VERSION}")


def _asset_reachable_for_host() -> bool:
    """True when this host has a published asset AND the release URL is reachable."""
    triple = bf.host_triple()
    if triple is None:
        return False
    try:
        raw = bf._http_get(f"{bf._RELEASE_BASE}/rally-{triple}.sha256", 8)
        return bool(raw)
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(
    _asset_reachable_for_host(),
    "pinned v0.1.3 asset not reachable for this host (offline / unsupported)",
)
class FetchNativeIntegrationTests(unittest.TestCase):
    """Runs the FETCHED v0.1.3 binary end-to-end from the build-loop cache."""

    def test_fetch_verify_pin(self) -> None:
        binary = bf.ensure_binary()
        self.assertIsNotNone(binary, "fetch should succeed when the asset is reachable")
        self.assertTrue(os.access(binary, os.X_OK))
        # Version pin: the fetched binary reports EXACTLY the pinned version.
        self.assertTrue(bf.version_matches_pin(binary))
        proc = subprocess.run(
            [str(binary), "version"], capture_output=True, text=True, timeout=5
        )
        self.assertIn(bf.PINNED_VERSION, proc.stdout + proc.stderr)

    def test_fetched_binary_tier_resolves_directly(self) -> None:
        # Exercise the fetched-binary discovery tier directly (proves the tier
        # provisions + runs `rally setup --json` to resolve a channel).
        tmp = Path(tempfile.mkdtemp(prefix="fetch-tier-"))
        try:
            repo = tmp / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            env = discovery_bridge._try_fetched_binary(repo)
            self.assertIsNotNone(env, "fetched-binary tier should resolve a channel")
            self.assertEqual(env.resolved_via, "fetched-binary")
            self.assertEqual(env.capability_level, capability.FULL)
            # v0.1.3 exposes the repo-local (protocol 1.0) surface.
            self.assertEqual(env.protocol_version, "1.0")
            self.assertTrue(env.channel_dir.endswith(".rally"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_discovery_resolves_full_via_fetched_binary(self) -> None:
        # No system/sibling/PATH binary in view → discovery must FETCH and resolve
        # a full-capability channel through the fetched binary.
        bf.ensure_binary()  # ensure cached
        tmp = Path(tempfile.mkdtemp(prefix="fetch-integ-"))
        try:
            repo = tmp / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            env = dict(os.environ)
            # Hide live binaries so the fetched-binary tier is the resolver.
            env_patches = {
                "AGENT_RALLY_BINARY": "",
                "BUILD_LOOP_DISABLE_SIBLING_RALLY": "1",
            }
            with mock.patch.dict(os.environ, env_patches), \
                 mock.patch("shutil.which", return_value=None):
                discovery_bridge.clear_cache()
                resolved = discovery_bridge.resolve(repo)
            # Resolution yields a full-capability native source (the fetched
            # binary, or a live binary that exposes the same surface — both are
            # full). The point of this test is that with no live binary forced
            # into view, discovery still reaches a FULL channel by provisioning.
            self.assertEqual(resolved.capability_level, capability.FULL)
            self.assertNotEqual(resolved.resolved_via, "build-loop-internal")
        finally:
            discovery_bridge.clear_cache()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
