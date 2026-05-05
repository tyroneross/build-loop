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
SCRIPT = HERE / "scan_transcript_for_decisions.py"


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


class ScanTranscriptLiveTests(unittest.TestCase):
    """Live-ollama tests. Slow (~30-60s per LLM-touching test)."""

    @classmethod
    def setUpClass(cls) -> None:
        if not _ollama_ready():
            raise RuntimeError(
                "ollama daemon must be running with qwen3:8b-q4_K_M pulled. "
                "Run `ollama pull qwen3:8b-q4_K_M` and ensure `ollama serve` is up."
            )

    def setUp(self) -> None:
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

    def test_live_extraction_produces_at_least_one_artifact(self) -> None:
        """End-to-end: live qwen3:8b extracts ≥1 decision from a transcript."""
        cp = run(
            [
                "--workdir", str(self.workdir),
                "--transcript", str(self.transcript),
                "--no-db",  # avoid touching production schema during tests
            ],
            timeout=180,  # qwen3:8b cold start can be slow
        )
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")

        review_dir = self.workdir / ".episodic" / "decisions" / "_review"
        trusted_dir = self.workdir / ".episodic" / "decisions"
        review_files = list(review_dir.glob("*.md"))
        trusted_files = [f for f in trusted_dir.glob("[0-9][0-9][0-9][0-9]-*.md") if f.is_file()]

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
