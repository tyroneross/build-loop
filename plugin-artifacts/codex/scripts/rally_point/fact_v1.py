# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""fact.v1 emitter — lossless ARP-ingestible records from build-loop coordination writes.

Build-loop's embedded fallback historically wrote its own ``changes.jsonl`` shape
(``changes.make_record``). The installed Rust agent-rally-point binary only ingests
``agent-rally.fact.v1``-shaped JSONL via ``rally migrate-legacy`` — any line whose
``schema`` field is not exactly ``"agent-rally.fact.v1"`` is SILENTLY SKIPPED
(discovery.rs:713-716). So for the fallback→ARP transition to migrate build-loop's
actual coordination history (not zero facts), the fallback store must already be in
fact.v1 shape.

This module is the emitter. ``to_fact_v1`` maps a build-loop coordination write onto
the upstream ``Fact`` wire shape (store.rs:229-272, FACT_SCHEMA lib.rs:40). Kind mapping
delegates to ``post._native_kind`` so the two can never diverge (single source of truth).

Key fidelity points (verified against the upstream Rust source this run):
  - ``schema`` must equal ``"agent-rally.fact.v1"`` exactly or migrate-legacy skips the line.
  - ``ref`` is the wire name for the upstream ``ref_id`` field (``#[serde(rename = "ref")]``).
  - ``seq`` is store-assigned; the emitter writes ``0`` and migrate-legacy resets it anyway
    (``Fact { seq: 0, ..fact }`` discovery.rs:742).
  - ``session`` / ``from_session_id`` are skip-if-none upstream — the emitter omits them.
  - Rally's migration reserializes through its closed ``Fact`` shape, so private
    ``bl_*`` keys are local-reader conveniences only. The original event is also
    encoded in tagged ``evidence`` chunks, which survive that reserialization.
    The per-run identity fields (build_loop_id,
    build_loop_started_at, build_loop_run_label) ride in a SEPARATE ``bl_build_loop`` key —
    orthogonal to ``bl_producer`` (producer = runtime identity; build_loop = run-instance
    identity), so neither nests the other. ``changes.normalize_record`` reads ALL of these
    back to TOP-LEVEL keys; ARP ignores them.

Pure / stdlib-only. NEVER imports agent-rally-point.
"""
from __future__ import annotations

import hashlib
import errno
import fcntl
import json
import os
import sqlite3
import stat
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path

try:  # package import
    from .post import _bounded_text, _native_kind, _native_subject
    from .payload_codec import encode_event
except ImportError:  # script import (sys.path-inserted, no parent package)
    from post import _bounded_text, _native_kind, _native_subject  # type: ignore
    from payload_codec import encode_event  # type: ignore

FACT_SCHEMA = "agent-rally.fact.v1"
_LOG_NAME = "changes.jsonl"
_REV_NAME = "revision"
_LOCK_TIMEOUT_S = 0.5
_LOCK_POLL_S = 0.01
_CORRUPT_TAIL_MAX_FILES = 8
_CORRUPT_TAIL_MAX_BYTES = 1024 * 1024

# Kinds excluded from claimable-work surfaces upstream; the build-loop fallback
# does not produce them, so the emitter refuses them defensively.
_NON_EMITTED_KINDS = frozenset({"read", "receipt"})


def map_kind(kind: str) -> str:
    """Return the fact.v1 wire kind for a build-loop ``kind``.

    Delegates to ``post._native_kind`` verbatim — single source of truth for the
    build-loop→ARP kind mapping, so the emitter and the native-CLI post path can
    never drift. (handoff/standby/wake pass through; phase→presence; escalation→risk;
    feedback/message/dep-change/arch-scan-complete and everything else incl. commit
    and the four lead-* kinds → the catch-all ``artifact``.)
    """
    return _native_kind(kind)


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _event_id(
    *,
    kind: str,
    tool: str,
    run_id: str,
    subject: str,
    created_at: str,
    revision: int,
    encoded_event: list[str],
) -> str:
    """Deterministic, stable event_id so migrate-legacy dedup works across replays.

    The authenticated event chunks carry the exact payload and optional source
    record. Including their digest plus the local revision prevents distinct
    same-second, same-subject writes from collapsing onto one Rally event while
    keeping exact replays idempotent.
    """
    encoded_digest = hashlib.sha256(
        "\x1e".join(encoded_event).encode("utf-8")
    ).hexdigest()
    canonical = "\x1f".join(
        (
            kind,
            tool,
            run_id,
            subject,
            created_at,
            str(int(revision)),
            encoded_digest,
        )
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"blf_{digest[:24]}"


def to_fact_v1(
    *,
    kind: str,
    tool: str,
    model: str,
    run_id: str,
    app_slug: str,
    payload: dict | None,
    subject: str | None = None,
    revision: int = 0,
    producer: dict | None = None,
    build_loop_fields: dict | None = None,
    source_record: dict | None = None,
    created_at: str | None = None,
) -> dict:
    """Build a fact.v1-shaped dict from a build-loop coordination write.

    The returned dict serializes (json.dumps) to a line ``rally migrate-legacy``
    ingests losslessly. Build-loop-private signal (revision, original payload,
    producer metadata, run identity) rides along as additive ``bl_*`` keys that
    ARP ignores and ``changes.normalize_record`` reads back to top-level.

    ``producer`` (runtime identity) and ``build_loop_fields`` (per-run identity:
    build_loop_id / build_loop_started_at / build_loop_run_label) are stored as
    DISTINCT keys (``bl_producer`` / ``bl_build_loop``), never nested in each
    other, so the two identity axes stay orthogonal on read-back.
    """
    payload = payload or {}
    if kind in _NON_EMITTED_KINDS:
        # Defensive: the fallback should never emit these; coerce to artifact so a
        # stray call still produces a valid, ingestible line rather than crashing.
        wire_kind = "artifact"
    else:
        wire_kind = map_kind(kind)

    # Subject derivation delegates to post._native_subject (DRY — same source of
    # truth the native-CLI post path uses), so a phase event yields
    # "phase: rally-start" rather than the bare "phase", and the event_id hash
    # stays consistent with the native path. An explicit subject arg still wins.
    subj = _bounded_text(str(subject) if subject else _native_subject(kind, payload), 512)
    ts = created_at or _iso_now()
    encoded_event = encode_event(
        kind=kind,
        payload=payload,
        model=model,
        run_id=run_id,
        app_slug=app_slug,
        source_record=source_record,
    )

    fact: dict = {
        "schema": FACT_SCHEMA,
        "event_id": _event_id(
            kind=wire_kind, tool=tool or "unknown", run_id=run_id or "",
            subject=subj, created_at=ts, revision=revision,
            encoded_event=encoded_event,
        ),
        "seq": 0,  # store-assigned; migrate-legacy resets to 0 anyway
        "thread_id": run_id or app_slug or "",
        "kind": wire_kind,
        "subject": subj,
        "scope": _bounded_string_list(
            payload.get("paths") or payload.get("path") or payload.get("scope"),
            limit=16,
            max_bytes=512,
        ),
        "created_at": ts,
        # The codec envelope already contains payload.evidence. Do not prepend
        # caller-controlled evidence: it could spoof a codec group or exceed
        # Rally's 64-entry/4096-byte migration boundary.
        "evidence": encoded_event,
    }
    if tool:
        fact["tool"] = tool

    summary = payload.get("summary") or payload.get("reason")
    if summary:
        fact["summary"] = _bounded_text(summary, 2048)
    target = payload.get("to") or payload.get("to_tool") or payload.get("target")
    if target:
        fact["target"] = _bounded_text(target, 256)
    ref = payload.get("ref") or payload.get("ref_id")
    if ref:
        fact["ref"] = _bounded_text(ref, 256)  # wire name for upstream ref_id
    status = payload.get("status") or payload.get("verdict")
    if status:
        fact["status"] = _bounded_text(status, 128)
    severity = payload.get("severity")
    if severity:
        fact["severity"] = _bounded_text(severity, 128)
    uri = payload.get("uri")
    if uri:
        fact["uri"] = _bounded_text(uri, 2048)

    # Build-loop-private additive keys (ARP tolerates: no deny_unknown_fields).
    fact["bl_revision"] = int(revision)
    if model:
        fact["bl_model"] = str(model)
    if app_slug:
        fact["bl_app_slug"] = str(app_slug)
    fact["bl_kind"] = str(kind)  # original (pre-map) build-loop kind, for lossless read-back
    if payload:
        fact["bl_payload"] = payload
    if producer:
        fact["bl_producer"] = producer
    if build_loop_fields:
        # Per-run identity, orthogonal to producer (runtime) identity. Stored
        # in its own key so normalize splices it back as TOP-LEVEL siblings of
        # the producer_* keys — never nested under bl_producer.
        fact["bl_build_loop"] = build_loop_fields
    return fact


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if v is not None]
    return [value]


def _bounded_string_list(value, *, limit: int, max_bytes: int) -> list[str]:
    values = value if isinstance(value, list) else ([] if value is None else [value])
    return [_bounded_text(item, max_bytes) for item in values[:limit] if item is not None]


def _acquire_write_lock(channel_dir: Path) -> int | None:
    """Acquire the fallback writer lock, returning its fd or ``None`` on timeout."""
    lock_fd = os.open(
        str(Path(channel_dir) / (_REV_NAME + ".lock")),
        os.O_CREAT | os.O_RDWR,
        0o644,
    )
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_fd
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                os.close(lock_fd)
                raise
            if time.monotonic() >= deadline:
                os.close(lock_fd)
                return None
            time.sleep(_LOCK_POLL_S)


def _release_write_lock(lock_fd: int) -> None:
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(lock_fd)


def _write_all(fd: int, data: bytes) -> None:
    """Write every byte or raise; ``os.write`` is allowed to be partial."""
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise OSError("zero-byte append while writing fallback ledger")
        offset += written


def _last_complete_line_offset(fd: int, size: int) -> int:
    """Return the byte after the final newline, or zero when none exists."""
    cursor = size
    while cursor > 0:
        start = max(0, cursor - 64 * 1024)
        chunk = os.pread(fd, cursor - start, start)
        newline = chunk.rfind(b"\n")
        if newline >= 0:
            return start + newline + 1
        cursor = start
    return 0


def _quarantine_torn_tail(channel_dir: Path, fd: int, size: int) -> int:
    """Preserve and remove an incomplete final record under the writer lock."""
    clean_size = _last_complete_line_offset(fd, size)
    quarantine = Path(channel_dir) / "corrupt-tails"
    quarantine.mkdir(parents=True, exist_ok=True)
    target = quarantine / f"changes-{time.time_ns()}-{os.getpid()}.partial"
    qfd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        cursor = clean_size
        evidence_end = min(size, clean_size + _CORRUPT_TAIL_MAX_BYTES)
        while cursor < evidence_end:
            chunk = os.pread(fd, min(64 * 1024, evidence_end - cursor), cursor)
            if not chunk:
                raise OSError("could not preserve torn fallback-ledger tail")
            _write_all(qfd, chunk)
            cursor += len(chunk)
        os.fsync(qfd)
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(qfd)
    _prune_corrupt_tail_quarantine(quarantine, newest=target)
    os.ftruncate(fd, clean_size)
    os.fsync(fd)
    return clean_size


def _prune_corrupt_tail_quarantine(quarantine: Path, *, newest: Path) -> None:
    """Retain newest torn-tail evidence within fixed count and byte budgets."""
    try:
        candidates = [
            path
            for path in quarantine.glob("changes-*.partial")
            if path.is_file() and not path.is_symlink()
        ]
        others = [path for path in candidates if path != newest]
        others.sort(
            key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True
        )
        ordered = [newest, *others] if newest in candidates else others
        kept = 0
        kept_bytes = 0
        for path in ordered:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if (
                kept < _CORRUPT_TAIL_MAX_FILES
                and kept_bytes + size <= _CORRUPT_TAIL_MAX_BYTES
            ):
                kept += 1
                kept_bytes += size
                continue
            try:
                path.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _append_line_locked(
    channel_dir: Path,
    fact: dict,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> bool:
    """Append one complete fact while the caller holds ``revision.lock``."""
    data = (json.dumps(fact, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(
        str(Path(channel_dir) / _LOG_NAME),
        os.O_RDWR
        | os.O_APPEND
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("fallback ledger must be a regular file")
        if expected_identity is not None and (
            metadata.st_dev,
            metadata.st_ino,
        ) != expected_identity:
            raise OSError("fallback ledger identity changed during migration")
        original_size = os.lseek(fd, 0, os.SEEK_END)
        clean_size = original_size
        if original_size:
            if os.pread(fd, 1, original_size - 1) != b"\n":
                clean_size = _quarantine_torn_tail(
                    Path(channel_dir), fd, original_size
                )
        try:
            _write_all(fd, data)
            os.fsync(fd)
        except Exception:
            # A failed/partial append must not poison every later row. The
            # writer lock makes rollback safe against other Build Loop writers.
            try:
                os.ftruncate(fd, clean_size)
                os.fsync(fd)
            except OSError:
                pass
            raise
        return True
    finally:
        os.close(fd)


def write_fact_v1_line(channel_dir: Path, fact: dict) -> bool:
    """Append one fact.v1 line under the fallback writer lock.

    Returns ``True`` only after the complete line is durable. A torn prior tail
    is preserved under ``corrupt-tails/`` before it is removed from the canonical
    JSONL, so a crashed write cannot strand all subsequent migration.
    """
    try:
        d = Path(channel_dir)
        d.mkdir(parents=True, exist_ok=True)
        lock_fd = _acquire_write_lock(d)
        if lock_fd is None:
            return False
        try:
            return _append_line_locked(d, fact)
        finally:
            _release_write_lock(lock_fd)
    except Exception:  # noqa: BLE001 — coordination writes never raise
        return False


def write_missing_fact_v1_lines(
    channel_dir: Path,
    facts: Iterable[dict],
    *,
    expected_identity: tuple[int, int] | None = None,
) -> int | None:
    """Append facts whose deterministic ``event_id`` is absent, under one lock.

    Legacy conversion first derives deterministic companions, then calls this
    helper. Re-reading the ledger after acquiring the shared writer lock makes
    the check-and-append boundary safe when several Build Loop processes recover
    the same fallback store concurrently. ``None`` means the transaction could
    not be completed; an integer is the number of durable lines appended.
    """
    try:
        d = Path(channel_dir)
        d.mkdir(parents=True, exist_ok=True)
        lock_fd = _acquire_write_lock(d)
        if lock_fd is None:
            return None
        try:
            # Exact dedup remains disk-backed so an unbounded historical
            # ledger cannot become an equally unbounded Python set.
            with tempfile.TemporaryDirectory(
                prefix="build-loop-fact-index-"
            ) as temp_dir:
                conn = sqlite3.connect(str(Path(temp_dir) / "event-ids.sqlite3"))
                try:
                    conn.execute("PRAGMA journal_mode=OFF")
                    conn.execute("PRAGMA synchronous=OFF")
                    conn.execute("PRAGMA cache_size=-1024")
                    conn.execute(
                        "CREATE TABLE event_ids (event_id TEXT PRIMARY KEY) WITHOUT ROWID"
                    )
                    log = d / _LOG_NAME
                    identity = expected_identity
                    try:
                        read_fd = os.open(
                            str(log), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                        )
                        read_stat = os.fstat(read_fd)
                        if not stat.S_ISREG(read_stat.st_mode):
                            os.close(read_fd)
                            return None
                        identity = (read_stat.st_dev, read_stat.st_ino)
                        if expected_identity is not None and identity != expected_identity:
                            os.close(read_fd)
                            return None
                        with os.fdopen(read_fd, "r", encoding="utf-8") as fh:
                            for line in fh:
                                try:
                                    row = json.loads(line)
                                except (TypeError, ValueError):
                                    continue
                                event_id = row.get("event_id") if isinstance(row, dict) else None
                                if isinstance(event_id, str) and event_id:
                                    conn.execute(
                                        "INSERT OR IGNORE INTO event_ids VALUES (?)",
                                        (event_id,),
                                    )
                    except FileNotFoundError:
                        if expected_identity is not None:
                            return None
                        identity = None
                    conn.commit()

                    appended = 0
                    for fact in facts:
                        event_id = fact.get("event_id")
                        if not isinstance(event_id, str) or not event_id:
                            return None
                        present = conn.execute(
                            "SELECT 1 FROM event_ids WHERE event_id = ?", (event_id,)
                        ).fetchone()
                        if present is not None:
                            continue
                        if not _append_line_locked(
                            d, fact, expected_identity=identity
                        ):
                            return None
                        conn.execute("INSERT INTO event_ids VALUES (?)", (event_id,))
                        appended += 1
                    return appended
                finally:
                    conn.close()
        finally:
            _release_write_lock(lock_fd)
    except Exception:  # noqa: BLE001 — migration discovery remains fail closed
        return None


def append_fact_v1_transaction(
    channel_dir: Path,
    fact_factory: Callable[[int], dict],
) -> tuple[int, dict] | None:
    """Allocate a revision, append its fact, then publish the revision atomically.

    All Build Loop fallback writers share ``revision.lock``. This prevents two
    processes from assigning duplicate revisions or appending revisions out of
    order. The revision file is published only after the corresponding JSONL line
    is durable.
    """
    try:
        try:  # package import
            from .revision import read_revision
        except ImportError:  # script import
            from revision import read_revision  # type: ignore

        d = Path(channel_dir)
        d.mkdir(parents=True, exist_ok=True)
        lock_fd = _acquire_write_lock(d)
        if lock_fd is None:
            return None
        try:
            revision = read_revision(d) + 1
            fact = fact_factory(revision)
            if not _append_line_locked(d, fact):
                return None
            tmp = d / f"{_REV_NAME}.tmp.{os.getpid()}.{time.time_ns()}"
            try:
                tmp.write_text(str(revision), encoding="utf-8")
                os.replace(str(tmp), str(d / _REV_NAME))
            except OSError:
                # The JSONL fact is already durable and authoritative. Its
                # bl_revision is included in read_revision()'s recovery scan,
                # so a stale/missing scalar revision file must not turn a
                # committed post into a retryable failure and duplicate event.
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            return revision, fact
        finally:
            _release_write_lock(lock_fd)
    except Exception:  # noqa: BLE001 — coordination writes never raise
        return None
