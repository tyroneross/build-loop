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

import credential_preflight as preflight
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

    def test_example_and_empty_values_do_not_satisfy_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        _write(tmp_path, "app.ts", "const key = process.env.GROQ_API_KEY;\n")
        _write(tmp_path, ".env.example", "GROQ_API_KEY=example-value\n")
        _write(tmp_path, ".env.local", 'GROQ_API_KEY=""\n')

        result = run_preflight(tmp_path, changed_files=None)

        assert result["missing"] == ["GROQ_API_KEY"]

    def test_comment_only_dotenv_value_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        _write(tmp_path, "app.ts", "process.env.GROQ_API_KEY\n")
        _write(tmp_path, ".env.local", "GROQ_API_KEY= # pending\n")

        assert run_preflight(tmp_path, changed_files=None)["missing"] == ["GROQ_API_KEY"]


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

    def test_default_scan_skips_tests_and_accepts_cjs_runtime(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("NEXT_RUNTIME", raising=False)
        _write(tmp_path, "tests/fixture.test.ts", "process.env.OPENAI_API_KEY\n")
        _write(tmp_path, "scripts/validate-env.cjs", "process.env.GROQ_API_KEY\nprocess.env.NEXT_RUNTIME\nprocess.env.API_URL\n")

        result = run_preflight(tmp_path, changed_files=None)

        assert result["missing"] == ["GROQ_API_KEY"]
        assert result["scanned_files"] == 1

    def test_python_fallback_has_no_arbitrary_500_file_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(preflight.shutil, "which", lambda _name: None)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        for index in range(501):
            _write(tmp_path, f"modules/m{index:03}.ts", "export const value = 1;\n")
        _write(tmp_path, "modules/z.ts", "process.env.GROQ_API_KEY\n")

        result = run_preflight(tmp_path, changed_files=None)

        assert result["scan_method"] == "python"
        assert result["scanned_files"] == 1
        assert result["missing"] == ["GROQ_API_KEY"]

    def test_generated_worktrees_are_excluded_in_both_scan_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("BREVO_API_KEY", raising=False)
        _write(tmp_path, ".rally/worktrees/old/app.ts", "process.env.BREVO_API_KEY\n")
        _write(tmp_path, ".claude/worktrees/old/app.ts", "process.env.BREVO_API_KEY\n")

        assert run_preflight(tmp_path, changed_files=None)["required"] == []
        monkeypatch.setattr(preflight.shutil, "which", lambda _name: None)
        assert run_preflight(tmp_path, changed_files=None)["required"] == []


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
    def test_database_urls_are_credentials_but_generic_api_url_is_not(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        names = ("MYSQL_URL", "MONGO_URL", "POSTGRESQL_URL", "DB_URL", "CUSTOM_MYSQL_URL")
        for name in names:
            monkeypatch.delenv(name, raising=False)
        _write(tmp_path, "db.ts", "\n".join(f"process.env.{name}" for name in (*names, "API_URL")))

        assert set(run_preflight(tmp_path, changed_files=None)["missing"]) == set(names)

    def test_optional_bracket_deno_and_destructuring_accesses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for key in ("OPENAI_API_KEY", "GROQ_API_KEY", "VITE_API_KEY", "FIREWORKS_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        _write(tmp_path, "runtime.ts", """const { OPENAI_API_KEY, GROQ_API_KEY: groq } = process.env;
const anthropic = process.env?.ANTHROPIC_API_KEY;
const vite = import.meta.env['VITE_API_KEY'];
const fireworks = Deno.env.get('FIREWORKS_API_KEY');
""")
        _write(tmp_path, "multiline.ts", """const {
  DATABASE_URL,
} = process.env;
""")
        monkeypatch.delenv("DATABASE_URL", raising=False)

        result = run_preflight(tmp_path, changed_files=None)

        assert set(result["missing"]) == {
            "OPENAI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY",
            "VITE_API_KEY", "FIREWORKS_API_KEY", "DATABASE_URL",
        }
        assert result["scan_method"] in {"ripgrep", "python"}

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

    def test_cli_json_is_compact_and_detail_is_opt_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        _write(tmp_path, "app.ts", "const key = process.env.GROQ_API_KEY;\n")

        assert preflight.main(["--workdir", str(tmp_path), "--json"]) == 0
        compact = json.loads(capsys.readouterr().out)
        assert compact["missing"] == ["GROQ_API_KEY"]
        assert compact["reference_samples"][0]["key"] == "GROQ_API_KEY"
        assert "required" not in compact

        assert preflight.main(["--workdir", str(tmp_path), "--json", "--details"]) == 0
        detailed = json.loads(capsys.readouterr().out)
        assert detailed["required"][0]["key"] == "GROQ_API_KEY"
        assert detailed["required"][0]["referenced_in"]
