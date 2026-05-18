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


def test_no_live_session_registry_invocation_in_tracked_files() -> None:
    """No tracked file carries a LIVE session_registry invocation:
    a CLI subcommand (`session_registry.py register|check|heartbeat|
    unregister`) or the `write_safe_stop_sentinel(` function call. A doc
    that merely STATES the mechanism was removed/dead is the desired end
    state, not a violation — so this matches the live-invocation pattern,
    not bare string presence. This is the criterion-1 oracle.
    """
    # Live-invocation signatures only. Historical prose ("the legacy
    # session_registry.py was removed") must NOT match.
    live_re = re.compile(
        r"session_registry(\.py)?\s+(register|check|heartbeat|unregister)\b"
        r"|session_registry\.write_safe_stop_sentinel\s*\("
        r"|import\s+session_registry|from\s+session_registry\s+import"
    )
    try:
        tracked = subprocess.check_output(
            ["git", "-C", str(REPO), "grep", "-l", "session_registry"],
            stderr=subprocess.DEVNULL, text=True,
        ).splitlines()
    except subprocess.CalledProcessError:
        tracked = []  # no matches at all = pass
    offenders: list[str] = []
    for rel in tracked:
        if not rel or rel == "tests/test_no_session_registry.py":
            continue  # this guard names the patterns by construction
        try:
            text = (REPO / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        for ln in text.splitlines():
            if live_re.search(ln):
                offenders.append(f"{rel}: {ln.strip()[:100]}")
    assert not offenders, (
        "Live session_registry invocation remains (criterion-1 failure):\n"
        + "\n".join(offenders)
        + "\nThese must point at App Pulse presence instead."
    )
