# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""MECE ownership validator for handoff payloads.

Thin adapter between post.py and the runtime ownership contract.
Validates that kind=handoff payloads carry all four MECE ownership
fields in their ``payload.ownership`` dict before the post is accepted.

The four required fields:
  - owns                  (list, may be empty)
  - does_not_own          (list, may be empty)
  - interface_contract    (str, non-empty)
  - integration_checkpoint (str, non-empty)

Lateral limits (G2 — added 2026-05-22): two additional REQUIRED list
fields naming the tool boundary of the handoff — the agentic analog of
military left/right limits / fire-control measures:
  - allowed_tools         (list, may be empty)
  - denied_tools          (list, may be empty)

A subordinate has full autonomy with ``allowed_tools`` and must not use
anything in ``denied_tools``. Both must be present on a ``kind=handoff``
post; either may be an empty list (an empty ``allowed_tools`` is a valid,
explicit "no tools" boundary — only a missing or non-list field rejects).

Rejections are logged to ``<channel_dir>/rejections.jsonl`` in a
fire-and-forget manner — this module never raises.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Fields that must exist in payload.ownership
_LIST_FIELDS = ("owns", "does_not_own", "allowed_tools", "denied_tools")
_NONEMPTY_STRING_FIELDS = ("interface_contract", "integration_checkpoint")


def validate_handoff(payload: dict[str, Any], *, tool: str) -> tuple[bool, dict]:
    """Validate payload for a kind=handoff post.

    Returns:
        (True, {})  when the payload passes MECE ownership checks.
        (False, rejection_detail_dict) when it fails.

    Never raises — all exceptions produce a (False, ...) rejection.
    """
    try:
        ownership = (payload or {}).get("ownership")
        if ownership is None or not isinstance(ownership, dict):
            return False, {
                "reason": "missing_mece_fields",
                "missing_or_invalid": ["ownership"],
            }

        missing_or_invalid: list[str] = []
        reason: str = "missing_mece_fields"

        for field in _LIST_FIELDS:
            if field not in ownership:
                missing_or_invalid.append(field)
            elif not isinstance(ownership[field], list):
                missing_or_invalid.append(field)
                reason = "invalid_field_type"

        for field in _NONEMPTY_STRING_FIELDS:
            if field not in ownership:
                missing_or_invalid.append(field)
            elif not isinstance(ownership[field], str):
                missing_or_invalid.append(field)
                reason = "invalid_field_type"
            elif not ownership[field].strip():
                missing_or_invalid.append(field)
                reason = "empty_required_string"

        if missing_or_invalid:
            # If we saw both missing-field and type/empty errors, pick the
            # most descriptive reason: prefer empty_required_string >
            # invalid_field_type > missing_mece_fields.
            return False, {
                "reason": reason,
                "missing_or_invalid": missing_or_invalid,
            }

        return True, {}

    except Exception as exc:  # noqa: BLE001
        return False, {
            "reason": "missing_mece_fields",
            "missing_or_invalid": ["unknown"],
            "_validator_error": str(exc),
        }


def log_rejection(
    channel_dir: Path,
    *,
    kind: str,
    tool: str,
    rejection: dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> None:
    """Atomically append a rejection record to <channel_dir>/rejections.jsonl.

    Fire-and-forget — never raises.
    """
    try:
        payload = payload or {}
        payload_bytes = json.dumps(payload, sort_keys=True, default=str).encode()
        payload_sha = hashlib.sha256(payload_bytes).hexdigest()

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "tool": tool,
            "session_id": payload.get("session_id", ""),
            "reason": rejection.get("reason", "unknown"),
            "missing_or_invalid": rejection.get("missing_or_invalid", []),
            "payload_sha256": payload_sha,
        }

        rejections_path = Path(channel_dir) / "rejections.jsonl"
        # O_APPEND is atomic on POSIX for writes within the pipe buffer
        # (~4 KB), which a single JSON line satisfies.
        line = json.dumps(record, sort_keys=True) + "\n"
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(rejections_path, flags, 0o644)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001
        pass  # fire-and-forget; never raise into caller
