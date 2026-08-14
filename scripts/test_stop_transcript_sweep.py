#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Host-contract regression tests for the four Stop-hook transcript sweeps.

THE DEFECT THESE CONVICT
------------------------
`hooks/hooks.json` guarded four Stop sweeps with
``if [ -n "$CLAUDE_TRANSCRIPT_PATH" ]``. **That environment variable does not
exist.** Claude Code delivers the transcript path only inside the stdin JSON
payload as ``transcript_path`` (verified live against Claude Code 2.1.232 on
2026-08-14; the documented hook env vars are CLAUDE_PROJECT_DIR /
CLAUDE_PLUGIN_ROOT / CLAUDE_PLUGIN_DATA / CLAUDE_EFFORT / CLAUDE_CODE_REMOTE /
CLAUDE_CODE_BRIDGE_SESSION_ID / CLAUDE_PLUGIN_OPTION_*). The guard was false in
every real session, the python never launched, and nothing logged.

`scripts/test_stop_hook_integration.py` missed it for three months because it
SET ``CLAUDE_TRANSCRIPT_PATH`` itself — it validated the scanner while never
testing the host contract.

These tests close that hole: they DELETE ``CLAUDE_TRANSCRIPT_PATH`` from the
environment and supply ``transcript_path`` ONLY on stdin, exactly as the host
does. Against the pre-fix wiring every sweep assertion fails (nothing launches);
against the fixed wiring all four launch with the stdin-supplied path.

STDIN SHARING
-------------
Sibling hooks in one matcher group run in PARALLEL and each command process
gets its OWN stdin pipe carrying a full copy of the payload — verified live with
a three-sibling ``cat > file`` probe that produced three byte-identical
payloads (same md5). So each entry reading stdin independently is correct and
cannot starve a later entry. These tests exercise each entry independently for
that reason.

Offline + deterministic: the real sweep scripts are replaced by recorder stubs
under a temporary ``CLAUDE_PLUGIN_ROOT``, so no Ollama, no Postgres, no network.

Point the tests at an alternate hooks.json with ``BUILD_LOOP_HOOKS_JSON=<path>``
(used to demonstrate the pre-fix failure).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HOOKS_JSON = Path(os.environ.get("BUILD_LOOP_HOOKS_JSON") or (REPO / "hooks" / "hooks.json"))

# Tokens that identify each sweep's Stop-hook entry. Both the pre-fix inline
# commands and the post-fix wrapper invocation are matched, so one test body
# can run against either wiring.
SWEEP_MARKERS = {
    "decisions": ("scan_transcript_for_decisions.py", "stop-transcript-sweep.sh\" decisions"),
    "corrections": ("scan_corrections", "stop-transcript-sweep.sh\" corrections"),
    "findings": ("scan_findings", "stop-transcript-sweep.sh\" findings"),
    "cost-ledger": ("cost_ledger_hook.py", "stop-transcript-sweep.sh\" cost-ledger"),
}

_RECORDER = '''#!/usr/bin/env python3
import json, os, sys
rec = {{"sweep": {sweep!r}, "argv": sys.argv[1:]}}
if {read_stdin!r}:
    try:
        rec["stdin"] = sys.stdin.read()
    except Exception:
        rec["stdin"] = ""
with open(os.environ["BL_TEST_RECORD"], "a") as fh:
    fh.write(json.dumps(rec) + "\\n")
'''


def _hook_command(sweep: str) -> str:
    """Return the Stop-hook command string for one sweep, from hooks.json."""
    data = json.loads(HOOKS_JSON.read_text())
    markers = SWEEP_MARKERS[sweep]
    for group in data["hooks"]["Stop"]:
        for entry in group["hooks"]:
            cmd = entry.get("command", "")
            if any(m in cmd for m in markers):
                return cmd
    raise AssertionError(f"no Stop-hook entry found for sweep {sweep!r} in {HOOKS_JSON}")


class StopSweepHostContractTests(unittest.TestCase):
    """Each sweep must launch from stdin `transcript_path` with NO env var."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        # Fake CLAUDE_PLUGIN_ROOT: real hooks/ (so the wrapper + its python
        # resolver are the shipped ones) + recorder stubs in place of the
        # four sweep scripts.
        (self.root / "hooks").symlink_to(REPO / "hooks")
        scripts = self.root / "scripts"
        scripts.mkdir()

        (scripts / "scan_transcript_for_decisions.py").write_text(
            _RECORDER.format(sweep="decisions", read_stdin=False)
        )
        (scripts / "cost_ledger_hook.py").write_text(
            _RECORDER.format(sweep="cost-ledger", read_stdin=True)
        )
        for mod, sweep in (("scan_corrections", "corrections"), ("scan_findings", "findings")):
            pkg = scripts / mod
            pkg.mkdir()
            (pkg / "__init__.py").write_text("")
            (pkg / "__main__.py").write_text(_RECORDER.format(sweep=sweep, read_stdin=False))

        self.workdir = self.root / "project"
        self.workdir.mkdir()
        self.transcript = self.workdir / "session.jsonl"
        self.transcript.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n"
        )
        self.record = self.root / "record.jsonl"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ---- helpers -------------------------------------------------------

    def _payload(self) -> str:
        """A Stop payload shaped exactly like the live host's."""
        return json.dumps(
            {
                "session_id": "0fa8e187-e330-403a-97ed-6ad8a2866e98",
                "transcript_path": str(self.transcript),
                "cwd": str(self.workdir),
                "hook_event_name": "Stop",
                "stop_hook_active": False,
                "permission_mode": "default",
            }
        )

    def _run(self, sweep: str, stdin: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        # THE POINT OF THIS TEST: the host does not set this. Any hook that
        # needs it is dead in production.
        env.pop("CLAUDE_TRANSCRIPT_PATH", None)
        env["CLAUDE_PLUGIN_ROOT"] = str(self.root)
        env["CLAUDE_PROJECT_DIR"] = str(self.workdir)
        env["BL_TEST_RECORD"] = str(self.record)
        env["HOME"] = str(self.root / "fakehome")
        env["XDG_STATE_HOME"] = str(self.root / "fakehome" / ".local" / "state")
        return subprocess.run(
            ["/bin/sh", "-c", _hook_command(sweep)],
            input=stdin,  # the host delivers the payload HERE, not via env
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.workdir),
            timeout=20,
        )

    def _records(self, timeout_s: float = 15.0) -> list[dict]:
        """Poll for the backgrounded stub's record rows."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.record.exists():
                rows = [
                    json.loads(line)
                    for line in self.record.read_text().splitlines()
                    if line.strip()
                ]
                if rows:
                    return rows
            time.sleep(0.1)
        return []

    def _assert_hook_contract(self, cp: subprocess.CompletedProcess) -> None:
        self.assertEqual(cp.returncode, 0, msg=f"hook exited nonzero: {cp.stderr!r}")
        self.assertEqual(json.loads(cp.stdout), {}, msg=f"stdout not no-op JSON: {cp.stdout!r}")
        self.assertEqual(cp.stderr, "", msg=f"unexpected stderr: {cp.stderr!r}")

    # ---- the convicting tests -----------------------------------------

    def test_decisions_sweep_launches_from_stdin_transcript_path(self) -> None:
        cp = self._run("decisions", self._payload())
        self._assert_hook_contract(cp)
        rows = self._records()
        self.assertTrue(
            rows,
            msg="decisions sweep never launched — transcript_path was not read from stdin JSON",
        )
        self.assertIn(str(self.transcript), rows[0]["argv"])

    def test_corrections_sweep_launches_from_stdin_transcript_path(self) -> None:
        cp = self._run("corrections", self._payload())
        self._assert_hook_contract(cp)
        rows = self._records()
        self.assertTrue(
            rows,
            msg="corrections sweep never launched — transcript_path was not read from stdin JSON",
        )
        self.assertIn(str(self.transcript), rows[0]["argv"])

    def test_findings_sweep_launches_from_stdin_transcript_path(self) -> None:
        cp = self._run("findings", self._payload())
        self._assert_hook_contract(cp)
        rows = self._records()
        self.assertTrue(
            rows,
            msg="findings sweep never launched — transcript_path was not read from stdin JSON",
        )
        self.assertIn(str(self.transcript), rows[0]["argv"])

    def test_cost_ledger_sweep_receives_raw_payload_on_stdin(self) -> None:
        """cost_ledger_hook.py parses the payload itself, so it must be piped it."""
        cp = self._run("cost-ledger", self._payload())
        self._assert_hook_contract(cp)
        rows = self._records()
        self.assertTrue(
            rows,
            msg="cost-ledger sweep never launched — transcript_path was not read from stdin JSON",
        )
        forwarded = json.loads(rows[0]["stdin"])
        self.assertEqual(forwarded["transcript_path"], str(self.transcript))
        self.assertEqual(forwarded["session_id"], "0fa8e187-e330-403a-97ed-6ad8a2866e98")

    # ---- fail-open / never-block contract ------------------------------

    def test_all_sweeps_no_op_cleanly_on_empty_stdin(self) -> None:
        """No payload (or a host that sends nothing) must not launch or block."""
        for sweep in SWEEP_MARKERS:
            with self.subTest(sweep=sweep):
                cp = self._run(sweep, "")
                self._assert_hook_contract(cp)
        self.assertEqual(self._records(timeout_s=1.0), [])

    def test_all_sweeps_no_op_cleanly_on_malformed_stdin(self) -> None:
        """Garbage on stdin must not crash the hook or block the session."""
        for sweep in SWEEP_MARKERS:
            with self.subTest(sweep=sweep):
                cp = self._run(sweep, "not json at all {{{")
                self._assert_hook_contract(cp)
        self.assertEqual(self._records(timeout_s=1.0), [])

    def test_all_sweeps_no_op_when_payload_has_no_transcript_path(self) -> None:
        payload = json.dumps({"session_id": "abc", "cwd": str(self.workdir)})
        for sweep in SWEEP_MARKERS:
            with self.subTest(sweep=sweep):
                cp = self._run(sweep, payload)
                self._assert_hook_contract(cp)
        self.assertEqual(self._records(timeout_s=1.0), [])

    def test_shell_metacharacters_in_transcript_path_do_not_inject(self) -> None:
        """Payload values must reach python as argv, never as shell source.

        Closes docs/SECURITY_FOLLOWUP_2026-05-05.md, which flagged the old
        inline hook for interpolating the path into a double-quoted shell string.
        """
        evil = self.workdir / 'a";touch "' / "pwned.jsonl"
        evil.parent.mkdir(parents=True, exist_ok=True)
        evil.write_text("{}\n")
        payload = json.dumps(
            {
                "session_id": "abc",
                "transcript_path": str(evil),
                "cwd": str(self.workdir),
            }
        )
        cp = self._run("decisions", payload)
        self._assert_hook_contract(cp)
        rows = self._records()
        self.assertTrue(rows, msg="decisions sweep never launched")
        self.assertIn(str(evil), rows[0]["argv"])
        self.assertFalse(
            (self.workdir / "pwned.jsonl").exists(),
            msg="shell injection succeeded — payload was interpolated into shell source",
        )


class StopHookEnvVarPolicyTests(unittest.TestCase):
    """No shipped hook command may gate on the non-existent env var."""

    def test_no_hook_command_gates_on_claude_transcript_path(self) -> None:
        data = json.loads(HOOKS_JSON.read_text())
        offenders = []
        for event, groups in data["hooks"].items():
            for group in groups:
                for entry in group.get("hooks", []):
                    cmd = entry.get("command", "")
                    if "CLAUDE_TRANSCRIPT_PATH" in cmd:
                        offenders.append(f"{event}: {cmd[:120]}")
        self.assertEqual(
            offenders,
            [],
            msg=(
                "CLAUDE_TRANSCRIPT_PATH is not a real Claude Code env var; a hook "
                "gating on it is dead in every session. Read transcript_path from "
                "the stdin JSON payload instead.\n" + "\n".join(offenders)
            ),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
