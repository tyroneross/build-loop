#!/usr/bin/env python3
"""Tests for scan_transcript_for_decisions.py against live ollama.

Run: python3 test_scan_transcript_for_decisions.py

Per the Phase 3 hardening direction (2026-05-04), this suite exercises
the LIVE qwen3:8b extraction pipeline rather than the
`--mock-llm-output` convenience flag (which still exists in the script
for developer use). If ollama is unreachable when these tests run, they
fail loudly — that's a real config issue, not a test problem.

Covers:
- Live LLM call: a transcript with an explicit decision yields ≥ 1
  captured artifact (trusted or _review/, depending on how the LLM
  classifies it and whether the primary_tag matches the taxonomy)
- Ollama-unreachable: no-op, exit 0 (script-level resilience)
- Empty transcript: exit 0
- Malformed transcript line: tolerated; exit 0
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
sys.path.insert(0, str(HERE))
SCRIPT = HERE / "scan_transcript_for_decisions.py"

from _test_helpers import MemIsolationMixin  # noqa: E402


def _ollama_ready() -> bool:
    if not shutil.which("ollama"):
        return False
    cp = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
    return cp.returncode == 0 and "qwen3:8b-q4_K_M" in cp.stdout


def run(args: list[str], cwd: Path | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
    )


def _seed_taxonomy() -> str:
    return """---
type: taxonomy
schema_version: 1
---

# Vocab

## 1. Decision tags

- `architecture`
- `data`
- `ui`
- `infra`
- `tooling`
- `process`
- `security`
- `performance`
- `testing`

## 3. Confidence levels

`assumed < inferred < confirmed < explicit`

## 6. Source attribution

- `manual`
- `auto-explicit`
- `auto-confirmed`
- `auto-inferred`
- `auto-assumed`
- `migration`
- `orchestrator`
"""


def _seed_transcript(path: Path) -> None:
    """Write a transcript with one clear explicit decision so the LLM
    has a strong signal to extract.
    """
    lines = [
        {"type": "user", "message": {"role": "user", "content": "what test framework should I use for the api package?"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "pytest is a strong default"}]}},
        {"type": "user", "message": {"role": "user", "content": "ok, let's use pytest for the api package."}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Got it. Using pytest."}]}},
        {"type": "user", "message": {"role": "user", "content": "I generally prefer keeping tooling minimal."}},
    ]
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")


class ScanTranscriptLiveTests(MemIsolationMixin, unittest.TestCase):
    """Live-ollama tests. Slow (~30-60s per LLM-touching test)."""

    @classmethod
    def setUpClass(cls) -> None:
        if not _ollama_ready():
            raise RuntimeError(
                "ollama daemon must be running with qwen3:8b-q4_K_M pulled. "
                "Run `ollama pull qwen3:8b-q4_K_M` and ensure `ollama serve` is up."
            )

    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        (self.workdir / ".semantic").mkdir(parents=True)
        (self.workdir / ".episodic" / "decisions" / "_history").mkdir(parents=True)
        (self.workdir / ".episodic" / "decisions" / "_review").mkdir(parents=True)
        (self.workdir / ".semantic" / "TAXONOMY.md").write_text(_seed_taxonomy())

        self.transcript = self.workdir / "transcript.jsonl"
        _seed_transcript(self.transcript)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        super().tearDown()

    def test_live_extraction_produces_at_least_one_artifact(self) -> None:
        """End-to-end: live qwen3:8b extracts ≥1 decision from a transcript."""
        env = os.environ.copy()
        env["AGENT_MEMORY_ROOT"] = self._memroot.name
        cp = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--workdir", str(self.workdir),
                "--transcript", str(self.transcript),
                "--no-db",  # avoid touching production schema during tests
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,  # qwen3:8b cold start can be slow
        )
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")

        # Phase C: decision files land in <AGENT_MEMORY_ROOT>/decisions/<project>/.
        decisions_root = Path(self._memroot.name) / "decisions"
        review_files = list(decisions_root.rglob("_review/*.md")) if decisions_root.exists() else []
        trusted_files = [
            f for f in (decisions_root.rglob("[0-9][0-9][0-9][0-9]-*.md") if decisions_root.exists() else [])
            if f.is_file() and "_review" not in f.parts and "_history" not in f.parts
        ]

        # The live LLM may classify the explicit decision as either:
        #   - "explicit" with primary_tag in our taxonomy → trusted (1 file)
        #   - "explicit" with primary_tag outside taxonomy → write_decision.py rejects, no file
        #   - "inferred" / "assumed" → quarantined to _review/
        # All three variations satisfy "the pipeline ran end-to-end and produced output";
        # we just require ≥1 artifact OR a clean log line indicating the LLM returned items.
        total_files = len(review_files) + len(trusted_files)

        # If no files landed, log output should mention LLM activity (returned candidates)
        ran_through_llm = (
            "scan: LLM returned" in cp.stderr
            or total_files >= 1
        )
        self.assertTrue(
            ran_through_llm,
            msg=(
                f"Live LLM run produced no files and no LLM-return log line. "
                f"stderr: {cp.stderr!r}, trusted={trusted_files}, review={review_files}"
            ),
        )
        # Stronger assertion: explicit decision in transcript should produce ≥1 captured row
        self.assertGreaterEqual(
            total_files,
            1,
            msg=(
                f"Expected ≥1 captured decision (trusted or _review). "
                f"stderr: {cp.stderr!r}, trusted={trusted_files}, review={review_files}"
            ),
        )

    def test_missing_transcript_exits_zero(self) -> None:
        """Hook-friendly: missing transcript → no-op, exit 0."""
        cp = run(
            [
                "--workdir", str(self.workdir),
                "--transcript", str(self.workdir / "nonexistent.jsonl"),
                "--no-db",
            ],
            timeout=15,
        )
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")
        review_dir = self.workdir / ".episodic" / "decisions" / "_review"
        self.assertEqual(len(list(review_dir.glob("*.md"))), 0)

    def test_ollama_unreachable_no_op(self) -> None:
        """Force-ollama-down path: clean exit, no artifacts.

        We replicate ollama-unreachable by pointing PATH at a directory
        that doesn't contain the ollama binary. The HTTP-fallback in
        ollama_embed only matters for write_decision.py paths; the scan
        script's primary path is `ollama run` via subprocess and uses
        shutil.which, which respects $PATH.
        """
        env = os.environ.copy()
        env["PATH"] = "/nonexistent-bin"
        cp = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--workdir", str(self.workdir),
                "--transcript", str(self.transcript),
                "--no-db",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")
        review_dir = self.workdir / ".episodic" / "decisions" / "_review"
        self.assertEqual(len(list(review_dir.glob("*.md"))), 0)

    def test_empty_transcript(self) -> None:
        """Empty transcript: exits 0 without a wasted LLM call."""
        empty = self.workdir / "empty.jsonl"
        empty.write_text("")
        cp = run(
            [
                "--workdir", str(self.workdir),
                "--transcript", str(empty),
                "--no-db",
            ],
            timeout=15,
        )
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")
        # Empty transcript path: scanner logs "nothing to do" without invoking LLM
        self.assertIn("nothing to do", cp.stderr.lower() + cp.stderr)

    def test_malformed_transcript_line_tolerated(self) -> None:
        """Malformed lines are skipped; remaining lines drive a clean LLM call."""
        bad = self.workdir / "bad.jsonl"
        bad.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}})
            + "\n"
            + "{not-json\n"
            + json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "ok"}})
            + "\n"
        )
        cp = run(
            [
                "--workdir", str(self.workdir),
                "--transcript", str(bad),
                "--no-db",
            ],
            timeout=180,
        )
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")


class HardeningTests(MemIsolationMixin, unittest.TestCase):
    """Offline tests for Stop-hook hardening (budget, log file, no-capture, lock).

    These tests do NOT require ollama; they use --mock-llm-output to drive
    the script with canned LLM responses, or rely on early-exit paths
    (no-capture, lock contention, missing transcript).
    """

    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        (self.workdir / ".semantic").mkdir(parents=True)
        (self.workdir / ".episodic" / "decisions" / "_history").mkdir(parents=True)
        (self.workdir / ".episodic" / "decisions" / "_review").mkdir(parents=True)
        (self.workdir / ".semantic" / "TAXONOMY.md").write_text(_seed_taxonomy())
        self.transcript = self.workdir / "transcript.jsonl"
        _seed_transcript(self.transcript)
        # Per-test log file lives inside tmpdir so we don't pollute
        # ~/.local/state/build-loop/scan.log.
        self.log_file = self.workdir / "scan.log"
        # Per-test lock file so parallel tests don't collide on /tmp/build-loop-scan.lock.
        self.lock_file = self.workdir / "scan.lock"
        # Mock LLM output: one tier-3 (inferred) item that hits write_review.
        self.mock_path = self.workdir / "mock-llm.json"
        self.mock_path.write_text(
            json.dumps([
                {
                    "decision": "Use pytest for the api package",
                    "evidence": "ok, let's use pytest for the api package.",
                    "confidence": "inferred",
                    "primary_tag": "tooling",
                    "entity": "api",
                    "tags": ["tooling", "testing"],
                    "context": "User confirmed pytest as the test framework",
                    "alternatives": "",
                    "rationale": "Topic-coherent confirmation",
                }
            ])
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()
        super().tearDown()

    def _run(self, extra: list[str], timeout: int = 30, env: dict | None = None) -> subprocess.CompletedProcess:
        args = [
            sys.executable, str(SCRIPT),
            "--workdir", str(self.workdir),
            "--transcript", str(self.transcript),
            "--no-db",
            "--log-file", str(self.log_file),
            "--lock-file", str(self.lock_file),
        ] + extra
        merged_env = os.environ.copy()
        # Phase C: inject isolated AGENT_MEMORY_ROOT so writes go to tmpdir.
        merged_env["AGENT_MEMORY_ROOT"] = self._memroot.name
        if env:
            merged_env.update(env)
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=merged_env)

    def test_log_file_written(self) -> None:
        """--log-file writes timestamped lines; stdout stays empty on success."""
        cp = self._run(["--mock-llm-output", str(self.mock_path)])
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")
        self.assertTrue(self.log_file.exists(), msg="log file not created")
        contents = self.log_file.read_text()
        self.assertIn("[scan]", contents, msg=f"log file missing tag: {contents!r}")
        # Each log line must start with an ISO-8601-ish timestamp (Z suffix).
        first_line = contents.strip().splitlines()[0]
        self.assertRegex(first_line, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z \[scan\]")
        # Stdout is empty on success (script does not print to stdout).
        self.assertEqual(cp.stdout, "", msg=f"unexpected stdout: {cp.stdout!r}")

    def test_no_capture_flag_skips_immediately(self) -> None:
        """`.episodic/.no-capture` causes immediate clean exit before any work."""
        (self.workdir / ".episodic" / ".no-capture").touch()
        cp = self._run(["--mock-llm-output", str(self.mock_path)])
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")
        contents = self.log_file.read_text() if self.log_file.exists() else ""
        self.assertIn("no-capture", contents.lower(), msg=f"expected no-capture log: {contents!r}")
        # No artifacts written.
        review_dir = self.workdir / ".episodic" / "decisions" / "_review"
        self.assertEqual(len(list(review_dir.glob("*.md"))), 0)

    def test_budget_exceeded_exits_clean(self) -> None:
        """SCAN_BUDGET_S=0 forces budget-exceeded path; script exits 0 with log message."""
        cp = self._run(
            ["--mock-llm-output", str(self.mock_path)],
            env={"SCAN_BUDGET_S": "0"},
        )
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")
        contents = self.log_file.read_text() if self.log_file.exists() else ""
        self.assertIn("budget exceeded", contents.lower(), msg=f"expected budget log: {contents!r}")

    def test_log_rotation_when_oversize(self) -> None:
        """Log file >10MB gets truncated to last 1MB on next write."""
        # Pre-fill with 12MB of garbage to trigger rotation
        oversized = b"X" * (12 * 1024 * 1024)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.write_bytes(oversized)
        # Use --no-capture path so we don't depend on ollama; it still calls log()
        (self.workdir / ".episodic" / ".no-capture").touch()
        cp = self._run([])
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")
        size_after = self.log_file.stat().st_size
        # After rotation, file should be ≤ ~1MB + a few new lines (well under 2MB)
        self.assertLess(size_after, 2 * 1024 * 1024, msg=f"rotation didn't shrink log; size={size_after}")
        # And the rotation marker line should be present.
        contents = self.log_file.read_text(errors="replace")
        self.assertIn("rotated", contents.lower())

    def test_lock_contention_skips_cleanly(self) -> None:
        """Second concurrent invocation skips with non-error log when lock held."""
        import fcntl
        # Hold the lock from this process; the subprocess should fail to acquire and exit 0.
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.lock_file), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Use SCAN_BUDGET_S=0 as a safety net: even if the lock check
            # silently regressed and let the subprocess through, the
            # zero-budget would force an immediate bail before any LLM call.
            # The test still asserts "another scan" log line is the actual
            # exit reason.
            cp = self._run([], env={"SCAN_BUDGET_S": "0"})
            self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")
            contents = self.log_file.read_text() if self.log_file.exists() else ""
            self.assertIn("another scan", contents.lower(), msg=f"expected lock-skip log: {contents!r}")
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
