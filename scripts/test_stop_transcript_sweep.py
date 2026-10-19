#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Host-contract regression tests for the shared Stop-hook transcript consumers.

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
if "--transcript" in sys.argv:
    from pathlib import Path
    transcript = Path(sys.argv[sys.argv.index("--transcript") + 1])
    rec["transcript_rows"] = [json.loads(line) for line in transcript.read_text().splitlines() if line.strip()]
if {read_stdin!r}:
    try:
        rec["stdin"] = sys.stdin.read()
    except Exception:
        rec["stdin"] = ""
with open(os.environ["BL_TEST_RECORD"], "a") as fh:
    fh.write(json.dumps(rec) + "\\n")
'''


def _sweep_registrations() -> list[str]:
    """Every Stop-hook command that invokes the transcript sweep."""
    data = json.loads(HOOKS_JSON.read_text())
    return [
        entry.get("command", "")
        for group in data["hooks"]["Stop"]
        for entry in group["hooks"]
        if "stop-transcript-sweep.sh" in entry.get("command", "")
    ]


def _hook_command(sweep: str) -> str:
    """Return the Stop-hook command that must launch ``sweep``.

    The consumers share a firing moment and an input, so they are
    registered ONCE in `all` mode. Each per-sweep test
    still drives the real registration and asserts its own recorder fired --
    the contract under test is "this sweep launches from stdin transcript_path",
    not "this sweep has its own hook entry".
    """
    registrations = _sweep_registrations()
    for cmd in registrations:
        if '.sh" all' in cmd:
            return cmd
    markers = SWEEP_MARKERS[sweep]
    for cmd in registrations:
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
        # five consumer scripts.
        (self.root / "hooks").symlink_to(REPO / "hooks")
        scripts = self.root / "scripts"
        scripts.mkdir()
        for dependency in ("transcript_adapter.py", "atomic_io.py"):
            (scripts / dependency).symlink_to(REPO / "scripts" / dependency)

        (scripts / "scan_transcript_for_decisions.py").write_text(
            _RECORDER.format(sweep="decisions", read_stdin=False)
        )
        (scripts / "cost_ledger_hook.py").write_text(
            _RECORDER.format(sweep="cost-ledger", read_stdin=True)
        )
        trace_recorder = _RECORDER.format(sweep="traces", read_stdin=False)
        (scripts / "tool_trace.py").write_text(
            "if __name__ == '__main__':\n" + "\n".join("    " + line for line in trace_recorder.splitlines()) + "\n"
            "def _ingestion_receipt(workdir, transcript, session_id, **kwargs):\n"
            " import json,os\n"
            " with open(os.environ['BL_TEST_RECORD'],'a') as f: f.write(json.dumps({'sweep':'trace-skip','source':str(transcript),'session':session_id})+'\\n')\n")
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
        # Detached worker can finish sibling recorders after the first assertion.
        # Wait for a quiet completed fixture before removing its source tree.
        deadline = time.monotonic() + 3
        changed = time.monotonic()
        previous = None
        while time.monotonic() < deadline:
            signature = self.record.stat().st_mtime_ns if self.record.exists() else None
            if signature != previous:
                previous, changed = signature, time.monotonic()
            leases = list((self.root / "fakehome/.local/state/build-loop/transcript-cache").glob(".normalized-*.lease-*.jsonl"))
            if not leases and time.monotonic() - changed > .25:
                break
            time.sleep(.02)
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
        env["BUILD_LOOP_TRANSCRIPT_WORKER_MS"] = str(getattr(self, "worker_budget_ms", 120000))
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

    def _records(self, sweep: str | None = None, timeout_s: float = 15.0) -> list[dict]:
        """Poll for the backgrounded stub's record rows.

        `all` mode launches five recorders CONCURRENTLY, so the first row to
        land is not necessarily the sweep under test. Waiting for the named
        sweep's own row is the difference between a real assertion and a race.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.record.exists():
                rows = [
                    json.loads(line)
                    for line in self.record.read_text().splitlines()
                    if line.strip()
                ]
                if sweep is not None:
                    matching = [r for r in rows if r.get("sweep") == sweep]
                    if matching:
                        return matching
                elif rows:
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
        rows = self._records("decisions")
        self.assertTrue(
            rows,
            msg="decisions sweep never launched — transcript_path was not read from stdin JSON",
        )
        self.assertIn(str(self.transcript), rows[0]["argv"])

    def test_corrections_sweep_launches_from_stdin_transcript_path(self) -> None:
        cp = self._run("corrections", self._payload())
        self._assert_hook_contract(cp)
        rows = self._records("corrections")
        self.assertTrue(
            rows,
            msg="corrections sweep never launched — transcript_path was not read from stdin JSON",
        )
        self.assertIn(str(self.transcript), rows[0]["argv"])

    def test_findings_sweep_launches_from_stdin_transcript_path(self) -> None:
        cp = self._run("findings", self._payload())
        self._assert_hook_contract(cp)
        rows = self._records("findings")
        self.assertTrue(
            rows,
            msg="findings sweep never launched — transcript_path was not read from stdin JSON",
        )
        self.assertIn(str(self.transcript), rows[0]["argv"])

    def test_cost_ledger_sweep_receives_raw_payload_on_stdin(self) -> None:
        """cost_ledger_hook.py parses the payload itself, so it must be piped it."""
        cp = self._run("cost-ledger", self._payload())
        self._assert_hook_contract(cp)
        rows = self._records("cost-ledger")
        self.assertTrue(
            rows,
            msg="cost-ledger sweep never launched — transcript_path was not read from stdin JSON",
        )
        forwarded = json.loads(rows[0]["stdin"])
        self.assertEqual(forwarded["transcript_path"], str(self.transcript))
        self.assertEqual(forwarded["session_id"], "0fa8e187-e330-403a-97ed-6ad8a2866e98")

    def test_native_codex_path_is_normalized_once_for_text_sweeps(self) -> None:
        self.transcript.write_text("\n".join(json.dumps(row) for row in [
            {"type": "session_meta", "payload": {"id": "codex-fixture", "cwd": str(self.workdir)}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Keep the output short."}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "Confirmed issue: missing event path."}},
        ]) + "\n")
        cp = self._run("decisions", self._payload())
        self._assert_hook_contract(cp)
        normalized_paths = []
        for sweep in ("decisions", "corrections", "findings", "traces"):
            rows = self._records(sweep)
            self.assertTrue(rows, msg=f"{sweep} never launched")
            argv = rows[0]["argv"]
            normalized_paths.append(Path(argv[argv.index("--transcript") + 1]))
        self.assertEqual(len(set(normalized_paths)), 1)
        normalized = normalized_paths[0]
        self.assertNotEqual(normalized, self.transcript)
        turns = self._records("decisions")[0]["transcript_rows"]
        self.assertEqual([row["message"]["role"] for row in turns], ["user", "assistant"])
        self.assertIn("Keep the output short.", json.dumps(turns))
        # Billing attribution must retain the original host path and identity.
        rows = self._records("cost-ledger")
        self.assertTrue(rows)
        self.assertEqual(json.loads(rows[0]["stdin"]), json.loads(self._payload()))

    def test_normalization_failure_cannot_rediscover_a_peer_or_drop_cost_event(self) -> None:
        adapter = self.root / "scripts/transcript_adapter.py"
        adapter.unlink()  # fixture symlink only; never edit the real adapter
        discovery = self.root / "discovery-called"
        for behavior in ("return None", "raise ImportError('missing cache dependency')"):
            with self.subTest(behavior=behavior):
                self.record.unlink(missing_ok=True)
                discovery.unlink(missing_ok=True)
                adapter.write_text(
                    "from pathlib import Path\nCODEX = 'codex'\n"
                    "def detect_format(path): return CODEX\n"
                    f"def normalize_cached(source, cache, **kwargs): {behavior}\n"
                    "if __name__ == '__main__':\n"
                    f"    Path({str(discovery)!r}).write_text('unexpected discovery')\n"
                    f"    print({str(self.transcript)!r})\n"
                )
                cp = self._run("all", self._payload())
                self._assert_hook_contract(cp)
                rows = self._records("cost-ledger")
                self.assertTrue(rows, "original cost event was dropped with text normalization")
                self.assertEqual(json.loads(rows[0]["stdin"]), json.loads(self._payload()))
                self.assertFalse(discovery.exists(), "explicit path must never fall back to another session")
                skipped = self._records("trace-skip", timeout_s=2)
                self.assertTrue(skipped)
                self.assertEqual(skipped[0]["source"], str(self.transcript))
                self.assertEqual(skipped[0]["session"], json.loads(self._payload())["session_id"])
                self.assertEqual({row["sweep"] for row in self._records()}, {"cost-ledger", "trace-skip"})

    def test_foreground_returns_while_normalization_is_blocked_and_cost_is_delivered(self) -> None:
        adapter = self.root / "scripts/transcript_adapter.py"
        adapter.unlink()
        release = self.root / "release-worker"
        started = self.root / "worker-started"
        adapter.write_text(
            "from pathlib import Path\nimport time\nCODEX='codex'\n"
            "def detect_format(path): return CODEX\n"
            "def normalize_cached(source, cache, **kwargs):\n"
            f" Path({str(started)!r}).touch()\n"
            " deadline=time.monotonic()+4\n"
            f" while not Path({str(release)!r}).exists() and time.monotonic()<deadline: time.sleep(.02)\n"
            " return None\n")
        before = time.monotonic()
        cp = self._run("all", self._payload())
        elapsed = time.monotonic() - before
        try:
            self._assert_hook_contract(cp)
            self.assertLess(elapsed, 1.5, "foreground waited for extraction")
            rows = self._records("cost-ledger", timeout_s=2)
            self.assertTrue(rows, "original billing event waited for extraction")
            self.assertEqual(json.loads(rows[0]["stdin"]), json.loads(self._payload()))
        finally:
            release.touch()

    def test_hung_billing_is_bounded_and_text_consumers_still_finish(self) -> None:
        import signal
        from unittest.mock import patch
        self.worker_budget_ms = 1000
        billing = self.root / "scripts/cost_ledger_hook.py"
        billing.write_text("import os,sys,time,json\n"
            "payload=sys.stdin.read()\n"
            "with open(os.environ['BL_TEST_RECORD'],'a') as f: f.write(json.dumps({'sweep':'cost-ledger','argv':[],'stdin':payload,'pid':os.getpid()})+'\\n')\n"
            "time.sleep(10)\n")
        for mode in ("all", "cost-ledger"):
            with self.subTest(mode=mode):
                self.record.unlink(missing_ok=True)
                command = 'bash "$CLAUDE_PLUGIN_ROOT/hooks/stop-transcript-sweep.sh" ' + mode
                with patch(__name__ + '._hook_command', return_value=command):
                    cp = self._run(mode, self._payload())
                self._assert_hook_contract(cp)
                rows = self._records('cost-ledger', timeout_s=2)
                self.assertTrue(rows)
                pid = rows[0]['pid']
                try:
                    self.assertEqual(json.loads(rows[0]['stdin']), json.loads(self._payload()))
                    if mode == 'all':
                        for consumer in ('decisions','corrections','findings'):
                            self.assertTrue(self._records(consumer, timeout_s=2), consumer)
                    deadline = time.monotonic() + 3
                    alive = True
                    while time.monotonic() < deadline:
                        try: os.kill(pid, 0)
                        except ProcessLookupError:
                            alive = False
                            break
                        time.sleep(.02)
                    self.assertFalse(alive, 'billing child outlived the worker deadline')
                finally:
                    # A RED baseline leaves this exact owned fixture running.
                    command = subprocess.run(['ps','-p',str(pid),'-o','command='], capture_output=True, text=True).stdout
                    if str(billing) in command:
                        try: os.kill(pid, signal.SIGTERM)
                        except ProcessLookupError: pass

    def test_stalled_trace_is_terminated_at_worker_deadline(self):
        import signal
        self.worker_budget_ms = 1000
        trace_script = self.root / "scripts/tool_trace.py"
        trace_script.write_text("import os,time,json\n"
            "with open(os.environ['BL_TEST_RECORD'],'a') as f: f.write(json.dumps({'sweep':'traces','pid':os.getpid()})+'\\n')\n"
            "time.sleep(10)\n")
        cp = self._run("all",self._payload())
        self._assert_hook_contract(cp)
        rows = self._records("traces", timeout_s=2)
        self.assertTrue(rows)
        pid = rows[0]["pid"]
        try:
            self.assertTrue(self._records("cost-ledger",timeout_s=2))
            for consumer in ("decisions","corrections","findings"):
                self.assertTrue(self._records(consumer,timeout_s=2))
            deadline = time.monotonic()+3
            while time.monotonic()<deadline:
                try: os.kill(pid,0)
                except ProcessLookupError: break
                time.sleep(.02)
            else: self.fail("trace consumer outlived worker deadline")
        finally:
            command = subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout
            if str(trace_script) in command:
                try: os.kill(pid,signal.SIGTERM)
                except ProcessLookupError: pass

    def test_trace_has_no_foreground_stop_registration(self):
        data = json.loads(HOOKS_JSON.read_text())
        commands = [h.get("command", "") for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertFalse(any("/scripts/tool_trace.py" in c and " ingest " in c for c in commands))

    def test_large_admitted_source_returns_before_trace_ingestion(self):
        self.worker_budget_ms = 1000
        with self.transcript.open("w") as stream:
            for index in range(75000):
                for role in ("assistant", "user"):
                    stream.write(json.dumps({"type":role,"message":{"role":role,"content":[
                        {"type":"tool_use" if role == "assistant" else "tool_result",
                         "id":str(index), "tool_use_id":str(index), "name":"Bash", "input":{"command":"true"},
                         "content":"ok"}]}})+"\n")
        self.assertGreater(self.transcript.stat().st_size, 17_000_000)
        started = time.monotonic()
        cp = self._run("all",self._payload())
        self._assert_hook_contract(cp)
        self.assertLess(time.monotonic()-started,1.5)
        time.sleep(1.1)  # let the owned process-group deadline close fixture readers

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
        rows = self._records("decisions")
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


class StopSweepRegistrationIsMeceTests(unittest.TestCase):
    """One firing moment + one input + one intent = one hook.

    Four separate registrations were four copies of the same hook, each
    re-resolving and re-normalizing the same transcript. A future edit that
    re-splits them has to argue with this test.
    """

    def test_exactly_one_sweep_registration_exists(self) -> None:
        registrations = _sweep_registrations()
        self.assertEqual(
            len(registrations), 1,
            f"expected ONE collapsed sweep registration, found {len(registrations)}: "
            f"{registrations}",
        )

    def test_the_single_registration_runs_all_mode(self) -> None:
        self.assertIn('.sh" all', _sweep_registrations()[0])
