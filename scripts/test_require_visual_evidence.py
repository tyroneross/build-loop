#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for skills/build-loop/scanners/require-visual-evidence.mjs (BL-1 gate).

Stdlib only. Run: python3 scripts/test_require_visual_evidence.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SCANNER = REPO / "skills" / "build-loop" / "scanners" / "require-visual-evidence.mjs"


def _have_node() -> bool:
    return shutil.which("node") is not None


def _run(envelope: dict) -> tuple[int, dict]:
    """Write envelope to tmpfile, invoke scanner, return (exit_code, parsed_stdout)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(envelope, fh)
        path = fh.name
    try:
        proc = subprocess.run(
            ["node", str(SCANNER), "--envelope-file", path],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    try:
        parsed = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        parsed = {"_raw_stdout": proc.stdout, "_raw_stderr": proc.stderr}
    return proc.returncode, parsed


@unittest.skipUnless(_have_node(), "node not available")
class RequireVisualEvidenceTests(unittest.TestCase):
    # ---- non-UI / N/A paths (must pass cleanly) ----

    def test_null_ui_target_passes(self):
        code, out = _run({
            "uiTarget": None,
            "files_changed": ["src/api.ts"],
            "verification": "ran tests",
        })
        self.assertEqual(code, 0, out)
        self.assertEqual(out["verdict"], "pass")
        self.assertFalse(out["ui_changed"])

    def test_non_ui_files_pass(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["scripts/foo.py", "README.md"],
            "verification": "ran python tests",
        })
        self.assertEqual(code, 0, out)
        self.assertEqual(out["verdict"], "pass")
        self.assertFalse(out["ui_changed"])

    # ---- UI files + valid evidence (must pass) ----

    def test_screenshot_token_passes(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["Sources/Views/Pane.swift"],
            "verification": "Launched the app (pid: 4421) and saved /tmp/pane.png screenshot.",
        })
        self.assertEqual(code, 0, out)
        self.assertEqual(out["verdict"], "pass")
        self.assertTrue(out["ui_changed"])

    def test_ax_tree_dump_passes(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["Sources/Views/Pane.swift"],
            "verification": "Captured AX-tree dump via native-ax-driver; pid=4421.",
        })
        self.assertEqual(code, 0, out)
        self.assertEqual(out["verdict"], "pass")

    def test_scan_macos_passes(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["App/Views/Main.swift"],
            "verification": "IBR scan_macos returned no findings.",
        })
        self.assertEqual(code, 0, out)
        self.assertEqual(out["verdict"], "pass")

    def test_ui_validator_passes_for_web(self):
        code, out = _run({
            "uiTarget": "web",
            "files_changed": ["app/page.tsx"],
            "verification": "ui-validator ran against dev server; no findings.",
        })
        self.assertEqual(code, 0, out)
        self.assertEqual(out["verdict"], "pass")

    def test_evidence_path_alone_passes(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["Sources/Views/Pane.swift"],
            "verification": "verified",
            "evidence_paths": ["artifacts/pane-pid4421.png"],
        })
        self.assertEqual(code, 0, out)
        self.assertEqual(out["verdict"], "pass")

    # ---- UI files + symbol-only evidence (must REJECT — the BL-1 fix) ----

    def test_nm_only_rejects(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["Sources/Views/Pane.swift"],
            "verification": "Ran nm EasyTerminal.app/Contents/MacOS/EasyTerminal; identifiers present.",
        })
        self.assertEqual(code, 2, out)
        self.assertEqual(out["verdict"], "reject")
        self.assertTrue(out["symbol_only"])
        self.assertIn("scan result", out["reason"].lower())

    def test_strings_only_rejects(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["App/Views/Main.swift"],
            "verification": "strings EasyTerminal.app shows the new label text.",
        })
        self.assertEqual(code, 2, out)
        self.assertEqual(out["verdict"], "reject")

    def test_grep_over_sources_rejects(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["Sources/Views/Pane.swift"],
            "verification": "git grep over Sources/ shows the new identifier; compiles cleanly.",
        })
        self.assertEqual(code, 2, out)
        self.assertEqual(out["verdict"], "reject")

    def test_compile_only_rejects(self):
        code, out = _run({
            "uiTarget": "web",
            "files_changed": ["components/Foo.tsx"],
            "verification": "pnpm build compiles cleanly.",
        })
        self.assertEqual(code, 2, out)
        self.assertEqual(out["verdict"], "reject")

    # ---- UI files + ambiguous evidence (must WARN) ----

    def test_empty_verification_warns(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["Sources/Views/Pane.swift"],
            "verification": "",
        })
        self.assertEqual(code, 1, out)
        self.assertEqual(out["verdict"], "warn")

    def test_unrelated_text_warns(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["Sources/Views/Pane.swift"],
            "verification": "Refactored layout logic for clarity.",
        })
        self.assertEqual(code, 1, out)
        self.assertEqual(out["verdict"], "warn")

    # ---- malformed envelope ----

    def test_missing_envelope_arg_is_malformed(self):
        proc = subprocess.run(
            ["node", str(SCANNER)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 3)
        parsed = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(parsed["verdict"], "malformed")

    def test_invalid_json_is_malformed(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{not json")
            path = fh.name
        try:
            proc = subprocess.run(
                ["node", str(SCANNER), "--envelope-file", path],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(proc.returncode, 3)
        parsed = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(parsed["verdict"], "malformed")

    # ---- evidence-path alone with no symbol noise: pass ----

    def test_evidence_path_with_pid_passes(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["App/Views/Main.swift"],
            "verification": "Inspected running app, pid=8123.",
        })
        self.assertEqual(code, 0, out)
        self.assertEqual(out["verdict"], "pass")


if __name__ == "__main__":
    unittest.main()
