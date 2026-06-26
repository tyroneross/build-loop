#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Reaper FACADE contract tests.

Reaping is Rust-only. These tests pin the facade contract:
  * below full capability → refuse (no reap, capability-marked report)
  * full capability → delegate to ``rally sessions --reap`` and surface the result

The retired cross-language parity sweep (presence/claim-index/lead deletion in
Python, double-pinned against golden fixtures) is GONE — its tests went with it.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from rally_point import capability  # noqa: E402
from rally_point import discovery_bridge  # noqa: E402
from rally_point import reaper as _reaper  # noqa: E402


class ReaperRefusesBelowFullTests(unittest.TestCase):
    """Below full capability the facade must refuse — never a shadow sweep."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="reaper-facade-"))
        self.channel = self.tmp / ".build-loop" / "chan"
        self.channel.mkdir(parents=True)
        self._old_internal = os.environ.get("BUILD_LOOP_BRIDGE_INTERNAL_ONLY")
        self._old_apps = os.environ.get("BUILD_LOOP_APPS_ROOT")
        os.environ["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = "1"
        os.environ["BUILD_LOOP_APPS_ROOT"] = str(self.tmp / "apps")
        discovery_bridge.clear_cache()

    def tearDown(self) -> None:
        for k, v in (
            ("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", self._old_internal),
            ("BUILD_LOOP_APPS_ROOT", self._old_apps),
        ):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        discovery_bridge.clear_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_degraded_refuses_and_marks_envelope(self) -> None:
        report = _reaper.reap_channel(self.channel, self.tmp, apply=True)
        self.assertEqual(report["capability_level"], capability.DEGRADED_BREADCRUMB)
        self.assertEqual(report["coordination_unavailable"], "no_binary")
        self.assertTrue(report["deferred_to_rust"])
        self.assertEqual(report["reaped"], [])
        self.assertIn("Rust-only", report["detail"])

    def test_dry_run_also_refused_below_full(self) -> None:
        # apply=False still resolves capability; below full it never claims to reap.
        report = _reaper.reap_channel(self.channel, self.tmp, apply=False)
        self.assertTrue(report["deferred_to_rust"])
        self.assertEqual(report["reaped"], [])


class ReaperDelegatesAtFullTests(unittest.TestCase):
    """Full capability → shell ``rally sessions --reap`` and surface the result."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="reaper-full-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        self.channel = self.tmp / "home" / ".agent-rally-point" / "apps" / "repo_fake"
        self.channel.mkdir(parents=True)
        self.calls = self.tmp / "calls.txt"
        # Fake rally exposing setup (so discovery resolves full) + sessions --reap.
        self.fake = self.tmp / "rally"
        self.fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"channel = {str(self.channel)!r}\n"
            f"calls = {str(self.calls)!r}\n"
            "a = sys.argv[1:]\n"
            "if not a:\n"
            "    print('rally stop <tool>\\nrally post --kind <kind>'); raise SystemExit(2)\n"
            "if a == ['setup','--json']:\n"
            "    print(json.dumps({'ok':True,'channel':channel})); raise SystemExit(0)\n"
            "if a[:1] == ['sessions']:\n"
            "    open(calls,'a').write(' '.join(a)+'\\n')\n"
            "    reaped = ['stale-sess'] if '--reap' in a else []\n"
            "    print(json.dumps({'command':'sessions','data':{'sessions':{'reaped':reaped}}}))\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(2)\n",
            encoding="utf-8",
        )
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self._old_bin = os.environ.get("AGENT_RALLY_BINARY")
        self._old_internal = os.environ.get("BUILD_LOOP_BRIDGE_INTERNAL_ONLY")
        os.environ["AGENT_RALLY_BINARY"] = str(self.fake)
        os.environ.pop("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", None)
        discovery_bridge.clear_cache()

    def tearDown(self) -> None:
        if self._old_bin is None:
            os.environ.pop("AGENT_RALLY_BINARY", None)
        else:
            os.environ["AGENT_RALLY_BINARY"] = self._old_bin
        if self._old_internal is not None:
            os.environ["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = self._old_internal
        discovery_bridge.clear_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_capability_resolves(self) -> None:
        env = discovery_bridge.resolve(self.repo)
        self.assertEqual(env.capability_level, capability.FULL)

    def test_apply_delegates_to_rally_sessions_reap(self) -> None:
        report = _reaper.reap_channel(self.channel, self.repo, apply=True)
        self.assertEqual(report["capability_level"], capability.FULL)
        self.assertFalse(report["deferred_to_rust"])
        self.assertEqual(report["reaped"], ["stale-sess"])
        self.assertIn("sessions --reap", self.calls.read_text())

    def test_dry_run_calls_sessions_without_reap(self) -> None:
        report = _reaper.reap_channel(self.channel, self.repo, apply=False)
        self.assertEqual(report["capability_level"], capability.FULL)
        calls = self.calls.read_text()
        self.assertIn("sessions --json", calls)
        self.assertNotIn("--reap", calls)


if __name__ == "__main__":
    unittest.main()
