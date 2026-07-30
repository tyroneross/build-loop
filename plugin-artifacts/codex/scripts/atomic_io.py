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
