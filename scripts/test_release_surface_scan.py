# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/release_surface_scan.py.

The hostile input is the 2026-09-25 incident shape: a tap-counter reveal plus a
trust-on-first-use owner anchor, compiled into Release (no #if DEBUG).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import release_surface_scan as rss  # noqa: E402

INCIDENT_SHAPE = """\
struct ProfileView: View {
    @State private var versionTapCount = 0
    private let adminUnlockTaps = 5
    func registerVersionTap() {
        versionTapCount += 1
        if versionTapCount >= adminUnlockTaps { showAdmin = true }
    }
}
final class AdminGate {
    static func establishOwnerAnchor(email: String?, userID: String) -> Bool { true }
}
"""


class ScanTextTests(unittest.TestCase):
    def test_incident_shape_unguarded_is_reported_high(self) -> None:
        r = rss.scan_text(INCIDENT_SHAPE, "ProfileView.swift")
        high = [f for f in r["findings"] if f["strength"] == "high"]
        lines = {f["line"] for f in high}
        self.assertIn(3, lines)   # adminUnlockTaps = 5
        self.assertIn(6, lines)   # versionTapCount >= adminUnlockTaps
        self.assertIn(10, lines)  # establishOwnerAnchor
        self.assertTrue(any(f["category"] == "owner_admin_surface" for f in r["findings"]))

    def test_same_shape_inside_if_debug_is_guarded(self) -> None:
        text = "#if DEBUG\n" + INCIDENT_SHAPE + "#endif\n"
        r = rss.scan_text(text, "ProfileView.swift")
        self.assertEqual(r["findings"], [])
        self.assertGreater(r["guarded"], 0)

    def test_else_branch_of_if_debug_ships(self) -> None:
        text = "#if DEBUG\nlet a = 1\n#else\nstruct AdminPanel: View {}\n#endif\n"
        r = rss.scan_text(text, "X.swift")
        self.assertEqual([f["line"] for f in r["findings"]], [4])

    def test_else_branch_of_not_debug_is_guarded(self) -> None:
        text = "#if !DEBUG\nlet a = 1\n#else\nstruct AdminPanel: View {}\n#endif\n"
        self.assertEqual(rss.scan_text(text, "X.swift")["findings"], [])

    def test_debug_or_other_flag_is_not_guarded(self) -> None:
        text = "#if DEBUG || INTERNAL\nstruct DebugMenu: View {}\n#endif\n"
        self.assertEqual(len(rss.scan_text(text, "X.swift")["findings"]), 1)
        # Declaring INTERNAL as a debug-only flag makes it guarded.
        r = rss.scan_text(text, "X.swift", ("DEBUG", "INTERNAL"))
        self.assertEqual(r["findings"], [])

    def test_debug_and_platform_is_guarded(self) -> None:
        text = "#if DEBUG && os(iOS)\nstruct DebugMenu: View {}\n#endif\n"
        self.assertEqual(rss.scan_text(text, "X.swift")["findings"], [])

    def test_nested_non_debug_inside_debug_is_guarded(self) -> None:
        text = "#if DEBUG\n#if os(iOS)\nstruct DebugMenu: View {}\n#endif\n#endif\nstruct OwnerPanel {}\n"
        r = rss.scan_text(text, "X.swift")
        self.assertEqual([f["line"] for f in r["findings"]], [6])

    def test_multi_tap_gesture_and_launch_arg(self) -> None:
        text = (
            'Text("v1").onTapGesture(count: 5) { reveal() }\n'
            'Text("v1").onTapGesture(count: 2) { zoom() }\n'
            'if ProcessInfo.processInfo.arguments.contains("-UITest") { }\n'
        )
        r = rss.scan_text(text, "X.swift")
        by_line = {f["line"]: f for f in r["findings"]}
        self.assertEqual(by_line[1]["category"], "gesture_reveal")
        self.assertNotIn(2, by_line)  # a double-tap is ordinary UI, not a reveal
        self.assertEqual(by_line[3]["strength"], "low")

    def test_comments_and_allowlist(self) -> None:
        text = (
            "// struct AdminPanel is removed\n"
            "/*\n struct DebugMenu {}\n*/\n"
            "// release-surface: allow read-only local telemetry, reviewed 2026-07-24\n"
            "struct CoachTelemetryDebugView: View {}\n"
        )
        r = rss.scan_text(text, "X.swift")
        self.assertEqual(r["findings"], [])
        self.assertEqual(len(r["allowed"]), 1)
        self.assertIn("read-only", r["allowed"][0]["allow_reason"])

    def test_ordinary_code_is_clean(self) -> None:
        text = "struct SettingsView: View {\n  var body: some View { Text(\"Owner of this device\") }\n}\n"
        self.assertEqual(rss.scan_text(text, "X.swift")["findings"], [])


class ScanRepoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_non_apple_repo_is_not_applicable(self) -> None:
        self._write("src/index.ts", "export const isAdmin = true;\n")
        r = rss.scan_repo(self.root)
        self.assertEqual(r["verdict"], "not_applicable")

    def test_repo_scan_excludes_tests_and_reports_app_code(self) -> None:
        self._write("Package.swift", "// swift-tools-version:5.9\n")
        self._write("App/Views/ProfileView.swift", INCIDENT_SHAPE)
        self._write("AppTests/AdminGateTests.swift", "struct AdminPanelTests {}\n")
        self._write("App/UITests/Flow.swift", "struct DebugMenu {}\n")
        r = rss.scan_repo(self.root)
        self.assertEqual(r["verdict"], "warn")
        self.assertTrue(all(f["file"].startswith("App/Views/") for f in r["findings"]))
        self.assertGreaterEqual(r["high_signal_count"], 3)

    def test_files_filter_restricts_scan(self) -> None:
        self._write("Package.swift", "")
        self._write("A.swift", "struct AdminPanel {}\n")
        self._write("B.swift", "struct DebugMenu {}\n")
        r = rss.scan_repo(self.root, ["B.swift"])
        self.assertEqual({f["file"] for f in r["findings"]}, {"B.swift"})

    def test_cli_is_advisory_exit_zero_with_findings(self) -> None:
        self._write("Package.swift", "")
        self._write("A.swift", INCIDENT_SHAPE)
        proc = subprocess.run(
            [sys.executable, str(HERE / "release_surface_scan.py"), "--path", str(self.root), "--json"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["verdict"], "warn")

    def test_cli_missing_path_is_usage_error(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(HERE / "release_surface_scan.py"), "--path", str(self.root / "nope")],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
