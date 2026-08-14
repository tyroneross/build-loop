# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Single backend boundary for standalone Rally and Build Loop fallback state.

Healthy native rooms are accessed only through the ``rally`` CLI.  Embedded
presence, inbox, heartbeat, cursor, watcher, and JSONL helpers receive only the
Build-Loop-owned local channel returned by :func:`resolve_context`.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:  # package import
    from . import channel_paths
    from .discovery_bridge import DiscoveryEnvelope, repo_local_rally_binary, resolve
except ImportError:  # script import
    import channel_paths  # type: ignore
    from discovery_bridge import (  # type: ignore
        DiscoveryEnvelope,
        repo_local_rally_binary,
        resolve,
    )


@dataclass(frozen=True)
class BackendContext:
    workdir: Path
    envelope: DiscoveryEnvelope
    local_channel_dir: Path

    @property
    def native(self) -> bool:
        return (
            self.envelope.backend == "rally"
            and self.envelope.transport == "rally-cli"
            and not self.envelope.coordination_unavailable
        )


@dataclass(frozen=True)
class NativeResult:
    status: str
    payload: dict[str, Any] | None = None
    returncode: int | None = None
    reason: str | None = None
    revision: int | None = None
    event_id: str | None = None
    remedy: str | None = None
    backend: str | None = None
    transport: str | None = None
    retryable_reconciliation: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def precommit_unavailable(self) -> bool:
        return self.status == "unavailable"


@dataclass(frozen=True)
class _NativeClaimPlan:
    requested_paths: tuple[str, ...]
    add_paths: tuple[str, ...]
    release_event_ids: tuple[str, ...]
    requested_count: int
    reused_count: int


def resolve_context(workdir: Path | str) -> BackendContext:
    wd = Path(workdir).expanduser().resolve()
    envelope = resolve(wd)
    return BackendContext(
        workdir=wd,
        envelope=envelope,
        local_channel_dir=channel_paths.fallback_channel_dir(wd, envelope.app_slug),
    )


def watcher_dir(context: BackendContext) -> Path:
    """Build Loop watcher process metadata never belongs to standalone Rally."""
    return context.local_channel_dir / "watchers"


def stable_session_id(
    workdir: Path | str,
    tool: str,
    explicit: str | None = None,
) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    configured = os.environ.get("BUILD_LOOP_RALLY_SESSION_ID")
    if configured and configured.strip():
        return configured.strip()
    inherited = os.environ.get("RALLY_SESSION_ID")
    if inherited and inherited.strip():
        return inherited.strip()
    wd = str(Path(workdir).expanduser().resolve())
    safe_tool = "".join(
        char if char.isalnum() or char in "-_." else "-" for char in tool
    ).strip("-")[:40] or "build-loop"
    digest = hashlib.sha256(f"{wd}\x1f{tool}".encode("utf-8")).hexdigest()[:12]
    return f"build-loop-{safe_tool}-{digest}"


def _native_env(workdir: Path, tool: str, session_id: str | None) -> dict[str, str]:
    env = dict(os.environ)
    env["RALLY_SESSION_ID"] = stable_session_id(workdir, tool, session_id)
    # Kept for Rally versions that predate managed-session precedence.
    env.setdefault("RALLY_OBSERVER_PID", str(os.getppid()))
    return env


def _walk(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _positive_int(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _session_id_matches(actual: Any, expected: str | None) -> bool:
    """Compare raw host ids with Rally's canonical managed-session identity."""
    if expected is None:
        return True
    if not isinstance(actual, str):
        return False

    prefix = "sess:managed:"

    def raw(value: str) -> str:
        if value.startswith(prefix) and "#" in value[len(prefix):]:
            return value[len(prefix):].rsplit("#", 1)[0]
        return value

    if expected.startswith(prefix):
        # A canonical identity includes the generation/liveness suffix. Never
        # collapse ``#old`` and ``#live`` into the same proof.
        return actual == expected
    return actual == expected or raw(actual) == expected


def _container_fact(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    container = data.get(name)
    fact = container.get("fact") if isinstance(container, dict) else None
    return fact if isinstance(fact, dict) else None


def _flag_value(argv: list[str], flag: str) -> str | None:
    try:
        index = argv.index(flag)
    except ValueError:
        return None
    return argv[index + 1] if index + 1 < len(argv) else None


def _flag_values(argv: list[str], flag: str) -> list[str]:
    values: list[str] = []
    for index, value in enumerate(argv[:-1]):
        if value == flag:
            values.append(argv[index + 1])
    return values


_STATUS_MARKER_FLAGS = (
    ("--state", "state"),
    ("--file", "file"),
    ("--intent", "intent"),
    ("--blocked-ref", "ref"),
    ("--wake-after", "wake_after"),
    ("--committed-sha", "committed_sha"),
    ("--worktree-branch", "worktree_branch"),
)


def _status_fact_matches(fact: Any, argv: list[str]) -> bool:
    """Require every caller-supplied status marker in the canonical fact.

    Rally may auto-fill omitted ``done`` metadata, so the receipt can contain
    more markers than the request.  Every marker the caller did provide must
    still survive exactly; a state-prefix match alone is not mutation proof.
    """
    if not isinstance(fact, dict):
        return False
    subject = fact.get("subject")
    if not isinstance(subject, str):
        return False
    markers: dict[str, str] = {}
    for segment in subject.split("|"):
        key, separator, value = segment.strip().partition("=")
        if separator:
            markers[key.strip()] = value.strip()
    for flag, marker in _STATUS_MARKER_FLAGS:
        value = _flag_value(argv, flag)
        if value is not None and markers.get(marker) != value:
            return False
    return True


def _normalized_file_scope(workdir: Path, path: str) -> str | None:
    """Mirror Rally's lexical file-scope normalization for repo-local paths."""
    raw = str(path).removeprefix("file:")
    if not raw or "\x00" in raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(workdir)
        except ValueError:
            return None
    parts: list[str] = []
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        return None
    return f"file:{Path(*parts).as_posix()}"


def _retraction_summary(target: str, reason: str, superseded_by: str | None) -> str:
    trimmed = reason.strip() or "retracted"
    marker = f"retracts={target}"
    if superseded_by:
        marker += f" superseded_by={superseded_by}"
    return f"{trimmed} [{marker}]"


def _retraction_fact_matches(
    fact: Any,
    *,
    target: str,
    tool: str,
    reason: str,
    superseded_by: str | None,
    session_id: str | None,
) -> bool:
    return bool(
        isinstance(fact, dict)
        and fact.get("kind") == "artifact"
        and fact.get("tool") == tool
        and fact.get("subject") == f"retract: {target}"
        and (fact.get("ref") == target or fact.get("ref_id") == target)
        and fact.get("status") == "retraction"
        and fact.get("summary")
        == _retraction_summary(target, reason, superseded_by)
        and _session_id_matches(fact.get("from_session_id"), session_id)
    )


def _lead_operation(argv: list[str]) -> str | None:
    return next(
        (
            value
            for value in argv[1:]
            if value in {"show", "assign", "handoff", "relinquish"}
        ),
        None,
    )


def _primary_fact(
    payload: dict[str, Any],
    argv: list[str],
    *,
    tool: str,
    session_id: str | None,
    workdir: Path | None = None,
) -> dict[str, Any] | None:
    """Return only the canonical fact proving this exact requested mutation."""
    if not argv:
        return None
    command = argv[0]
    fact: dict[str, Any] | None
    if command == "say" and len(argv) > 1:
        fact = _container_fact(payload, "say")
        if not isinstance(fact, dict):
            return None
        if fact.get("kind") != argv[1] or fact.get("tool") != tool:
            return None
        subject = _flag_value(argv, "--subject")
        if subject is not None and fact.get("subject") != subject:
            return None
        evidence = _flag_values(argv, "--evidence")
        actual_evidence = fact.get("evidence", [])
        if not isinstance(actual_evidence, list):
            return None
        if argv[1] == "claim":
            # Rally adds lease/source-grounding evidence to claims. Prove every
            # caller marker while allowing only the authority's derived data.
            if any(item not in actual_evidence for item in evidence):
                return None
        elif actual_evidence != evidence:
            return None
        paths = _flag_values(argv, "--path")
        if paths:
            if workdir is None:
                return None
            expected_scopes = [_normalized_file_scope(workdir, path) for path in paths]
            if any(scope is None for scope in expected_scopes):
                return None
            actual_scopes = fact.get("scope")
            if not isinstance(actual_scopes, list) or any(
                not isinstance(scope, str) for scope in actual_scopes
            ):
                return None
            if argv[1] == "claim":
                if sorted(set(actual_scopes)) != sorted(set(expected_scopes)):
                    return None
            elif any(scope not in actual_scopes for scope in expected_scopes):
                return None
        ref_id = _flag_value(argv, "--ref")
        if ref_id is not None and fact.get("ref") != ref_id and fact.get("ref_id") != ref_id:
            return None
        standby_ref = _flag_value(argv, "--ref-standby")
        if (
            standby_ref is not None
            and fact.get("ref") != standby_ref
            and fact.get("ref_id") != standby_ref
        ):
            return None
        summary = str(fact.get("summary") or "")
        if argv[1] == "standby":
            reason = _flag_value(argv, "--reason")
            if reason is not None and f"reason:{reason}" not in summary:
                return None
            wake_after = _flag_value(argv, "--wake-after")
            if wake_after is not None and "wake_after:" not in summary:
                return None
        if not _session_id_matches(fact.get("from_session_id"), session_id):
            return None
        return fact
    if command == "enter":
        return _committed_fact(
            payload,
            kind="presence",
            tool=tool,
            session_id=session_id,
        )
    if command == "status" and "post" in argv:
        fact = _container_fact(payload, "status_post")
        if (
            not isinstance(fact, dict)
            or fact.get("kind") != "presence"
            or fact.get("tool") != tool
            or not _status_fact_matches(fact, argv)
            or not _session_id_matches(fact.get("from_session_id"), session_id)
        ):
            return None
        return fact
    if command == "retract":
        target = argv[1] if len(argv) > 1 else ""
        reason = _flag_value(argv, "--reason") or ""
        superseded_by = _flag_value(argv, "--superseded-by")
        data = payload.get("data")
        retract = data.get("retract") if isinstance(data, dict) else None
        if isinstance(retract, dict) and (
            retract.get("target") != target
            or retract.get("reason") != reason
            or retract.get("superseded_by") != superseded_by
            or retract.get("status") not in {"retracted", "noop_already_retracted"}
        ):
            return None
        fact = _container_fact(payload, "retract")
        if fact is None:
            fact = _committed_fact(
                payload,
                kind="artifact",
                tool=tool,
                session_id=session_id,
                subject=f"retract: {target}",
            )
        return fact if _retraction_fact_matches(
            fact,
            target=target,
            tool=tool,
            reason=reason,
            superseded_by=superseded_by,
            session_id=session_id,
        ) else None
    if command == "lead":
        operation = _lead_operation(argv)
        if operation == "show":
            return None
        data = payload.get("data")
        lead = data.get("lead") if isinstance(data, dict) else None
        if not isinstance(lead, dict) or lead.get("action") != operation:
            return None
        fact = _container_fact(payload, "lead")
        if fact is None:
            subject = (
                "role:lead:relinquished"
                if operation == "relinquish"
                else "role:lead"
            )
            fact = _committed_fact(
                payload,
                kind="decision",
                tool=tool,
                subject=subject,
            )
        expected_target = _flag_value(argv, "--to")
        expected_subject = (
            "role:lead:relinquished" if operation == "relinquish" else "role:lead"
        )
        expected_assigned = (
            "relinquished" if operation == "relinquish" else operation
        )
        if (
            not isinstance(fact, dict)
            or fact.get("kind") != "decision"
            or fact.get("tool") != tool
            or fact.get("subject") != expected_subject
            or fact.get("target") != expected_target
            or f"assigned:{expected_assigned}" not in fact.get("evidence", [])
            or lead.get("current_lead") != expected_target
        ):
            return None
        return fact
    if command == "next":
        return _committed_fact(payload, kind="read", tool=tool)
    return None


def _successful_enter_noop(
    payload: dict[str, Any], *, tool: str, session_id: str | None
) -> bool:
    """An idempotent enter may succeed without appending a new presence fact."""
    data = payload.get("data")
    enter = data.get("enter") if isinstance(data, dict) else None
    return bool(
        isinstance(enter, dict)
        and enter.get("tool") == tool
        and session_id is not None
        and _session_id_matches(enter.get("session_id"), session_id)
    )


def _retract_noop_assessment(
    payload: dict[str, Any], argv: list[str]
) -> tuple[bool, str | None]:
    """Assess an idempotent retraction without inventing prior-reason proof.

    Rally exposes the prior retraction id and replacement id, but its no-op
    envelope echoes the *newly requested* reason rather than returning the
    prior fact's reason.  Replacement identity is therefore provable; reason
    equality is not and must stay explicit in the result.
    """
    if not argv or argv[0] != "retract" or len(argv) < 2:
        return False, None
    data = payload.get("data")
    retract = data.get("retract") if isinstance(data, dict) else None
    requested_superseded_by = _flag_value(argv, "--superseded-by")
    structural_match = bool(
        isinstance(retract, dict)
        and retract.get("target") == argv[1]
        and retract.get("status") == "noop_already_retracted"
        and retract.get("reason") == (_flag_value(argv, "--reason") or "")
        and retract.get("superseded_by") == requested_superseded_by
        and isinstance(retract.get("prior_retraction"), str)
        and retract.get("prior_retraction")
    )
    if not structural_match:
        return False, None
    if retract.get("prior_superseded_by") != requested_superseded_by:
        return (
            False,
            "Rally retraction no-op named a different prior_superseded_by; "
            "the requested replacement is not proven",
        )
    return (
        True,
        "Rally proved the prior replacement id exactly; prior retraction reason "
        "equality is unprovable because the no-op receipt does not expose it",
    )


def _committed_fact(
    payload: dict[str, Any] | None,
    *,
    kind: str,
    tool: str | None = None,
    session_id: str | None = None,
    subject_prefix: str | None = None,
    subject: str | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any] | None:
    """Return a matching canonical fact from a partial-commit envelope."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    outcomes = data.get("append_outcomes") if isinstance(data, dict) else None
    if not isinstance(outcomes, list):
        return None
    for outcome in reversed(outcomes):
        fact = outcome.get("fact") if isinstance(outcome, dict) else None
        if not isinstance(fact, dict) or fact.get("kind") != kind:
            continue
        if tool is not None and fact.get("tool") != tool:
            continue
        if not _session_id_matches(fact.get("from_session_id"), session_id):
            continue
        if subject_prefix is not None and not str(fact.get("subject") or "").startswith(
            subject_prefix
        ):
            continue
        if subject is not None and fact.get("subject") != subject:
            continue
        if evidence is not None and fact.get("evidence") != evidence:
            continue
        return fact
    return None


def is_synthetic_service_tool(tool: str) -> bool:
    """Return whether ``tool`` is telemetry machinery, not a coding agent.

    Native Rally automatically creates presence and may assign the first actor
    as room lead. Service identities must therefore never mutate a native room;
    callers bind telemetry to the real Codex/Claude/Cursor host instead.
    """
    base = str(tool or "").split(":", 1)[0].replace("-", "_")
    return base in {"build_loop", "build_orchestrator"}


def _outcome_unknown(payload: dict[str, Any] | None) -> tuple[bool, str | None, str | None]:
    if not isinstance(payload, dict):
        return False, None, None
    command = payload.get("command")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    unknown = data.get("outcome_unknown")
    if command == "mutation_outcome_unknown" or unknown:
        source = unknown if isinstance(unknown, dict) else data
        event_id = source.get("event_id") if isinstance(source, dict) else None
        remedy = None
        if isinstance(source, dict):
            remedy = source.get("remedy") or source.get("query_remedy")
        return True, str(event_id) if event_id else None, str(remedy) if remedy else None
    for key, value in _walk(payload):
        if key == "code" and value == "outcome_unknown":
            event_id = next(
                (str(v) for k, v in _walk(payload) if k == "event_id" and v),
                None,
            )
            remedy = next(
                (
                    str(v)
                    for k, v in _walk(payload)
                    if k in {"remedy", "query_remedy"} and v
                ),
                None,
            )
            return True, event_id, remedy
    return False, None, None


def invoke_native(
    context: BackendContext,
    argv: list[str],
    *,
    expected_schema: str,
    tool: str,
    session_id: str | None = None,
    mutating: bool = False,
    timeout: float = 5,
) -> NativeResult:
    """Invoke Rally with typed delivery semantics.

    Only a process-spawn failure is proven pre-commit unavailability.  A
    timeout or completed nonzero mutation may already be durable and therefore
    never authorizes a second write to the fallback ledger.
    """
    if not context.native:
        return NativeResult("unavailable", reason="native backend not selected")
    if mutating and is_synthetic_service_tool(tool):
        return NativeResult(
            "rejected",
            reason="synthetic service actor cannot mutate native Rally; use the host actor",
        )
    binary_value = context.envelope.raw.get("rally_binary")
    binary = str(binary_value) if binary_value else None
    if not binary:
        return NativeResult(
            "unavailable",
            reason="discovery did not pin the validated Rally binary before spawn",
        )
    try:
        proc = subprocess.run(
            [binary, *argv],
            cwd=str(context.workdir),
            env=_native_env(context.workdir, tool, session_id),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except OSError as exc:
        return NativeResult("unavailable", reason=f"rally spawn failed: {exc}")
    except subprocess.TimeoutExpired as exc:
        status = "outcome_unknown" if mutating else "failed"
        return NativeResult(status, reason=f"rally timed out: {exc}")
    except subprocess.SubprocessError as exc:
        status = "outcome_unknown" if mutating else "failed"
        return NativeResult(status, reason=f"rally transport failed: {exc}")

    parsed: dict[str, Any] | None = None
    if proc.stdout.strip():
        try:
            candidate = json.loads(proc.stdout)
            parsed = candidate if isinstance(candidate, dict) else None
        except (TypeError, ValueError):
            parsed = None

    unknown, event_id, remedy = _outcome_unknown(parsed)
    if unknown:
        return NativeResult(
            "outcome_unknown",
            payload=parsed,
            returncode=proc.returncode,
            reason="native mutation outcome is unknown; locate before retry",
            event_id=event_id,
            remedy=remedy,
        )
    if (
        mutating
        and isinstance(parsed, dict)
        and parsed.get("product") == "rally"
        and parsed.get("command") == "partial_commit"
        and isinstance(parsed.get("data"), dict)
        and parsed["data"].get("committed") is True
    ):
        fact = _primary_fact(
            parsed,
            argv,
            tool=tool,
            session_id=session_id,
            workdir=context.workdir,
        )
        revision = _positive_int(fact.get("seq")) if fact else None
        committed_event_id = (
            str(fact.get("event_id")) if fact and fact.get("event_id") else None
        )
        detail = proc.stderr.strip() or str(parsed["data"].get("message") or "")
        return NativeResult(
            "partial_commit",
            payload=parsed,
            returncode=proc.returncode,
            reason=detail or "native command committed partially; do not retry wholesale",
            revision=revision,
            event_id=committed_event_id,
        )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        return NativeResult(
            "rejected" if mutating else "failed",
            payload=parsed,
            returncode=proc.returncode,
            reason=detail,
        )
    if (
        not isinstance(parsed, dict)
        or parsed.get("ok") is not True
        or parsed.get("product") != "rally"
        or parsed.get("schema") != expected_schema
    ):
        return NativeResult(
            "invalid",
            payload=parsed,
            returncode=proc.returncode,
            reason="invalid Rally success envelope",
        )
    fact = _primary_fact(
        parsed,
        argv,
        tool=tool,
        session_id=session_id,
        workdir=context.workdir,
    )
    revision = _positive_int(fact.get("seq")) if fact else None
    event_id = str(fact.get("event_id")) if fact and fact.get("event_id") else None
    retract_noop, retract_noop_reason = _retract_noop_assessment(parsed, argv)
    if (
        mutating
        and argv
        and argv[0] == "retract"
        and retract_noop_reason is not None
        and not retract_noop
    ):
        return NativeResult(
            "invalid",
            payload=parsed,
            returncode=proc.returncode,
            reason=retract_noop_reason,
        )
    mutation_noop = bool(
        mutating
        and (
            (
                argv
                and argv[0] == "enter"
                and _successful_enter_noop(parsed, tool=tool, session_id=session_id)
            )
            or retract_noop
        )
    )
    if mutating and revision is None and not mutation_noop:
        return NativeResult(
            "invalid",
            payload=parsed,
            returncode=proc.returncode,
            reason="Rally mutation success omitted a positive fact sequence",
        )
    return NativeResult(
        "ok",
        payload=parsed,
        returncode=proc.returncode,
        reason=retract_noop_reason if retract_noop else None,
        revision=revision,
        event_id=event_id,
    )


_FILES_IN_FLIGHT_CLAIM_SUBJECT = "Build Loop files_in_flight ownership"
_FILES_IN_FLIGHT_RELEASE_SUBJECT = "Build Loop files_in_flight ownership released"


def _claim_fact(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    fact = item.get("fact")
    return fact if isinstance(fact, dict) else item


def _native_claim_plan(
    context: BackendContext,
    *,
    tool: str,
    session_id: str,
    paths: Iterable[str],
) -> tuple[_NativeClaimPlan | None, NativeResult | None]:
    """Read authority state before planning exact files-in-flight ownership."""
    requested: dict[str, str] = {}
    for raw_value in paths:
        raw = str(raw_value)
        scope = _normalized_file_scope(context.workdir, raw)
        if scope is None:
            return None, NativeResult(
                "rejected",
                reason=f"files_in_flight path is not a repo-local file: {raw!r}",
                backend="rally",
                transport="rally-cli",
            )
        requested.setdefault(scope, raw)

    snapshot = room_snapshot(context, actor=tool, readers=False)
    if not snapshot.ok:
        return None, snapshot
    summary = native_room_summary(snapshot)
    claims = summary.get("active_claims")
    if not isinstance(claims, list):
        return None, NativeResult(
            "invalid",
            payload=snapshot.payload,
            returncode=snapshot.returncode,
            reason="Rally room success omitted active_claims; ownership cannot be reconciled",
            backend="rally",
            transport="rally-cli",
        )

    owned_scopes: set[str] = set()
    managed_by_scope: dict[str, list[dict[str, Any]]] = {}
    for item in claims:
        fact = _claim_fact(item)
        if (
            not isinstance(fact, dict)
            or fact.get("kind") != "claim"
            or fact.get("tool") != tool
            or not _session_id_matches(fact.get("from_session_id"), session_id)
        ):
            continue
        scopes = fact.get("scope")
        if not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes):
            return None, NativeResult(
                "invalid",
                payload=snapshot.payload,
                returncode=snapshot.returncode,
                reason="Rally returned a malformed claim for the exact tool/session",
                backend="rally",
                transport="rally-cli",
            )
        owned_scopes.update(scopes)
        if fact.get("subject") != _FILES_IN_FLIGHT_CLAIM_SUBJECT:
            continue
        event_id = fact.get("event_id")
        if (
            not isinstance(event_id, str)
            or not event_id
            or len(scopes) != 1
            or not scopes[0].startswith("file:")
        ):
            return None, NativeResult(
                "invalid",
                payload=snapshot.payload,
                returncode=snapshot.returncode,
                reason="Rally returned a malformed Build Loop managed claim",
                backend="rally",
                transport="rally-cli",
            )
        managed_by_scope.setdefault(scopes[0], []).append(fact)

    release_ids: list[str] = []
    for scope, managed in managed_by_scope.items():
        # Keep the newest exact managed claim for each still-requested scope;
        # remove older duplicates and every managed claim no longer requested.
        ordered = sorted(
            managed,
            key=lambda fact: (
                _positive_int(fact.get("seq")) or 0,
                str(fact.get("event_id") or ""),
            ),
            reverse=True,
        )
        keep = 1 if scope in requested else 0
        release_ids.extend(str(fact["event_id"]) for fact in ordered[keep:])

    add_paths = tuple(
        raw for scope, raw in requested.items() if scope not in owned_scopes
    )
    return (
        _NativeClaimPlan(
            requested_paths=tuple(requested.values()),
            add_paths=add_paths,
            release_event_ids=tuple(release_ids),
            requested_count=len(requested),
            reused_count=len(requested) - len(add_paths),
        ),
        None,
    )


def _partial_native_operation(
    primary: NativeResult,
    secondary: NativeResult,
    *,
    action: str,
    retryable_reconciliation: bool = False,
) -> NativeResult:
    """Preserve a known earlier commit without overstating a later mutation."""
    detail = secondary.reason or secondary.status
    if secondary.status == "outcome_unknown":
        return NativeResult(
            "outcome_unknown",
            payload=secondary.payload,
            returncode=secondary.returncode,
            reason=f"native presence committed, but {action} is outcome-unknown: {detail}",
            revision=secondary.revision,
            event_id=secondary.event_id,
            remedy=secondary.remedy,
            backend="rally",
            transport="rally-cli",
        )
    return NativeResult(
        "partial_commit",
        payload=secondary.payload or primary.payload,
        returncode=secondary.returncode,
        reason=f"native presence committed, but {action} did not complete exactly: {detail}",
        revision=secondary.revision or primary.revision,
        event_id=secondary.event_id or primary.event_id,
        remedy=secondary.remedy,
        backend="rally",
        transport="rally-cli",
        retryable_reconciliation=retryable_reconciliation,
    )


def _apply_native_claim_plan_once(
    context: BackendContext,
    *,
    tool: str,
    session_id: str,
    plan: _NativeClaimPlan,
    primary: NativeResult,
) -> NativeResult:
    last = primary
    added = 0
    released = 0
    stale_release_rejections = 0
    for path in plan.add_paths:
        claim = invoke_native(
            context,
            [
                "say",
                "claim",
                "--json",
                "--tool",
                tool,
                "--subject",
                _FILES_IN_FLIGHT_CLAIM_SUBJECT,
                "--path",
                path,
            ],
            expected_schema="agent-rally.command.say.v1",
            tool=tool,
            session_id=session_id,
            mutating=True,
        )
        if not claim.ok:
            return _partial_native_operation(
                primary,
                claim,
                action=f"claiming files_in_flight path {path!r}",
                retryable_reconciliation=claim.status
                not in {"outcome_unknown", "partial_commit"},
            )
        last = claim
        added += 1

    for event_id in plan.release_event_ids:
        release = invoke_native(
            context,
            [
                "say",
                "release",
                "--json",
                "--tool",
                tool,
                "--subject",
                _FILES_IN_FLIGHT_RELEASE_SUBJECT,
                "--ref",
                event_id,
            ],
            expected_schema="agent-rally.command.say.v1",
            tool=tool,
            session_id=session_id,
            mutating=True,
        )
        if not release.ok:
            if release.status == "rejected":
                # Another same-session reconciler may have closed this exact
                # reserved claim after our snapshot. Continue through the
                # remaining deterministic ids; the mandatory post-read below
                # decides whether the whole requested state converged.
                stale_release_rejections += 1
                continue
            return _partial_native_operation(
                primary,
                release,
                action=f"releasing omitted managed claim {event_id!r}",
                retryable_reconciliation=release.status
                not in {"outcome_unknown", "partial_commit"},
            )
        last = release
        released += 1

    return NativeResult(
        "ok",
        payload=last.payload,
        returncode=last.returncode,
        reason=(
            "native presence and files_in_flight ownership are exact "
            f"(requested={plan.requested_count}, reused={plan.reused_count}, "
            f"added={added}, released={released}, "
            f"stale_release_rejections={stale_release_rejections})"
        ),
        revision=last.revision,
        event_id=last.event_id,
        backend="rally",
        transport="rally-cli",
    )


def _apply_native_claim_plan(
    context: BackendContext,
    *,
    tool: str,
    session_id: str,
    plan: _NativeClaimPlan,
    primary: NativeResult,
) -> NativeResult:
    """Apply and then converge a claim plan across concurrent same-session writers.

    Rally intentionally permits overlapping claims from the same session. Two
    Build Loop processes can therefore read the same empty snapshot and both
    append. A bounded read-after-write loop deterministically keeps the newest
    managed claim and releases the rest. The chronologically last writer always
    performs a post-read, so a completed identical write cannot leave growth.
    """
    current = plan
    last = primary
    for convergence_pass in range(1, 4):
        applied = _apply_native_claim_plan_once(
            context,
            tool=tool,
            session_id=session_id,
            plan=current,
            primary=primary,
        )
        if not applied.ok:
            # Concurrent writers may race to close the same duplicate. A fresh
            # authority read can prove the requested ownership already converged.
            # Never collapse Rally's typed ambiguous/partial outcomes.
            if applied.status != "outcome_unknown" and not (
                applied.status == "partial_commit"
                and not applied.retryable_reconciliation
            ):
                verified, verify_error = _native_claim_plan(
                    context,
                    tool=tool,
                    session_id=session_id,
                    paths=current.requested_paths,
                )
                if (
                    verify_error is None
                    and verified is not None
                    and not verified.add_paths
                    and not verified.release_event_ids
                ):
                    return NativeResult(
                        "ok",
                        payload=applied.payload or last.payload,
                        returncode=applied.returncode,
                        reason=(
                            "concurrent files_in_flight reconciliation already "
                            f"reached the exact requested state on pass {convergence_pass}"
                        ),
                        revision=applied.revision or last.revision,
                        event_id=applied.event_id or last.event_id,
                        backend="rally",
                        transport="rally-cli",
                    )
                if verify_error is not None:
                    return _partial_native_operation(
                        primary,
                        verify_error,
                        action="verifying a concurrent files_in_flight mutation",
                    )
                if verified is not None:
                    current = verified
                    continue
            return applied
        last = applied
        verified, verify_error = _native_claim_plan(
            context,
            tool=tool,
            session_id=session_id,
            paths=current.requested_paths,
        )
        if verify_error is not None:
            return _partial_native_operation(
                primary,
                verify_error,
                action="verifying files_in_flight ownership convergence",
            )
        if verified is None:
            return NativeResult(
                "partial_commit",
                payload=last.payload,
                returncode=last.returncode,
                reason=(
                    "native presence committed, but ownership convergence returned "
                    "no verification plan"
                ),
                revision=last.revision,
                event_id=last.event_id,
                backend="rally",
                transport="rally-cli",
            )
        if not verified.add_paths and not verified.release_event_ids:
            return NativeResult(
                "ok",
                payload=last.payload,
                returncode=last.returncode,
                reason=(
                    f"{last.reason}; converged_after_pass={convergence_pass}"
                ),
                revision=last.revision,
                event_id=last.event_id,
                backend="rally",
                transport="rally-cli",
            )
        current = verified

    return NativeResult(
        "partial_commit",
        payload=last.payload,
        returncode=last.returncode,
        reason=(
            "native presence committed, but files_in_flight ownership did not "
            "converge after 3 bounded passes; do not retry wholesale"
        ),
        revision=last.revision,
        event_id=last.event_id,
        backend="rally",
        transport="rally-cli",
    )


def enter_session(
    context: BackendContext,
    *,
    tool: str,
    session_id: str,
    role: str | None = None,
    paths: Iterable[str] = (),
    tier: str | None = None,
) -> NativeResult:
    argv = ["enter", "--tool", tool, "--session-id", session_id, "--json"]
    if role:
        argv.extend(["--role", role])
    if tier:
        argv.extend(["--tier", tier])
    for path in paths:
        if path:
            argv.extend(["--path", str(path)])
    result = invoke_native(
        context,
        argv,
        expected_schema="agent-rally.command.enter.v1",
        tool=tool,
        session_id=session_id,
        mutating=True,
    )
    if result.status == "partial_commit":
        fact = _committed_fact(
            result.payload,
            kind="presence",
            tool=tool,
            session_id=session_id,
        )
        if fact is not None:
            return NativeResult(
                "ok",
                payload=result.payload,
                returncode=result.returncode,
                reason="Rally enter committed presence; later projection work was partial",
                revision=_positive_int(fact.get("seq")),
                event_id=str(fact.get("event_id") or "") or None,
                backend="rally",
                transport="rally-cli",
            )
    return result


def write_presence(
    context: BackendContext,
    *,
    session_id: str,
    tool: str,
    local_session_id: str | None = None,
    local_tool: str | None = None,
    model: str,
    run_id: str,
    app_slug: str,
    phase: str,
    files_in_flight: Iterable[str] = (),
    cwd: Path | str | None = None,
    task: str | None = None,
    parent: str | None = None,
    spawned: Any = None,
    pid: int | None = None,
    host: str | None = None,
    tier: str | None = None,
) -> NativeResult:
    """Write presence through the selected authority, never through ``.rally`` files."""
    paths = [str(path) for path in files_in_flight if path]
    fallback_session_id = (
        session_id if local_session_id is None else local_session_id
    )
    fallback_tool = tool if local_tool is None else local_tool
    if context.native:
        claim_plan, plan_error = _native_claim_plan(
            context,
            tool=tool,
            session_id=session_id,
            paths=paths,
        )
        if plan_error is not None and not plan_error.precommit_unavailable:
            return plan_error
        if claim_plan is None and plan_error is None:
            return NativeResult(
                "invalid",
                reason="native files_in_flight claim planning returned no result",
            )
        if plan_error is not None:
            local = write_local_presence(
                context,
                session_id=fallback_session_id,
                tool=fallback_tool,
                model=model,
                run_id=run_id,
                app_slug=app_slug,
                phase=phase,
                files_in_flight=paths,
                cwd=cwd,
                task=task,
                parent=parent,
                spawned=spawned,
                pid=pid,
                host=host,
            )
            if local.ok:
                return NativeResult(
                    "ok",
                    reason=(
                        "native Rally unavailable before ownership read; wrote "
                        "Build Loop local presence"
                    ),
                    backend="build-loop-local",
                    transport="presence-json",
                )
            return local
        native = enter_session(
            context,
            tool=tool,
            session_id=session_id,
            paths=paths,
            tier=tier,
        )
        if not native.precommit_unavailable:
            if not native.ok:
                return native
            assert claim_plan is not None
            return _apply_native_claim_plan(
                context,
                tool=tool,
                session_id=session_id,
                plan=claim_plan,
                primary=native,
            )
        local = write_local_presence(
            context,
            session_id=fallback_session_id,
            tool=fallback_tool,
            model=model,
            run_id=run_id,
            app_slug=app_slug,
            phase=phase,
            files_in_flight=paths,
            cwd=cwd,
            task=task,
            parent=parent,
            spawned=spawned,
            pid=pid,
            host=host,
        )
        if local.ok:
            return NativeResult(
                "ok",
                reason="native Rally unavailable before enter; wrote Build Loop local presence",
                backend="build-loop-local",
                transport="presence-json",
            )
        return local
    if context.envelope.backend != "build-loop-local":
        return NativeResult("failed", reason="coordination backend unavailable")
    return write_local_presence(
        context,
        session_id=fallback_session_id,
        tool=fallback_tool,
        model=model,
        run_id=run_id,
        app_slug=app_slug,
        phase=phase,
        files_in_flight=paths,
        cwd=cwd,
        task=task,
        parent=parent,
        spawned=spawned,
        pid=pid,
        host=host,
    )


def write_local_presence(
    context: BackendContext,
    *,
    session_id: str,
    tool: str,
    model: str,
    run_id: str,
    app_slug: str,
    phase: str,
    files_in_flight: Iterable[str] = (),
    cwd: Path | str | None = None,
    task: str | None = None,
    parent: str | None = None,
    spawned: Any = None,
    pid: int | None = None,
    host: str | None = None,
) -> NativeResult:
    """Explicit pre-commit failover writer for Build Loop's local channel."""
    try:
        try:
            from . import presence
        except ImportError:
            import presence  # type: ignore
        written = presence.write_presence(
            context.local_channel_dir,
            session_id=session_id,
            tool=tool,
            model=model,
            run_id=run_id,
            app_slug=app_slug,
            phase=phase,
            files_in_flight=[str(path) for path in files_in_flight if path],
            cwd=cwd or context.workdir,
            task=task,
            parent=parent,
            spawned=spawned,
            pid=pid,
            host=host,
        )
        if not written:
            return NativeResult(
                "failed",
                reason="Build Loop local presence write was rejected or not committed",
                backend="build-loop-local",
                transport="presence-json",
            )
        return NativeResult(
            "ok",
            backend="build-loop-local",
            transport="presence-json",
        )
    except Exception as exc:  # noqa: BLE001 - coordination remains fail-open
        return NativeResult("failed", reason=str(exc))


def write_heartbeat_presence(
    context: BackendContext,
    *,
    session_id: str,
    tool: str,
    local_session_id: str | None = None,
    local_tool: str | None = None,
    phase: str,
    intent: str,
    files_in_flight: Iterable[str] = (),
    model: str = "orchestrator",
    run_id: str = "unknown",
    app_slug: str | None = None,
    cwd: Path | str | None = None,
) -> NativeResult:
    fallback_session_id = (
        session_id if local_session_id is None else local_session_id
    )
    fallback_tool = tool if local_tool is None else local_tool
    if context.native:
        paths = [str(path) for path in files_in_flight if path]
        claim_plan, plan_error = _native_claim_plan(
            context,
            tool=tool,
            session_id=session_id,
            paths=paths,
        )
        if plan_error is not None and not plan_error.precommit_unavailable:
            return plan_error
        if claim_plan is None and plan_error is None:
            return NativeResult(
                "invalid",
                reason="native files_in_flight claim planning returned no result",
            )
        if plan_error is not None:
            local = write_local_presence(
                context,
                session_id=fallback_session_id,
                tool=fallback_tool,
                model=model,
                run_id=run_id,
                app_slug=app_slug or context.envelope.app_slug,
                phase=phase,
                files_in_flight=paths,
                cwd=cwd or context.workdir,
                task=intent or phase,
            )
            if local.ok:
                return NativeResult(
                    "ok",
                    reason=(
                        "native Rally unavailable before heartbeat ownership read; "
                        "wrote Build Loop local presence"
                    ),
                    backend="build-loop-local",
                    transport="presence-json",
                )
            return local
        native = status_post(
            context,
            tool=tool,
            session_id=session_id,
            state="working",
            file=paths[0] if paths else ".",
            intent=intent or phase,
        )
        if not native.precommit_unavailable:
            if not native.ok:
                return native
            assert claim_plan is not None
            return _apply_native_claim_plan(
                context,
                tool=tool,
                session_id=session_id,
                plan=claim_plan,
                primary=native,
            )
        local = write_local_presence(
            context,
            session_id=fallback_session_id,
            tool=fallback_tool,
            model=model,
            run_id=run_id,
            app_slug=app_slug or context.envelope.app_slug,
            phase=phase,
            files_in_flight=paths,
            cwd=cwd or context.workdir,
            task=intent or phase,
        )
        if local.ok:
            return NativeResult(
                "ok",
                reason=(
                    "native Rally unavailable before heartbeat; wrote Build Loop local presence"
                ),
                backend="build-loop-local",
                transport="presence-json",
            )
        return local
    return NativeResult("unavailable", reason="local heartbeat uses task-heartbeat store")


def room_snapshot(
    context: BackendContext,
    *,
    tool: str | None = None,
    actor: str | None = None,
    since: int | None = None,
    readers: bool = True,
) -> NativeResult:
    argv = ["room", "--json"]
    if tool:
        argv.extend(["--tool", tool])
    if readers:
        argv.append("--readers")
    if since is not None:
        argv.extend(["--since", str(max(0, since))])
    return invoke_native(
        context,
        argv,
        expected_schema="agent-rally.command.room.v1",
        tool=actor or tool or "build_loop:room-read",
    )


def status_read(context: BackendContext, *, tool: str | None = None) -> NativeResult:
    actor = tool or "build_loop:status"
    argv = ["status", "--json", "read"]
    if tool:
        argv.extend(["--tool", tool])
    return invoke_native(
        context,
        argv,
        expected_schema="agent-rally.command.status_read.v1",
        tool=actor,
    )


def recent(
    context: BackendContext,
    *,
    limit: int = 100,
    all_repos: bool = False,
) -> NativeResult:
    argv = ["recent", "--json"]
    if all_repos:
        argv.append("--all")
    argv.extend(["--limit", str(max(1, min(limit, 500)))])
    return invoke_native(
        context,
        argv,
        expected_schema="agent-rally.command.recent.v1",
        tool="build_loop:recent-read",
    )


def status_post(
    context: BackendContext,
    *,
    tool: str,
    state: str,
    session_id: str | None = None,
    file: str | None = None,
    intent: str | None = None,
    blocked_ref: str | None = None,
    wake_after: str | None = None,
    committed_sha: str | None = None,
    worktree_branch: str | None = None,
) -> NativeResult:
    argv = ["status", "--json", "post", "--tool", tool, "--state", state]
    for flag, value in (
        ("--file", file),
        ("--intent", intent),
        ("--blocked-ref", blocked_ref),
        ("--wake-after", wake_after),
        ("--committed-sha", committed_sha),
        ("--worktree-branch", worktree_branch),
    ):
        if value:
            argv.extend([flag, value])
    result = invoke_native(
        context,
        argv,
        expected_schema="agent-rally.command.status_post.v1",
        tool=tool,
        session_id=session_id,
        mutating=True,
    )
    if result.status == "partial_commit":
        fact = _primary_fact(
            result.payload or {},
            argv,
            tool=tool,
            session_id=session_id,
            workdir=context.workdir,
        )
        if fact is not None:
            return NativeResult(
                "ok",
                payload=result.payload,
                returncode=result.returncode,
                reason=(
                    "Rally status fact committed; later projection work was partial"
                ),
                revision=_positive_int(fact.get("seq")),
                event_id=str(fact.get("event_id") or "") or None,
                backend="rally",
                transport="rally-cli",
            )
    return result


def _append_issues(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    issues = data.get("append_issues") if isinstance(data, dict) else None
    if not isinstance(issues, list):
        return []
    return [issue for issue in issues if isinstance(issue, dict)]


def _valid_success_envelope(
    payload: dict[str, Any] | None,
    *,
    schema: str,
) -> bool:
    return bool(
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("product") == "rally"
        and payload.get("schema") == schema
    )


def acknowledge(
    context: BackendContext,
    *,
    tool: str,
    session_id: str | None = None,
) -> NativeResult:
    """Advance Rally's reader checkpoint through every visible content fact.

    Rally's ``next --limit`` limit controls only the ranked response. The
    command checkpoints ``snapshot.content_max_seq`` independently of that
    limit. A successful no-op is therefore a truthful acknowledgement too: it
    means the canonical reader checkpoint was already at that content tip.
    """
    result = invoke_native(
        context,
        ["next", "--json", "--tool", tool, "--limit", "20"],
        expected_schema="agent-rally.command.next.v1",
        tool=tool,
        session_id=session_id,
        mutating=True,
    )
    trusted_payload = result.status in {"ok", "partial_commit"} or (
        result.returncode == 0
        and _valid_success_envelope(
            result.payload,
            schema="agent-rally.command.next.v1",
        )
    )
    checkpoint = (
        _committed_fact(result.payload, kind="read", tool=tool)
        if trusted_payload
        else None
    )
    if checkpoint is not None:
        summary = str(checkpoint.get("summary") or "")
        read_seq = summary.removeprefix("read_seq:") if summary.startswith("read_seq:") else "?"
        return NativeResult(
            "ok",
            payload=result.payload,
            returncode=result.returncode,
            reason=(
                f"reader checkpoint advanced through content seq {read_seq}; "
                "--limit 20 limits returned recommendations, not checkpoint scope"
            ),
            revision=_positive_int(checkpoint.get("seq")),
            event_id=str(checkpoint.get("event_id") or "") or None,
            backend="rally",
            transport="rally-cli",
        )
    issues = _append_issues(result.payload)
    if issues:
        return NativeResult(
            "failed",
            payload=result.payload,
            returncode=result.returncode,
            reason="Rally next did not prove a reader checkpoint: optional append failed",
        )
    if result.ok or (
        result.status == "invalid"
        and result.returncode == 0
        and _valid_success_envelope(
            result.payload,
            schema="agent-rally.command.next.v1",
        )
    ):
        return NativeResult(
            "ok",
            payload=result.payload,
            returncode=result.returncode,
            reason=(
                "reader checkpoint was already current; --limit 20 limits "
                "returned recommendations, not checkpoint scope"
            ),
            revision=result.revision,
            event_id=result.event_id,
            backend="rally",
            transport="rally-cli",
        )
    return result


def audit_next(context: BackendContext, *, tool: str) -> NativeResult:
    """Read the next coordination packet without advancing Rally's cursor."""
    return invoke_native(
        context,
        ["next", "--json", "--tool", tool, "--limit", "20", "--audit"],
        expected_schema="agent-rally.command.next.v1",
        tool=tool,
        mutating=False,
    )


def retract_fact(
    context: BackendContext,
    *,
    fact_id: str,
    tool: str,
    reason: str,
    superseded_by: str | None = None,
    session_id: str | None = None,
) -> NativeResult:
    argv = [
        "retract",
        fact_id,
        "--tool",
        tool,
        "--reason",
        reason,
        "--json",
    ]
    if superseded_by:
        argv.extend(["--superseded-by", superseded_by])
    return invoke_native(
        context,
        argv,
        expected_schema="agent-rally.command.retract.v1",
        tool=tool,
        session_id=session_id,
        mutating=True,
    )


def lead_command(
    context: BackendContext,
    *,
    argv: list[str],
    tool: str,
    session_id: str | None = None,
    mutating: bool,
) -> NativeResult:
    return invoke_native(
        context,
        ["lead", "--json", *argv],
        expected_schema="agent-rally.command.lead.v1",
        tool=tool,
        session_id=session_id,
        mutating=mutating,
    )


def release_active_claims(
    context: BackendContext,
    *,
    tool: str,
    session_id: str | None,
    reason: str,
) -> tuple[list[str], NativeResult | None]:
    """Release only claim event ids currently owned by ``tool``."""
    snapshot = room_snapshot(context, actor=tool)
    if not snapshot.ok:
        return [], snapshot
    claims = native_room_summary(snapshot).get("active_claims")
    if not isinstance(claims, list):
        claims = []
    released: list[str] = []
    for item in claims:
        if not isinstance(item, dict):
            continue
        fact = item.get("fact") if isinstance(item.get("fact"), dict) else item
        if fact.get("tool") != tool or not fact.get("event_id"):
            continue
        if session_id and fact.get("from_session_id") != session_id:
            continue
        event_id = str(fact["event_id"])
        result = invoke_native(
            context,
            [
                "say",
                "release",
                "--json",
                "--tool",
                tool,
                "--subject",
                reason or "agent stopped",
                "--ref",
                event_id,
            ],
            expected_schema="agent-rally.command.say.v1",
            tool=tool,
            session_id=session_id,
            mutating=True,
        )
        if not result.ok:
            return released, result
        released.append(event_id)
    return released, None


def native_room_data(result: NativeResult) -> dict[str, Any]:
    if not result.ok or not isinstance(result.payload, dict):
        return {}
    data = result.payload.get("data")
    if not isinstance(data, dict):
        return {}
    room = data.get("room")
    return room if isinstance(room, dict) else {}


def native_room_summary(result: NativeResult) -> dict[str, Any]:
    outer = native_room_data(result)
    nested = outer.get("room")
    return nested if isinstance(nested, dict) else outer


def _recent_facts(result: NativeResult | None) -> tuple[list[dict[str, Any]], bool]:
    if result is None or not result.ok or not isinstance(result.payload, dict):
        return [], False
    data = result.payload.get("data")
    recent_data = data.get("recent") if isinstance(data, dict) else None
    rows = recent_data.get("rows") if isinstance(recent_data, dict) else None
    if not isinstance(rows, list):
        return [], False
    facts: list[dict[str, Any]] = []
    for row in rows:
        fact = row.get("fact") if isinstance(row, dict) else None
        if isinstance(fact, dict):
            facts.append(fact)
    return facts, True


def _artifact_coverage(
    summary: dict[str, Any],
    *,
    room_recent_artifacts: list[dict[str, Any]],
    room_unconsumed_artifacts: list[dict[str, Any]],
    recent_facts: list[dict[str, Any]],
    recent_available: bool,
    last_read: int,
) -> tuple[bool, dict[str, Any]]:
    composition = summary.get("composition")
    buckets = composition.get("buckets") if isinstance(composition, dict) else None
    artifact_bucket = buckets.get("recent_artifacts") if isinstance(buckets, dict) else None
    unconsumed_bucket = (
        buckets.get("unconsumed_artifacts") if isinstance(buckets, dict) else None
    )
    stale_bucket = buckets.get("stale_facts") if isinstance(buckets, dict) else None
    totals = summary.get("totals")
    total = totals.get("recent_artifacts") if isinstance(totals, dict) else None
    unconsumed_total = (
        totals.get("unconsumed_artifacts") if isinstance(totals, dict) else None
    )
    stale_total = totals.get("stale_facts") if isinstance(totals, dict) else None
    emitted = len(room_recent_artifacts)
    unconsumed_emitted = len(room_unconsumed_artifacts)
    omitted = (
        artifact_bucket.get("omitted")
        if isinstance(artifact_bucket, dict)
        else None
    )
    if type(omitted) is not int or omitted < 0:
        omitted = max(0, total - emitted) if type(total) is int else 0
    unconsumed_omitted = (
        unconsumed_bucket.get("omitted")
        if isinstance(unconsumed_bucket, dict)
        else None
    )
    if type(unconsumed_omitted) is not int or unconsumed_omitted < 0:
        unconsumed_omitted = (
            max(0, unconsumed_total - unconsumed_emitted)
            if type(unconsumed_total) is int
            else 0
        )
    stale_omitted = (
        stale_bucket.get("omitted") if isinstance(stale_bucket, dict) else None
    )
    if type(stale_omitted) is not int or stale_omitted < 0:
        stale_omitted = stale_total if type(stale_total) is int else 0
    omitted_ids = (
        artifact_bucket.get("omitted_ids")
        if isinstance(artifact_bucket, dict)
        else []
    )
    if not isinstance(omitted_ids, list):
        omitted_ids = []
    omitted_id_set = {str(value) for value in omitted_ids if value}
    unconsumed_omitted_ids = (
        unconsumed_bucket.get("omitted_ids")
        if isinstance(unconsumed_bucket, dict)
        else []
    )
    if not isinstance(unconsumed_omitted_ids, list):
        unconsumed_omitted_ids = []
    unconsumed_omitted_id_set = {
        str(value) for value in unconsumed_omitted_ids if value
    }
    inspected_ids = {
        str(fact.get("event_id"))
        for fact in recent_facts
        if fact.get("event_id")
    }
    recovered_ids = omitted_id_set & inspected_ids
    recovered_unconsumed_ids = unconsumed_omitted_id_set & inspected_ids
    ids_truncated = bool(
        isinstance(artifact_bucket, dict)
        and artifact_bucket.get("omitted_ids_truncated")
    )
    unconsumed_ids_truncated = bool(
        isinstance(unconsumed_bucket, dict)
        and unconsumed_bucket.get("omitted_ids_truncated")
    )
    reasons: list[str] = []
    if (omitted > 0 or unconsumed_omitted > 0) and not recent_available:
        reasons.append("repo_recent_unavailable")
    if ids_truncated or omitted > len(omitted_id_set):
        reasons.append("room_omitted_artifact_ids_incomplete")
    if omitted_id_set - inspected_ids:
        reasons.append("room_omitted_artifacts_not_in_repo_recent")
    if (
        unconsumed_ids_truncated
        or unconsumed_omitted > len(unconsumed_omitted_id_set)
    ):
        reasons.append("room_unconsumed_artifact_ids_incomplete")
    if unconsumed_omitted_id_set - inspected_ids:
        reasons.append("room_unconsumed_artifacts_not_in_repo_recent")
    content_max = summary.get("content_max_seq")
    if stale_omitted > 0 and (
        type(content_max) is not int or last_read < content_max
    ):
        reasons.append("archived_facts_may_contain_unread_messages")
    coverage_incomplete = bool(reasons)
    return coverage_incomplete, {
        "room_artifacts_emitted": emitted,
        "room_artifacts_omitted": omitted,
        "room_unconsumed_artifacts_emitted": unconsumed_emitted,
        "room_unconsumed_artifacts_omitted": unconsumed_omitted,
        "room_archived_facts_omitted": stale_omitted,
        "repo_recent_available": recent_available,
        "repo_recent_rows_inspected": len(recent_facts),
        "omitted_artifact_ids_recovered": len(recovered_ids),
        "omitted_unconsumed_artifact_ids_recovered": len(
            recovered_unconsumed_ids
        ),
        "reasons": reasons,
    }


def native_inbox_snapshot(
    result: NativeResult,
    *,
    tool: str,
    recent_result: NativeResult | None = None,
) -> dict[str, Any]:
    """Project unread native handoffs and Build Loop message artifacts.

    Acknowledgement-required messages are Rally handoffs. Fire-and-forget
    messages are Rally artifacts carrying the authenticated Build Loop event
    codec, so both forms must participate in the same reader checkpoint. The
    room's ``unconsumed_artifacts`` is the primary artifact surface; a
    repo-scoped ``recent(..., limit=500)`` result recovers artifacts omitted
    from the room response's byte budget. ``coverage_incomplete`` stays true
    whenever budget or archive metadata cannot prove that every potentially
    unread artifact was inspected.
    """
    if not result.ok or not isinstance(result.payload, dict):
        return {
            "counts": {"direct": 0, "broadcast": 0, "total": 0},
            "latest": [],
            "coverage_incomplete": True,
            "coverage": {
                "room_artifacts_emitted": 0,
                "room_artifacts_omitted": 0,
                "room_unconsumed_artifacts_emitted": 0,
                "room_unconsumed_artifacts_omitted": 0,
                "room_archived_facts_omitted": 0,
                "repo_recent_available": False,
                "repo_recent_rows_inspected": 0,
                "omitted_artifact_ids_recovered": 0,
                "omitted_unconsumed_artifact_ids_recovered": 0,
                "reasons": ["room_snapshot_unavailable"],
            },
        }
    data = result.payload.get("data")
    if not isinstance(data, dict):
        data = {}
    readers = data.get("readers")
    if not isinstance(readers, list):
        readers = []
    last_read = 0
    for reader in readers:
        if isinstance(reader, dict) and reader.get("tool") == tool:
            value = reader.get("last_read_seq")
            if type(value) is int and value >= 0:
                last_read = max(last_read, value)
    summary = native_room_summary(result)
    handoffs = summary.get("open_handoffs")
    if not isinstance(handoffs, list):
        handoffs = []
    artifacts = summary.get("recent_artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
    unconsumed = summary.get("unconsumed_artifacts")
    if not isinstance(unconsumed, list):
        unconsumed = []
    room_recent_artifacts = [item for item in artifacts if isinstance(item, dict)]
    room_unconsumed_artifacts = [
        item for item in unconsumed if isinstance(item, dict)
    ]
    recent_facts, recent_available = _recent_facts(recent_result)
    coverage_incomplete, coverage = _artifact_coverage(
        summary,
        room_recent_artifacts=room_recent_artifacts,
        room_unconsumed_artifacts=room_unconsumed_artifacts,
        recent_facts=recent_facts,
        recent_available=recent_available,
        last_read=last_read,
    )

    try:
        try:
            from .payload_codec import decode_event
        except ImportError:
            from payload_codec import decode_event  # type: ignore
    except ImportError:
        decode_event = lambda _evidence: None  # type: ignore[assignment]

    candidates: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for item in handoffs:
        if not isinstance(item, dict):
            continue
        fact = item.get("fact") if isinstance(item.get("fact"), dict) else item
        candidates.append((fact, None))
    for item in [
        *room_unconsumed_artifacts,
        *room_recent_artifacts,
        *recent_facts,
    ]:
        fact = item.get("fact") if isinstance(item.get("fact"), dict) else item
        event = decode_event(
            fact.get("evidence") if isinstance(fact.get("evidence"), list) else []
        )
        if not isinstance(event, dict) or event.get("kind") != "message":
            continue
        event_payload = event.get("payload")
        if not isinstance(event_payload, dict) or bool(event_payload.get("requires_ack")):
            continue
        if not (fact.get("target") or event_payload.get("to")):
            # A codec-tagged message without an explicit recipient is a
            # channel event, not an inbox broadcast. Real broadcasts name
            # ``all`` (or the legacy ``peer`` target) explicitly.
            continue
        candidates.append((fact, event_payload))

    direct: list[dict[str, Any]] = []
    broadcast: list[dict[str, Any]] = []
    seen: set[str] = set()
    decoded_by_event: dict[str, dict[str, Any]] = {}
    for fact, event_payload in candidates:
        seq = fact.get("seq")
        if type(seq) is not int or seq <= last_read:
            continue
        event_key = str(fact.get("event_id") or f"seq:{seq}")
        if event_key in seen:
            continue
        seen.add(event_key)
        if event_payload is not None:
            decoded_by_event[event_key] = event_payload
        target = fact.get("target") or (
            event_payload.get("to") if event_payload is not None else None
        )
        if target == tool:
            direct.append(fact)
        elif target in (None, "", "all", "peer"):
            broadcast.append(fact)
    unread = sorted(direct + broadcast, key=lambda fact: int(fact.get("seq") or 0))
    latest = []
    for fact in unread[-3:]:
        event_key = str(fact.get("event_id") or f"seq:{fact.get('seq')}")
        decoded = decoded_by_event.get(event_key) or {}
        nested = decoded.get("payload") if isinstance(decoded.get("payload"), dict) else {}
        latest.append({
            "kind": fact.get("kind"),
            "from_tool": decoded.get("from") or fact.get("tool"),
            "to_tool": decoded.get("to") or fact.get("target"),
            "subject": decoded.get("subject") or fact.get("subject"),
            "summary": nested.get("summary") or nested.get("message") or fact.get("summary"),
            "requires_ack": bool(decoded.get("requires_ack", fact.get("kind") == "handoff")),
            "revision": fact.get("seq"),
            "event_id": fact.get("event_id"),
        })
    return {
        "counts": {
            "direct": len(direct),
            "broadcast": len(broadcast),
            "total": len(direct) + len(broadcast),
        },
        "latest": latest,
        "coverage_incomplete": coverage_incomplete,
        "coverage": coverage,
    }
