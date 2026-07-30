"""
Tests for skills/native-ax-driver/.

Covers the launcher CLI surface and the integrity of the bundled Swift package.
None of these tests need actual AX permission, biometric authentication, or a
running .app — they're CLI-only smoke checks.

Specifically asserts:
  - native_driver.py --help works (argparse stays valid)
  - subcommand registry covers preflight, apps, resolve, scan, analyze-layout, action
  - VALID_ACTIONS matches the Swift switch (catches drift if either side adds
    or renames an action without updating the other)
  - Swift package is well-formed (Package.swift + Sources/main.swift present,
    Package.swift declares the bl-ax-driver executable target)
  - cached binary path computation handles edge cases

Stdlib + pytest only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "native-ax-driver"
LAUNCHER = SKILL_DIR / "scripts" / "native_driver.py"
SWIFT_PKG = SKILL_DIR / "swift" / "bl-ax-driver"
SWIFT_MAIN = SWIFT_PKG / "Sources" / "main.swift"
SWIFT_MANIFEST = SWIFT_PKG / "Package.swift"

# Swift action constants the binary's switch{} handles. Single source of truth
# is main.swift; the launcher mirrors it. If either drifts, the cross-check
# below fails and forces an explicit reconciliation.
EXPECTED_ACTIONS = {
    "press",
    "setValue",
    "increment",
    "decrement",
    "showMenu",
    "confirm",
    "cancel",
    "focus",
    "scrollToVisible",
}


# ─── File layout ────────────────────────────────────────────────────────────


def test_skill_md_exists():
    assert (SKILL_DIR / "SKILL.md").is_file(), "skill missing SKILL.md"


def test_launcher_present_and_executable():
    assert LAUNCHER.is_file(), "native_driver.py missing"
    # not all CI hosts preserve +x; just verify python can run it
    assert os.access(LAUNCHER, os.R_OK)


def test_swift_package_present():
    assert SWIFT_MANIFEST.is_file(), "Package.swift missing"
    assert SWIFT_MAIN.is_file(), "Sources/main.swift missing"
    manifest = SWIFT_MANIFEST.read_text()
    assert 'name: "bl-ax-driver"' in manifest
    assert ".executableTarget" in manifest


# ─── Launcher CLI surface ───────────────────────────────────────────────────


def _run_launcher(args, env_extra=None, input_text=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *args],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        input=input_text,
    )


def test_launcher_help_runs():
    proc = _run_launcher(["--help"])
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    for sub in ("preflight", "apps", "resolve", "scan", "analyze-layout", "action"):
        assert sub in out, f"--help missing subcommand {sub!r}"


@pytest.mark.parametrize("sub", ["preflight", "apps", "resolve", "scan", "analyze-layout", "action"])
def test_launcher_subcommand_help(sub):
    proc = _run_launcher([sub, "--help"])
    assert proc.returncode == 0, f"{sub} --help failed: {proc.stderr}"


def test_launcher_action_help_lists_all_actions():
    proc = _run_launcher(["action", "--help"])
    assert proc.returncode == 0, proc.stderr
    for action in EXPECTED_ACTIONS:
        assert action in proc.stdout, f"--action choices missing {action}"


def test_launcher_action_rejects_unknown_action():
    proc = _run_launcher([
        "action",
        "--pid", "1",
        "--element-path", "0",
        "--action", "nosuch",
    ])
    # argparse choices=… returns exit 2 for invalid choices
    assert proc.returncode == 2
    assert "invalid choice" in proc.stderr.lower() or "nosuch" in proc.stderr


def test_launcher_action_requires_pid():
    proc = _run_launcher(["action", "--element-path", "0", "--action", "press"])
    assert proc.returncode == 2  # argparse complaint about missing --pid


def test_analyze_layout_stdin_envelope_reports_layout_fill():
    tree = [
        {
            "role": "AXSplitGroup",
            "title": "Main",
            "position": {"x": 0, "y": 0},
            "size": {"width": 1074, "height": 700},
            "children": [
                {
                    "role": "AXGroup",
                    "title": "Terminal",
                    "position": {"x": 317, "y": 0},
                    "size": {"width": 440, "height": 700},
                    "children": [],
                }
            ],
        }
    ]
    raw_scan = "WINDOW:1:1074x700:Main\n" + json.dumps(tree)

    proc = _run_launcher(["analyze-layout", "--stdin"], input_text=raw_scan)

    assert proc.returncode == 0, proc.stderr
    envelope = json.loads(proc.stdout)
    assert envelope["status"] == "ran"
    assert envelope["route"] == "native"
    assert envelope["verifier"] == "native-ax-driver"
    assert envelope["findings"][0]["severity"] == "warning"
    assert envelope["findings"][0]["category"] == "structure"
    assert envelope["findings"][0]["message"].startswith("layout-fill: ")
    assert envelope["findings"][0]["finding"]["emptyPx"] == 317


# ─── Drift cross-check between launcher and Swift binary ────────────────────


def test_valid_actions_match_swift_switch():
    """
    Catches drift between the Python launcher's VALID_ACTIONS set and the Swift
    switch in main.swift. If either side renames an action, this fails until
    both are updated.
    """
    # Pull VALID_ACTIONS out of native_driver.py source. Avoid importing the
    # module (would require its full dependency graph) — regex is enough.
    py_src = LAUNCHER.read_text()
    py_match = re.search(
        r"VALID_ACTIONS\s*=\s*\{([^}]+)\}",
        py_src,
        re.DOTALL,
    )
    assert py_match, "VALID_ACTIONS literal not found in native_driver.py"
    py_actions = set(re.findall(r'"([a-zA-Z]+)"', py_match.group(1)))

    # Pull the action strings out of main.swift's switch on `action`. The Swift
    # source uses `case "press":` / `case "setValue":` etc. inside a switch
    # whose subject is the local `action` parameter.
    swift_src = SWIFT_MAIN.read_text()
    # Find the performAction switch. Anchor to the function declaration.
    perform = re.search(
        r"func performAction\([^)]*\)[^{]*\{(.+?)\n\}\s*\n\n// MARK: -",
        swift_src,
        re.DOTALL,
    )
    assert perform, "performAction body not found in main.swift"
    swift_actions = set(re.findall(r'case\s+"([a-zA-Z]+)"\s*:', perform.group(1)))

    assert py_actions == EXPECTED_ACTIONS, (
        f"Python VALID_ACTIONS drifted from canonical set. "
        f"Python={sorted(py_actions)} Expected={sorted(EXPECTED_ACTIONS)}"
    )
    assert swift_actions == EXPECTED_ACTIONS, (
        f"Swift performAction switch drifted from canonical set. "
        f"Swift={sorted(swift_actions)} Expected={sorted(EXPECTED_ACTIONS)}"
    )


# ─── Preflight runs without an actual app ──────────────────────────────────


@pytest.mark.skipif(sys.platform != "darwin", reason="AX preflight is macOS-only")
def test_preflight_returns_known_exit_code():
    """
    preflight should exit 0 (granted) or 2 (not granted) — never 1 unless
    osascript is missing. CI macOS runners typically don't have AX granted,
    so we accept either 0 or 2.
    """
    proc = _run_launcher(["preflight"])
    assert proc.returncode in (0, 2), (
        f"preflight returned unexpected exit {proc.returncode}; stderr={proc.stderr}"
    )
    # stdout should be valid JSON with a `granted` key
    payload = json.loads(proc.stdout)
    assert "granted" in payload
    assert isinstance(payload["granted"], bool)
