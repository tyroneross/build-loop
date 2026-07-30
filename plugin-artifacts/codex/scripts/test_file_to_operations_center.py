#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for file_to_operations_center.py.

Hermetic: never invokes the real Operations Center binary. Command construction,
urgency mapping, output parsing, and binary discovery are pure functions; the
end-to-end path is exercised with a FAKE `oc` binary written to a temp dir.

Run: uv run pytest scripts/test_file_to_operations_center.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "file_to_operations_center.py"

_spec = importlib.util.spec_from_file_location("file_to_operations_center", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _run(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True, text=True, timeout=60, env=full_env,
    )


def _write_fake_oc(dirpath: Path, *, add_line: str = "added  abc12345  the title",
                   exit_code: int = 0) -> Path:
    """Write a fake `oc`-compatible binary that echoes an `added ...` line."""
    p = dirpath / "rosslabs-operations-center"
    p.write_text(
        "#!/usr/bin/env bash\n"
        "# fake oc: log argv, echo a canned `added ...` line\n"
        'echo "$@" >> "$0.calls"\n'
        f'echo "{add_line}"\n'
        f"exit {exit_code}\n"
    )
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


class TestUrgencyMapping(unittest.TestCase):
    def test_known_urgencies(self) -> None:
        self.assertEqual(mod.urgency_to_priority("critical"), 0)
        self.assertEqual(mod.urgency_to_priority("high"), 1)
        self.assertEqual(mod.urgency_to_priority("normal"), 2)
        self.assertEqual(mod.urgency_to_priority("low"), 3)

    def test_case_insensitive_and_default(self) -> None:
        self.assertEqual(mod.urgency_to_priority("LOW"), 3)
        self.assertEqual(mod.urgency_to_priority(None), 2)
        self.assertEqual(mod.urgency_to_priority("nonsense"), 2)


class TestBuildArgv(unittest.TestCase):
    def test_global_db_flag_precedes_subcommand(self) -> None:
        argv = mod.build_add_argv(
            "oc", title="T", repo="R", priority=3, spec="S",
            task_type="fix", db="/tmp/x.db",
        )
        # --db is a GLOBAL flag; clap requires it before the `add` subcommand.
        self.assertEqual(argv[:3], ["oc", "--db", "/tmp/x.db"])
        self.assertIn("add", argv)
        self.assertLess(argv.index("--db"), argv.index("add"))

    def test_flags_present(self) -> None:
        argv = mod.build_add_argv(
            "oc", title="My Title", repo="WorkWiki", priority=3,
            spec="desc", task_type="fix",
        )
        # Subcommand first, then flags; the title is the LAST token, after `--`.
        self.assertEqual(argv[:2], ["oc", "add"])
        self.assertEqual(argv[-2:], ["--", "My Title"])
        self.assertIn("--repo", argv)
        self.assertEqual(argv[argv.index("--repo") + 1], "WorkWiki")
        self.assertEqual(argv[argv.index("--priority") + 1], "3")
        self.assertEqual(argv[argv.index("--task-type") + 1], "fix")
        # Every flag precedes the `--` separator (so clap parses them as flags).
        dd = argv.index("--")
        self.assertLess(argv.index("--repo"), dd)
        self.assertLess(argv.index("--task-type"), dd)

    def test_spec_omitted_when_none(self) -> None:
        argv = mod.build_add_argv("oc", title="T", repo="R", priority=2, spec=None)
        self.assertNotIn("--spec", argv)

    def test_leading_dash_title_cannot_be_flag_parsed(self) -> None:
        """A title starting with '-' (e.g. "--db=/tmp/evil.db") must be protected
        by the `--` end-of-options separator so clap takes it literally and can
        never hijack the global --db or fail intake with a missing-<TITLE> error.
        Verified against the real CLI: `add --repo R -- "--db=x"` files title
        exactly "--db=x"."""
        argv = mod.build_add_argv(
            "oc", title="--db=/tmp/evil.db", repo="R", priority=3,
        )
        self.assertIn("--", argv)
        dd = argv.index("--")
        # Title is the last token, immediately after the separator.
        self.assertEqual(argv[-1], "--db=/tmp/evil.db")
        self.assertEqual(argv[dd + 1], "--db=/tmp/evil.db")
        # The malicious title never appears BEFORE the separator, where clap
        # would flag-parse it and hijack --db.
        self.assertNotIn("--db=/tmp/evil.db", argv[:dd])


class TestParseOutput(unittest.TestCase):
    def test_parses_added_line(self) -> None:
        self.assertEqual(mod.parse_add_output("added  deadbeef  some title\n"), "deadbeef")

    def test_returns_none_on_junk(self) -> None:
        self.assertIsNone(mod.parse_add_output("no match here"))
        self.assertIsNone(mod.parse_add_output(""))


class TestBinaryDiscovery(unittest.TestCase):
    def test_explicit_missing_returns_none(self) -> None:
        self.assertIsNone(mod.find_oc_binary("/nonexistent/path/oc"))

    def test_env_bin_used(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake = _write_fake_oc(Path(d))
            os.environ["OC_BIN"] = str(fake)
            try:
                found = mod.find_oc_binary(None)
                self.assertEqual(found, fake)
            finally:
                del os.environ["OC_BIN"]


class TestFileTaskEndToEnd(unittest.TestCase):
    def test_files_via_fake_binary(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake = _write_fake_oc(Path(d))
            result, code = mod.file_task(
                repo="WorkWiki", title="parameterize SQL", spec="f-string SQL at line 312",
                urgency="low", oc_bin=str(fake),
            )
            self.assertEqual(code, 0)
            self.assertTrue(result["filed"])
            self.assertEqual(result["task_id"], "abc12345")
            self.assertEqual(result["priority"], 3)
            self.assertEqual(result["repo"], "WorkWiki")
            # full_id is forward-compat only (the CLI `show` needs a full id, not
            # a prefix), so it stays null — the 8-char task_id is the receipt.
            self.assertIsNone(result["full_id"])
            # verify the fake actually received an `add` with our args; the
            # title is passed last, after the `--` end-of-options separator.
            calls = (Path(str(fake) + ".calls")).read_text()
            self.assertIn("add ", calls)
            self.assertIn("-- parameterize SQL", calls)
            self.assertIn("--repo WorkWiki", calls)
            self.assertIn("--priority 3", calls)

    def test_missing_binary_is_blocker_not_silent(self) -> None:
        result, code = mod.file_task(
            repo="WorkWiki", title="x", oc_bin="/nonexistent/oc",
        )
        self.assertEqual(code, 1, msg="missing binary must be a non-zero blocker")
        self.assertFalse(result["filed"])
        self.assertIsNotNone(result["reason"])
        self.assertIn("not found", result["reason"])

    def test_oc_add_failure_is_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake = _write_fake_oc(Path(d), exit_code=1)
            result, code = mod.file_task(repo="R", title="t", oc_bin=str(fake))
            self.assertEqual(code, 1)
            self.assertFalse(result["filed"])
            self.assertIn("exited 1", result["reason"])

    def test_dry_run_files_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake = _write_fake_oc(Path(d))
            result, code = mod.file_task(
                repo="R", title="t", oc_bin=str(fake), dry_run=True,
            )
            self.assertEqual(code, 0)
            self.assertFalse(result["filed"])
            self.assertTrue(result["argv"])  # argv computed
            self.assertFalse((Path(str(fake) + ".calls")).exists(),
                             msg="dry-run must not invoke the binary")


class TestCli(unittest.TestCase):
    def test_cli_emits_pure_json_and_exit(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake = _write_fake_oc(Path(d))
            r = _run(
                ["--repo", "WorkWiki", "--title", "hi", "--urgency", "low", "--json"],
                env={"OC_BIN": str(fake)},
            )
            self.assertEqual(r.returncode, 0, msg=f"stderr={r.stderr}")
            payload = json.loads(r.stdout)
            self.assertTrue(payload["filed"])
            self.assertEqual(payload["priority"], 3)

    def test_cli_missing_binary_exit_1(self) -> None:
        r = _run(
            ["--repo", "R", "--title", "t", "--oc-bin", "/nonexistent/oc", "--json"],
        )
        self.assertEqual(r.returncode, 1)
        payload = json.loads(r.stdout)
        self.assertFalse(payload["filed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
