# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Lossless Build Loop payloads carried through Rally's recognized evidence field.

Standalone Rally intentionally deserializes legacy facts into a closed ``Fact``
shape before reserializing them.  Private ``bl_*`` keys are therefore useful to
Build Loop's local reader, but cannot be the migration contract.  This module
stores the canonical payload in tagged, bounded evidence chunks that Rally does
preserve.  The decoder verifies length and SHA-256 before returning JSON.

Payloads above 32 KiB remain authoritative in Build Loop's local ledger and
receive an explicit oversize marker.  Auto-migration must not watermark a store
containing such a row because that row cannot be represented losslessly in the
native command transport.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Iterable

PREFIX = "build-loop-payload.v1"
OVERSIZE_PREFIX = "build-loop-payload.v1-oversize"
EVENT_SCHEMA = "build-loop-event.v1"
MAX_PAYLOAD_BYTES = 32 * 1024
# Base64 expands by 4/3. A 2,800-byte raw chunk plus the authenticated header
# remains below Rally's 4,096-byte per-evidence-entry boundary. The 32-KiB raw
# ceiling expands to under 44 KiB, leaving headroom inside Rally's 64-KiB total
# fact-text boundary for bounded subject/summary/scope fields.
CHUNK_BYTES = 2800
MAX_CHUNKS = (MAX_PAYLOAD_BYTES + CHUNK_BYTES - 1) // CHUNK_BYTES


def canonical_payload(payload: dict[str, Any] | None) -> bytes:
    return json.dumps(
        payload or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def encode_payload(payload: dict[str, Any] | None) -> list[str]:
    """Return tagged evidence chunks, or an explicit oversize marker."""
    raw = canonical_payload(payload)
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) > MAX_PAYLOAD_BYTES:
        return [f"{OVERSIZE_PREFIX}:{digest}:{len(raw)}"]
    chunks = [raw[i : i + CHUNK_BYTES] for i in range(0, len(raw), CHUNK_BYTES)]
    chunks = chunks or [b""]
    total = len(chunks)
    return [
        (
            f"{PREFIX}:{digest}:{len(raw)}:{index}/{total}:"
            + base64.urlsafe_b64encode(chunk).decode("ascii").rstrip("=")
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


def encode_event(
    *,
    kind: str,
    payload: dict[str, Any] | None,
    model: str = "",
    run_id: str = "",
    app_slug: str = "",
    source_record: dict[str, Any] | None = None,
) -> list[str]:
    """Encode the original Build Loop event, including pre-map semantics."""
    event = {
        "schema": EVENT_SCHEMA,
        "kind": str(kind),
        "model": str(model),
        "run_id": str(run_id),
        "app_slug": str(app_slug),
        "payload": payload or {},
    }
    if source_record is not None:
        # Historical schema-less rows remain untouched in changes.jsonl.  A
        # canonical copy here authenticates every original key/value through
        # the codec digest so Rally's closed Fact reserialization cannot erase
        # data when the append-only companion fact is imported.
        event["source_record"] = source_record
    return encode_payload(event)


def has_oversize_marker(evidence: Iterable[Any] | None) -> bool:
    return any(
        isinstance(item, str) and item.startswith(f"{OVERSIZE_PREFIX}:")
        for item in (evidence or [])
    )


def decode_payload(evidence: Iterable[Any] | None) -> dict[str, Any] | None:
    """Decode one complete payload group; malformed/untrusted input returns None."""
    groups: dict[tuple[str, int, int], dict[int, str]] = {}
    for item in evidence or []:
        if not isinstance(item, str) or not item.startswith(f"{PREFIX}:"):
            continue
        try:
            _prefix, digest, length_raw, ordinal, encoded = item.split(":", 4)
            index_raw, total_raw = ordinal.split("/", 1)
            length = int(length_raw)
            index = int(index_raw)
            total = int(total_raw)
        except (TypeError, ValueError):
            continue
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or length < 0
            or length > MAX_PAYLOAD_BYTES
            or total < 1
            or total > MAX_CHUNKS
            or index < 1
            or index > total
        ):
            continue
        groups.setdefault((digest, length, total), {})[index] = encoded

    decoded: list[dict[str, Any]] = []
    for (digest, length, total), chunks in groups.items():
        if len(chunks) != total or any(index not in chunks for index in range(1, total + 1)):
            continue
        try:
            raw = b"".join(
                base64.urlsafe_b64decode(chunks[index] + "=" * (-len(chunks[index]) % 4))
                for index in range(1, total + 1)
            )
        except (ValueError, TypeError):
            continue
        if len(raw) != length or hashlib.sha256(raw).hexdigest() != digest:
            continue
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            decoded.append(value)
    # Ambiguity is a validation failure. Never let a caller-supplied tagged
    # group win merely because it appeared before the canonical group.
    return decoded[0] if len(decoded) == 1 else None


def decode_event(evidence: Iterable[Any] | None) -> dict[str, Any] | None:
    value = decode_payload(evidence)
    if not isinstance(value, dict) or value.get("schema") != EVENT_SCHEMA:
        return None
    if not isinstance(value.get("payload"), dict):
        return None
    return value
