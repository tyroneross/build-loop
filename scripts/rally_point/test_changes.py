"""Tests for scripts/rally_point/changes.py — append-only immutable log.

  - record schema build + validate (warns-not-drops unknown kind, D7)
  - NON-GOAL guard: no frequency/invocation/count keys anywhere
  - O_APPEND atomic under concurrency: N writers -> N intact JSON lines
  - read_changes_since(offset) returns only new records + new offset
  - immutable: no rewrite/delete/truncate API exists
"""
from __future__ import annotations

import json
import multiprocessing as mp
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import changes as ch  # noqa: E402

_FREQ_KEYS = {
    "count", "counts", "frequency", "freq", "invocations", "invocation_count",
    "calls", "num_calls", "call_count", "hits", "usage", "usage_count",
}


def _assert_no_freq(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert k.lower() not in _FREQ_KEYS, f"non-goal key leaked: {k}"
            _assert_no_freq(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_no_freq(v)


@pytest.fixture()
def chan(tmp_path: Path) -> Path:
    d = tmp_path / "chan"
    d.mkdir()
    return d


def test_make_record_schema():
    r = ch.make_record(
        kind="commit", tool="claude", model="opus", run_id="r1",
        app_slug="app", payload={"sha": "abc"}, revision=3,
    )
    assert set(r) == {
        "ts", "kind", "tool", "model", "run_id", "app_slug", "payload",
        "revision",
    }
    assert r["kind"] == "commit" and r["revision"] == 3
    _assert_no_freq(r)


def test_validate_warns_not_drops_unknown_kind(capsys):
    r = ch.make_record(
        kind="totally-new-kind", tool="t", model="m", run_id="r",
        app_slug="a", payload={}, revision=1,
    )
    out = ch.validate_record(r)  # must RETURN the record, not drop it
    assert out == r
    assert "totally-new-kind" in capsys.readouterr().err


def test_append_and_read_since(chan: Path):
    r1 = ch.make_record(kind="commit", tool="t", model="m", run_id="r",
                         app_slug="a", payload={}, revision=1)
    ch.append_change(chan, r1)
    recs, off = ch.read_changes_since(chan, 0)
    assert [x["kind"] for x in recs] == ["commit"]
    r2 = ch.make_record(kind="phase", tool="t", model="m", run_id="r",
                        app_slug="a", payload={"phase": "execute"}, revision=2)
    ch.append_change(chan, r2)
    recs2, off2 = ch.read_changes_since(chan, off)
    assert [x["kind"] for x in recs2] == ["phase"]
    assert off2 > off
    # absent log -> empty, offset 0
    assert ch.read_changes_since(chan / "nope", 0) == ([], 0)


def _writer(d: str, tag: int):
    for i in range(20):
        ch.append_change(
            Path(d),
            ch.make_record(kind="commit", tool="t", model="m",
                           run_id=f"w{tag}", app_slug="a",
                           payload={"i": i}, revision=1),
        )


def test_concurrent_append_no_torn_lines(chan: Path):
    procs = [mp.Process(target=_writer, args=(str(chan), t)) for t in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    recs, _ = ch.read_changes_since(chan, 0)
    assert len(recs) == 6 * 20
    for r in recs:  # every line parsed cleanly == no torn writes
        assert set(r) >= {"kind", "run_id", "payload"}
        _assert_no_freq(r)


def test_no_mutation_api():
    for forbidden in ("rewrite", "delete_change", "truncate", "overwrite",
                       "update_change", "remove_change"):
        assert not hasattr(ch, forbidden), f"immutability violated: {forbidden}"
