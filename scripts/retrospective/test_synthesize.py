# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for scripts/retrospective/synthesize (F1+F2+F3)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from retrospective.synthesize import run as synth_run  # noqa: E402


def _build_workdir(tmp: Path, *, with_repeats: bool = True) -> tuple[Path, Path]:
    """Create a fixture build-loop project workdir + a fixture transcript JSONL."""
    workdir = tmp / "myproj"
    (workdir / ".build-loop").mkdir(parents=True)
    (workdir / ".build-loop" / "intent.md").write_text(
        "# Intent\n\n## Restated intent\nDo the build well.\n", encoding="utf-8",
    )
    (workdir / ".build-loop" / "plan.md").write_text(
        "# Plan — test build\n", encoding="utf-8",
    )
    (workdir / ".build-loop" / "state.json").write_text(json.dumps({
        "execution": {"build_loop_id": "test-run-1"},
        "runs": [{"outcome": "pass", "run_id": "test-run-1"}],
    }), encoding="utf-8")
    # Fixture transcript
    tx = tmp / "transcript.jsonl"
    turns = []
    if with_repeats:
        turns += [
            "please commit small chunks",
            "intermediate question",
            "please commit small chunks",
        ]
    else:
        turns += ["unique-1", "unique-2"]
    lines = []
    for t in turns:
        lines.append(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": t},
        }))
    tx.write_text("\n".join(lines), encoding="utf-8")
    return workdir, tx


class SynthesizeRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_dir = Path(self.tmp.name)

    # ----- F1 — all sections present in active file -----

    def test_F1_active_file_contains_all_nine_sections(self) -> None:
        workdir, tx = _build_workdir(self.tmp_dir, with_repeats=False)
        r = synth_run(workdir, run_id="run-F1", transcript=tx)
        self.assertEqual(r["status"], "ok")
        body = Path(r["active_path"]).read_text(encoding="utf-8")
        for title in (
            "Lessons learned",
            "Key takeaways",
            "Recommendations",
            "What could be done better",
            "What went well",
            "What went well by accident",
            "What should be enforced",
            "User prompts this thread",
            "Issues (with causal tree)",
        ):
            self.assertIn(f"## {title}", body, f"missing section: {title}")

    def test_F1_summary_file_has_max_five_non_blank_lines(self) -> None:
        workdir, tx = _build_workdir(self.tmp_dir, with_repeats=False)
        r = synth_run(workdir, run_id="run-F1b", transcript=tx)
        s = Path(r["summary_path"]).read_text(encoding="utf-8")
        non_blank = [ln for ln in s.splitlines() if ln.strip()]
        self.assertLessEqual(len(non_blank), 5)

    # ----- F2 — non-gating behavior is a contract of the dispatch site,
    #         but synthesize.run() itself must never raise. -----

    def test_F2_never_raises_on_bad_workdir(self) -> None:
        # Bad workdir → degraded, not exception
        r = synth_run(Path("/nonexistent/zzz/proj"), run_id="r")
        self.assertIn(r["status"], ("ok", "degraded", "skipped"))

    def test_F2_returns_quickly_for_empty_transcript(self) -> None:
        # Smoke: completes without raising.
        workdir = self.tmp_dir / "empty"
        (workdir / ".build-loop").mkdir(parents=True)
        r = synth_run(workdir, run_id="empty-r")
        self.assertIn(r["status"], ("ok", "degraded"))

    # ----- F3 — prompted-≥2× → section 8 + enforce-candidate file -----

    def test_F3_repeated_prompt_creates_enforce_candidate_file(self) -> None:
        workdir, tx = _build_workdir(self.tmp_dir, with_repeats=True)
        r = synth_run(workdir, run_id="run-F3", transcript=tx)
        self.assertEqual(r["status"], "ok")
        # Section 8 in the active file shows the repeated cluster
        body = Path(r["active_path"]).read_text(encoding="utf-8")
        self.assertIn("Prompted ≥2×", body)
        self.assertIn("please commit small chunks", body)
        # At least one enforce-candidate file was written
        self.assertGreater(len(r["enforce_candidates"]), 0)
        for p in r["enforce_candidates"]:
            ec_body = Path(p).read_text(encoding="utf-8")
            self.assertIn("Adopt as default", ec_body)

    def test_gap2_judge_decisions_file_surfaces_with_hook_only_state(self) -> None:
        workdir = self.tmp_dir / "gap2"
        build_loop = workdir / ".build-loop"
        build_loop.mkdir(parents=True)
        (build_loop / "state.json").write_text(json.dumps({
            "execution": {"build_loop_id": "gap2-run"},
            "runs": [{
                "run_id": "hook-only",
                "outcome": "pass",
                "judge_decisions": [{
                    "judge_id": "independent-auditor-hook",
                    "checkpoint_id": "",
                    "verdict": "suggest",
                    "variances": [],
                }],
                "lessons": [],
            }],
        }), encoding="utf-8")
        (build_loop / "judge-decisions.json").write_text(json.dumps({
            "decisions": [{
                "judge_id": "independent-auditor",
                "checkpoint_id": "Review-A",
                "verdict": "nay",
                "variances": [{
                    "id": "V-1",
                    "severity": "HIGH",
                    "why_it_matters": "Cookie secret leaked into logs",
                }],
                "meta_guidance": ["Persist auditor findings into retrospectives"],
            }],
        }), encoding="utf-8")

        r = synth_run(
            workdir,
            run_id="gap2-run",
            transcript=None,
            memory_root=self.tmp_dir / "no-memory",
        )

        self.assertEqual(r["status"], "ok")
        body = Path(r["active_path"]).read_text(encoding="utf-8")
        self.assertIn("Cookie secret leaked into logs", body)
        self.assertIn("independent-auditor: HIGH: Cookie secret leaked into logs", body)
        self.assertIn("Enforce gate: Review-A", body)
        self.assertNotIn("_No issues surfaced this run._", body)
        self.assertGreater(len(r["enforce_candidates"]), 0)

    # ----- Durable promotion behavior -----

    def test_durable_skipped_when_memory_root_missing(self) -> None:
        workdir, tx = _build_workdir(self.tmp_dir, with_repeats=False)
        # Point memory_root somewhere absent
        r = synth_run(workdir, run_id="r-d", transcript=tx,
                      memory_root=self.tmp_dir / "no-memory-here")
        self.assertIsNone(r["durable_path"])

    def test_durable_writes_when_memory_root_present(self) -> None:
        workdir, tx = _build_workdir(self.tmp_dir, with_repeats=False)
        memroot = self.tmp_dir / "blm"
        memroot.mkdir()
        r = synth_run(workdir, run_id="r-d2", transcript=tx, memory_root=memroot)
        self.assertIsNotNone(r["durable_path"])
        self.assertTrue(Path(r["durable_path"]).exists())


class CLITests(unittest.TestCase):
    """Smoke-test the CLI returns 0 and emits parseable JSON."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_dir = Path(self.tmp.name)

    def test_cli_json_output(self) -> None:
        import subprocess
        workdir, tx = _build_workdir(self.tmp_dir, with_repeats=True)
        repo_root = HERE.parent.parent  # .../build-loop
        scripts_dir = repo_root / "scripts"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(scripts_dir)
        out = subprocess.run(
            [sys.executable, "-m", "retrospective",
             "--workdir", str(workdir),
             "--run-id", "cli-r",
             "--transcript", str(tx),
             "--memory-root", str(self.tmp_dir / "no-memory"),
             "--json"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        self.assertEqual(data["status"], "ok")
        self.assertTrue(Path(data["active_path"]).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
