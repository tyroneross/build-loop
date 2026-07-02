#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for ``rally`` binary candidate discovery ORDER.

Root cause under test: a stale sibling ``agent-rally-point/target/{release,
debug}/rally`` dev build used to be probed BEFORE the fetch-on-install pinned
cache and BEFORE ``rally`` on ``$PATH``. Since ``rust_rally_binary()`` accepts
the FIRST candidate that merely passes a help-text surface check (no version
comparison), an old local ``cargo build`` output could silently win over the
correct pinned release.

These tests pin the corrected priority order — env override → pinned cache →
PATH → sibling dev builds (last) — WITHOUT ever invoking a real ``rally``
binary: ``_rally_binary_supports_required_surface`` (the only function that
shells out) is monkeypatched to a pure-Python predicate over candidate path
strings, and ``_rally_binary_candidates`` itself never touches the filesystem
except via ``Path.is_file()`` on the (test-created, empty) pinned-cache
placeholder file.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from rally_point import binary_fetch, discovery_bridge  # noqa: E402


class DiscoveryOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="rally-discovery-order-"))

        # Placeholder pinned-cache file: never executed (the surface-check
        # function is mocked below), only its path/existence matters.
        self.pinned_cache = self.tmp / "pinned-cache-rally"
        self.pinned_cache.write_text("pinned-placeholder", encoding="utf-8")

        self.sibling_release = self.tmp / "sibling" / "target" / "release" / "rally"
        self.sibling_release.parent.mkdir(parents=True)
        self.sibling_release.write_text("sibling-placeholder", encoding="utf-8")

        self._env_keys = (
            "AGENT_RALLY_BINARY",
            "BUILD_LOOP_DISABLE_SIBLING_RALLY",
            "BUILD_LOOP_APPS_ROOT",
            "BUILD_LOOP_DISABLE_BINARY_FETCH",
        )
        self._saved_env = {k: os.environ.get(k) for k in self._env_keys}
        for k in self._env_keys:
            os.environ.pop(k, None)

        # Deterministic sibling-root resolution regardless of the real
        # filesystem the test happens to run on.
        self._roots_patcher = mock.patch.object(
            discovery_bridge,
            "_repo_associated_roots",
            return_value=[self.tmp / "sibling"],
        )
        self._roots_patcher.start()

        # Pinned cache path always resolves to our placeholder file.
        self._cache_patcher = mock.patch.object(
            binary_fetch, "cached_binary_path", return_value=self.pinned_cache
        )
        self._cache_patcher.start()

        discovery_bridge.clear_cache()

    def tearDown(self) -> None:
        self._roots_patcher.stop()
        self._cache_patcher.stop()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        discovery_bridge.clear_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _candidates(self) -> list[str]:
        return discovery_bridge._rally_binary_candidates(None)

    def test_pinned_cache_precedes_sibling_build(self) -> None:
        """Pinned cache and PATH must both be probed before any sibling build."""
        with mock.patch.object(shutil, "which", return_value=None):
            candidates = self._candidates()

        pinned_idx = candidates.index(str(self.pinned_cache))
        sibling_idx = candidates.index(str(self.sibling_release))
        self.assertLess(
            pinned_idx, sibling_idx,
            "pinned cache must be discovered before the sibling dev build",
        )

    def test_pinned_cache_selected_over_stale_sibling(self) -> None:
        """When both pass the surface check, rust_rally_binary() must pick the pin."""
        passing = {str(self.pinned_cache), str(self.sibling_release)}
        with mock.patch.object(shutil, "which", return_value=None), \
             mock.patch.object(
                 discovery_bridge,
                 "_rally_binary_supports_required_surface",
                 side_effect=lambda path: path in passing,
             ):
            chosen = discovery_bridge.rust_rally_binary(None)

        self.assertEqual(chosen, str(self.pinned_cache))

    def test_sibling_build_reachable_when_nothing_higher_priority(self) -> None:
        """Sibling dev builds must still resolve when the pin/PATH are absent."""
        os.environ["BUILD_LOOP_DISABLE_BINARY_FETCH"] = "1"  # no pinned cache tier
        with mock.patch.object(shutil, "which", return_value=None), \
             mock.patch.object(
                 discovery_bridge,
                 "_rally_binary_supports_required_surface",
                 side_effect=lambda path: path == str(self.sibling_release),
             ):
            chosen = discovery_bridge.rust_rally_binary(None)

        self.assertEqual(chosen, str(self.sibling_release))

    def test_path_binary_precedes_sibling_build(self) -> None:
        """A rally on $PATH must be probed before any sibling build."""
        path_rally = str(self.tmp / "path-rally")
        with mock.patch.object(shutil, "which", return_value=path_rally):
            candidates = self._candidates()

        path_idx = candidates.index(path_rally)
        sibling_idx = candidates.index(str(self.sibling_release))
        self.assertLess(path_idx, sibling_idx)

    def test_env_override_wins_over_everything(self) -> None:
        """AGENT_RALLY_BINARY must win over pinned cache, PATH, and sibling."""
        env_rally = str(self.tmp / "env-rally")
        os.environ["AGENT_RALLY_BINARY"] = env_rally
        passing = {env_rally, str(self.pinned_cache), str(self.sibling_release)}
        with mock.patch.object(shutil, "which", return_value=str(self.tmp / "path-rally")), \
             mock.patch.object(
                 discovery_bridge,
                 "_rally_binary_supports_required_surface",
                 side_effect=lambda path: path in passing,
             ):
            candidates = self._candidates()
            chosen = discovery_bridge.rust_rally_binary(None)

        self.assertEqual(candidates[0], env_rally)
        self.assertEqual(chosen, env_rally)


if __name__ == "__main__":
    unittest.main()
