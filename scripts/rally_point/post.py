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

from pathlib import Path

try:  # package import
    from .build_loop_id import rally_fields_for
    from .changes import append_change, make_record
    from .producer_metadata import producer_metadata
    from .revision import bump_revision
except ImportError:  # script import (sys.path-inserted, no parent package)
    from build_loop_id import rally_fields_for  # type: ignore
    from changes import append_change, make_record  # type: ignore
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

        record = make_record(
            kind=kind,
            tool=tool,
            model=model,
            run_id=run_id,
            app_slug=app_slug,
            payload=payload,
            revision=new_rev,
        )
        # β1: attach producer identity to every outgoing record.
        record.update(producer_metadata())
        # build_loop_id: top-level run-instance identity, orthogonal to
        # producer_metadata (runtime identity). Merge AFTER producer so
        # no producer_* field can shadow it. Absent when workdir is None
        # or state.json lacks ``execution.build_loop_id`` — write proceeds.
        record.update(rally_fields_for(workdir))
        append_change(d, record)
        if kind == "phase" and (payload or {}).get("phase") == "rally-start":
            try:
                try:  # package import
                    from . import rally
                except ImportError:  # script import
                    import rally  # type: ignore

                rally.write_current(d, record)
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
                    # Mirror the same record AND bump legacy's revision so
                    # legacy-side readers see a fresh signal.
                    bump_revision(legacy_dir)
                    append_change(legacy_dir, record)
            except Exception:
                # Fire-and-forget per protocol; mirror failure is silent.
                pass

        return new_rev
    except Exception:
        # Fire-and-forget per protocol; never raise into the caller.
        return None
