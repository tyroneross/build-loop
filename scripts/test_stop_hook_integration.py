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
sys.path.insert(0, str(HERE))
REPO = HERE.parent
HOOKS_JSON = REPO / "hooks" / "hooks.json"
SCAN_SCRIPT = HERE / "scan_transcript_for_decisions.py"

from _test_helpers import MemIsolationMixin  # noqa: E402


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


class StopHookIntegrationTests(MemIsolationMixin, unittest.TestCase):
    """Live integration test. Slow (~30-60s)."""

    @classmethod
    def setUpClass(cls) -> None:
        if not _ollama_ready():
            raise RuntimeError(
                "ollama daemon must be running with qwen3:8b-q4_K_M pulled. "
                "Run `ollama pull qwen3:8b-q4_K_M` and ensure `ollama serve` is up."
            )

    def setUp(self) -> None:
        # MemIsolationMixin.setUp isolates AGENT_MEMORY_ROOT to a tmpdir.
        # Call super() BEFORE setting up self.workdir since _events_path()
        # requires self.workdir.
        super().setUp()
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
        # The hook now backgrounds the scan via `nohup ... &` — wait for any
        # in-flight bg process holding the global lock to release before
        # we delete the workdir, otherwise the bg process tries to read
        # --transcript from a deleted path and fails silently.
        self._wait_for_bg_scan_to_finish(timeout_s=200)
        self._cleanup_test_pollution()
        self.tmp.cleanup()
        super().tearDown()

    def _wait_for_bg_scan_to_finish(self, timeout_s: float = 200.0) -> None:
        """Block until /tmp/build-loop-scan.lock is no longer held, or timeout."""
        import fcntl
        import time

        lock_path = Path("/tmp/build-loop-scan.lock")
        if not lock_path.exists():
            return
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                fd = os.open(str(lock_path), os.O_RDWR)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    return  # lock free, no bg scan running
                finally:
                    os.close(fd)
            except BlockingIOError:
                time.sleep(0.5)
            except OSError:
                return  # lock file gone or unreadable

    def _snapshot_production_subjects(self) -> dict[str, set[str]]:
        """Return baseline ids per production-schema name (Phase B may write
        to both `personal_memory` and `build_loop_memory` when dual-write is
        on; default writes go to `personal_memory`).
        """
        sys.path.insert(0, str(REPO / "scripts"))
        out: dict[str, set[str]] = {}
        try:
            from _paths import default_schema, legacy_schema  # type: ignore
            schemas = {default_schema(), legacy_schema()}
            from db import close_connection, query
            for s in schemas:
                try:
                    rows = query(f"SELECT id::text AS id FROM {s}.semantic_facts")
                    out[s] = {r["id"] for r in rows}
                except Exception:  # noqa: BLE001
                    out[s] = set()
            return out
        except Exception:  # noqa: BLE001
            return {}
        finally:
            try:
                close_connection()
            except Exception:  # noqa: BLE001
                pass

    def _cleanup_test_pollution(self) -> None:
        """Delete any rows that appeared in either tracked schema between
        setUp and tearDown.
        """
        try:
            from db import close_connection, execute, query
            close_connection()  # discard any state from inside the hook subprocess
            for schema, baseline in self._pre_pollution_subjects.items():
                try:
                    current = {r["id"] for r in query(
                        f"SELECT id::text AS id FROM {schema}.semantic_facts"
                    )}
                except Exception:  # noqa: BLE001
                    continue
                new_ids = current - baseline
                if new_ids:
                    execute(
                        f"DELETE FROM {schema}.semantic_facts WHERE id::text = ANY(%s)",
                        (list(new_ids),),
                    )
        except Exception:  # noqa: BLE001
            pass

    def test_hook_command_runs_end_to_end_with_live_qwen(self) -> None:
        """The exact command from hooks.json runs cleanly and produces files.

        The hook now backgrounds the scan via ``nohup ... &`` so the hook
        itself returns in <500ms; the actual scan completes asynchronously.
        We poll the artifact dirs until decisions appear OR a timeout, then
        assert the captured count.
        """
        import time

        cmd = _extract_scan_command_from_hooks_json()

        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(self.workdir)
        env["CLAUDE_TRANSCRIPT_PATH"] = str(self.transcript)
        # Phase C: pass the isolated AGENT_MEMORY_ROOT so the backgrounded
        # scan (and any write_decision.py it spawns) writes to the tmpdir,
        # not to the real ~/dev/git-folder/build-loop-memory store.
        env["AGENT_MEMORY_ROOT"] = self._memroot.name

        cp = subprocess.run(
            ["/bin/sh", "-c", cmd],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.workdir),
            timeout=10,  # hook itself returns fast; bg process polled below
        )
        # Hook contract: never fail the session.
        self.assertEqual(cp.returncode, 0, msg=f"hook exited nonzero. stdout={cp.stdout!r} stderr={cp.stderr!r}")
        # Backgrounding contract: terminal stays clean.
        self.assertEqual(cp.stdout, "", msg=f"unexpected stdout: {cp.stdout!r}")
        self.assertEqual(cp.stderr, "", msg=f"unexpected stderr: {cp.stderr!r}")

        # Phase C: decision files land in <AGENT_MEMORY_ROOT>/decisions/<project>/.
        # We don't know the project tag (derived from workdir basename), so
        # scan all per-project subdirs in the isolated memroot.
        decisions_root = Path(self._memroot.name) / "decisions"
        deadline = time.monotonic() + 180
        review_files: list = []
        trusted_files: list = []
        while time.monotonic() < deadline:
            review_files = list(decisions_root.rglob("_review/*.md"))
            trusted_files = [
                f for f in decisions_root.rglob("[0-9][0-9][0-9][0-9]-*.md")
                if f.is_file() and "_review" not in f.parts and "_history" not in f.parts
            ]
            if (len(review_files) + len(trusted_files)) >= 1:
                break
            time.sleep(2)

        total = len(review_files) + len(trusted_files)
        self.assertGreaterEqual(
            total,
            1,
            msg=(
                f"Expected at least 1 captured decision (trusted or review) within 180s, "
                f"got 0. memroot contents: trusted={trusted_files}, review={review_files}"
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
        env["AGENT_MEMORY_ROOT"] = self._memroot.name

        cp = subprocess.run(
            ["/bin/sh", "-c", cmd],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.workdir),
            timeout=30,
        )
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")
        # No files should be produced in the isolated memroot.
        decisions_root = Path(self._memroot.name) / "decisions"
        review = list(decisions_root.rglob("_review/*.md")) if decisions_root.exists() else []
        self.assertEqual(review, [])


class StopHookHardeningTests(unittest.TestCase):
    """Offline hardening tests: output suppression, lock contention.

    These do NOT require ollama. They use the `.no-capture` flag to make
    the scanner exit before any LLM call, then verify the hook command
    shape suppresses output and respects the lock.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        (self.workdir / ".semantic").mkdir(parents=True)
        (self.workdir / ".episodic" / "decisions" / "_history").mkdir(parents=True)
        (self.workdir / ".episodic" / "decisions" / "_review").mkdir(parents=True)
        (self.workdir / ".semantic" / "TAXONOMY.md").write_text(_seed_taxonomy())
        (self.workdir / "scripts").symlink_to(REPO / "scripts")
        # Force the scanner to early-exit so we don't depend on ollama.
        (self.workdir / ".episodic" / ".no-capture").touch()

        self.transcript = self.workdir / "transcript.jsonl"
        with self.transcript.open("w") as f:
            for line in _seed_transcript_with_explicit_decision():
                f.write(json.dumps(line) + "\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_hook_command_suppresses_output(self) -> None:
        """The exact hook command produces no stdout/stderr to the calling shell."""
        cmd = _extract_scan_command_from_hooks_json()
        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(self.workdir)
        env["CLAUDE_TRANSCRIPT_PATH"] = str(self.transcript)
        # Re-route HOME so the default --log-file path doesn't pollute the user's real state dir.
        env["HOME"] = str(self.workdir / "fakehome")
        env["XDG_STATE_HOME"] = str(self.workdir / "fakehome" / ".local" / "state")

        cp = subprocess.run(
            ["/bin/sh", "-c", cmd],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.workdir),
            timeout=15,
        )
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr!r}")
        # Critical: terminal stays clean.
        self.assertEqual(cp.stdout, "", msg=f"unexpected stdout: {cp.stdout!r}")
        self.assertEqual(cp.stderr, "", msg=f"unexpected stderr: {cp.stderr!r}")

    def test_hook_lock_contention_skips_silently(self) -> None:
        """When another scan holds the lock, the hook exits 0 with no terminal output."""
        import fcntl
        # Remove the .no-capture flag so the script reaches the lock-acquire path.
        no_cap = self.workdir / ".episodic" / ".no-capture"
        if no_cap.exists():
            no_cap.unlink()

        # The hook command uses the script's default lock at /tmp/build-loop-scan.lock.
        # Hold it from this process and verify the subprocess exits cleanly without LLM.
        lock_path = Path("/tmp/build-loop-scan.lock")
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            cmd = _extract_scan_command_from_hooks_json()
            env = os.environ.copy()
            env["CLAUDE_PROJECT_DIR"] = str(self.workdir)
            env["CLAUDE_TRANSCRIPT_PATH"] = str(self.transcript)
            env["HOME"] = str(self.workdir / "fakehome")
            env["XDG_STATE_HOME"] = str(self.workdir / "fakehome" / ".local" / "state")
            # Belt-and-braces: zero budget so any regression in lock handling
            # would still cause a fast bail before the LLM is consulted.
            env["SCAN_BUDGET_S"] = "0"

            cp = subprocess.run(
                ["/bin/sh", "-c", cmd],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(self.workdir),
                timeout=15,
            )
            self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr!r}")
            self.assertEqual(cp.stdout, "")
            self.assertEqual(cp.stderr, "")

            # The lock-skip should be visible in the log file.
            log_file = Path(env["XDG_STATE_HOME"]) / "build-loop" / "scan.log"
            self.assertTrue(log_file.exists(), msg="log file not created")
            contents = log_file.read_text()
            self.assertIn("another scan", contents.lower(), msg=f"log contents: {contents!r}")
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)

    def test_hook_returns_immediately_via_backgrounding(self) -> None:
        """The hook command MUST return in <500ms regardless of scan duration.

        Per user direction: hooks must work in background and not interrupt
        normal Claude Code session-end timing. The hook command uses
        ``nohup ... &`` so the scan runs detached; the hook itself
        exits 0 immediately.

        Important: the hook command in hooks.json doesn't pass
        ``--mock-llm-output``, so its backgrounded scan would call live
        qwen3:8b (cold start 30-120s) and hold the global lock for that
        long, polluting subsequent tests. We test by directly invoking the
        scanner script with ``--mock-llm-output`` (empty array) wrapped in
        ``nohup ... & exit 0`` to mirror the hook's backgrounding shape
        without the live LLM work. This isolates the latency concern
        (does ``& exit 0`` return fast?) from the LLM-call concern
        (covered by the live-qwen integration test).
        """
        import time

        # Mock LLM output: empty array → script exits cleanly, fast.
        mock_path = self.workdir / "mock-llm.json"
        mock_path.write_text("[]")

        scanner = REPO / "scripts" / "scan_transcript_for_decisions.py"
        log_dir = self.workdir / "fakehome" / ".local" / "state" / "build-loop"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "scan.log"
        # Use a per-test lock to avoid contention with live-qwen test.
        lock_file = self.workdir / "test.lock"

        # Mirror hooks.json's backgrounding pattern exactly.
        cmd = (
            f'nohup python3 "{scanner}" '
            f'--workdir "{self.workdir}" '
            f'--transcript "{self.transcript}" '
            f'--mock-llm-output "{mock_path}" '
            f'--log-file "{log_file}" '
            f'--lock-file "{lock_file}" '
            f'</dev/null >/dev/null 2>&1 & exit 0'
        )

        t0 = time.perf_counter()
        cp = subprocess.run(
            ["/bin/sh", "-c", cmd],
            capture_output=True,
            text=True,
            cwd=str(self.workdir),
            timeout=5,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr!r}")
        self.assertEqual(cp.stdout, "", msg=f"unexpected stdout: {cp.stdout!r}")
        self.assertEqual(cp.stderr, "", msg=f"unexpected stderr: {cp.stderr!r}")
        self.assertLess(
            elapsed_ms,
            500,
            msg=f"hook took {elapsed_ms:.0f}ms; backgrounding regressed (expected <500ms)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
