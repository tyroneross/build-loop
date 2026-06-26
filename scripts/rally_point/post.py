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
    1. Compute the next revision (read current + 1) via existing
       ``bump_revision`` (locked write).
    2. Build a record with that revision number via ``make_record``.
    3. Atomically append the record to ``changes.jsonl``.

This ordering matches the protocol: bump revision BEFORE writing the
record. That way readers who see the new revision can always find the
corresponding record (no race where revision is ahead of the change log).

Fire-and-forget like the underlying primitives. Errors are swallowed
(caller can't be blocked by a coordination write).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

try:  # package import
    from .build_loop_id import rally_fields_for
    from .producer_metadata import producer_metadata
    from .revision import bump_revision
except ImportError:  # script import (sys.path-inserted, no parent package)
    from build_loop_id import rally_fields_for  # type: ignore
    from producer_metadata import producer_metadata  # type: ignore
    from revision import bump_revision  # type: ignore


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
        d = Path(channel_dir)
        d.mkdir(parents=True, exist_ok=True)

        if workdir is not None:
            try:
                try:  # package import
                    from .discovery_bridge import (
                        repo_local_rally_binary,
                        resolve as _bridge_resolve,
                    )
                except ImportError:  # script import
                    from discovery_bridge import (  # type: ignore
                        repo_local_rally_binary,
                        resolve as _bridge_resolve,
                    )

                envelope = _bridge_resolve(workdir)
                if (
                    envelope.resolved_via == "repo-local-rally-cli"
                    and str(Path(envelope.channel_dir).resolve()) == str(d.resolve())
                ):
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
                    return _post_via_repo_local_rally(
                        binary=repo_local_rally_binary(workdir),
                        workdir=workdir,
                        kind=kind,
                        tool=tool,
                        run_id=run_id,
                        payload=payload,
                    )
            except Exception:
                return None

        if workdir is None and _looks_like_rust_channel(d):
            return None

        # MECE validation: reject malformed handoff payloads before any write
        if kind == "handoff":
            try:  # package import
                from . import mece_gate
            except ImportError:  # script import
                import mece_gate  # type: ignore

            valid, rejection = mece_gate.validate_handoff(payload or {}, tool=tool)
            if not valid:
                mece_gate.log_rejection(
                    d, kind=kind, tool=tool, rejection=rejection, payload=payload or {}
                )
                return None

        # Bump first so the new record's revision matches what readers see
        new_rev = bump_revision(d)

        # Local-fallback writes now emit the agent-rally.fact.v1 shape so the
        # store is losslessly ingestible by ``rally migrate-legacy`` (which
        # silently skips any non-fact.v1 line). Build-loop's own readers consume
        # it back through the single ``changes.read_changes_since`` →
        # ``changes.normalize_record`` chokepoint; build-loop-private signal
        # (revision, payload, producer metadata) rides along as additive bl_*
        # keys that ARP ignores (no deny_unknown_fields).
        try:  # package import
            from .fact_v1 import to_fact_v1, write_fact_v1_line
        except ImportError:  # script import
            from fact_v1 import to_fact_v1, write_fact_v1_line  # type: ignore

        # Two orthogonal identity axes, kept as SEPARATE dicts: producer =
        # runtime identity (what code/version is writing), build_loop fields =
        # per-run identity (which build-loop run this is). Merging them (the
        # prior ``producer.update(...)``) nested the run-identity fields inside
        # bl_producer, so normalize never recovered them top-level. to_fact_v1
        # now stores each in its own bl_* key and normalize splices both back.
        producer = producer_metadata()
        build_loop_fields = rally_fields_for(workdir)
        fact = to_fact_v1(
            kind=kind,
            tool=tool,
            model=model,
            run_id=run_id,
            app_slug=app_slug,
            payload=payload,
            revision=new_rev,
            producer=producer,
            build_loop_fields=build_loop_fields,
        )
        write_fact_v1_line(d, fact)
        if kind == "phase" and (payload or {}).get("phase") == "rally-start":
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

        return new_rev
    except Exception:
        # Fire-and-forget per protocol; never raise into the caller.
        return None


def _looks_like_rust_channel(channel_dir: Path) -> bool:
    return (
        (channel_dir / "rally.tail.json").exists()
        or (channel_dir / "rally.checkpoint.json").exists()
        or (channel_dir / "rally.lock").exists()
    )


def _post_via_repo_local_rally(
    *,
    binary: str | None,
    workdir: Path,
    kind: str,
    tool: str,
    run_id: str,
    payload: dict,
) -> int | None:
    if not binary:
        return None
    native_kind = _native_kind(kind)
    subject = _native_subject(kind, payload)
    cmd = [
        binary,
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
        cmd.extend(["--summary", str(summary)])
    target = (payload or {}).get("to") or (payload or {}).get("to_tool")
    if target:
        cmd.extend(["--to", str(target)])
    status = (payload or {}).get("status") or (payload or {}).get("verdict")
    if status:
        cmd.extend(["--status", str(status)])
    severity = (payload or {}).get("severity")
    if severity:
        cmd.extend(["--severity", str(severity)])
    for path in _payload_paths(payload):
        cmd.extend(["--path", path])
    evidence = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"))
    cmd.extend(["--evidence", evidence])
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        out = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(out, dict) or out.get("ok") is not True:
        return None
    return _native_seq(out)


def _native_kind(kind: str) -> str:
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
    for key in ("path", "paths", "scope", "files"):
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
    return out


def _native_seq(out: dict) -> int | None:
    try:
        seq = (((out.get("data") or {}).get("say") or {}).get("fact") or {}).get("seq")
        if seq:
            return int(seq)
        verified_seq = ((out.get("data") or {}).get("verified") or {}).get("seq")
        if verified_seq:
            return int(verified_seq)
    except (TypeError, ValueError):
        return None
    return 0
