# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the deprecated ``app_pulse`` alias boundary."""
from __future__ import annotations

import importlib
import sys
import unittest
import warnings
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
APP_PULSE_DIR = SCRIPTS_DIR / "app_pulse"


class AppPulseAliasTests(unittest.TestCase):
    def setUp(self) -> None:
        self._sys_path = list(sys.path)
        self._cleanup_modules()
        warnings.simplefilter("ignore", DeprecationWarning)

    def tearDown(self) -> None:
        sys.path[:] = self._sys_path
        self._cleanup_modules()
        warnings.simplefilter("default", DeprecationWarning)

    def _cleanup_modules(self) -> None:
        for name in list(sys.modules):
            if (
                name == "app_pulse"
                or name.startswith("app_pulse.")
                or name == "scripts.app_pulse"
                or name.startswith("scripts.app_pulse.")
                or name in {"channel_paths", "post", "presence"}
            ):
                sys.modules.pop(name, None)

    def test_scripts_package_import_routes_to_rally_point(self) -> None:
        sys.path.insert(0, str(ROOT))

        legacy = importlib.import_module("scripts.app_pulse.post")
        target = importlib.import_module("scripts.rally_point.post")

        self.assertIs(legacy, target)

    def test_top_level_package_import_routes_to_rally_point(self) -> None:
        sys.path.insert(0, str(SCRIPTS_DIR))

        legacy = importlib.import_module("app_pulse.presence")
        target = importlib.import_module("rally_point.presence")

        self.assertIs(legacy, target)

    def test_bare_legacy_module_import_routes_to_rally_point(self) -> None:
        sys.path.insert(0, str(APP_PULSE_DIR))

        legacy = importlib.import_module("channel_paths")
        target = importlib.import_module("rally_point.channel_paths")

        self.assertIs(legacy, target)


if __name__ == "__main__":
    unittest.main()
