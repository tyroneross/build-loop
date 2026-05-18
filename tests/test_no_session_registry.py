"""Regression guard: the dead M4 collision mechanism stays retired.

`scripts/session_registry.py` was the M4 concurrent-collision-detection
mechanism that never fired (KNOWN-ISSUES.md §M4). App Pulse presence
(`scripts/app_pulse/presence.py` + `channel_paths.py`) is now the single
concurrent-presence source of truth. This test fails loudly if anyone
re-introduces the dead parallel mechanism — the exact ambiguity the
2026-05-18 retirement build removed.

Zero deps. Run: uv run --with pytest python -m pytest tests/test_no_session_registry.py
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_session_registry_module_deleted() -> None:
    assert not (REPO / "scripts" / "session_registry.py").exists(), (
        "scripts/session_registry.py is the retired dead M4 collision "
        "mechanism. It must stay deleted — App Pulse presence "
        "(scripts/app_pulse/presence.py) is the single presence source."
    )


def test_session_registry_test_deleted() -> None:
    assert not (REPO / "scripts" / "test_session_registry.py").exists(), (
        "scripts/test_session_registry.py tested a deleted module; it must "
        "stay deleted (no test may import a deleted module)."
    )


def test_no_python_imports_session_registry() -> None:
    """No tracked .py imports the deleted module."""
    offenders: list[str] = []
    import_re = re.compile(r"^\s*(import\s+session_registry|from\s+session_registry\s+import)")
    for py in list((REPO / "scripts").rglob("*.py")) + list((REPO / "tests").rglob("*.py")):
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        for ln in text.splitlines():
            if import_re.match(ln):
                offenders.append(f"{py.relative_to(REPO)}: {ln.strip()}")
    assert not offenders, "session_registry is imported by:\n" + "\n".join(offenders)


def test_no_live_collision_invocation_in_tracked_files() -> None:
    """No tracked file carries a live session_registry CLI invocation
    (register|check|heartbeat|unregister) or the write_safe_stop_sentinel
    function reference. Dated historical/design records are allow-listed:
    they are provenance, not live callers.
    """
    allow = {
        "docs/audit-tests-duplication-2026-05-11.md",
        "docs/DESIGN_2026-05-17_app-pulse-cross-session-live-architecture.md",
        "tests/test_no_session_registry.py",  # this guard names the term
        ".build-loop/plan.md",
        ".build-loop/intent.md",
        ".build-loop/goal.md",
    }
    try:
        tracked = subprocess.check_output(
            ["git", "-C", str(REPO), "grep", "-l", "session_registry"],
            stderr=subprocess.DEVNULL, text=True,
        ).splitlines()
    except subprocess.CalledProcessError:
        tracked = []  # no matches at all = pass
    residue = [f for f in tracked if f and f not in allow]
    assert not residue, (
        "Live session_registry references remain (criterion-1 failure):\n"
        + "\n".join(residue)
        + "\nThese must point at App Pulse presence instead."
    )
