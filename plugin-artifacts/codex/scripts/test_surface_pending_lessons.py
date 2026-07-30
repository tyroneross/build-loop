# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for surface_pending_lessons (tier-3 host-agent surface)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "surface_pending_lessons.py"


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(HERE) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _seed_pending(workdir: Path, *, id_hash: str, kind: str, signal: str, quote: str, scope: str = "project") -> Path:
    pending = workdir / ".build-loop" / "pending-lessons"
    pending.mkdir(parents=True, exist_ok=True)
    p = pending / f"20260101T000000Z-{kind}-{id_hash}.md"
    body = (
        "---\n"
        f"id: {id_hash}\n"
        f"kind: {kind}\n"
        f"signal_type: {signal}\n"
        "confidence: confirmed\n"
        f"scope: {scope}\n"
        "turn_index: 0\n"
        "captured_chars: 10\n"
        "tier: 1-deterministic\n"
        "source: stop-hook\n"
        "captured_at: 2026-01-01T00:00:00+00:00\n"
        "extras:\n"
        "  prior_assistant_acted: true\n"
        "---\n\n"
        "## Quote\n\n"
        f"> {quote}\n\n"
    )
    p.write_text(body, encoding="utf-8")
    return p


def test_empty_state_prints_zero(tmp_path: Path) -> None:
    r = _run(["--workdir", str(tmp_path)], cwd=tmp_path)
    assert r.returncode == 0
    assert "0" in r.stdout
    assert "Pending lesson candidates" in r.stdout


def test_quiet_suppresses_zero_output(tmp_path: Path) -> None:
    r = _run(["--workdir", str(tmp_path), "--quiet"], cwd=tmp_path)
    assert r.returncode == 0
    assert r.stdout == ""


def test_markdown_render_includes_quote_and_path(tmp_path: Path) -> None:
    _seed_pending(tmp_path, id_hash="abc12345", kind="correction", signal="revert", quote="Revert that")
    r = _run(["--workdir", str(tmp_path)], cwd=tmp_path)
    assert r.returncode == 0
    out = r.stdout
    assert "Pending lesson candidates (1)" in out
    assert "correction · revert" in out
    assert "Revert that" in out
    assert ".build-loop/pending-lessons" in out


def test_project_vs_global_scope_routing_in_render(tmp_path: Path) -> None:
    _seed_pending(tmp_path, id_hash="proj0001", kind="preference", signal="always", quote="Always X", scope="project")
    _seed_pending(tmp_path, id_hash="glob0001", kind="preference", signal="always", quote="Always Y", scope="global")
    r = _run(["--workdir", str(tmp_path)], cwd=tmp_path)
    out = r.stdout
    assert "Always X" in out
    assert "Always Y" in out
    assert "build-loop-memory/lessons/" in out  # global lane
    assert "build-loop-memory/projects/<slug>/lessons/" in out  # project lane


def test_high_signal_prior_action_flagged(tmp_path: Path) -> None:
    _seed_pending(tmp_path, id_hash="actfp001", kind="correction", signal="revert", quote="Revert that")
    r = _run(["--workdir", str(tmp_path)], cwd=tmp_path)
    assert "user reacted to assistant's just-taken action" in r.stdout


def test_json_envelope_shape(tmp_path: Path) -> None:
    _seed_pending(tmp_path, id_hash="json0001", kind="tradeoff", signal="instead_of", quote="A instead of B")
    r = _run(["--workdir", str(tmp_path), "--json"], cwd=tmp_path)
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["total"] == 1
    assert payload["shown"] == 1
    assert payload["items"][0]["kind"] == "tradeoff"
    assert payload["items"][0]["quote"] == "A instead of B"
    assert "promotion_guide" in payload


def test_limit_caps_render(tmp_path: Path) -> None:
    for i in range(5):
        _seed_pending(tmp_path, id_hash=f"lim{i:05d}", kind="preference", signal="always", quote=f"item {i}")
    r = _run(["--workdir", str(tmp_path), "--limit", "2"], cwd=tmp_path)
    assert r.returncode == 0
    assert "showing 2 of 5" in r.stdout


def test_malformed_frontmatter_is_skipped(tmp_path: Path) -> None:
    pending = tmp_path / ".build-loop" / "pending-lessons"
    pending.mkdir(parents=True)
    (pending / "20260101T000000Z-bad-deadbeef.md").write_text("just some text\n", encoding="utf-8")
    _seed_pending(tmp_path, id_hash="good0001", kind="preference", signal="always", quote="Always X")
    r = _run(["--workdir", str(tmp_path)], cwd=tmp_path)
    assert r.returncode == 0
    assert "Pending lesson candidates (1)" in r.stdout


def test_decisions_review_lane_included_when_flag_set(tmp_path: Path) -> None:
    slug = tmp_path.name
    review = tmp_path / "build-loop-memory" / "projects" / slug / "decisions" / "_review"
    review.mkdir(parents=True)
    review_md = review / "decision-foo-20260101.md"
    review_md.write_text(
        "---\n"
        "id: dec-foo-20260101\n"
        "title: Foo\n"
        "primary_tag: tooling\n"
        "confidence: inferred\n"
        "created_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "Body of the decision.\n",
        encoding="utf-8",
    )
    r = _run(["--workdir", str(tmp_path), "--include-decisions-review"], cwd=tmp_path)
    assert r.returncode == 0
    assert "decision · tooling" in r.stdout


def test_idempotent_re_render(tmp_path: Path) -> None:
    _seed_pending(tmp_path, id_hash="idem0001", kind="correction", signal="revert", quote="Revert that")
    r1 = _run(["--workdir", str(tmp_path), "--json"], cwd=tmp_path)
    r2 = _run(["--workdir", str(tmp_path), "--json"], cwd=tmp_path)
    p1 = json.loads(r1.stdout)
    p2 = json.loads(r2.stdout)
    assert p1["items"] == p2["items"]
