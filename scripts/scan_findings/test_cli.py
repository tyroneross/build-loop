# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for the findings-sweep CLI — the exact path the Stop hook
invokes. Covers the acceptance contract (auto-create via backlog.py, no manual
invocation/selection), idempotency, routing, fail-open, and the .no-capture
opt-out."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from scan_findings import __main__ as cli  # noqa: E402


def _transcript(tmp_path: Path) -> Path:
    """A realistic transcript: a dispatched audit returns findings as a
    tool_result, plus an isMeta record that must be ignored."""
    t = tmp_path / "transcript.jsonl"
    t.write_text("\n".join([
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Dispatching the auditor."},
            {"type": "tool_use", "name": "Task", "input": {"subagent_type": "security-reviewer"}},
        ]}}),
        json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": [{"type": "text", "text": "\n".join([
                "Audit complete.",
                "HIGH: verify-install.yml interpolates dispatch input into shell — command injection",
                "LOW: missing newline at EOF in README",
                "I suspect a race condition in the worker pool",  # no severity -> review
            ])}]},
        ]}}),
        json.dumps({"isMeta": True, "type": "user",
                    "message": {"role": "user", "content": "git diff injected by a hook"}}),
    ]), encoding="utf-8")
    return t


def _run(workdir: Path, transcript: Path):
    rc = cli.main([
        "--workdir", str(workdir),
        "--transcript", str(transcript),
        "--today", "2026-06-27",
        "--print-json",
        # unique lock file per test so concurrent runs never collide
        "--lock-file", str(workdir / ".findings.lock"),
    ])
    return rc


def _items(workdir: Path) -> list[Path]:
    d = workdir / ".build-loop" / "backlog" / "items"
    return sorted(d.glob("*.md")) if d.is_dir() else []


def _proposals(workdir: Path) -> list[Path]:
    d = workdir / ".build-loop" / "proposals"
    return sorted(d.glob("auto-finding-*.md")) if d.is_dir() else []


def test_acceptance_finding_auto_creates_backlog_item(tmp_path, capsys):
    transcript = _transcript(tmp_path)
    rc = _run(tmp_path, transcript)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # HIGH + LOW -> backlog; race condition (no severity) -> review
    assert len(out["backlog"]) == 2
    assert len(out["review"]) == 1

    items = _items(tmp_path)
    assert len(items) == 2
    high = next(p for p in items if "command injection" in p.read_text())
    body = high.read_text()
    assert "priority: P1" in body                       # HIGH -> P1
    assert "provenance" in body and "auto-finding-sweep:security-reviewer" in body
    assert "finding-hash:" in body                       # dedup key persisted
    assert len(_proposals(tmp_path)) == 1


def test_rerun_is_idempotent_no_duplicates(tmp_path, capsys):
    transcript = _transcript(tmp_path)
    _run(tmp_path, transcript)
    capsys.readouterr()
    items_after_first = len(_items(tmp_path))
    proposals_after_first = len(_proposals(tmp_path))

    rc = _run(tmp_path, transcript)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["backlog"] == [] and out["review"] == []
    assert out["skipped_dup"] == 3
    assert len(_items(tmp_path)) == items_after_first         # no new items
    assert len(_proposals(tmp_path)) == proposals_after_first  # no new proposals


def test_capture_goes_through_backlog_py_cli(tmp_path):
    """The item is a real backlog.py item: backlog.py list sees it."""
    _run(tmp_path, _transcript(tmp_path))
    result = subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "backlog.py"), "list",
         "--repo", str(tmp_path), "--json"],
        capture_output=True, text=True, check=True,
    )
    listed = json.loads(result.stdout)
    titles = " ".join(str(r.get("title")) for r in listed["items"])
    assert "command injection" in titles
    assert all(r["type"] == "fix" and r["area"] == "audit" for r in listed["items"])


def test_no_capture_opt_out(tmp_path, capsys):
    transcript = _transcript(tmp_path)
    (tmp_path / ".build-loop").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".build-loop" / ".no-capture").touch()
    rc = _run(tmp_path, transcript)
    assert rc == 0
    assert _items(tmp_path) == []     # nothing written
    # opt-out exits before printing the JSON summary
    assert capsys.readouterr().out.strip() == ""


def test_fail_open_on_missing_transcript(tmp_path):
    rc = cli.main([
        "--workdir", str(tmp_path),
        "--transcript", str(tmp_path / "does-not-exist.jsonl"),
        "--strict",
        "--lock-file", str(tmp_path / ".findings.lock"),
    ])
    assert rc == 0
    assert _items(tmp_path) == []


def test_no_findings_in_transcript_is_clean(tmp_path, capsys):
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant",
                 "content": [{"type": "text", "text": "All good, nothing to flag."}]}}) + "\n",
                 encoding="utf-8")
    rc = _run(tmp_path, t)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["candidates"] == [] and out["backlog"] == []


def test_hook_invocation_via_subprocess_with_pythonpath(tmp_path):
    """Reproduce the literal Stop-hook command form: `python3 -m scan_findings`
    with PYTHONPATH=scripts. Guards against the built-not-wired failure mode."""
    transcript = _transcript(tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_SCRIPTS_DIR)
    proc = subprocess.run(
        [sys.executable, "-m", "scan_findings",
         "--workdir", str(tmp_path), "--transcript", str(transcript),
         "--today", "2026-06-27", "--lock-file", str(tmp_path / ".findings.lock")],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert len(_items(tmp_path)) == 2
