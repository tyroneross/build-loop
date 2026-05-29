# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/rally_point/revision.py — fcntl-locked monotonic counter.

  - missing file == revision 0
  - bump is monotonic
  - concurrent bumps never corrupt: final == #successful bumps or fewer
    (skip-on-timeout allowed), never higher
  - reader takes no lock
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

import revision as rev  # noqa: E402


@pytest.fixture()
def chan(tmp_path: Path) -> Path:
    d = tmp_path / "chan"
    d.mkdir()
    return d


def test_missing_is_zero(chan: Path):
    assert rev.read_revision(chan) == 0


def test_hash_chain_tail_sets_revision(chan: Path):
    (chan / "rally.tail.json").write_text(
        json.dumps({"next_seq": 8}),
        encoding="utf-8",
    )

    assert rev.read_revision(chan) == 7


def test_hash_chain_log_sets_revision_when_tail_missing(chan: Path):
    rows = [
        {"event": {"kind": "profile"}, "local_seq": 2},
        {"event": {"kind": "handoff"}, "local_seq": 5},
    ]
    (chan / "changes.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    assert rev.read_revision(chan) == 5


def test_bump_monotonic(chan: Path):
    assert rev.bump_revision(chan) == 1
    assert rev.bump_revision(chan) == 2
    assert rev.bump_revision(chan) == 3
    assert rev.read_revision(chan) == 3


def _worker(d: str, n: int):
    for _ in range(n):
        rev.bump_revision(Path(d))


def test_concurrent_no_corruption(chan: Path):
    procs = [mp.Process(target=_worker, args=(str(chan), 25)) for _ in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    final = rev.read_revision(chan)
    # monotonic-or-skip: never exceeds total attempted bumps, always a
    # well-formed int, strictly positive (at least one bump landed).
    assert isinstance(final, int)
    assert 0 < final <= 6 * 25


def test_reader_no_lock_when_present(chan: Path):
    rev.bump_revision(chan)
    # read_revision must not raise even if file is concurrently held;
    # smoke: two sequential reads are stable.
    assert rev.read_revision(chan) == rev.read_revision(chan) == 1
