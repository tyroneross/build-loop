#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for privileged_audit.py. Zero deps. Run: python3 test_privileged_audit.py

Fixtures reproduce the REAL transcript shapes byte-for-byte where it matters:
the Codex rollout record wraps the command twice (a JSON line whose `input` is a
JavaScript source string containing a JSON object), which is exactly what an
earlier regex-only extractor got wrong.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "privileged_audit.py"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import privileged_audit as pa  # noqa: E402
import privileged_broker as pb  # noqa: E402

# The two invocations that produced the 2026-08-20 dialogs, verbatim.
OBSERVED_FIRST = "sfltool dumpbtm 2>/dev/null | rg -n -C 2 'bash|env|python|RossLabs' | sed -n '1,260p'"
OBSERVED_RETRY = "set -o pipefail\nsfltool dumpbtm | sed -n '1,120p'\nrc=$?\necho \"sfltool_rc=$rc\""


def codex_call(ts: str, command: str) -> str:
    """A Codex rollout line: the command sits inside a JS source string."""
    js = 'const r = await tools.exec_command(' + json.dumps(
        {"cmd": command, "workdir": "/repo", "yield_time_ms": 10000}
    ) + '); text(r.output);'
    return json.dumps({
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "custom_tool_call", "call_id": "c1", "name": "exec", "input": js},
    })


def codex_output(ts: str, command: str) -> str:
    """A tool-call OUTPUT that echoes the command. Must NOT be counted."""
    return json.dumps({
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "custom_tool_call_output", "call_id": "c1",
                    "output": [{"type": "input_text", "text": f"ran: {command}"}]},
    })


def claude_call(ts: str, command: str) -> str:
    return json.dumps({
        "timestamp": ts,
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Bash",
                                 "input": {"command": command, "description": "d"}}]},
    })


def assistant_prose(ts: str, command: str) -> str:
    """An assistant message merely QUOTING a command. Must NOT be counted."""
    return json.dumps({
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": f"You could run `{command}`."}]},
    })


class AuditCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="privaudit-"))
        self.codex = self.tmp / "codex"
        self.claude = self.tmp / "claude"
        for d in (self.codex, self.claude):
            d.mkdir(parents=True)
        self.roots = {"codex": self.codex, "claude": self.claude}

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, root: Path, name: str, lines: list[str]) -> Path:
        path = root / f"{name}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def scan(self, **kw):
        return pa.scan(roots=self.roots, **kw)


class TestExtraction(AuditCase):
    def test_double_wrapped_codex_command_is_recovered_intact(self):
        line = codex_call("2026-08-20T08:29:25.873Z", OBSERVED_RETRY)
        record = json.loads(line)
        found = set()
        for payload in pa.tool_call_inputs(record):
            found.update(pa.commands_in(payload))
        self.assertIn(OBSERVED_RETRY, found,
                      "embedded newlines and quotes must survive extraction")

    def test_claude_tool_use_command_is_recovered(self):
        record = json.loads(claude_call("2026-08-20T08:00:00Z", "sfltool dumpbtm"))
        found = set()
        for payload in pa.tool_call_inputs(record):
            found.update(pa.commands_in(payload))
        self.assertEqual(found, {"sfltool dumpbtm"})

    def test_tool_call_outputs_are_not_counted(self):
        record = json.loads(codex_output("2026-08-20T08:00:00Z", "sfltool dumpbtm"))
        self.assertEqual(pa.tool_call_inputs(record), [],
                         "an echoed command in an output must not inflate the baseline")

    def test_quoted_commands_in_prose_are_not_counted(self):
        record = json.loads(assistant_prose("2026-08-20T08:00:00Z", "sudo rm -rf /"))
        self.assertEqual(pa.tool_call_inputs(record), [])


class TestCounting(AuditCase):
    def test_the_observed_incident_is_reconstructed_as_one_retry(self):
        self.write(self.codex, "rollout-A", [
            codex_call("2026-08-20T08:29:11.478Z", OBSERVED_FIRST),
            codex_call("2026-08-20T08:29:25.873Z", OBSERVED_RETRY),
        ])
        out = self.scan(window=300)
        self.assertEqual(out["counts"]["privileged_invocations"], 2)
        self.assertEqual(out["counts"]["os_prompts"], 2)
        self.assertEqual(out["counts"]["distinct_requests"], 1,
                         "the two shell strings are ONE privileged request")
        self.assertEqual(out["counts"]["retries"], 1)
        self.assertEqual(len(out["retry_detail"]), 1)
        self.assertAlmostEqual(out["retry_detail"][0]["gap_seconds"], 14.4, places=1)

    def test_a_repeat_outside_the_window_is_not_a_retry(self):
        self.write(self.codex, "rollout-A", [
            codex_call("2026-08-20T08:00:00Z", "sfltool dumpbtm"),
            codex_call("2026-08-20T09:00:00Z", "sfltool dumpbtm"),
        ])
        self.assertEqual(self.scan(window=300)["counts"]["retries"], 0)

    def test_two_sessions_on_one_key_are_a_simultaneous_cluster(self):
        self.write(self.codex, "rollout-A", [codex_call("2026-08-20T08:29:11Z", OBSERVED_FIRST)])
        self.write(self.codex, "rollout-B", [codex_call("2026-08-20T08:29:40Z", "sfltool dumpbtm")])
        out = self.scan(window=300)
        self.assertEqual(out["counts"]["simultaneous_task_clusters"], 1)
        self.assertEqual(out["counts"]["sessions"], 2)
        self.assertEqual(out["counts"]["retries"], 0, "different sessions are not a retry")

    def test_different_commands_are_different_requests(self):
        self.write(self.codex, "rollout-A", [
            codex_call("2026-08-20T08:00:00Z", "sfltool dumpbtm"),
            codex_call("2026-08-20T08:00:10Z", "csrutil disable"),
        ])
        out = self.scan(window=300)
        self.assertEqual(out["counts"]["distinct_requests"], 2)
        self.assertEqual(out["counts"]["retries"], 0)

    def test_unprivileged_commands_are_ignored(self):
        self.write(self.codex, "rollout-A", [
            codex_call("2026-08-20T08:00:00Z", "csrutil status"),
            codex_call("2026-08-20T08:00:01Z", "spctl -a -vv -t exec /Applications/X.app"),
            codex_call("2026-08-20T08:00:02Z", "nvram -p"),
            codex_call("2026-08-20T08:00:03Z", "ls -la"),
        ])
        self.assertEqual(self.scan()["counts"]["privileged_invocations"], 0)

    def test_non_prompting_privileged_commands_count_as_invocations_not_prompts(self):
        self.write(self.codex, "rollout-A", [codex_call("2026-08-20T08:00:00Z", "sudo -n true")])
        out = self.scan()
        self.assertEqual(out["counts"]["privileged_invocations"], 1)
        self.assertEqual(out["counts"]["os_prompts"], 0, "sudo -n cannot open a dialog")

    def test_every_baseline_invocation_is_unattributed(self):
        self.write(self.codex, "rollout-A", [codex_call("2026-08-20T08:00:00Z", "sfltool dumpbtm")])
        out = self.scan()
        self.assertEqual(out["counts"]["unattributed"], out["counts"]["privileged_invocations"])

    def test_since_filter_excludes_older_activity(self):
        self.write(self.codex, "rollout-A", [
            codex_call("2026-01-01T08:00:00Z", "sfltool dumpbtm"),
            codex_call("2026-08-20T08:00:00Z", "sfltool dumpbtm"),
        ])
        out = self.scan(since=pa._since_epoch("2026-08-01"))
        self.assertEqual(out["counts"]["privileged_invocations"], 1)

    def test_both_transcript_sources_are_scanned(self):
        self.write(self.codex, "rollout-A", [codex_call("2026-08-20T08:00:00Z", "sfltool dumpbtm")])
        self.write(self.claude, "session-B", [claude_call("2026-08-20T08:00:05Z", "csrutil disable")])
        out = self.scan()
        self.assertEqual(out["counts"]["privileged_invocations"], 2)
        self.assertEqual({"codex", "claude"} & {"codex", "claude"}, {"codex", "claude"})


class TestCounterfactual(AuditCase):
    def test_the_observed_retry_would_not_have_reprompted(self):
        self.write(self.codex, "rollout-A", [
            codex_call("2026-08-20T08:29:11.478Z", OBSERVED_FIRST),
            codex_call("2026-08-20T08:29:25.873Z", OBSERVED_RETRY),
        ])
        out = self.scan(window=300)
        cf = out["counterfactual"]
        self.assertEqual(out["counts"]["os_prompts"], 2, "before: two dialogs")
        self.assertEqual(cf["os_prompts"], 1, "after: one dialog")
        self.assertEqual(cf["coalesced"], 1)

    def test_mutating_requests_are_never_projected_away(self):
        self.write(self.codex, "rollout-A", [
            codex_call("2026-08-20T08:00:00Z", "csrutil disable"),
            codex_call("2026-08-20T08:00:05Z", "csrutil disable"),
        ])
        cf = self.scan()["counterfactual"]
        self.assertEqual(cf["os_prompts"], 2, "a mutation must never inherit an approval")
        self.assertEqual(cf["coalesced"], 0)

    def test_a_repeat_after_the_ttl_reprompts(self):
        # sfltool-dumpbtm ships with ttl_seconds 900.
        self.write(self.codex, "rollout-A", [
            codex_call("2026-08-20T08:00:00Z", "sfltool dumpbtm"),
            codex_call("2026-08-20T08:20:00Z", "sfltool dumpbtm"),
        ])
        cf = self.scan(window=300)["counterfactual"]
        self.assertEqual(cf["os_prompts"], 2, "an expired result must not be replayed")


class TestReport(AuditCase):
    def test_report_separates_projected_from_measured(self):
        self.write(self.codex, "rollout-A", [
            codex_call("2026-08-20T08:29:11Z", OBSERVED_FIRST),
            codex_call("2026-08-20T08:29:25Z", OBSERVED_RETRY),
        ])
        out = pa.report(roots=self.roots, root=self.tmp / "empty-store", window=300)
        self.assertFalse(out["after_projected"]["measured"])
        self.assertTrue(out["after_measured"]["measured"])
        self.assertEqual(out["before"]["os_prompts"], 2)
        self.assertEqual(out["after_projected"]["os_prompts"], 1)
        self.assertEqual(out["after_measured"]["os_prompts"], 0)
        self.assertEqual(out["after_projected"]["unattributed"], 0)

    def test_an_empty_ledger_is_reported_as_no_traffic_not_as_no_requests(self):
        out = pa.report(roots=self.roots, root=self.tmp / "empty-store")
        self.assertIn("NOT that no privileged request occurred", out["note"])
        self.assertFalse(out["after_measured"]["ledger_present"])

    def test_measured_counts_come_from_a_real_broker_ledger(self):
        root = self.tmp / "store"
        pb.ensure_root(root)
        config = json.loads(json.dumps(pb.DEFAULT_CONFIG))
        for event in ("requested", "prompted", "completed", "coalesced"):
            pb.append_event(root, {"event": event, "key": "k1",
                                   "initiating_task_id": "T1", "waiter_task_ids": []}, config)
        out = pa.report(roots=self.roots, root=root)
        self.assertEqual(out["after_measured"]["privileged_invocations"], 1)
        self.assertEqual(out["after_measured"]["os_prompts"], 1)
        self.assertEqual(out["after_measured"]["coalesced"], 1)
        self.assertTrue(out["after_measured"]["ledger_integrity_ok"])

    def test_coverage_gaps_are_surfaced_as_unattributed(self):
        root = self.tmp / "store"
        pb.ensure_root(root)
        pb.coverage_gap(root, reason="broker_root_unusable", risk_class="read_only",
                        behavior="proceed_uncoalesced", detail="x")
        out = pa.report(roots=self.roots, root=root)
        self.assertEqual(out["after_measured"]["unattributed"], 1)
        self.assertEqual(out["after_measured"]["coverage_gaps"]["total"], 1)


class TestCLI(AuditCase):
    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)

    def test_scan_json_is_parseable(self):
        self.write(self.codex, "rollout-A", [codex_call("2026-08-20T08:00:00Z", "sfltool dumpbtm")])
        out = self.cli("scan", "--transcripts", f"codex={self.codex}", "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        payload = json.loads(out.stdout)
        self.assertEqual(payload["counts"]["privileged_invocations"], 1)

    def test_report_human_output_names_all_five_metrics(self):
        out = self.cli("report", "--transcripts", f"codex={self.codex}",
                       "--root", str(self.tmp / "store"))
        self.assertEqual(out.returncode, 0, out.stderr)
        for metric in ("privileged_invocations", "os_prompts", "coalesced",
                       "retries", "unattributed"):
            self.assertIn(metric, out.stdout)

    def test_audit_never_executes_a_privileged_command(self):
        """The scanner is read-only by construction: it must not spawn anything."""
        src = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("subprocess.run", "subprocess.Popen", "os.system", "os.exec"):
            self.assertNotIn(forbidden, src,
                             f"{forbidden} has no place in a read-only forensics tool")


if __name__ == "__main__":
    unittest.main(verbosity=2)
