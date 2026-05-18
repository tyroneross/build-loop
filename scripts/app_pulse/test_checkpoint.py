"""Tests for scripts/app_pulse/checkpoint.py — the one consume entry point.

  - revision == session cursor.revision -> empty envelope, NO tail read
  - changed -> {new_changes, active_peers, arch_digest|null, reactions}
  - channel/dir absent -> empty envelope, lazy-create-safe, zero error
  - reader writes only its own cursor, never locks the log
  - reactions: dep-change->reinstall, arch-scan-complete->re-baseline,
    peer file overlap -> soft-claim WARNING
  - NON-GOAL guard: envelope carries no frequency/invocation keys
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import changes as ch  # noqa: E402
import checkpoint as cp  # noqa: E402
import presence as pr  # noqa: E402
import revision as rev  # noqa: E402

_FREQ = {"count", "frequency", "invocations", "calls", "num_calls", "hits",
         "usage", "call_count"}


def _no_freq(o):
    if isinstance(o, dict):
        for k, v in o.items():
            assert k.lower() not in _FREQ
            _no_freq(v)
    elif isinstance(o, list):
        for v in o:
            _no_freq(v)


@pytest.fixture()
def chan(tmp_path: Path) -> Path:
    d = tmp_path / "chan"
    d.mkdir()
    return d


def test_absent_channel_empty_envelope(tmp_path: Path):
    env = cp.checkpoint_read(tmp_path / "nope", session_id="s1")
    assert env["changed"] is False
    assert env["new_changes"] == [] and env["active_peers"] == []
    assert env["arch_digest"] is None and env["reactions"] == []


def test_unchanged_returns_empty(chan: Path):
    pr.write_presence(chan, session_id="s1", tool="t", model="m",
                      run_id="r1", app_slug="a", phase="p")
    env = cp.checkpoint_read(chan, session_id="s1")  # revision 0 == cursor 0
    assert env["changed"] is False and env["new_changes"] == []


def test_change_surfaces_within_one_call(chan: Path):
    # session B present
    pr.write_presence(chan, session_id="B", tool="claude", model="m",
                      run_id="rB", app_slug="a", phase="p")
    # session A commits
    pr.write_presence(chan, session_id="A", tool="codex", model="m",
                      run_id="rA", app_slug="a", phase="execute")
    ch.append_change(chan, ch.make_record(
        kind="commit", tool="codex", model="m", run_id="rA",
        app_slug="a", payload={"sha": "deadbee"}, revision=1))
    rev.bump_revision(chan)
    env = cp.checkpoint_read(chan, session_id="B")
    assert env["changed"] is True
    assert [c["kind"] for c in env["new_changes"]] == ["commit"]
    assert any(p["session_id"] == "A" for p in env["active_peers"])
    _no_freq(env)
    # cursor advanced — second read is empty (delta-only)
    env2 = cp.checkpoint_read(chan, session_id="B")
    assert env2["changed"] is False and env2["new_changes"] == []


def test_reactions(chan: Path):
    pr.write_presence(chan, session_id="B", tool="t", model="m",
                      run_id="rB", app_slug="a", phase="p")
    # peer A owns overlapping file
    pr.write_presence(chan, session_id="A", tool="t", model="m",
                      run_id="rA", app_slug="a", phase="execute",
                      files_in_flight=["src/x.py"])
    for k in ("dep-change", "arch-scan-complete"):
        ch.append_change(chan, ch.make_record(
            kind=k, tool="t", model="m", run_id="rA", app_slug="a",
            payload={}, revision=1))
    rev.bump_revision(chan)
    env = cp.checkpoint_read(chan, session_id="B",
                             my_files=["src/x.py", "src/y.py"])
    types = {r["type"] for r in env["reactions"]}
    assert "reinstall" in types and "re-baseline" in types
    sc = [r for r in env["reactions"] if r["type"] == "soft-claim"]
    assert sc and sc[0]["severity"] == "warning" and "src/x.py" in sc[0]["files"]


def test_reader_does_not_lock_log(chan: Path):
    pr.write_presence(chan, session_id="B", tool="t", model="m",
                      run_id="rB", app_slug="a", phase="p")
    ch.append_change(chan, ch.make_record(
        kind="commit", tool="t", model="m", run_id="r", app_slug="a",
        payload={}, revision=1))
    rev.bump_revision(chan)
    # no <log>.lock file should be created by a read
    cp.checkpoint_read(chan, session_id="B")
    assert not (chan / "changes.jsonl.lock").exists()
