# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for hooks/post-push-closeout.sh — warm-context soft closeout.

The hook fires the EXISTING `python3 -m closeout` in the background after a
`git push`, complementing (not replacing) the pre-push armed-baton +
SessionStart drain crash fallback. Contract:

1. Only `git push` commands trigger it; any other command is a clean no-op.
2. Outside a build-loop project (no `.build-loop/`) it is a no-op.
3. It always exits 0 (PostToolUse discipline — never blocks the turn).
4. On a `git push` inside a build-loop project, it launches a background
   closeout that writes a NON-EMPTY, valid-JSON stdout log under
   `.build-loop/closeout/`.  (Empty = closeout failed to run — dormancy.)

Driven as a subprocess against a real temp project so we exercise the
installed contract, not a mock.  CLAUDE_PLUGIN_ROOT is passed (the real hook
contract); PYTHONPATH is intentionally absent — the hook must set it itself.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent  # scripts/ → repo
HOOK = REPO_ROOT / "hooks" / "post-push-closeout.sh"


def _run_hook(workdir: Path, tool_input: str) -> subprocess.CompletedProcess[str]:
    """Run the hook with TOOL_INPUT set and CLAUDE_PROJECT_DIR pointed at workdir.

    CLAUDE_PLUGIN_ROOT is the real hook contract: the hook exports
    PYTHONPATH=${PLUGIN_ROOT}/scripts internally.  No PYTHONPATH rigging here —
    this exercises the production code path, not a test-injected shortcut.
    """
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "TOOL_INPUT": tool_input,
        "CLAUDE_PROJECT_DIR": str(workdir),
        "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
        "HOME": str(workdir),
    }
    return subprocess.run(
        ["bash", str(HOOK)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(workdir),
        timeout=15,
    )


def _wait_for_log(closeout_dir: Path, timeout_s: float = 8.0) -> list[Path]:
    """Poll for background closeout stdout logs that are non-empty.

    nohup creates the output file immediately (0 bytes) before the background
    process writes to it, so we must wait for size > 0, not just existence.
    A 0-byte file means the background closeout has not yet written output (or
    failed silently) — the dormancy case we are testing against.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if closeout_dir.exists():
            logs = [
                p for p in closeout_dir.glob("postpush-*.stdout.json")
                if p.stat().st_size > 0
            ]
            if logs:
                return logs
        time.sleep(0.2)
    # Final check — return whatever exists (caller will assert size > 0).
    return (
        [p for p in closeout_dir.glob("postpush-*.stdout.json") if p.stat().st_size > 0]
        if closeout_dir.exists() else []
    )


class TestHookExists(unittest.TestCase):
    def test_hook_present_and_executable(self) -> None:
        self.assertTrue(HOOK.exists(), f"hook missing at {HOOK}")
        import os
        self.assertTrue(os.access(HOOK, os.X_OK), "hook is not executable")


class TestGating(unittest.TestCase):
    def test_non_push_command_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / ".build-loop" / "closeout").mkdir(parents=True)
            cp = _run_hook(wd, "ls -la && echo done")
            self.assertEqual(cp.returncode, 0)
            # No background closeout launched → no postpush log.
            time.sleep(0.5)
            logs = list((wd / ".build-loop" / "closeout").glob("postpush-*.stdout.json"))
            self.assertEqual(logs, [], "non-push command must not fire closeout")

    def test_outside_build_loop_project_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)  # no .build-loop/
            cp = _run_hook(wd, "git push origin main")
            self.assertEqual(cp.returncode, 0)
            self.assertFalse((wd / ".build-loop").exists())


class TestFiresOnPush(unittest.TestCase):
    def test_git_push_fires_background_closeout(self) -> None:
        """Closeout must run (non-empty valid JSON log), not just exist as 0-byte file.

        This is the dormancy closure proof: a 0-byte log means python3 -m closeout
        failed silently (e.g. PYTHONPATH missing). We assert:
          1. A postpush-*.stdout.json log file is created.
          2. Its content is non-empty (>0 bytes).
          3. Its content parses as valid JSON.
        """
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / ".build-loop").mkdir(parents=True)
            cp = _run_hook(wd, "git push origin main")
            self.assertEqual(cp.returncode, 0, f"hook must exit 0; stderr={cp.stderr}")
            logs = _wait_for_log(wd / ".build-loop" / "closeout")
            self.assertTrue(
                logs, "git push must launch a background closeout writing a postpush log"
            )
            log_path = logs[0]
            log_bytes = log_path.read_bytes()
            self.assertGreater(
                len(log_bytes), 0,
                f"closeout log is empty (0 bytes) — python3 -m closeout did not run; "
                f"PYTHONPATH was not set by the hook. File: {log_path}"
            )
            try:
                json.loads(log_bytes)
            except json.JSONDecodeError as exc:
                self.fail(
                    f"closeout log is not valid JSON — closeout ran but output is corrupt. "
                    f"Content: {log_bytes[:200]!r}. Error: {exc}"
                )

    def test_exit_zero_even_if_python_missing(self) -> None:
        # An empty PATH plus the hidden fallback paths overridden: hook must still
        # exit 0 (fail-open) when no python3 is resolvable. We point the fallback
        # absolute paths at nothing by running with an empty PATH and HOME set to a
        # dir with no python; bash is invoked by absolute path so the shell starts.
        import shutil
        bash = shutil.which("bash") or "/bin/bash"
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / ".build-loop").mkdir(parents=True)
            # Empty PATH: command -v python3/python both fail. The hook's hardcoded
            # /usr/bin/python3 etc. may still exist on the host, which is fine — the
            # contract under test is "exit 0 regardless". Either way it must be 0.
            cp = subprocess.run(
                [bash, str(HOOK)],
                capture_output=True, text=True, timeout=10,
                env={"PATH": "", "TOOL_INPUT": "git push origin main",
                     "CLAUDE_PROJECT_DIR": str(wd)},
                cwd=str(wd),
            )
            self.assertEqual(cp.returncode, 0)


if __name__ == "__main__":
    unittest.main()
