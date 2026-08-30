# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Canonical "post a change" helper — single operation that does the right thing.

The bug this prevents: callers who do `append_change(...)` and forget the
subsequent `bump_revision(...)` leave the channel in a state where
`checkpoint_read(...)` returns `changed: false` for peer consumers because
the current revision still matches their cursor. The change record IS in
the changes.jsonl file, but no consumer ever notices.

This was hit in the 2026-05-20 Step 0 bootstrap dogfood. Codex's
verifier-role observation surfaced the gap. This helper bakes in the
canonical pattern so future callers can't repeat the mistake.

Usage:

    from scripts.rally_point.post import post

    post(
        channel_dir=...,
        kind="feedback",
        tool="codex",
        model="gpt-5",
        run_id="...",
        app_slug="build-loop",
        payload={"step": 4, "verdict": "PASS", ...},
    )

Behavior:
    1. With an operational standalone Rally room, delegate to ``rally say``;
       Build Loop never creates a shadow ``changes.jsonl`` inside ``.rally``.
    2. Otherwise, compute the next Build Loop revision and append one
       ``agent-rally.fact.v1`` record to the shared Build-Loop-only spool.
    3. When Rally returns, discovery replays that spool idempotently before
       the next native write.

The local fallback allocates the revision and appends the record under one
writer lock, then publishes the revision. Readers therefore cannot observe a
new revision before its corresponding record is durable.

Fire-and-forget like the underlying primitives. Errors are swallowed
(caller can't be blocked by a coordination write).
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

try:  # package import
    from .build_loop_id import rally_fields_for
    from .producer_metadata import producer_metadata
    from .revision import bump_revision
except ImportError:  # script import (sys.path-inserted, no parent package)
    from build_loop_id import rally_fields_for  # type: ignore
    from producer_metadata import producer_metadata  # type: ignore
    from revision import bump_revision  # type: ignore


def _terminal_closeout_ready(workdir: Path | None, run_id: str) -> bool:
    """Gate the canonical terminal phase post on verified branch hygiene."""
    if workdir is None or not run_id:
        return False
    try:
        try:
            from scripts.branch_closeout_gate import check_branch_closeout
        except ImportError:
            from branch_closeout_gate import check_branch_closeout  # type: ignore
        return bool(check_branch_closeout(workdir, run_id).get("ready"))
    except Exception:
        return False


def _record_outcome(
    outcome: dict[str, Any] | None,
    *,
    status: str,
    backend: str | None = None,
    transport: str | None = None,
    revision: int | None = None,
    reason: str | None = None,
) -> None:
    """Expose the actual write route without changing post's scalar API."""
    if outcome is None:
        return
    outcome.clear()
    outcome.update(
        {
            "status": status,
            "backend": backend,
            "transport": transport,
            "revision": revision,
            "reason": reason,
        }
    )


def _local_identity_payload(
    payload: dict,
    *,
    local_tool: str | None,
    local_session_id: str | None,
) -> dict:
    """Return a fallback payload using Build Loop's base identity semantics."""
    if local_tool is None and local_session_id is None:
        return payload
    normalized = dict(payload or {})
    if local_session_id is not None:
        normalized["session_id"] = local_session_id
    heartbeat = normalized.get("task_heartbeat")
    if isinstance(heartbeat, dict):
        heartbeat = dict(heartbeat)
        if local_tool is not None:
            heartbeat["tool"] = local_tool
        if local_session_id is not None:
            heartbeat["session_id"] = local_session_id
        normalized["task_heartbeat"] = heartbeat
    return normalized


def post(
    *,
    channel_dir: Path,
    kind: str,
    tool: str,
    model: str,
    run_id: str,
    app_slug: str,
    payload: dict,
    workdir: Path | None = None,
    outcome: dict[str, Any] | None = None,
    local_tool: str | None = None,
    local_session_id: str | None = None,
) -> int | None:
    """Bump revision + append a change record. Returns new revision on success, None on error.

    The canonical "I have something to tell peers" operation. Use this
    instead of calling ``append_change`` + ``bump_revision`` separately;
    the helper guarantees the canonical ordering and prevents the
    "appended without bumping" silent-no-op bug.

    β1: every outgoing record carries ``producer_metadata`` so peers can
    detect version skew + cache-vs-source drift across coding hosts.

    β1.2: when ``workdir`` is provided and the discovery bridge reports
    ``policy: "migration"`` with a populated ``legacy_channel_dir``
    distinct from ``channel_dir``, mirror-write the same record to the
    legacy channel. The mirror is fire-and-forget — any failure is
    swallowed and never affects the canonical write's return value. This
    keeps non-upgraded peers (e.g. a Codex poller still on the legacy
    channel) visible during the migration window.
    """
    try:
        if kind == "phase" and (payload or {}).get("phase") == "run-closeout":
            if not _terminal_closeout_ready(workdir, run_id):
                _record_outcome(outcome, status="blocked", reason="closeout-not-ready")
                return None
        d = Path(channel_dir)
        write_tool = tool
        write_payload = payload

        # Validate before ANY backend mutation. A malformed native handoff must
        # not create presence, claims, or a lead seat before being rejected.
        if kind == "handoff":
            try:  # package import
                from . import mece_gate
            except ImportError:  # script import
                import mece_gate  # type: ignore

            valid, rejection = mece_gate.validate_handoff(payload or {}, tool=tool)
            if not valid:
                rejection_dir: Path | None = None
                if workdir is not None:
                    try:
                        try:
                            from .discovery_bridge import resolve as _reject_resolve
                        except ImportError:
                            from discovery_bridge import resolve as _reject_resolve  # type: ignore
                        reject_envelope = _reject_resolve(workdir)
                        if reject_envelope.backend == "build-loop-local":
                            rejection_dir = Path(reject_envelope.channel_dir)
                    except Exception:
                        rejection_dir = None
                elif d.name != ".rally" and not _looks_like_rust_channel(d):
                    rejection_dir = d
                if rejection_dir is not None:
                    mece_gate.log_rejection(
                        rejection_dir,
                        kind=kind,
                        tool=tool,
                        rejection=rejection,
                        payload=payload or {},
                    )
                _record_outcome(outcome, status="rejected", reason="invalid-handoff")
                return None

        if workdir is not None:
            try:
                try:  # package import
                    from .discovery_bridge import resolve as _bridge_resolve
                except ImportError:  # script import
                    from discovery_bridge import resolve as _bridge_resolve  # type: ignore

                envelope = _bridge_resolve(workdir)
                if (
                    envelope.coordination_unavailable
                    or envelope.backend == "unavailable"
                ):
                    _record_outcome(
                        outcome,
                        status="refused",
                        backend="unavailable",
                        transport="none",
                        reason=(
                            envelope.coordination_unavailable
                            or "coordination-unavailable"
                        ),
                    )
                    return None
                if envelope.transport == "rally-cli":
                    # Zero-seam transition: on the first coordination write after a
                    # native rally binary owns the channel, replay any stranded
                    # global fact.v1 fallback store into the rally ledger (lossless +
                    # idempotent). Fire-and-forget; never blocks this post.
                    try:
                        try:  # package import
                            from .discovery_bridge import maybe_auto_migrate
                        except ImportError:  # script import
                            from discovery_bridge import maybe_auto_migrate  # type: ignore
                        maybe_auto_migrate(workdir, envelope)
                    except Exception:
                        pass
                    try:
                        from .backend_adapter import BackendContext
                    except ImportError:
                        from backend_adapter import BackendContext  # type: ignore
                    native_context = BackendContext(
                        workdir=Path(workdir).expanduser().resolve(),
                        envelope=envelope,
                        local_channel_dir=_build_loop_fallback_channel(workdir),
                    )
                    native_result = _post_via_repo_local_rally(
                        context=native_context,
                        kind=kind,
                        tool=tool,
                        model=model,
                        run_id=run_id,
                        app_slug=app_slug,
                        payload=payload,
                    )
                    if native_result.ok and native_result.revision is not None:
                        _record_outcome(
                            outcome,
                            status="posted",
                            backend="rally",
                            transport="rally-cli",
                            revision=native_result.revision,
                            # A successful post is exactly where a kind demotion
                            # happens, so the reason must survive the success arm.
                            reason=native_result.reason,
                        )
                        if outcome is not None and native_result.event_id:
                            outcome["event_id"] = native_result.event_id
                        return native_result.revision
                    if native_result.precommit_unavailable:
                        # Only proven before-spawn absence authorizes a second
                        # backend. Timeouts/nonzero/malformed/oversize replies
                        # do not prove that Rally failed before commit.
                        d = _build_loop_fallback_channel(workdir)
                        write_tool = local_tool if local_tool is not None else tool
                        write_payload = _local_identity_payload(
                            payload,
                            local_tool=local_tool,
                            local_session_id=local_session_id,
                        )
                    else:
                        _record_outcome(
                            outcome,
                            status=native_result.status,
                            backend="rally",
                            transport="rally-cli",
                            revision=native_result.revision,
                            reason=native_result.reason,
                        )
                        if outcome is not None:
                            if native_result.event_id:
                                outcome["event_id"] = native_result.event_id
                            if native_result.remedy:
                                outcome["remedy"] = native_result.remedy
                        return None
                elif envelope.backend == "build-loop-local":
                    # The resolver owns routing. Never let a stale/arbitrary
                    # caller-supplied path override the selected backend.
                    d = Path(envelope.channel_dir)
                    write_tool = local_tool if local_tool is not None else tool
                    write_payload = _local_identity_payload(
                        payload,
                        local_tool=local_tool,
                        local_session_id=local_session_id,
                    )
                else:
                    _record_outcome(
                        outcome,
                        status="refused",
                        backend=envelope.backend,
                        transport=envelope.transport,
                        reason="unsupported coordination backend",
                    )
                    return None
            except Exception as exc:
                _record_outcome(
                    outcome,
                    status="failed",
                    reason=f"backend resolution failed: {exc}",
                )
                return None

        # Without a workdir the adapter cannot resolve or authenticate the
        # owning backend. Never infer permission to append Build Loop files to
        # a standalone Rally directory from its current on-disk contents: a
        # healthy room may contain only facts.db + log segments and none of the
        # historic marker files checked by _looks_like_rust_channel().
        if workdir is None and (_is_within_dot_rally(d) or _looks_like_rust_channel(d)):
            _record_outcome(
                outcome,
                status="refused",
                reason="standalone-rally-requires-workdir",
            )
            return None

        d.mkdir(parents=True, exist_ok=True)

        # Local-fallback writes now emit the agent-rally.fact.v1 shape so the
        # store is losslessly ingestible by ``rally migrate-legacy`` (which
        # silently skips any non-fact.v1 line). Build-loop's own readers consume
        # it back through the single ``changes.read_changes_since`` →
        # ``changes.normalize_record`` chokepoint; build-loop-private signal
        # (revision, payload, producer metadata) rides along as additive bl_*
        # keys that ARP ignores (no deny_unknown_fields).
        try:  # package import
            from .fact_v1 import append_fact_v1_transaction, to_fact_v1
        except ImportError:  # script import
            from fact_v1 import append_fact_v1_transaction, to_fact_v1  # type: ignore

        # Two orthogonal identity axes, kept as SEPARATE dicts: producer =
        # runtime identity (what code/version is writing), build_loop fields =
        # per-run identity (which build-loop run this is). Merging them (the
        # prior ``producer.update(...)``) nested the run-identity fields inside
        # bl_producer, so normalize never recovered them top-level. to_fact_v1
        # now stores each in its own bl_* key and normalize splices both back.
        producer = producer_metadata()
        build_loop_fields = rally_fields_for(workdir)
        transaction = append_fact_v1_transaction(
            d,
            lambda new_rev: to_fact_v1(
                kind=kind,
                tool=write_tool,
                model=model,
                run_id=run_id,
                app_slug=app_slug,
                payload=write_payload,
                revision=new_rev,
                producer=producer,
                build_loop_fields=build_loop_fields,
            ),
        )
        if transaction is None:
            _record_outcome(
                outcome,
                status="failed",
                backend="build-loop-local",
                transport="fact-v1",
                reason="fallback fact append was not durably committed",
            )
            return None
        new_rev, fact = transaction
        if kind == "phase" and (write_payload or {}).get("phase") == "rally-start":
            try:
                try:  # package import
                    from . import rally
                    from .changes import normalize_record
                except ImportError:  # script import
                    import rally  # type: ignore
                    from changes import normalize_record  # type: ignore

                # write_current expects the legacy reader shape (payload+revision);
                # reuse the read chokepoint to convert the fact.v1 record.
                rally.write_current(d, normalize_record(fact))
            except Exception:
                pass

        # β1.2: dual-write mirror to legacy channel during migration.
        # Fire-and-forget — mirror failure NEVER blocks or invalidates
        # the canonical write that just succeeded above.
        if workdir is not None:
            try:
                try:  # package import
                    from .discovery_bridge import resolve as _bridge_resolve
                except ImportError:  # script import
                    from discovery_bridge import resolve as _bridge_resolve  # type: ignore

                envelope = _bridge_resolve(workdir)
                legacy = envelope.legacy_channel_dir
                if (
                    envelope.policy == "migration"
                    and legacy
                    and str(Path(legacy).resolve()) != str(d.resolve())
                ):
                    legacy_dir = Path(legacy)
                    legacy_dir.mkdir(parents=True, exist_ok=True)
                    # The migration window exists for NON-UPGRADED peers (e.g. a
                    # Codex poller predating the fact.v1 emitter) reading the
                    # legacy channel. Those raw readers expect the legacy
                    # ``{ts, kind, tool, model, run_id, app_slug, payload, revision}``
                    # shape and KeyError on a fact.v1 line, so mirror the
                    # NORMALIZED (legacy-shaped) record — not the fact.v1 fact.
                    # The canonical store above stays fact.v1; only the legacy
                    # mirror is down-converted. normalize_record is the same
                    # read chokepoint build-loop's own readers use, so the
                    # mirrored line is bit-identical to what they'd reconstruct.
                    try:  # package import
                        from .changes import (
                            append_change as _legacy_append,
                            normalize_record as _legacy_normalize,
                        )
                    except ImportError:  # script import
                        from changes import (  # type: ignore
                            append_change as _legacy_append,
                            normalize_record as _legacy_normalize,
                        )
                    # Bump legacy's revision so its readers see a fresh signal.
                    bump_revision(legacy_dir)
                    _legacy_append(legacy_dir, _legacy_normalize(fact))
            except Exception:
                # Fire-and-forget per protocol; mirror failure is silent.
                pass

        _record_outcome(
            outcome,
            status="posted",
            backend="build-loop-local",
            transport="fact-v1",
            revision=new_rev,
        )
        return new_rev
    except Exception as exc:
        # Fire-and-forget per protocol; never raise into the caller.
        _record_outcome(outcome, status="failed", reason=str(exc))
        return None


def _looks_like_rust_channel(channel_dir: Path) -> bool:
    return (
        (channel_dir / "rally.tail.json").exists()
        or (channel_dir / "rally.checkpoint.json").exists()
        or (channel_dir / "rally.lock").exists()
    )


def _is_within_dot_rally(path: Path) -> bool:
    """Return True for ``.rally`` itself, descendants, and symlinks into it."""
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except OSError:
        resolved = Path(path).expanduser().absolute()
    return ".rally" in resolved.parts


def _build_loop_fallback_channel(workdir: Path) -> Path:
    """Return the shared Build-Loop-only coordination spool for this repo."""
    try:  # package import
        from . import channel_paths
    except ImportError:  # script import
        import channel_paths  # type: ignore
    return channel_paths.fallback_channel_dir(
        workdir, channel_paths.app_slug(workdir)
    )


def _post_via_repo_local_rally(
    *,
    context: Any,
    kind: str,
    tool: str,
    model: str,
    run_id: str,
    app_slug: str,
    payload: dict,
) -> Any:
    try:  # package import
        from .backend_adapter import (
            NativeResult,
            _committed_fact,
            invoke_native,
            is_synthetic_service_tool,
        )
        from .payload_codec import encode_event, has_oversize_marker
    except ImportError:  # script import
        from backend_adapter import (  # type: ignore
            NativeResult,
            _committed_fact,
            invoke_native,
            is_synthetic_service_tool,
        )
        from payload_codec import encode_event, has_oversize_marker  # type: ignore
    # Legacy and test envelopes may omit ``raw``; an unknown binary just means
    # the gate fails open to the static mapping, never a crash on this path.
    raw_envelope = getattr(context.envelope, "raw", None)
    binary_value = (
        raw_envelope.get("rally_binary") if isinstance(raw_envelope, dict) else None
    )
    native_kind, degraded_reason = _negotiated_native_kind(
        kind, str(binary_value) if binary_value else None
    )
    subject = _bounded_text(_native_subject(kind, payload), 512)
    cmd = [
        "say",
        native_kind,
        "--json",
        "--tool",
        tool,
        "--subject",
        subject,
    ]
    if run_id:
        cmd.extend(["--run", run_id])
    summary = (payload or {}).get("summary") or (payload or {}).get("reason")
    if summary:
        cmd.extend(["--summary", _bounded_text(summary, 2048)])
    target = (payload or {}).get("to") or (payload or {}).get("to_tool")
    if target:
        cmd.extend(["--to", _bounded_text(target, 256)])
    status = (payload or {}).get("status") or (payload or {}).get("verdict")
    if status:
        cmd.extend(["--status", _bounded_text(status, 128)])
    severity = (payload or {}).get("severity")
    if severity:
        cmd.extend(["--severity", _bounded_text(severity, 128)])
    for path in _payload_paths(payload):
        cmd.extend(["--path", path])
    evidence = encode_event(
        kind=kind,
        payload=payload,
        model=model,
        run_id=run_id,
        app_slug=app_slug,
    )
    if has_oversize_marker(evidence):
        return NativeResult(
            "oversize",
            reason="payload exceeds lossless native evidence boundary",
        )
    for item in evidence:
        cmd.extend(["--evidence", item])
    session_id = str((payload or {}).get("session_id") or "") or None
    if is_synthetic_service_tool(tool):
        return NativeResult(
            "rejected",
            reason="synthetic service actor cannot mutate native Rally; use the host actor",
        )
    result = invoke_native(
        context,
        cmd,
        expected_schema="agent-rally.command.say.v1",
        tool=tool,
        session_id=session_id,
        mutating=True,
    )
    if result.status == "partial_commit":
        fact = _committed_fact(
            result.payload,
            kind=native_kind,
            tool=tool,
            session_id=session_id,
            subject=subject,
            evidence=evidence,
        )
        if fact is not None:
            revision_value = fact.get("seq")
            revision = (
                revision_value
                if type(revision_value) is int and revision_value > 0
                else None
            )
            return NativeResult(
                "ok",
                payload=result.payload,
                returncode=result.returncode,
                reason="Rally say fact committed; later projection work was partial",
                revision=revision,
                event_id=str(fact.get("event_id") or "") or None,
                backend="rally",
                transport="rally-cli",
            )
    return _with_degradation_reason(result, degraded_reason)


def _with_degradation_reason(result: Any, degraded_reason: str | None) -> Any:
    """Record a kind demotion on the result so it is never silent."""
    if not degraded_reason:
        return result
    merged = (
        f"{result.reason}; {degraded_reason}" if result.reason else degraded_reason
    )
    return dataclasses.replace(result, reason=merged)


def _native_kind(kind: str, binary: str | None = None) -> str:
    """Map a build-loop kind onto rally's native positional.

    ``binary`` is the resolved ``rally`` path. When given, the static mapping
    below is gated against the vocabulary that binary actually accepts, so a
    kind build-loop learned before the installed binary did degrades instead of
    being rejected at the wire. ``binary=None`` (the local fact.v1 fallback path,
    which shells out to nothing) preserves the static mapping verbatim.
    """
    return _negotiated_native_kind(kind, binary)[0]


def _negotiated_native_kind(
    kind: str, binary: str | None = None
) -> tuple[str, str | None]:
    """Return ``(native_kind, degraded_reason)`` — see ``_native_kind``."""
    native = _static_native_kind(kind)
    if not binary:
        return native, None
    try:  # package import
        from . import kind_capability
    except ImportError:  # script import
        import kind_capability  # type: ignore
    return kind_capability.negotiate_kind(native, binary)


def _static_native_kind(kind: str) -> str:
    supported = {
        "claim",
        "release",
        "blocker",
        "resolve",
        "decision",
        "artifact",
        "handoff",
        "risk",
        "lesson",
        "session",
        "wake",
        "standby",
        "presence",
        "backlog-item",
        "mission",
    }
    if kind in supported:
        return kind
    if kind == "phase":
        return "presence"
    if kind in {"feedback", "message", "dep-change", "arch-scan-complete"}:
        return "artifact"
    if kind == "escalation":
        return "risk"
    return "artifact"


def _native_subject(kind: str, payload: dict) -> str:
    payload = payload or {}
    subject = payload.get("subject") or payload.get("message")
    if subject:
        return str(subject)
    if kind == "phase" and payload.get("phase"):
        return f"phase: {payload['phase']}"
    return kind


def _payload_paths(payload: dict) -> list[str]:
    payload = payload or {}
    out: list[str] = []
    for key in ("path", "paths", "files"):
        value = payload.get(key)
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, list):
            out.extend(str(item) for item in value if item)
    ownership = payload.get("ownership")
    if isinstance(ownership, dict):
        owns = ownership.get("owns")
        if isinstance(owns, list):
            out.extend(str(item) for item in owns if item)
    # Exact unbounded data remains in the authenticated event evidence. Keep
    # the native indexing projection within Rally's aggregate fact-text bound.
    return [_bounded_text(item, 512) for item in out[:16]]


def _bounded_text(value: Any, max_bytes: int) -> str:
    raw = str(value).encode("utf-8")
    if len(raw) <= max_bytes:
        return raw.decode("utf-8")
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def _native_seq(out: dict) -> int | None:
    try:
        seq = (((out.get("data") or {}).get("say") or {}).get("fact") or {}).get("seq")
    except (AttributeError, TypeError):
        return None
    if isinstance(seq, bool) or not isinstance(seq, int) or seq <= 0:
        return None
    return seq
