#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""The decision-write pipeline: supersession, id alloc, frontmatter, triad.

`main` and `_do_write` orchestrate the memory-triad write-once contract
(design §9.A). The flat-module logic is preserved exactly; the long bodies are
decomposed into focused helpers (`_resolve_supersession`, `_build_frontmatter`,
`_emit_write_events`) with no change to control flow, exit codes, or written
bytes.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _paths import (  # type: ignore
    cutover_lock_active,
    default_schema as _default_schema,
    project_decisions_dir,
)
from project_resolver import resolve_project  # type: ignore

from cli import parse_args, split_csv
from constants import CONFIDENCE_ORDER, LockedFile, atomic_write_bytes, log
from dbwrite import db_dualwrite
from ids import (
    canonical_decision_id,
    find_same_topic,
    next_id,
    slugify,
)
from io_ops import append_event, archive_to_history, iso_utc, regenerate_index
from schema import (
    apply_v2_defaults,
    apply_v3_defaults,
    render_madr,
    validate_v2,
    validate_v3,
)
from taxonomy import load_taxonomy, validate_tags
import memory_update_ledger as mul  # type: ignore


def _validate_inputs(
    args: argparse.Namespace, workdir: Path
) -> "tuple[list[str], str] | None":
    """Load taxonomy, normalise + validate tags/source/date.

    Returns ``(tags, date)`` on success, or ``None`` after logging a validation
    error (the caller returns exit 1). The control flow + messages are
    identical to the historical inline prologue in ``main``.
    """
    # Load taxonomy
    try:
        tax = load_taxonomy(workdir)
    except Exception as e:  # noqa: BLE001
        log(f"validation error: failed to load TAXONOMY: {e}")
        return None

    tags = split_csv(args.tags)
    if args.primary_tag not in tags:
        tags = [args.primary_tag] + tags

    try:
        validate_tags(tags, args.primary_tag, tax)
    except ValueError as e:
        log(f"validation error: {e}")
        return None

    if args.source not in tax["sources"]:
        log(f"validation error: source {args.source!r} not in taxonomy. Allowed: {sorted(tax['sources'])}")
        return None

    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        log(f"validation error: --date must be YYYY-MM-DD, got {date!r}")
        return None
    return tags, date


def main(argv: list[str] | None = None) -> int:
    # Cutover-lock guard: when /tmp/agent-memory-cutover.lock is present,
    # exit cleanly with no side effects so in-flight callers (Stop hook,
    # auto-decision-capture, build-orchestrator Phase 5) bail without
    # splitting state across old/new during the freeze window.
    if cutover_lock_active():
        print("cutover in progress, skipping", file=sys.stderr)
        return 0

    try:
        args = parse_args(argv)
    except SystemExit as e:
        return 1 if e.code else 0

    # Resolve --schema default lazily so $AGENT_MEMORY_SCHEMA can override.
    if args.schema is None:
        args.schema = _default_schema()

    workdir = Path(args.workdir).resolve()

    # Memory-store cutover (2026-05-26): decision files now live in the
    # canonical build-loop-memory tree under projects/<project>/decisions/.
    # `events.jsonl` stays in repo-local .build-loop/ as short-term run
    # context, not durable memory.
    project_tag = args.project or resolve_project(workdir)
    args.project = project_tag
    decisions_dir = project_decisions_dir(project_tag)
    history_dir = decisions_dir / "_history"
    events_path = workdir / ".build-loop" / "events.jsonl"

    decisions_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    events_path.parent.mkdir(parents=True, exist_ok=True)

    validated = _validate_inputs(args, workdir)
    if validated is None:
        return 1
    tags, date = validated

    # Acquire writer lock for the whole flow (id alloc + supersession + writes are atomic)
    writer_lock_target = decisions_dir / ".writer"
    writer_lock_target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LockedFile(writer_lock_target):
            return _do_write(args, workdir, decisions_dir, history_dir, events_path, tags, date)
    except TimeoutError as e:
        log(f"filesystem error: {e}")
        return 2


def _resolve_supersession(
    same: "tuple[Path, dict] | None",
    explicit_supersede: str | None,
    new_conf: str,
) -> "tuple[str | None, int | None]":
    """Decide the supersession outcome for a same-topic prior.

    Returns ``(auto_supersede_id, error_code)``: when ``error_code`` is not
    None the caller must return it (a validation error, exit 1). Mirrors the
    confidence-ladder logic of the historical `_do_write` step 1 exactly.
    """
    if same is None:
        return None, None
    _prior_path, prior_fm = same
    prior_id = prior_fm.get("id", "")
    prior_conf = prior_fm.get("confidence", "assumed")
    prior_rank = CONFIDENCE_ORDER.get(prior_conf, 0)
    new_rank = CONFIDENCE_ORDER.get(new_conf, 0)

    if explicit_supersede is not None:
        # User asserted; bypass ladder.
        if explicit_supersede != prior_id:
            log(
                f"validation error: --supersedes={explicit_supersede} but same-topic prior is {prior_id}; "
                f"either match or remove the prior first"
            )
            return None, 1
        return prior_id, None

    if new_rank > prior_rank:
        # Higher-confidence: auto-supersede
        return prior_id, None
    if new_rank == prior_rank:
        log(
            f"validation error: same-topic decision {prior_id} ({prior_conf}) already exists at equal "
            f"confidence; pass --supersedes {prior_id} to replace it explicitly"
        )
        return None, 1
    log(
        f"validation error: same-topic decision {prior_id} has higher confidence ({prior_conf}); "
        f"lower-confidence ({new_conf}) cannot displace it"
    )
    return None, 1


def _build_frontmatter(
    args: argparse.Namespace,
    *,
    new_id: str,
    canonical_id: str,
    slug: str,
    date: str,
    tags: list[str],
    auto_supersede_id: str | None,
    v2: dict[str, Any],
    v3: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the canonical-ordered frontmatter dict (v1 + v2 + v3).

    Field order is the on-disk schema contract — v3 is appended after v2 to
    keep canonical order stable across schema versions.
    """
    return {
        "id": new_id,
        "canonical_id": canonical_id,
        "canonical": True,
        "slug": slug,
        "title": args.title,
        "type": "decision",
        "status": args.status,
        "confidence": args.confidence,
        "date": date,
        "created": date,
        "updated": date,
        "tags": tags,
        "primary_tag": args.primary_tag,
        "entity": args.entity,
        "project": v2["project"],
        "tool": v2["tool"],
        "model": v2["model"],
        "task_category": v2["task_category"],
        "author": v2["author"],
        "source": args.source,
        "related_runs": split_csv(args.related_runs),
        "related_decisions": split_csv(args.related_decisions),
        "supersedes": auto_supersede_id,
        "superseded_by": None,
        "bookmark_snapshot_id": args.bookmark_snapshot_id,
        "captured_turn_excerpt": args.captured_turn_excerpt,
        "last_validated": v2["last_validated"],
        "last_accessed": v2["last_accessed"],
        "files_touched": v2["files_touched"],
        "closing_commit": v2["closing_commit"],
        # v3 (design §16) — appended after v2 to keep canonical order stable.
        "confidence_source": v3["confidence_source"],
        "confirmation_count": v3["confirmation_count"],
        "valid_until": v3["valid_until"],
        "causal_parent_id": v3["causal_parent_id"],
        "embedding_model_version": v3["embedding_model_version"],
        "domain": v3["domain"],
        "goal": v3["goal"],
    }


def _emit_write_events(
    args: argparse.Namespace,
    events_path: Path,
    *,
    new_id: str,
    canonical_id: str,
    auto_supersede_id: str | None,
    prior_fm: dict | None,
    v2: dict[str, Any],
    v3: dict[str, Any],
) -> None:
    """Append the supersession event (if any) + the accept/propose event."""
    if auto_supersede_id is not None:
        assert prior_fm is not None
        append_event(
            events_path,
            {
                "ts": iso_utc(),
                "kind": "decision_superseded",
                "decision_id": auto_supersede_id,
                "canonical_id": prior_fm.get("canonical_id"),
                "superseded_by": new_id,
                "superseded_by_canonical_id": canonical_id,
                "primary_tag": args.primary_tag,
                "entity": args.entity,
                "project": v2["project"],
                "tool": v2["tool"],
                "model": v2["model"],
                "task_category": v2["task_category"],
                "author": v2["author"],
                "source": args.source,
                # v3 mirror (design §16)
                "confidence_source": v3["confidence_source"],
                "confirmation_count": v3["confirmation_count"],
                "valid_until": v3["valid_until"],
                "causal_parent_id": v3["causal_parent_id"],
                "embedding_model_version": v3["embedding_model_version"],
                "domain": v3["domain"],
                "goal": v3["goal"],
                "dedup_key": f"decision:{auto_supersede_id}:superseded_by:{new_id}",
            },
        )
    accept_kind = "decision_accepted" if args.status == "accepted" else "decision_proposed"
    append_event(
        events_path,
        {
            "ts": iso_utc(),
            "kind": accept_kind,
            "decision_id": new_id,
            "canonical_id": canonical_id,
            "title": args.title,
            "primary_tag": args.primary_tag,
            "entity": args.entity,
            "project": v2["project"],
            "tool": v2["tool"],
            "model": v2["model"],
            "task_category": v2["task_category"],
            "author": v2["author"],
            "confidence": args.confidence,
            "source": args.source,
            "supersedes": auto_supersede_id,
            # v3 mirror (design §16)
            "confidence_source": v3["confidence_source"],
            "confirmation_count": v3["confirmation_count"],
            "valid_until": v3["valid_until"],
            "causal_parent_id": v3["causal_parent_id"],
            "embedding_model_version": v3["embedding_model_version"],
            "domain": v3["domain"],
            "goal": v3["goal"],
            "dedup_key": f"decision:{new_id}:{accept_kind}",
        },
    )


def _resolve_v2_v3(args: argparse.Namespace, workdir: Path) -> "tuple[dict[str, Any], dict[str, Any]] | None":
    """Apply + validate v2 then v3 defaults. Returns ``(v2, v3)`` or ``None``
    on a validation error (caller logs were already emitted, returns exit 1)."""
    files_touched_arg: list[str] | None = None
    if getattr(args, "files_touched", None):
        files_touched_arg = split_csv(args.files_touched)
    try:
        v2 = apply_v2_defaults(
            project=getattr(args, "project", None),
            tool=getattr(args, "tool", None),
            model=getattr(args, "model", None),
            task_category=getattr(args, "task_category", None),
            author=getattr(args, "author", None),
            files_touched=files_touched_arg,
            closing_commit=getattr(args, "closing_commit", None),
            last_validated=getattr(args, "last_validated", None),
            last_accessed=getattr(args, "last_accessed", None),
            source=args.source,
            entity=args.entity,
            workdir=workdir,
            infer_files=getattr(args, "infer_files_touched", False),
        )
        validate_v2(v2)
        # v3 (design §16). Applied AFTER v2 so v3 defaults can read v2.source.
        v3 = apply_v3_defaults(
            confidence_source=getattr(args, "confidence_source", None),
            confirmation_count=getattr(args, "confirmation_count", None),
            valid_until=getattr(args, "valid_until", None),
            causal_parent_id=getattr(args, "causal_parent_id", None),
            embedding_model_version=getattr(args, "embedding_model_version", None),
            domain=getattr(args, "domain", None),
            goal=getattr(args, "goal", None),
            source=args.source,
        )
        validate_v3(v3)
    except ValueError as e:
        log(f"validation error: {e}")
        return None
    return v2, v3


def _do_write(
    args: argparse.Namespace,
    workdir: Path,
    decisions_dir: Path,
    history_dir: Path,
    events_path: Path,
    tags: list[str],
    date: str,
) -> int:
    # 1) Resolve supersession
    same = find_same_topic(decisions_dir, args.primary_tag, args.entity)
    auto_supersede_id, err = _resolve_supersession(same, args.supersedes, args.confidence)
    if err is not None:
        return err

    # 2) Allocate ID
    new_id = next_id(decisions_dir, history_dir)
    slug = slugify(args.title)

    # 3) Build frontmatter
    # Apply v2 + v3 defaults (design §15/§16). Validate before writing.
    resolved = _resolve_v2_v3(args, workdir)
    if resolved is None:
        return 1
    v2, v3 = resolved

    canonical_id = canonical_decision_id(v2["project"], slug, date, new_id)
    new_filename = f"{canonical_id}.md"
    new_path = decisions_dir / new_filename

    fm = _build_frontmatter(
        args,
        new_id=new_id,
        canonical_id=canonical_id,
        slug=slug,
        date=date,
        tags=tags,
        auto_supersede_id=auto_supersede_id,
        v2=v2,
        v3=v3,
    )
    body = {
        "context": args.context,
        "decision": args.decision,
        "alternatives": args.alternatives,
        "consequences": args.consequences,
        "notes": args.notes,
    }
    body_text = render_madr(fm, body)
    history_path: Path | None = None

    try:
        # 4) Write the new MADR atomically
        atomic_write_bytes(new_path, body_text.encode("utf-8"))

        # 5) If supersession, archive the prior + update its frontmatter
        if auto_supersede_id is not None:
            assert same is not None
            prior_path, prior_fm = same
            history_path = archive_to_history(prior_path, prior_fm, history_dir, new_id)
            log(f"archived prior decision {auto_supersede_id} → {history_path}")

        # 6) Regenerate INDEX
        regenerate_index(decisions_dir)

        # 7) Append event(s) to events.jsonl
        prior_fm = same[1] if same is not None else None
        _emit_write_events(
            args,
            events_path,
            new_id=new_id,
            canonical_id=canonical_id,
            auto_supersede_id=auto_supersede_id,
            prior_fm=prior_fm,
            v2=v2,
            v3=v3,
        )
    except (OSError, TimeoutError) as e:
        log(f"filesystem error: {e}")
        return 2

    try:
        memory_root = mul.infer_memory_root_for_path(new_path, fallback=decisions_dir)
        source_commit = fm.get("closing_commit") or None
        related_runs = fm.get("related_runs") or []
        run_id = related_runs[0] if related_runs else None
        if history_path is not None and auto_supersede_id is not None:
            mul.append_update(
                memory_root=memory_root,
                project=v2["project"],
                lane="decisions",
                action="supersede",
                path=history_path,
                writer="write_decision.py",
                run_id=run_id,
                source_workdir=workdir,
                source_commit=source_commit,
                memory_id=auto_supersede_id,
                summary=f"Superseded decision {auto_supersede_id} with {new_id}",
                metadata={"superseded_by": new_id, "canonical_id": canonical_id},
            )
        mul.append_update(
            memory_root=memory_root,
            project=v2["project"],
            lane="decisions",
            action="write",
            path=new_path,
            writer="write_decision.py",
            run_id=run_id,
            source_workdir=workdir,
            source_commit=source_commit,
            memory_id=new_id,
            summary=args.title,
            metadata={
                "canonical_id": canonical_id,
                "confidence": args.confidence,
                "entity": args.entity,
                "primary_tag": args.primary_tag,
                "status": args.status,
                "supersedes": auto_supersede_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        log(f"WARN: memory_update_ledger append failed: {exc}")

    # 8) DB write to the post-cutover schema (default: personal_memory).
    # The Phase B dual-write block was removed at Phase C cutover (2026-05-05).
    # Legacy schema (build_loop_memory) is now read-only; it gets dropped in Phase D.
    if args.db:
        db_dualwrite(new_id, fm, body_text, workdir, args.schema, args.embed_model)

    print(new_id)
    log(f"wrote decision {new_id} to {new_path}")
    return 0
