#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rally Point revision counter — the cheap "did anything change" signal.

One ``revision`` integer per channel. Bumped under a short-timeout
``fcntl`` exclusive lock; **skip-on-timeout** (return current value
without bumping rather than block the host action) — one cycle of
staleness is acceptable, corruption is not. Monotonic by construction:
a bump only ever writes ``current + 1`` while holding the lock, via
tmp+rename so a reader never observes a torn file.

Readers take **no lock** (design: readers never lock). A missing or
unparseable file reads as revision ``0``.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from pathlib import Path

_REV_NAME = "revision"
_TAIL_NAME = "rally.tail.json"
_LOG_NAME = "changes.jsonl"
_LOCK_TIMEOUT_S = 0.5
_LOCK_POLL_S = 0.01


def _rev_path(channel_dir: Path) -> Path:
    return Path(channel_dir) / _REV_NAME


def _hash_chain_revision(channel_dir: Path) -> int:
    d = Path(channel_dir)
    try:
        tail = json.loads((d / _TAIL_NAME).read_text(encoding="utf-8"))
        next_seq = int(tail.get("next_seq", 0))
        if next_seq > 0:
            return next_seq - 1
    except (FileNotFoundError, OSError, ValueError, TypeError):
        pass

    latest = 0
    try:
        with open(d / _LOG_NAME, "r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    seq = int(row.get("local_seq", 0))
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
                if seq > latest:
                    latest = seq
    except OSError:
        return 0
    return latest


def read_revision(channel_dir: Path) -> int:
    """Return the current revision (0 if absent/unreadable). No lock."""
    p = _rev_path(channel_dir)
    hash_chain_rev = _hash_chain_revision(Path(channel_dir))
    try:
        raw = p.read_text().strip()
    except (FileNotFoundError, OSError):
        return hash_chain_rev
    try:
        v = int(raw)
    except (ValueError, TypeError):
        return hash_chain_rev
    return max(v if v >= 0 else 0, hash_chain_rev)


def bump_revision(channel_dir: Path) -> int:
    """Increment and return the revision under a short-timeout lock.

    On lock-acquire timeout, return the current value WITHOUT bumping
    (skip-on-timeout — never blocks/fails the host action). Write is
    tmp+rename so readers never see a partial value.
    """
    d = Path(channel_dir)
    d.mkdir(parents=True, exist_ok=True)
    lock_path = d / (_REV_NAME + ".lock")
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    return read_revision(d)  # skip-on-timeout
                time.sleep(_LOCK_POLL_S)
        # Critical section: read-modify-write monotonically.
        cur = read_revision(d)
        new = cur + 1
        tmp = d / (_REV_NAME + f".tmp.{os.getpid()}")
        tmp.write_text(str(new))
        os.replace(str(tmp), str(_rev_path(d)))
        return new
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)
