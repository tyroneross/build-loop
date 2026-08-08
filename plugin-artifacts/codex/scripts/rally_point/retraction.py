# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rally Point retraction — neutralize a posted fact without mutating the log.

The problem this closes: the change log is append-only and immutable by
construction (``changes.py`` exposes no rewrite/delete/truncate entry point,
enforced by ``test_no_mutation_api``). So an accidental or incorrect fact had
no remedy — the only recourse was posting a free-text corrective message that
NO reader interpreted, which meant the wrong fact kept being re-surfaced to
every peer and into every LLM context, forever.

The mechanism keeps immutability and adds a remedy: a **retraction is itself an
appended record** naming the fact it withdraws. Nothing on disk is ever
rewritten. Readers resolve retractions at read time — the retracted record is
dropped from the surfaced stream while the retraction record survives, so the
correction stays auditable and peers can see that the fact was withdrawn and
why.

Record shape (build-loop kind ``retract``)::

    subject : "retract: <target-event-id>"
    payload : {retracts, reason, superseded_by?, subject, summary}
    summary : "<reason> [retracts=<id>][ superseded_by=<id>]"

The marker is encoded redundantly into ``subject``/``summary`` on purpose. The
canonical rally binary keeps its own fixed fact schema — it remaps unknown
kinds (``retract`` → ``artifact``) and drops unknown payload keys — so the
free-text fields are the only carrier that survives a round-trip through the
native store. ``_is_status_record`` in ``agent_rally.py`` uses the same
dual-detection trick for the same reason.

``superseded_by`` is optional and additive: a retraction may simply withdraw a
fact, or withdraw it AND point at the corrected fact that replaces it.

Pure / stdlib-only. Never imports agent-rally-point.
"""
from __future__ import annotations

import re

RETRACT_KIND = "retract"

# "retract: <target>" as the leading token of a subject. Anchored so an ordinary
# fact merely *discussing* retraction (e.g. "how do we retract: a design note")
# is not mistaken for one — the target must be a single bare token.
_SUBJECT_RE = re.compile(r"^\s*retract:\s*(?P<target>\S+)\s*$")
_SUPERSEDED_RE = re.compile(r"\bsuperseded_by=(?P<target>[^\s\]]+)")
_RETRACTS_RE = re.compile(r"\bretracts=(?P<target>[^\s\]]+)")


def subject_for(target: str) -> str:
    """Return the canonical retraction subject for ``target``."""
    return f"retract: {target}"


def summary_for(*, target: str, reason: str, superseded_by: str | None = None) -> str:
    """Return the free-text summary that survives the native store round-trip."""
    text = (reason or "").strip() or "retracted"
    text = f"{text} [retracts={target}"
    if superseded_by:
        text = f"{text} superseded_by={superseded_by}"
    return f"{text}]"


def build_payload(
    *,
    target: str,
    reason: str,
    superseded_by: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Build the ``post(kind="retract", payload=...)`` payload for ``target``.

    Structured keys are for build-loop's own fact.v1 read-back; ``subject`` and
    ``summary`` are the redundant carriers that survive the native store.
    """
    payload: dict = {
        "retracts": target,
        "reason": (reason or "").strip(),
        "subject": subject_for(target),
        "summary": summary_for(
            target=target, reason=reason, superseded_by=superseded_by
        ),
    }
    if superseded_by:
        payload["superseded_by"] = superseded_by
    if session_id:
        payload["session_id"] = session_id
    return payload


def _text_fields(record: dict) -> tuple[str, str]:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    subject = record.get("subject") or payload.get("subject") or ""
    summary = payload.get("summary") or payload.get("reason") or record.get("summary") or ""
    return str(subject), str(summary)


def target_of(record) -> str | None:
    """Return the event id this record retracts, or None if it is not a retraction.

    Detection is cross-store by design:
      1. structured ``payload.retracts`` (build-loop fallback / fact.v1 read-back);
      2. the ``retract: <id>`` subject (survives the native store);
      3. a ``retracts=<id>`` token in the summary (last-resort carrier).
    """
    if not isinstance(record, dict):
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    explicit = payload.get("retracts")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    subject, summary = _text_fields(record)
    match = _SUBJECT_RE.match(subject)
    if match:
        return match.group("target")
    match = _RETRACTS_RE.search(summary)
    return match.group("target") if match else None


def is_retraction(record) -> bool:
    """Return True if ``record`` is a retraction record (even a targetless one)."""
    if not isinstance(record, dict):
        return False
    if record.get("kind") == RETRACT_KIND:
        return True
    return target_of(record) is not None


def superseded_by_of(record) -> str | None:
    """Return the replacement event id a retraction points at, or None."""
    if not isinstance(record, dict):
        return None
    payload = record.get("payload")
    if isinstance(payload, dict):
        value = payload.get("superseded_by")
        if isinstance(value, str) and value.strip():
            return value.strip()
    _subject, summary = _text_fields(record)
    match = _SUPERSEDED_RE.search(summary)
    return match.group("target") if match else None


def _clean_reason(record: dict) -> str:
    """Return the human-facing reason, without the machine marker suffix.

    On a native round-trip the structured ``payload.reason`` is dropped and the
    reader reconstructs it from ``summary`` — which still carries the trailing
    ``[retracts=... superseded_by=...]`` block the emitter appended. Strip it so
    a surfaced reason reads as prose rather than as wire format.
    """
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    reason = payload.get("reason") or payload.get("summary") or ""
    return re.sub(r"\s*\[retracts=[^\]]*\]\s*$", "", str(reason)).strip()


def index(records) -> dict:
    """Return ``{target_event_id: retraction_info}`` for ``records``.

    A target retracted more than once keeps the LAST retraction — a later
    correction supersedes an earlier one, matching the append-only reading of
    the log. ``retraction_info`` carries ``retracted_by`` / ``reason`` /
    ``superseded_by`` / ``ts`` / ``event_id`` so a surface can explain the
    withdrawal without re-reading the log.
    """
    out: dict = {}
    for record in records or ():
        target = target_of(record)
        if not target:
            continue
        out[target] = {
            "retracted_by": record.get("tool") or "unknown",
            "reason": _clean_reason(record),
            "superseded_by": superseded_by_of(record),
            "ts": record.get("ts"),
            "event_id": record.get("event_id"),
            "revision": record.get("revision"),
        }
    return out


def apply(records, *, include_retracted: bool = False) -> list:
    """Return ``records`` with retracted records resolved.

    Default: retracted records are dropped from the returned stream and the
    retraction records themselves are kept, so a reader sees the correction but
    not the withdrawn claim. ``include_retracted=True`` keeps every record and
    annotates the retracted ones with ``_retracted`` instead — the audit view.

    Never mutates the input records (annotation copies) and never touches disk.
    Resolution is batch-scoped: a retraction can only neutralize a target that
    is present in the same batch. A tail read whose window contains only the
    retraction still surfaces it as a new record, which is the correct signal —
    a fact a peer already consumed cannot be un-read.
    """
    rows = list(records or ())
    idx = index(rows)
    if not idx:
        return rows
    out: list = []
    for record in rows:
        if is_retraction(record):
            out.append(record)  # a retraction is never itself suppressed
            continue
        event_id = record.get("event_id") if isinstance(record, dict) else None
        info = idx.get(event_id) if event_id else None
        if info is None:
            out.append(record)
            continue
        if include_retracted:
            annotated = dict(record)
            annotated["_retracted"] = info
            out.append(annotated)
    return out
