"""Shared atomic-write primitives for build-loop scripts.

Single source of truth for the tmpfile + fsync + os.replace write pattern and
the fcntl.flock sidecar-lockfile guard. Previously each of write_decision.py,
write_run_entry.py, and working_state_writer.py carried its own byte-identical
copy; this module collapses them so the contract has one failure site.

Stdlib only. Sub-millisecond typical. POSIX (fcntl) — matches the existing
writers, which were already POSIX-only.

Callers that need a different lock timeout pass `timeout_s` to LockedFile;
the module-level DEFAULT_LOCK_TIMEOUT_S is only the fallback default.
"""
from __future__ import annotations

import fcntl
import os
import tempfile
import time
from pathlib import Path

DEFAULT_LOCK_TIMEOUT_S = 10


class LockedFile:
    """Exclusive fcntl.flock on a sidecar lockfile. Auto-released on close."""

    def __init__(self, target: Path, timeout_s: float = DEFAULT_LOCK_TIMEOUT_S) -> None:
        self.lock_path = target.with_suffix(target.suffix + ".lock")
        self.timeout_s = timeout_s
        self._fd: int | None = None

    def __enter__(self) -> "LockedFile":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise TimeoutError(
                        f"Could not acquire lock on {self.lock_path} within {self.timeout_s}s"
                    )
                time.sleep(0.05)

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def _content_index_writethrough(target: Path) -> None:
    """Upsert *target* into the FTS content index right after a durable write lands.

    Measured 2026-09-01: nothing refreshed content_fts.sqlite on write, so three
    decisions written that day were absent from it. A full incremental build()
    costs 33s over 10,545 docs, too slow to sit on a write path, so this calls
    the targeted single-file upsert (content_index.index_paths) instead.

    Never raises. Bounds any lock-contention stall to a short fail-fast busy
    timeout well under a second (not sqlite3's 5.0s default) rather than truly
    never slowing the write — under contention it SKIPS the upsert rather than
    blocking, so a contended document stays absent from the index until the next
    `content_index.py build`. Guard is cheapest
    test first so the common case (non-.md writes — atomic_io has 29 importers,
    most writing non-memory files) returns before paying any import: env
    switches, then suffix, then store-root membership, and only then the
    content_index import (sqlite3 et al) and the actual upsert.
    """
    if os.environ.get("BUILD_LOOP_CONTENT_INDEX_WRITETHROUGH", "1") == "0":
        return
    if os.environ.get("BUILD_LOOP_MEMORY_CONTENT", "1") == "0":
        return
    try:
        if Path(target).suffix.lower() != ".md":
            return
        from _paths import memory_store_root  # noqa: PLC0415
        store = memory_store_root()
        try:
            Path(target).resolve().relative_to(store.resolve())
        except ValueError:
            return
        import content_index  # noqa: PLC0415
        content_index.index_paths([target], store=store)
    except Exception:  # noqa: BLE001 — an index failure must never fail a memory write
        pass


def atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".tmp.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    _content_index_writethrough(target)
