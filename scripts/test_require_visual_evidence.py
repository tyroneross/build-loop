#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for skills/build-loop/scanners/require-visual-evidence.mjs (BL-1 gate).

Stdlib only. Run: python3 scripts/test_require_visual_evidence.py
"""
from __future__ import annotations

import json
import struct
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SCANNER = REPO / "skills" / "build-loop" / "scanners" / "require-visual-evidence.mjs"


def _have_node() -> bool:
    return shutil.which("node") is not None


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def _valid_png() -> bytes:
    width, height = 320, 640
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = (b"\x00" + b"\xff\xff\xff" * width) * height
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header) + _png_chunk(b"IDAT", zlib.compress(pixels)) + _png_chunk(b"IEND", b"")


def _run(envelope: dict, *, with_probe: bool = False) -> tuple[int, dict]:
    """Write envelope to tmpfile, invoke scanner, return (exit_code, parsed_stdout)."""
    probe_path: Path | None = None
    if with_probe:
        with tempfile.NamedTemporaryFile("wb", suffix=".png", delete=False) as image:
            image.write(_valid_png())
            probe_path = Path(image.name)
        envelope = {**envelope, "layout_probe": {
            "container": "Today sheet, 320 pt usable width",
            "constraint": "320 pt window with large text",
            "content": "Long realistic task title in the populated state",
            "capture_target": "TruePace Today in running macOS app pid 4421",
            "evidence_path": str(probe_path),
            "inspection": "Text wraps without clipping or overlap; final action is visible.",
            "outcome": "pass",
        }}
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
        if probe_path is not None:
            probe_path.unlink(missing_ok=True)
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

    def test_nonrendering_swift_file_does_not_require_probe(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["Sources/Services/SessionStore.swift"],
            "verification": "Focused model tests passed.",
        })
        self.assertEqual(code, 0, out)
        self.assertFalse(out["ui_changed"])

    def test_explicit_ui_touched_requires_probe(self):
        code, out = _run({
            "uiTarget": "macos",
            "uiTouched": True,
            "files_changed": ["Sources/Custom/TodayContent.swift"],
            "verification": "Captured screenshot.",
        })
        self.assertEqual(code, 2, out)
        self.assertIn("layout_probe", out["reason"])

    # ---- UI files + valid evidence (must pass) ----

    def test_screenshot_token_passes(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["Sources/Views/Pane.swift"],
            "verification": "Launched the app (pid: 4421) and saved /tmp/pane.png screenshot.",
        }, with_probe=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["verdict"], "pass")
        self.assertTrue(out["ui_changed"])

    def test_ax_tree_dump_passes(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["Sources/Views/Pane.swift"],
            "verification": "Captured AX-tree dump via native-ax-driver; pid=4421.",
        }, with_probe=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["verdict"], "pass")

    def test_scan_macos_passes(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["App/Views/Main.swift"],
            "verification": "IBR scan_macos returned no findings.",
        }, with_probe=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["verdict"], "pass")

    def test_ui_validator_passes_for_web(self):
        code, out = _run({
            "uiTarget": "web",
            "files_changed": ["app/page.tsx"],
            "verification": "ui-validator ran against dev server; no findings.",
        }, with_probe=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["verdict"], "pass")

    def test_evidence_path_alone_passes(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["Sources/Views/Pane.swift"],
            "verification": "verified",
            "evidence_paths": ["artifacts/pane-pid4421.png"],
        }, with_probe=True)
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
        }, with_probe=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["verdict"], "pass")

    def test_screenshot_without_container_probe_rejects(self):
        code, out = _run({
            "uiTarget": "macos",
            "files_changed": ["App/Views/Main.swift"],
            "verification": "Captured screenshot of the running app.",
        })
        self.assertEqual(code, 2, out)
        self.assertIn("layout_probe", out["reason"])

    def test_probe_with_missing_file_rejects(self):
        code, out = _run({
            "uiTarget": "web",
            "files_changed": ["app/page.tsx"],
            "verification": "Captured screenshot.",
            "layout_probe": {
                "container": "Focus sheet",
                "constraint": "320 px width",
                "content": "Long heading",
                "capture_target": "Web app Today page at http://localhost:3000",
                "evidence_path": "/tmp/no-such-container-probe-truepace.png",
                "inspection": "No clipping or overlap in screenshot.",
                "outcome": "pass",
            },
        })
        self.assertEqual(code, 2, out)
        self.assertIn("does not exist", out["reason"])

    def test_text_renamed_png_rejects(self):
        with tempfile.NamedTemporaryFile("wb", suffix=".png", delete=False) as image:
            image.write(b"rendered-fixture")
            image_path = Path(image.name)
        try:
            code, out = _run({
                "uiTarget": "macos",
                "files_changed": ["Sources/Views/TodayView.swift"],
                "verification": "Captured screenshot.",
                "layout_probe": {
                    "container": "Today sheet, 320 pt usable width",
                    "constraint": "320 pt with large text",
                    "content": "Long task title",
                    "capture_target": "TruePace Today app pid 4421",
                    "evidence_path": str(image_path),
                    "inspection": "Text wraps without clipping.",
                    "outcome": "pass",
                },
            })
            self.assertEqual(code, 2, out)
            self.assertIn("not a PNG", out["reason"])
        finally:
            image_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
