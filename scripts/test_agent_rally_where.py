#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``scripts/agent_rally.py where`` delegation contract.

PR #48 added the ``where`` subcommand using build-loop's internal
``channel_paths`` resolution (legacy path only). This PR adds a delegation
layer: when ``agent-rally-point`` is installed, ``where`` asks its
``discover()`` for the channel_dir (protocol-of-record — canonical→legacy
fallback chain); otherwise it falls back to the existing internal
resolution. The JSON envelope reports which path was taken via
``resolved_via``.

These tests cover both paths deterministically by installing a fake
``agent_rally_point`` package in a tmp dir and toggling PYTHONPATH.
"""
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
sys.path.insert(0, str(HERE))

from rally_point import channel_paths  # noqa: E402
from rally_point import discovery_bridge as _bridge  # test isolation


class AgentRallyWhereTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-rally-where-"))
        self.apps = self.tmp / "apps"
        self.workdir = self.tmp / "repo"
        self.workdir.mkdir()
        self._old_apps_root = os.environ.get("BUILD_LOOP_APPS_ROOT")
        os.environ["BUILD_LOOP_APPS_ROOT"] = str(self.apps)
        os.environ["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = "1"
        from rally_point import discovery_bridge as _bridge
        _bridge.clear_cache()
        subprocess.run(
            ["git", "init"], cwd=self.workdir, check=True, capture_output=True
        )

    def tearDown(self) -> None:
        if self._old_apps_root is None:
            os.environ.pop("BUILD_LOOP_APPS_ROOT", None)
        else:
            os.environ["BUILD_LOOP_APPS_ROOT"] = self._old_apps_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _install_fake_arp(self, channel_dir: str, slug: str) -> str:
        fake_root = self.tmp / "fake_arp"
        pkg = fake_root / "agent_rally_point"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "discover.py").write_text(
            "def discover(cwd=None):\n"
            "    return {\n"
            f"        'installed': True,\n"
            f"        'channel_dir': {channel_dir!r},\n"
            f"        'app_slug': {slug!r},\n"
            "    }\n"
        )
        return str(fake_root)

    def _run_where(self, env: dict[str, str] | None = None, *,
                   json_mode: bool = True) -> subprocess.CompletedProcess:
        cmd = [
            sys.executable, str(HERE / "agent_rally.py"), "where",
            "--workdir", str(self.workdir),
        ]
        if json_mode:
            cmd.append("--json")
        return subprocess.run(
            cmd, capture_output=True, text=True, check=True, env=env,
        )

    def test_delegates_to_agent_rally_point_when_installed(self) -> None:
        """When agent_rally_point is importable AND discover() returns
        installed=true, ``where --json`` carries the discovered values
        verbatim and reports ``resolved_via: "agent-rally-point"``.
        """
        fake_channel = str(self.tmp / "discovered_channel")
        fake_slug = "slug-from-discover"
        fake_path = self._install_fake_arp(fake_channel, fake_slug)
        env = os.environ.copy()
        # β1 follow-up: this test explicitly exercises the canonical-
        # delegation path. setUp() sets BUILD_LOOP_BRIDGE_INTERNAL_ONLY=1
        # for the OTHER tests; pop it for this subprocess so the bridge
        # actually probes Python import.
        env.pop("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", None)
        # Strip PATH so the real pipx-installed agent-rally-discover
        # cannot shadow the Python-import probe under test.
        env["PATH"] = "/usr/bin:/bin"
        env["PYTHONPATH"] = fake_path + os.pathsep + env.get("PYTHONPATH", "")
        r = self._run_where(env=env)
        result = json.loads(r.stdout)
        self.assertEqual(result["resolved_via"], "agent-rally-point")
        self.assertEqual(result["channel_dir"], fake_channel)
        self.assertEqual(result["app_slug"], fake_slug)

    def test_falls_back_to_internal_when_agent_rally_point_missing(self) -> None:
        """When agent_rally_point is NOT importable, ``where --json``
returns the build-loop-internal resolution and reports
``resolved_via: "build-loop-internal"``. Uses ``-I`` to isolate
from any system-wide install.
        """
        empty = self.tmp / "empty"
        empty.mkdir(exist_ok=True)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(empty)
        cmd = [
            sys.executable, "-I", str(HERE / "agent_rally.py"), "where",
            "--workdir", str(self.workdir), "--json",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           check=True, env=env)
        result = json.loads(r.stdout)
        self.assertEqual(result["resolved_via"], "build-loop-internal")
        slug = channel_paths.app_slug(self.workdir)
        self.assertEqual(result["app_slug"], slug)
        self.assertEqual(
            result["channel_dir"], str(channel_paths.app_channel_dir(slug))
        )


if __name__ == "__main__":
    unittest.main()
