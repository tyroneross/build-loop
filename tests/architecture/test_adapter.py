"""Tests for the NavGator adapter.

These tests must NOT depend on NavGator being installed. We monkeypatch both
``shutil.which`` and ``subprocess.run`` so the suite is hermetic.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, List

import pytest

from build_loop.architecture.adapter import (
    Adapter,
    AdapterError,
    CapabilityNotAvailable,
    NavGatorNotAvailable,
    is_navgator_available,
)
from build_loop.architecture.adapter import navgator_adapter as NA


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_is_navgator_available_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``which`` returning a path → available."""
    monkeypatch.setattr(NA.shutil, "which", lambda name: "/usr/local/bin/navgator")
    assert is_navgator_available(tmp_path) is True


def test_is_navgator_available_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No CLI on PATH and no ``.mcp.json`` plugin_navgator → unavailable."""
    monkeypatch.setattr(NA.shutil, "which", lambda name: None)
    # Ensure no .mcp.json side effects from the surrounding repo.
    assert is_navgator_available(tmp_path) is False


def test_is_navgator_available_via_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``.mcp.json`` registering a navgator MCP server → available even without CLI."""
    monkeypatch.setattr(NA.shutil, "which", lambda name: None)
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"plugin_navgator": {"command": "node"}}}),
        encoding="utf-8",
    )
    assert is_navgator_available(tmp_path) is True


# ---------------------------------------------------------------------------
# Native mode
# ---------------------------------------------------------------------------


def _seed_python_repo(root: Path) -> None:
    """Minimal fixture: two Python files importing each other."""
    (root / "a.py").write_text("from b import x\n", encoding="utf-8")
    (root / "b.py").write_text("x = 1\n", encoding="utf-8")


def test_native_mode_scan_uses_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapter(mode='native').scan() runs the native scanner end-to-end."""
    monkeypatch.setattr(NA.shutil, "which", lambda name: None)  # NavGator absent
    _seed_python_repo(tmp_path)

    result = Adapter(mode="native").scan(tmp_path)
    assert result["ok"] is True
    assert result["components"] == 2
    assert result["files_scanned"] == 2
    # Verify the engine wrote its index to .build-loop/architecture/.
    assert (tmp_path / ".build-loop" / "architecture" / "index.json").exists()


def test_native_mode_llm_map_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling an escalation-only capability in native mode raises."""
    monkeypatch.setattr(NA.shutil, "which", lambda name: None)
    with pytest.raises(CapabilityNotAvailable):
        Adapter(mode="native").llm_map(tmp_path)


def test_native_mode_schema_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NA.shutil, "which", lambda name: None)
    with pytest.raises(CapabilityNotAvailable):
        Adapter(mode="native").schema(tmp_path)


def test_native_mode_diagram_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NA.shutil, "which", lambda name: None)
    with pytest.raises(CapabilityNotAvailable):
        Adapter(mode="native").diagram(tmp_path)


# ---------------------------------------------------------------------------
# NavGator-forced mode
# ---------------------------------------------------------------------------


def test_navgator_mode_raises_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """mode='navgator' + no NavGator → NavGatorNotAvailable on every call."""
    monkeypatch.setattr(NA.shutil, "which", lambda name: None)
    adapter = Adapter(mode="navgator")
    with pytest.raises(NavGatorNotAvailable):
        adapter.scan(tmp_path)
    with pytest.raises(NavGatorNotAvailable):
        adapter.llm_map(tmp_path)
    with pytest.raises(NavGatorNotAvailable):
        adapter.schema(tmp_path)


# ---------------------------------------------------------------------------
# Auto mode + escalation fallback
# ---------------------------------------------------------------------------


def test_auto_falls_back_for_unported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """auto + NavGator absent + escalation-only call → graceful dict, no exception."""
    monkeypatch.setattr(NA.shutil, "which", lambda name: None)
    result = Adapter(mode="auto").llm_map(tmp_path)
    assert result == {
        "available": False,
        "reason": (
            "NavGator not installed; llm-map unavailable until ported into the "
            "native engine."
        ),
        "capability": "llm_map",
    }


def test_auto_falls_back_for_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NA.shutil, "which", lambda name: None)
    result = Adapter(mode="auto").schema(tmp_path)
    assert result["available"] is False
    assert result["capability"] == "schema"


# ---------------------------------------------------------------------------
# Subprocess invocation shape
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, stdout: str = '{"ok":true}', stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_subprocess_invocation_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When NavGator IS available, _run_navgator emits ``navgator <subcmd> ... --json --agent``."""
    monkeypatch.setattr(NA.shutil, "which", lambda name: "/usr/local/bin/navgator")

    captured: List[Any] = []

    def fake_run(cmd: List[str], **kwargs: Any) -> _FakeProc:
        captured.append({"cmd": cmd, "kwargs": kwargs})
        return _FakeProc(stdout='{"ok": true, "use_cases": []}')

    monkeypatch.setattr(NA.subprocess, "run", fake_run)

    result = Adapter(mode="navgator").llm_map(tmp_path)
    assert result == {"ok": True, "use_cases": []}
    assert len(captured) == 1
    cmd = captured[0]["cmd"]
    # Shape: [<binary>, 'llm-map', '--json', '--agent']
    assert cmd[0] == "/usr/local/bin/navgator"
    assert cmd[1] == "llm-map"
    assert "--json" in cmd
    assert "--agent" in cmd
    # Transport flags trail the subcommand.
    assert cmd.index("--json") > cmd.index("llm-map")
    # cwd, capture_output, text, timeout were forwarded.
    kw = captured[0]["kwargs"]
    assert kw["cwd"] == str(tmp_path.resolve())
    assert kw["capture_output"] is True
    assert kw["text"] is True
    assert kw["timeout"] == NA.NAVGATOR_TIMEOUT_S


def test_subprocess_nonzero_exit_raises_adapter_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(NA.shutil, "which", lambda name: "/usr/local/bin/navgator")
    monkeypatch.setattr(
        NA.subprocess,
        "run",
        lambda cmd, **kw: _FakeProc(stdout="", stderr="boom", returncode=1),
    )
    with pytest.raises(AdapterError, match="boom"):
        Adapter(mode="navgator").llm_map(tmp_path)


def test_subprocess_timeout_raises_adapter_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(NA.shutil, "which", lambda name: "/usr/local/bin/navgator")

    def fake_run(cmd: List[str], **kwargs: Any) -> _FakeProc:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 30))

    monkeypatch.setattr(NA.subprocess, "run", fake_run)
    with pytest.raises(AdapterError, match="timed out"):
        Adapter(mode="navgator").llm_map(tmp_path)


def test_invalid_mode_rejected() -> None:
    with pytest.raises(ValueError):
        Adapter(mode="bogus")  # type: ignore[arg-type]


def test_navgator_passthrough_no_reshape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapter must NOT reshape NavGator's JSON; passthrough verbatim."""
    monkeypatch.setattr(NA.shutil, "which", lambda name: "/usr/local/bin/navgator")
    raw = {
        "ok": True,
        "use_cases": [{"id": "uc-1", "purpose": "summarize"}],
        "summary": {"total": 1},
    }
    monkeypatch.setattr(
        NA.subprocess,
        "run",
        lambda cmd, **kw: _FakeProc(stdout=json.dumps(raw)),
    )
    result = Adapter(mode="navgator").llm_map(tmp_path)
    assert result == raw  # exact equality — no fields added or stripped
