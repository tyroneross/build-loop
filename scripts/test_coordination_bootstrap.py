#!/usr/bin/env python3
"""Tests for scripts/coordination_bootstrap.py.

Covers:
- Happy path: bootstraps a new coord file with placeholders substituted.
- Idempotency: second call on the same coord file does NOT overwrite;
  returns action="joined-existing-coord".
- Missing template: returns action="error", does not crash.
- Custom --coord-file path is honored.
- Placeholder substitutions are deterministic (topic, scope, date).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import coordination_bootstrap as cb  # noqa: E402


# Minimal template stand-in for tests that don't need the full canonical template.
MINI_TEMPLATE = """# Coordination — {{RUN_TITLE}} ({{DATE_YYYY_MM_DD}})

**Date:** {{DATE_YYYY_MM_DD}}
**Session:** {{PRIMARY_TOOL}} ({{PRIMARY_ROLE}}); {{VERIFIER_TOOL}} ({{VERIFIER_ROLE}})
**Status:** active
**Predecessor:** {{PREVIOUS_RUN_FILE}}

## Scope

{{SCOPE_SUMMARY_2_TO_4_SENTENCES}}

## Operating Rule

Per-run amendments: {{ANY_RUN_SPECIFIC_OPERATING_AMENDMENTS_OR_NONE}}

## File reference

{{THIS_FILE_NAME}}
"""


class BootstrapHappyPathTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bootstrap-test-")
        self.workdir = Path(self.tmpdir)
        self.template_path = self.workdir / "template.md"
        self.template_path.write_text(MINI_TEMPLATE, encoding="utf-8")
        # Isolate App Pulse channel side effects via XDG-style env override.
        # channel_paths.app_slug(workdir) -> app_channel_dir(slug); to avoid
        # writing to the real ~/.build-loop/, monkey-patch app_channel_dir.
        from rally_point import channel_paths
        self._orig_app_channel_dir = channel_paths.app_channel_dir
        self._fake_channel = self.workdir / "fake-channel"
        channel_paths.app_channel_dir = lambda slug: self._fake_channel
        # Cache module reference for teardown
        self._channel_paths = channel_paths

    def tearDown(self):
        self._channel_paths.app_channel_dir = self._orig_app_channel_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_happy_path_writes_coord_file_with_substitutions(self):
        result = cb.bootstrap(
            workdir=self.workdir,
            topic="v0130-feature-x",
            scope="Add feature X across orchestrator and tests.",
            session_id="test-session-001",
            template_path=self.template_path,
            tool="claude_code",
            model="claude-opus-4-7",
        )
        self.assertEqual(result["action"], "bootstrapped")
        coord_path = Path(result["coord_file"])
        self.assertTrue(coord_path.exists(), "coord file not written")
        text = coord_path.read_text(encoding="utf-8")
        # Substitutions
        self.assertIn("v0130 feature x", text.lower())  # title from topic
        self.assertIn("Add feature X across orchestrator and tests.", text)
        self.assertIn("claude_code", text)
        self.assertIn("codex", text)
        # No raw placeholders left for the substituted keys
        self.assertNotIn("{{RUN_TITLE}}", text)
        self.assertNotIn("{{DATE_YYYY_MM_DD}}", text)
        self.assertNotIn("{{SCOPE_SUMMARY_2_TO_4_SENTENCES}}", text)
        # File is under .build-loop/coordination/
        self.assertEqual(coord_path.parent.name, "coordination")
        self.assertEqual(coord_path.parent.parent.name, ".build-loop")
        active_pointer = coord_path.parent / "active.json"
        self.assertTrue(active_pointer.exists(), "active pointer not written")
        pointer = json.loads(active_pointer.read_text(encoding="utf-8"))
        self.assertEqual(Path(pointer["coord_file"]).resolve(), coord_path.resolve())
        self.assertEqual(pointer["session_id"], "test-session-001")
        self.assertRegex(pointer["created_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertTrue(result["active_pointer_written"])

    def test_custom_coord_file_path_is_honored(self):
        custom = self.workdir / "custom" / "myfile.md"
        result = cb.bootstrap(
            workdir=self.workdir,
            topic="t",
            scope="s",
            session_id="sid",
            coord_file=custom,
            template_path=self.template_path,
        )
        self.assertEqual(result["action"], "bootstrapped")
        self.assertTrue(custom.exists())
        # On macOS /var is a symlink to /private/var; compare resolved.
        self.assertEqual(
            Path(result["coord_file"]).resolve(),
            custom.resolve(),
        )


class BootstrapIdempotencyTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bootstrap-idem-")
        self.workdir = Path(self.tmpdir)
        self.template_path = self.workdir / "template.md"
        self.template_path.write_text(MINI_TEMPLATE, encoding="utf-8")
        from rally_point import channel_paths
        self._orig = channel_paths.app_channel_dir
        self._channel_paths = channel_paths
        channel_paths.app_channel_dir = lambda slug: self.workdir / "fake-channel"

    def tearDown(self):
        self._channel_paths.app_channel_dir = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_second_call_does_not_overwrite(self):
        # First call -> bootstrap.
        r1 = cb.bootstrap(
            workdir=self.workdir,
            topic="t",
            scope="original scope",
            session_id="sid-1",
            template_path=self.template_path,
        )
        self.assertEqual(r1["action"], "bootstrapped")
        coord_path = Path(r1["coord_file"])
        original_text = coord_path.read_text(encoding="utf-8")
        original_mtime = coord_path.stat().st_mtime

        # Second call with DIFFERENT scope -> join, no overwrite.
        r2 = cb.bootstrap(
            workdir=self.workdir,
            topic="t",
            scope="DIFFERENT scope; should NOT replace original",
            session_id="sid-2",
            template_path=self.template_path,
        )
        self.assertEqual(r2["action"], "joined-existing-coord")
        self.assertEqual(Path(r2["coord_file"]), coord_path)
        self.assertFalse(r2["active_pointer_written"])
        # Content preserved
        self.assertEqual(coord_path.read_text(encoding="utf-8"), original_text)
        # mtime preserved (no write occurred)
        self.assertAlmostEqual(coord_path.stat().st_mtime, original_mtime, places=2)


class BootstrapErrorHandlingTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bootstrap-err-")
        self.workdir = Path(self.tmpdir)
        from rally_point import channel_paths
        self._orig = channel_paths.app_channel_dir
        self._channel_paths = channel_paths
        channel_paths.app_channel_dir = lambda slug: self.workdir / "fake-channel"

    def tearDown(self):
        self._channel_paths.app_channel_dir = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_missing_template_returns_error_action(self):
        result = cb.bootstrap(
            workdir=self.workdir,
            topic="t",
            scope="s",
            session_id="sid",
            template_path=self.workdir / "does-not-exist.md",
        )
        self.assertEqual(result["action"], "error")
        self.assertTrue(any("template" in e.lower() for e in result["errors"]))


class BootstrapConcurrencyTests(unittest.TestCase):
    """R1 (v0.12.10): atomic-create regression test.

    Spawns N concurrent bootstrap calls against the same topic in the same
    workdir. Pre-v0.12.10 (exists+write_text), all N could race past the
    exists() check and produce N "bootstrapped" envelopes + N duplicate
    handoff posts. With open('x') atomic create, exactly 1 returns
    "bootstrapped" and N-1 return "joined-existing-coord".

    This is the concurrency property Codex's rev 71 VARIANCE flagged
    against v0.12.9.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bootstrap-concurrent-")
        self.workdir = Path(self.tmpdir)
        self.template_path = self.workdir / "template.md"
        self.template_path.write_text(MINI_TEMPLATE, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_concurrent_bootstrap_invocations_atomic_exactly_one_creates(self):
        """5 parallel bootstrap subprocesses against same topic →
        exactly 1 'bootstrapped', 4 'joined-existing-coord'."""
        N = 5
        env = dict(os.environ)
        env["HOME"] = str(self.workdir)
        cmd = [
            sys.executable,
            str(REPO / "scripts" / "coordination_bootstrap.py"),
            "--workdir", str(self.workdir),
            "--topic", "v0130-concurrent-test",
            "--scope", "concurrency atomicity smoke",
            "--template", str(self.template_path),
            "--json",
        ]
        procs = []
        for i in range(N):
            p = subprocess.Popen(
                cmd + ["--session-id", f"concurrent-sid-{i}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
            )
            procs.append(p)
        results = []
        for p in procs:
            stdout, stderr = p.communicate(timeout=30)
            self.assertEqual(p.returncode, 0, f"subprocess failed: stderr={stderr}")
            results.append(json.loads(stdout))
        actions = [r["action"] for r in results]
        bootstrapped_count = sum(1 for a in actions if a == "bootstrapped")
        joined_count = sum(1 for a in actions if a == "joined-existing-coord")
        self.assertEqual(
            bootstrapped_count, 1,
            f"Expected exactly 1 'bootstrapped' (atomic race winner), got {bootstrapped_count}. Actions: {actions}"
        )
        self.assertEqual(
            joined_count, N - 1,
            f"Expected {N-1} 'joined-existing-coord' (race losers), got {joined_count}. Actions: {actions}"
        )
        # Only one coord file should exist (all subprocesses computed the same path)
        coord_files = list((self.workdir / ".build-loop" / "coordination").glob("v0130-concurrent-test-*.md"))
        self.assertEqual(len(coord_files), 1, f"Expected exactly 1 coord file, got {len(coord_files)}")


class BootstrapCLITests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bootstrap-cli-")
        self.workdir = Path(self.tmpdir)
        self.template_path = self.workdir / "template.md"
        self.template_path.write_text(MINI_TEMPLATE, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cli_invocation_emits_json_and_exits_zero(self):
        # Run as a subprocess with HOME pointed at tmp to keep ~/.build-loop/ pristine.
        env = dict(os.environ)
        env["HOME"] = str(self.workdir)
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts" / "coordination_bootstrap.py"),
                "--workdir", str(self.workdir),
                "--topic", "v0199-cli-test",
                "--scope", "CLI smoke test scope.",
                "--session-id", "cli-sid",
                "--template", str(self.template_path),
                "--json",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        envelope = json.loads(proc.stdout)
        self.assertEqual(envelope["action"], "bootstrapped")
        self.assertIn("coord_file", envelope)
        self.assertTrue(Path(envelope["coord_file"]).exists())


if __name__ == "__main__":
    unittest.main()
