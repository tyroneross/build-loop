"""Tests for ``scripts/detect_runtime_server.py``.

Covers the five detection cases the C2 plan locks:
1. Positive fixture (HTTP + SSE + embedded UI) → all 7 envelope fields
   populated correctly, event_handler_locations non-empty.
2. Negative fixture (CLI-only) → silent ``runtimeServer: false``.
3. No-UI fixture (server but no embedded HTML) → ``runtimeServer: true``,
   ``embedded_ui_module`` null, handler locations empty.
4. JSON output is parseable.
5. Skip-dirs (node_modules, .venv) are ignored — a fixture under one of
   them must not match even when its content would otherwise trigger.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "detect_runtime_server.py"
FIXTURES_ROOT = REPO / "tests" / "test-fixtures"


def _run(workdir: Path) -> tuple[int, dict, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload: dict = {}
    if proc.stdout.strip():
        payload = json.loads(proc.stdout)
    return proc.returncode, payload, proc.stderr


def test_positive_fixture_returns_runtime_true_with_paths() -> None:
    """Positive fixture exercises the full envelope: HTTP + SSE + embedded UI."""
    rc, payload, stderr = _run(FIXTURES_ROOT / "runtime-server-positive")
    assert rc == 0, stderr
    assert payload["runtimeServer"] is True, payload
    assert payload["server_module"] == "serve.py", payload
    assert payload["sse_route"] == "/api/research", payload
    assert payload["default_port"] == 11435, payload
    assert payload["embedded_ui_module"] == "serve.py", payload

    locations = payload["event_handler_locations"]
    assert isinstance(locations, list) and locations, payload
    # The fixture defines `function handleEvent(d)` — at least one match.
    assert any(loc["function"] == "handleEvent" for loc in locations), locations
    for loc in locations:
        assert loc["file"] == "serve.py"
        assert isinstance(loc["line"], int) and loc["line"] >= 1
        assert isinstance(loc["function"], str) and loc["function"]

    # Evidence is descriptive, non-empty, and references the fixture file.
    assert payload["evidence"], payload
    assert any("serve.py" in e for e in payload["evidence"])


def test_negative_fixture_returns_runtime_false_silent() -> None:
    """CLI-only project produces a silent negative envelope."""
    rc, payload, stderr = _run(FIXTURES_ROOT / "runtime-server-negative")
    assert rc == 0, stderr
    assert payload["runtimeServer"] is False, payload
    assert payload["server_module"] is None
    assert payload["sse_route"] is None
    assert payload["default_port"] is None
    assert payload["embedded_ui_module"] is None
    assert payload["event_handler_locations"] == []
    assert payload["evidence"] == []


def test_no_ui_fixture_returns_runtime_true_but_empty_handlers() -> None:
    """Server-only project: server detected, but no embedded UI to scan."""
    rc, payload, stderr = _run(FIXTURES_ROOT / "runtime-server-no-ui")
    assert rc == 0, stderr
    assert payload["runtimeServer"] is True, payload
    assert payload["server_module"] == "server_only.py", payload
    assert payload["sse_route"] == "/api/stream", payload
    assert payload["embedded_ui_module"] is None, payload
    assert payload["event_handler_locations"] == [], payload


def test_json_output_is_valid() -> None:
    """Stdout is a single JSON envelope with the documented top-level keys."""
    rc, payload, _ = _run(FIXTURES_ROOT / "runtime-server-positive")
    assert rc == 0
    expected_keys = {
        "runtimeServer",
        "server_module",
        "sse_route",
        "default_port",
        "embedded_ui_module",
        "event_handler_locations",
        "evidence",
    }
    assert set(payload.keys()) == expected_keys, payload.keys()


def test_skips_node_modules_and_venv(tmp_path: Path) -> None:
    """Files under skip-dirs must not trigger detection.

    Reproduce the positive fixture under ``node_modules/`` and ``.venv/``
    inside a fresh project root that has no other server files. The
    detector must return ``runtimeServer: false`` regardless.
    """
    # Drop a CLI-only fixture as the only "real" code in the project root,
    # so a non-skip walk would still produce false on the project itself.
    shutil.copy(
        FIXTURES_ROOT / "runtime-server-negative" / "cli.py",
        tmp_path / "cli.py",
    )
    # Place the positive fixture entirely under node_modules and .venv.
    for skip_dir in ("node_modules", ".venv"):
        target = tmp_path / skip_dir / "fake-pkg"
        target.mkdir(parents=True)
        shutil.copy(
            FIXTURES_ROOT / "runtime-server-positive" / "serve.py",
            target / "serve.py",
        )
    rc, payload, stderr = _run(tmp_path)
    assert rc == 0, stderr
    assert payload["runtimeServer"] is False, (
        f"detector must skip node_modules and .venv; got {payload}; stderr={stderr}"
    )
