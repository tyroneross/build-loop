#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for hook_hygiene_lint.py. Stdlib only.

Run: ``python3 scripts/test_hook_hygiene_lint.py``
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "hook_hygiene_lint.py"
REPO_ROOT = HERE.parent
REAL_HOOKS = REPO_ROOT / "hooks" / "hooks.json"


def _write_json(payload: dict, suffix: str = ".json") -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False)
    json.dump(payload, f)
    f.flush()
    f.close()
    return f.name


def _write_text(text: str, suffix: str = ".sh") -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False)
    f.write(text)
    f.flush()
    f.close()
    return f.name


def run_script(hooks_path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--hooks", hooks_path, "--json"],
        capture_output=True, text=True, timeout=30,
    )


# ---------------------------------------------------------------------------
# Fixture: build-loop's own hooks.json must PASS
# ---------------------------------------------------------------------------

class RealHooksPassTests(unittest.TestCase):
    """The repo's own hooks/hooks.json is the positive model — it must lint
    cleanly with zero findings. If this test fails, EITHER the linter has a
    false positive (fix the linter) OR build-loop's own hook hygiene drifted
    (fix the hook)."""

    def test_repo_hooks_lints_clean(self) -> None:
        self.assertTrue(REAL_HOOKS.is_file(),
                         f"Expected real hooks at {REAL_HOOKS}")
        r = run_script(str(REAL_HOOKS))
        payload = json.loads(r.stdout)
        self.assertEqual(
            r.returncode, 0,
            f"build-loop's own hooks/hooks.json must lint clean. "
            f"Findings:\n{json.dumps(payload['findings'], indent=2)}",
        )
        self.assertEqual(payload["summary"]["total"], 0)


# ---------------------------------------------------------------------------
# Fixture: synthetic bad hooks.json produces ≥1 finding per rule class
# ---------------------------------------------------------------------------

BAD_HOOKS = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    # HH001: bare risky binary
                    {"type": "command", "command": "node --version"},
                    # HH004: advisory deny without safety marker
                    {"type": "command",
                     "command": 'printf \'{"permissionDecision":"deny"}\''},
                ],
            }
        ],
        "Stop": [
            {
                "matcher": "",
                "hooks": [
                    # HH003: external bin (rally), no fail-open tail
                    {"type": "command", "command": "rally announce stop"},
                ],
            }
        ],
    }
}


class SyntheticBadHooksTests(unittest.TestCase):
    """The synthetic bad fixture must produce at least one finding per class
    (HH001, HH003, HH004). HH002 needs a real shell script on disk; covered
    in HH002ScriptScanTests below."""

    def test_bad_fixture_flags_each_class(self) -> None:
        path = _write_json(BAD_HOOKS)
        try:
            r = run_script(path)
            self.assertEqual(r.returncode, 1, f"stdout: {r.stdout}")
            payload = json.loads(r.stdout)
            rules = {f["rule_id"] for f in payload["findings"]}
            for required in ("HH001", "HH003", "HH004"):
                self.assertIn(required, rules,
                              f"missing {required} in {sorted(rules)}; "
                              f"findings: {payload['findings']}")
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# HH001 — bare-binary detection
# ---------------------------------------------------------------------------

class HH001BareBinaryTests(unittest.TestCase):

    def test_bare_node_flagged(self) -> None:
        path = _write_json({"hooks": {"Stop": [
            {"matcher": "", "hooks": [
                {"type": "command", "command": "node -e 'console.log(1)'"},
            ]}
        ]}})
        try:
            r = run_script(path)
            self.assertEqual(r.returncode, 1)
            payload = json.loads(r.stdout)
            hh001 = [f for f in payload["findings"] if f["rule_id"] == "HH001"]
            self.assertEqual(len(hh001), 1)
            self.assertEqual(hh001[0]["evidence"]["binary"], "node")
        finally:
            os.unlink(path)

    def test_absolute_path_not_flagged(self) -> None:
        path = _write_json({"hooks": {"Stop": [
            {"matcher": "", "hooks": [
                {"type": "command",
                 "command": '/opt/homebrew/bin/node -e "1"; exit 0'},
            ]}
        ]}})
        try:
            r = run_script(path)
            payload = json.loads(r.stdout)
            hh001 = [f for f in payload["findings"] if f["rule_id"] == "HH001"]
            self.assertEqual(hh001, [], payload["findings"])
        finally:
            os.unlink(path)

    def test_command_v_guard_not_flagged(self) -> None:
        path = _write_json({"hooks": {"Stop": [
            {"matcher": "", "hooks": [
                {"type": "command",
                 "command": 'command -v node >/dev/null 2>&1 && node -v; exit 0'},
            ]}
        ]}})
        try:
            r = run_script(path)
            payload = json.loads(r.stdout)
            hh001 = [f for f in payload["findings"] if f["rule_id"] == "HH001"]
            self.assertEqual(hh001, [], payload["findings"])
        finally:
            os.unlink(path)

    def test_plugin_root_path_not_flagged(self) -> None:
        path = _write_json({"hooks": {"SessionStart": [
            {"matcher": "", "hooks": [
                {"type": "command",
                 "command": ('bash "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}'
                              '/hooks/foo.sh"; exit 0')},
            ]}
        ]}})
        try:
            r = run_script(path)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["findings"], [])
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# HH002 — set -e + unguarded subst in a referenced script
# ---------------------------------------------------------------------------

BAD_SCRIPT_CONTENTS = """#!/usr/bin/env bash
set -euo pipefail
meta="$(printf 'x' | node -e 'process.stdout.write("y")')"
echo "$meta"
"""

GOOD_SCRIPT_CONTENTS = """#!/usr/bin/env bash
set -euo pipefail
if command -v node >/dev/null 2>&1; then
  meta="$(printf 'x' | node -e 'process.stdout.write("y")')"
else
  meta=""
fi
echo "$meta"
exit 0
"""


class HH002ScriptScanTests(unittest.TestCase):
    """HH002 reads referenced bash scripts. We construct a tiny repo-like
    layout under a temp dir: ``<tmp>/hooks/hooks.json`` + ``<tmp>/scripts/bad.sh``
    so the lint's `_referenced_script` resolver works the same way it does on
    the real repo."""

    def _make_repo(self, script_contents: str) -> tuple[str, str]:
        tmp = tempfile.mkdtemp(prefix="hhlint-")
        repo = Path(tmp)
        (repo / "hooks").mkdir()
        (repo / "scripts").mkdir()
        script = repo / "scripts" / "test.sh"
        script.write_text(script_contents)
        hooks = repo / "hooks" / "hooks.json"
        hooks.write_text(json.dumps({
            "hooks": {
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command",
                             "command": 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/test.sh"; exit 0'},
                        ],
                    }
                ]
            }
        }))
        return str(hooks), tmp

    def test_set_e_plus_unguarded_subst_flagged(self) -> None:
        hooks_path, tmp = self._make_repo(BAD_SCRIPT_CONTENTS)
        try:
            r = run_script(hooks_path)
            payload = json.loads(r.stdout)
            hh002 = [f for f in payload["findings"] if f["rule_id"] == "HH002"]
            self.assertEqual(len(hh002), 1, payload["findings"])
            self.assertEqual(hh002[0]["evidence"]["binary"], "node")
            self.assertIsNotNone(hh002[0]["script_path"])
        finally:
            import shutil
            shutil.rmtree(tmp)

    def test_set_e_plus_guarded_subst_not_flagged(self) -> None:
        hooks_path, tmp = self._make_repo(GOOD_SCRIPT_CONTENTS)
        try:
            r = run_script(hooks_path)
            payload = json.loads(r.stdout)
            hh002 = [f for f in payload["findings"] if f["rule_id"] == "HH002"]
            self.assertEqual(hh002, [], payload["findings"])
        finally:
            import shutil
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# HH003 — fail-open tail
# ---------------------------------------------------------------------------

class HH003FailOpenTests(unittest.TestCase):

    def test_bare_external_without_exit0_flagged(self) -> None:
        path = _write_json({"hooks": {"Stop": [
            {"matcher": "", "hooks": [
                {"type": "command", "command": "rally announce stop"},
            ]}
        ]}})
        try:
            r = run_script(path)
            payload = json.loads(r.stdout)
            hh003 = [f for f in payload["findings"] if f["rule_id"] == "HH003"]
            self.assertEqual(len(hh003), 1)
        finally:
            os.unlink(path)

    def test_explicit_exit0_tail_not_flagged(self) -> None:
        path = _write_json({"hooks": {"Stop": [
            {"matcher": "", "hooks": [
                {"type": "command", "command": "rally announce stop; exit 0"},
            ]}
        ]}})
        try:
            r = run_script(path)
            payload = json.loads(r.stdout)
            hh003 = [f for f in payload["findings"] if f["rule_id"] == "HH003"]
            self.assertEqual(hh003, [])
        finally:
            os.unlink(path)

    def test_pipe_or_true_not_flagged(self) -> None:
        path = _write_json({"hooks": {"Stop": [
            {"matcher": "", "hooks": [
                {"type": "command", "command": "rally announce stop || true"},
            ]}
        ]}})
        try:
            r = run_script(path)
            payload = json.loads(r.stdout)
            hh003 = [f for f in payload["findings"] if f["rule_id"] == "HH003"]
            self.assertEqual(hh003, [])
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# HH004 — advisory deny / block
# ---------------------------------------------------------------------------

class HH004AdvisoryEnforcementTests(unittest.TestCase):

    def test_permission_decision_deny_flagged(self) -> None:
        path = _write_json({"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command",
                 "command": 'printf \'{"permissionDecision":"deny"}\''},
            ]}
        ]}})
        try:
            r = run_script(path)
            payload = json.loads(r.stdout)
            hh004 = [f for f in payload["findings"] if f["rule_id"] == "HH004"]
            self.assertEqual(len(hh004), 1, payload["findings"])
        finally:
            os.unlink(path)

    def test_decision_block_flagged(self) -> None:
        path = _write_json({"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command",
                 "command": 'printf \'{"decision":"block"}\''},
            ]}
        ]}})
        try:
            r = run_script(path)
            payload = json.loads(r.stdout)
            hh004 = [f for f in payload["findings"] if f["rule_id"] == "HH004"]
            self.assertEqual(len(hh004), 1, payload["findings"])
        finally:
            os.unlink(path)

    def test_safety_marker_exempts(self) -> None:
        path = _write_json({"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command",
                 "command": ('# safety gate: block dangerous bash\n'
                              'printf \'{"permissionDecision":"deny"}\'')},
            ]}
        ]}})
        try:
            r = run_script(path)
            payload = json.loads(r.stdout)
            hh004 = [f for f in payload["findings"] if f["rule_id"] == "HH004"]
            self.assertEqual(hh004, [], payload["findings"])
        finally:
            os.unlink(path)

    def test_bl_safety_gate_env_exempts(self) -> None:
        path = _write_json({"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command",
                 "command": ('[ "$BL_SAFETY_GATE" = "1" ] && '
                              'printf \'{"permissionDecision":"deny"}\'; '
                              'exit 0')},
            ]}
        ]}})
        try:
            r = run_script(path)
            payload = json.loads(r.stdout)
            hh004 = [f for f in payload["findings"] if f["rule_id"] == "HH004"]
            self.assertEqual(hh004, [], payload["findings"])
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# CLI / error handling
# ---------------------------------------------------------------------------

class CliTests(unittest.TestCase):

    def test_missing_file_returns_2(self) -> None:
        r = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--hooks", "/tmp/nonexistent-hooks-file.json"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_malformed_json_returns_2(self) -> None:
        path = _write_text("{not json", suffix=".json")
        try:
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--hooks", path],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 2, r.stderr)
        finally:
            os.unlink(path)

    def test_self_test_passes(self) -> None:
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--self-test"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
