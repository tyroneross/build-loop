#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for credential_preflight.py.

Run: uv run pytest scripts/test_credential_preflight.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Make scripts/ importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from credential_preflight import run_preflight


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMissingKey:
    def test_ts_process_env_missing(self, tmp_path: Path) -> None:
        """A .ts file referencing process.env.GROQ_API_KEY with no .env → GROQ_API_KEY in missing."""
        _write(tmp_path, "app.ts", "const client = new Groq({ apiKey: process.env.GROQ_API_KEY });\n")

        # Remove GROQ_API_KEY from process env for this test if it happens to be set.
        env_backup = os.environ.pop("GROQ_API_KEY", None)
        try:
            result = run_preflight(tmp_path, changed_files=None)
        finally:
            if env_backup is not None:
                os.environ["GROQ_API_KEY"] = env_backup

        assert "GROQ_API_KEY" in result["missing"], (
            f"Expected GROQ_API_KEY in missing[]; got missing={result['missing']}"
        )
        matching = [r for r in result["required"] if r["key"] == "GROQ_API_KEY"]
        assert matching, "GROQ_API_KEY should appear in required[]"
        assert matching[0]["present"] is False
        assert matching[0]["source"] is None


class TestSatisfiedByDotenv:
    def test_dotenv_satisfies_key(self, tmp_path: Path) -> None:
        """Same key present in a .env file → present=True, not in missing[]."""
        _write(tmp_path, "app.ts", "const key = process.env.GROQ_API_KEY;\n")
        _write(tmp_path, ".env", "GROQ_API_KEY=sk-test-placeholder\n")

        result = run_preflight(tmp_path, changed_files=None)

        assert "GROQ_API_KEY" not in result["missing"], (
            f"GROQ_API_KEY should be satisfied by .env; missing={result['missing']}"
        )
        matching = [r for r in result["required"] if r["key"] == "GROQ_API_KEY"]
        assert matching, "GROQ_API_KEY should appear in required[]"
        assert matching[0]["present"] is True
        # source is "env" if process env has it, "dotenv" if only the file does
        assert matching[0]["source"] in ("env", "dotenv")


class TestPythonPatterns:
    def test_py_os_getenv_detected(self, tmp_path: Path) -> None:
        """A Python file with os.getenv("OPENAI_API_KEY") → key detected."""
        _write(
            tmp_path,
            "service.py",
            'import os\nclient = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))\n',
        )

        env_backup = os.environ.pop("OPENAI_API_KEY", None)
        try:
            result = run_preflight(tmp_path, changed_files=None)
        finally:
            if env_backup is not None:
                os.environ["OPENAI_API_KEY"] = env_backup

        keys_found = {r["key"] for r in result["required"]}
        assert "OPENAI_API_KEY" in keys_found, (
            f"OPENAI_API_KEY not detected in Python source; required keys={keys_found}"
        )

    def test_py_os_environ_get_detected(self, tmp_path: Path) -> None:
        """os.environ.get("ANTHROPIC_API_KEY") is detected."""
        _write(
            tmp_path,
            "llm.py",
            'import os\nkey = os.environ.get("ANTHROPIC_API_KEY")\n',
        )

        env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            result = run_preflight(tmp_path, changed_files=None)
        finally:
            if env_backup is not None:
                os.environ["ANTHROPIC_API_KEY"] = env_backup

        keys_found = {r["key"] for r in result["required"]}
        assert "ANTHROPIC_API_KEY" in keys_found

    def test_py_os_environ_bracket_detected(self, tmp_path: Path) -> None:
        """os.environ["SOME_API_KEY"] is detected."""
        _write(
            tmp_path,
            "config.py",
            'import os\ntoken = os.environ["SOME_API_KEY"]\n',
        )

        env_backup = os.environ.pop("SOME_API_KEY", None)
        try:
            result = run_preflight(tmp_path, changed_files=None)
        finally:
            if env_backup is not None:
                os.environ["SOME_API_KEY"] = env_backup

        keys_found = {r["key"] for r in result["required"]}
        assert "SOME_API_KEY" in keys_found


class TestNoValuesEmitted:
    def test_dotenv_value_absent_from_json_output(self, tmp_path: Path) -> None:
        """The .env value string must never appear in the JSON output."""
        secret_value = "sk-super-secret-do-not-leak-xyzzy1234"
        _write(tmp_path, "app.ts", "const x = process.env.OPENAI_API_KEY;\n")
        _write(tmp_path, ".env", f"OPENAI_API_KEY={secret_value}\n")

        result = run_preflight(tmp_path, changed_files=None)
        result_json = json.dumps(result)

        assert secret_value not in result_json, (
            "Secret value from .env was emitted in JSON output — credential leak!"
        )

    def test_process_env_value_absent_from_json_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A value injected into process env must not appear in JSON output."""
        secret_value = "process-env-secret-do-not-emit-abc987"
        monkeypatch.setenv("MY_API_TOKEN", secret_value)

        _write(tmp_path, "app.py", 'import os\nval = os.getenv("MY_API_TOKEN")\n')

        result = run_preflight(tmp_path, changed_files=None)
        result_json = json.dumps(result)

        assert secret_value not in result_json, (
            "Process-env secret value leaked into JSON output!"
        )


class TestNodeModulesSkipped:
    def test_node_modules_skipped(self, tmp_path: Path) -> None:
        """Files under node_modules/ are not scanned."""
        nm = tmp_path / "node_modules" / "some-lib"
        nm.mkdir(parents=True)
        _write(nm, "index.ts", "const k = process.env.SOME_SECRET_TOKEN;\n")

        # No .env, so if node_modules were scanned this key would appear as missing.
        env_backup = os.environ.pop("SOME_SECRET_TOKEN", None)
        try:
            result = run_preflight(tmp_path, changed_files=None)
        finally:
            if env_backup is not None:
                os.environ["SOME_SECRET_TOKEN"] = env_backup

        keys_found = {r["key"] for r in result["required"]}
        assert "SOME_SECRET_TOKEN" not in keys_found, (
            "node_modules was scanned — it should be skipped"
        )


class TestChangedFilesScope:
    def test_only_changed_files_scanned(self, tmp_path: Path) -> None:
        """When --changed-files is given, only those files are scanned."""
        f1 = _write(tmp_path, "included.ts", "const x = process.env.STRIPE_SECRET_KEY;\n")
        _write(tmp_path, "excluded.ts", "const y = process.env.GITHUB_TOKEN;\n")

        env_backup_s = os.environ.pop("STRIPE_SECRET_KEY", None)
        env_backup_g = os.environ.pop("GITHUB_TOKEN", None)
        try:
            result = run_preflight(tmp_path, changed_files=[f1])
        finally:
            if env_backup_s is not None:
                os.environ["STRIPE_SECRET_KEY"] = env_backup_s
            if env_backup_g is not None:
                os.environ["GITHUB_TOKEN"] = env_backup_g

        keys_found = {r["key"] for r in result["required"]}
        assert "STRIPE_SECRET_KEY" in keys_found
        assert "GITHUB_TOKEN" not in keys_found


class TestJsTsPatterns:
    def test_import_meta_env_detected(self, tmp_path: Path) -> None:
        """import.meta.env.VITE_API_KEY is detected (Vite pattern)."""
        _write(tmp_path, "app.tsx", "const key = import.meta.env.VITE_API_KEY;\n")

        env_backup = os.environ.pop("VITE_API_KEY", None)
        try:
            result = run_preflight(tmp_path, changed_files=None)
        finally:
            if env_backup is not None:
                os.environ["VITE_API_KEY"] = env_backup

        keys_found = {r["key"] for r in result["required"]}
        assert "VITE_API_KEY" in keys_found

    def test_bracket_syntax_detected(self, tmp_path: Path) -> None:
        """process.env["DATABASE_URL"] bracket syntax is detected."""
        _write(tmp_path, "db.js", 'const url = process.env["DATABASE_URL"];\n')

        env_backup = os.environ.pop("DATABASE_URL", None)
        try:
            result = run_preflight(tmp_path, changed_files=None)
        finally:
            if env_backup is not None:
                os.environ["DATABASE_URL"] = env_backup

        keys_found = {r["key"] for r in result["required"]}
        assert "DATABASE_URL" in keys_found


class TestExitCodeAndJson:
    def test_json_output_structure(self, tmp_path: Path) -> None:
        """JSON output has required, missing, scanned_files, errors keys."""
        _write(tmp_path, "app.ts", "const x = process.env.OPENAI_API_KEY;\n")

        env_backup = os.environ.pop("OPENAI_API_KEY", None)
        try:
            result = run_preflight(tmp_path, changed_files=None)
        finally:
            if env_backup is not None:
                os.environ["OPENAI_API_KEY"] = env_backup

        assert "required" in result
        assert "missing" in result
        assert "scanned_files" in result
        assert "errors" in result
        assert isinstance(result["required"], list)
        assert isinstance(result["missing"], list)
        assert isinstance(result["scanned_files"], int)
        assert isinstance(result["errors"], list)
