# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the codex rollout transcript source added to locate.py (Item 3 part A)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # scripts/ on path
sys.path.insert(0, str(HERE))

import locate  # noqa: E402


def _write_rollout(day_dir: Path, name: str, cwd: str, tss: list[str]) -> Path:
    day_dir.mkdir(parents=True, exist_ok=True)
    p = day_dir / name
    lines = [json.dumps({"timestamp": tss[0], "type": "session_meta",
                         "payload": {"cwd": cwd, "session_id": "s1"}})]
    for t in tss[1:]:
        lines.append(json.dumps({"timestamp": t, "type": "event", "payload": {}}))
    p.write_text("\n".join(lines) + "\n")
    return p


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_codex_transcript_cwd(tmp_path):
    p = _write_rollout(tmp_path, "rollout-x.jsonl", "/repo/a",
                       ["2026-07-15T23:44:02Z", "2026-07-15T23:45:00Z"])
    assert locate.codex_transcript_cwd(p) == "/repo/a"


def test_codex_match_by_cwd_and_window(tmp_path, monkeypatch):
    root = tmp_path / "codex" / "sessions"
    day = root / "2026" / "07" / "15"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _write_rollout(day, "rollout-2026-07-15T23-44-00-uuid.jsonl", str(repo),
                   ["2026-07-15T23:44:02Z", "2026-07-15T23:50:00Z"])
    monkeypatch.setattr(locate, "codex_sessions_root", lambda: root)

    path, marker = locate.find_codex_transcript_for_run(
        repo,
        run_start=_dt("2026-07-15T23:40:00Z"),
        run_end=_dt("2026-07-15T23:55:00Z"),
        run_host="codex",
    )
    assert path is not None and marker is None


def test_codex_rejects_wrong_cwd(tmp_path, monkeypatch):
    root = tmp_path / "codex" / "sessions"
    day = root / "2026" / "07" / "15"
    _write_rollout(day, "rollout-a.jsonl", "/some/other/repo",
                   ["2026-07-15T23:44:02Z", "2026-07-15T23:50:00Z"])
    monkeypatch.setattr(locate, "codex_sessions_root", lambda: root)
    repo = tmp_path / "myrepo"
    repo.mkdir()
    path, marker = locate.find_codex_transcript_for_run(
        repo, run_start=_dt("2026-07-15T23:40:00Z"),
        run_end=_dt("2026-07-15T23:55:00Z"), run_host="codex")
    assert path is None and "codex-transcript" in marker


def test_find_transcript_falls_through_to_codex(tmp_path, monkeypatch):
    # No Claude transcript for this cwd -> codex source is tried and matches.
    root = tmp_path / "codex" / "sessions"
    day = root / "2026" / "07" / "15"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _write_rollout(day, "rollout-b.jsonl", str(repo),
                   ["2026-07-15T23:44:02Z", "2026-07-15T23:50:00Z"])
    monkeypatch.setattr(locate, "codex_sessions_root", lambda: root)
    monkeypatch.setattr(locate, "sessions_root", lambda: tmp_path / "no-claude")

    path, marker = locate.find_transcript_for_run(
        repo, run_start=_dt("2026-07-15T23:40:00Z"),
        run_end=_dt("2026-07-15T23:55:00Z"), run_host="codex")
    assert path is not None and marker is None


def test_claude_host_run_skips_codex(tmp_path, monkeypatch):
    # A run KNOWN to be claude_code-hosted must not pick up a codex rollout.
    root = tmp_path / "codex" / "sessions"
    day = root / "2026" / "07" / "15"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _write_rollout(day, "rollout-c.jsonl", str(repo),
                   ["2026-07-15T23:44:02Z", "2026-07-15T23:50:00Z"])
    monkeypatch.setattr(locate, "codex_sessions_root", lambda: root)
    monkeypatch.setattr(locate, "sessions_root", lambda: tmp_path / "no-claude")

    path, marker = locate.find_transcript_for_run(
        repo, run_start=_dt("2026-07-15T23:40:00Z"),
        run_end=_dt("2026-07-15T23:55:00Z"), run_host="claude_code")
    assert path is None  # codex skipped for a claude_code run
