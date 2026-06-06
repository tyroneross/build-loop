#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/pytest_collect_gate.py.

Two acceptance probes:
  1. A deliberately-broken import in a test module makes the gate fail
     (exit 1, status="fail", finding points at the broken file).
  2. A clean tree passes (exit 0, status="pass").

Plus a few parser unit tests against captured pytest output.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "pytest_collect_gate.py"

sys.path.insert(0, str(HERE))
from pytest_collect_gate import (  # noqa: E402
    _parse_collection_errors,
    _parse_tests_collected,
    _should_skip,
)


# ---------------------------------------------------------------------------
# Parser unit tests (no subprocess)
# ---------------------------------------------------------------------------

SAMPLE_BROKEN_OUTPUT = textwrap.dedent("""
    ==================================== ERRORS ====================================
    ___________ ERROR collecting tests/test_run_entry_execution_state.py ___________
    ImportError while importing test module '/path/tests/test_run_entry_execution_state.py'.
    Hint: make sure your test modules/packages have valid Python names.
    Traceback:
    /.../importlib/__init__.py:88: in import_module
        return _bootstrap._gcd_import(name[level:], package, level)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    tests/test_run_entry_execution_state.py:19: in <module>
        from write_run_entry import (  # noqa: E402
    E   ImportError: cannot import name 'EXECUTION_SCHEMA_VERSION' from 'write_run_entry'
    =========================== short test summary info ============================
    ERROR tests/test_run_entry_execution_state.py
    !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
    2153 tests collected, 1 error in 25.96s
""").strip()


def test_parse_single_collection_error():
    findings = _parse_collection_errors(SAMPLE_BROKEN_OUTPUT)
    assert len(findings) == 1
    f = findings[0]
    assert f["file"] == "tests/test_run_entry_execution_state.py"
    assert f["line"] == 19
    assert f["error_class"] == "ImportError"
    assert "EXECUTION_SCHEMA_VERSION" in f["message"]


def test_parse_tests_collected_count():
    assert _parse_tests_collected(SAMPLE_BROKEN_OUTPUT) == 2153


def test_parse_no_errors_in_clean_output():
    clean = "tests/test_foo.py: 5\ntests/test_bar.py: 12\n42 tests collected in 1.23s\n"
    assert _parse_collection_errors(clean) == []
    assert _parse_tests_collected(clean) == 42


def test_should_skip_when_no_paths_and_no_config(tmp_path):
    skip, reason = _should_skip(tmp_path, ["tests/"])
    assert skip is True
    assert reason


def test_should_not_skip_when_paths_present(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_a(): pass\n")
    skip, _reason = _should_skip(tmp_path, ["tests/"])
    assert skip is False


# ---------------------------------------------------------------------------
# Subprocess acceptance — broken-import sandbox
# ---------------------------------------------------------------------------

def _make_sandbox(tmp_path: Path, broken: bool) -> Path:
    """Build a minimal pytest-able sandbox with one healthy test module and,
    optionally, one module with a deliberately-broken import."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_healthy.py").write_text(textwrap.dedent("""
        def test_truth():
            assert 1 + 1 == 2
    """).lstrip())
    if broken:
        (tests_dir / "test_broken.py").write_text(textwrap.dedent("""
            from nonexistent_module_zzz import does_not_exist  # noqa: F401

            def test_should_never_run():
                assert False
        """).lstrip())
    # Minimal pytest config so _should_skip is satisfied
    (tmp_path / "pyproject.toml").write_text(textwrap.dedent("""
        [tool.pytest.ini_options]
        testpaths = ["tests"]
    """).lstrip())
    return tmp_path


def _run_gate_in(workdir: Path) -> tuple[int, dict]:
    """Invoke the gate as a subprocess; return (exit_code, envelope)."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir),
         "--paths", "tests/", "--json"],
        capture_output=True, text=True, check=False,
    )
    # The gate emits JSON on stdout when --json; envelope lives on the first
    # JSON object printed (the only one).
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"gate stdout was not JSON: {exc}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        )
    return proc.returncode, envelope


def test_broken_import_makes_gate_fail(tmp_path):
    sandbox = _make_sandbox(tmp_path, broken=True)
    rc, env = _run_gate_in(sandbox)
    assert rc == 1, f"expected exit 1 on broken import, got {rc}; envelope={env}"
    assert env["status"] == "fail"
    assert env["errors_count"] >= 1
    # The finding should name the deliberately-broken module
    broken_files = {f["file"] for f in env["findings"]}
    assert any("test_broken.py" in f for f in broken_files), (
        f"expected test_broken.py in findings; got {broken_files}"
    )


def test_clean_tree_passes(tmp_path):
    sandbox = _make_sandbox(tmp_path, broken=False)
    rc, env = _run_gate_in(sandbox)
    assert rc == 0, f"expected exit 0 on clean tree, got {rc}; envelope={env}"
    assert env["status"] == "pass"
    assert env["errors_count"] == 0
    # We collected the one healthy test
    assert env["tests_collected"] is None or env["tests_collected"] >= 1


def test_dry_run_does_not_invoke_pytest(tmp_path):
    sandbox = _make_sandbox(tmp_path, broken=True)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(sandbox),
         "--paths", "tests/", "--json", "--dry-run"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    env = json.loads(proc.stdout)
    assert env["status"] == "dry_run"
    assert env["command"][1] == "-m" and env["command"][2] == "pytest"
    # Broken import should be invisible to dry-run (no execution)
    assert env["errors_count"] == 0


def test_skipped_when_no_test_paths(tmp_path):
    # No tests/ dir, no pyproject — should skip
    rc, env = _run_gate_in(tmp_path)
    assert rc == 0
    assert env["status"] == "skipped"
    assert env["reason"]
