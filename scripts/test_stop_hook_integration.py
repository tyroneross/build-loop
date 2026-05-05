#!/usr/bin/env python3
"""End-to-end integration test for the Stop hook.

Synthesizes a small Claude Code transcript JSONL containing decisions of
mixed confidence, then invokes the actual Stop-hook command (extracted
from `hooks/hooks.json`). Verifies:

- The hook runs without exception (exit 0)
- At least one file lands in `.episodic/decisions/_review/` OR
  `.episodic/decisions/` (the live LLM may classify either way)
- The hook tolerates a missing transcript without crashing

Requires a live `ollama` daemon with `qwen3:8b-q4_K_M` pulled. Tests
fail loud if ollama is unreachable — that's a real config issue, not a
test problem (per the user direction for Phase 3 hardening).

Live-LLM caveats:
  - qwen3:8b can take 10-30s per call. A single end-to-end run uses
    ~30-60s of LLM time. We bump unittest timeouts accordingly.
  - The model may pick a `primary_tag` outside our taxonomy. The scan
    script will reject the row through `write_decision.py` and log it;
    the file may still appear in `_review/` (tier-3 path). Either is
    valid pass behavior for "the hook didn't crash and produced output".
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
HOOKS_JSON = REPO / "hooks" / "hooks.json"
SCAN_SCRIPT = HERE / "scan_transcript_for_decisions.py"


def _ollama_ready() -> bool:
    """Verify ollama is up and qwen3:8b-q4_K_M is pulled."""
    if not shutil.which("ollama"):
        return False
    cp = subprocess.run(
        ["ollama", "list"], capture_output=True, text=True, timeout=10
    )
    return cp.returncode == 0 and "qwen3:8b-q4_K_M" in cp.stdout


def _seed_taxonomy() -> str:
    return """---
type: taxonomy
schema_version: 1
---

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

## 6. Source attribution

- `manual`
- `auto-explicit`
- `auto-confirmed`
- `auto-inferred`
- `auto-assumed`
- `migration`
- `orchestrator`
"""


def _seed_transcript_with_explicit_decision() -> list[dict]:
    """Transcript with one clear explicit decision and one weaker signal."""
    return [
        {"type": "user", "message": {"role": "user", "content": "what test framework should I use for the api package?"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "pytest is a strong default"}]}},
        # Explicit decision
        {"type": "user", "message": {"role": "user", "content": "ok, let's use pytest for the api package."}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Got it. Using pytest."}]}},
        # Soft signal
        {"type": "user", "message": {"role": "user", "content": "I generally prefer keeping tooling minimal."}},
    ]


def _extract_scan_command_from_hooks_json() -> str:
    """Pull the scan_transcript command string out of hooks/hooks.json.

    We don't reconstruct the full hook environment; we just verify the
    same command line the hook runs works end-to-end.
    """
    data = json.loads(HOOKS_JSON.read_text())
    for stop_hook in data["hooks"]["Stop"]:
        for h in stop_hook["hooks"]:
            cmd = h.get("command", "")
            if "scan_transcript_for_decisions.py" in cmd:
                return cmd
    raise AssertionError("scan_transcript_for_decisions hook not found in hooks.json")


class StopHookIntegrationTests(unittest.TestCase):
    """Live integration test. Slow (~30-60s)."""

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

        # The hook command expects scripts/ at $CLAUDE_PROJECT_DIR/scripts.
        # Symlink the repo's scripts directory in so the live hook command
        # can resolve scan_transcript_for_decisions.py without a path edit.
        (self.workdir / "scripts").symlink_to(REPO / "scripts")

        self.transcript = self.workdir / "transcript.jsonl"
        with self.transcript.open("w") as f:
            for line in _seed_transcript_with_explicit_decision():
                f.write(json.dumps(line) + "\n")

        # Snapshot production schema row count so we can detect (and
        # clean up) test pollution. The hook command does not pass
        # --no-db, so any explicit/confirmed decision the live LLM
        # extracts will be inserted into build_loop_memory.semantic_facts.
        self._pre_pollution_subjects = self._snapshot_production_subjects()

    def tearDown(self) -> None:
        self._cleanup_test_pollution()
        self.tmp.cleanup()

    def _snapshot_production_subjects(self) -> set[str]:
        """Return the set of (subject, object) tuples currently in the
        production schema. We use this as a baseline so tearDown can
        delete rows that appeared during the test.
        """
        sys.path.insert(0, str(REPO / "scripts"))
        try:
            from db import close_connection, query
            rows = query("SELECT id::text AS id FROM build_loop_memory.semantic_facts")
            return {r["id"] for r in rows}
        except Exception:  # noqa: BLE001
            return set()
        finally:
            try:
                close_connection()
            except Exception:  # noqa: BLE001
                pass

    def _cleanup_test_pollution(self) -> None:
        """Delete any rows that appeared in build_loop_memory.semantic_facts
        between setUp and tearDown.
        """
        try:
            from db import close_connection, execute, query
            close_connection()  # discard any state from inside the hook subprocess
            current = {r["id"] for r in query("SELECT id::text AS id FROM build_loop_memory.semantic_facts")}
            new_ids = current - self._pre_pollution_subjects
            if new_ids:
                # Bulk delete the new rows by id
                execute(
                    "DELETE FROM build_loop_memory.semantic_facts WHERE id::text = ANY(%s)",
                    (list(new_ids),),
                )
        except Exception:  # noqa: BLE001
            pass

    def test_hook_command_runs_end_to_end_with_live_qwen(self) -> None:
        """The exact command from hooks.json runs cleanly and produces files.

        We invoke via /bin/sh to mirror Claude Code's hook execution
        contract. Pass `CLAUDE_PROJECT_DIR` and `CLAUDE_TRANSCRIPT_PATH`
        so the hook resolves paths correctly.
        """
        cmd = _extract_scan_command_from_hooks_json()

        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(self.workdir)
        env["CLAUDE_TRANSCRIPT_PATH"] = str(self.transcript)
        # The hook's prefix `test -d "$CLAUDE_PROJECT_DIR/.episodic"` will
        # pass; rest of the command pipes scan_transcript output through
        # `head -3` and ORs with `true` to swallow failure. We can't easily
        # detect "did the script run" from the hook command line; we
        # validate by checking artifact files exist after the run.

        cp = subprocess.run(
            ["/bin/sh", "-c", cmd],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.workdir),
            timeout=180,  # qwen3:8b cold call can be slow
        )
        # Hook contract: never fail the session.
        self.assertEqual(cp.returncode, 0, msg=f"hook exited nonzero. stdout={cp.stdout!r} stderr={cp.stderr!r}")

        # Confirm the hook actually invoked the scanner: scanner logs go
        # to stdout (head -3 truncates), but artifacts are durable.
        review_dir = self.workdir / ".episodic" / "decisions" / "_review"
        trusted_dir = self.workdir / ".episodic" / "decisions"
        review_files = list(review_dir.glob("*.md"))
        trusted_files = [f for f in trusted_dir.glob("[0-9][0-9][0-9][0-9]-*.md") if f.is_file()]
        total = len(review_files) + len(trusted_files)
        self.assertGreaterEqual(
            total,
            1,
            msg=(
                f"Expected at least 1 captured decision (trusted or review), "
                f"got 0. Hook stdout: {cp.stdout!r}, stderr: {cp.stderr!r}, "
                f"workdir contents: trusted={trusted_files}, review={review_files}"
            ),
        )

    def test_hook_no_op_when_episodic_missing(self) -> None:
        """The hook's `test -d` guard exits 0 cleanly if .episodic is absent."""
        # Move/remove .episodic to trigger the no-op path
        shutil.rmtree(self.workdir / ".episodic")

        cmd = _extract_scan_command_from_hooks_json()
        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(self.workdir)
        env["CLAUDE_TRANSCRIPT_PATH"] = str(self.transcript)

        cp = subprocess.run(
            ["/bin/sh", "-c", cmd],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.workdir),
            timeout=15,
        )
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")

    def test_hook_handles_missing_transcript(self) -> None:
        """Missing transcript path: scanner logs no-op and exits 0."""
        # Don't actually create the transcript; use a path that doesn't exist.
        bogus = self.workdir / "missing.jsonl"

        cmd = _extract_scan_command_from_hooks_json()
        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(self.workdir)
        env["CLAUDE_TRANSCRIPT_PATH"] = str(bogus)

        cp = subprocess.run(
            ["/bin/sh", "-c", cmd],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.workdir),
            timeout=30,
        )
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")
        # No files should be produced
        review = list((self.workdir / ".episodic" / "decisions" / "_review").glob("*.md"))
        self.assertEqual(review, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
