# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""synthesize.py — entry point for the post-push retrospective.

Reads the run's transcript JSONL + state.json + intent + plan; calls
``sections.build``; writes the active + summary files; promotes a durable
copy to ``build-loop-memory``; emits each enforce-candidate as a separate
file under ``.build-loop/proposals/enforce-from-retro/``.

Public API::

    run(workdir, *, run_id=None, transcript=None, memory_root=None) -> dict

Non-raising. Background contract: callers do NOT await this. The
``retrospective-synthesizer`` agent dispatch is non-gating, so any error
returns ``status="degraded"`` with a reason rather than crashing the run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from retrospective.locate import find_transcript_for_cwd
from retrospective.sections import build as build_sections
from retrospective.write import (
    write_active,
    promote_durable,
    write_enforce_candidates,
)


def _load_state_json(workdir: Path) -> dict[str, Any]:
    p = workdir / ".build-loop" / "state.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_md(workdir: Path, name: str) -> str:
    p = workdir / ".build-loop" / name
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _derive_run_id(state: dict[str, Any]) -> str:
    """Return a stable run id from state.json.

    Prefers ``state.execution.build_loop_id``; falls back to the latest
    ``runs[-1].run_id`` or 'unknown'.
    """
    exe = state.get("execution") or {}
    if exe.get("build_loop_id"):
        return str(exe["build_loop_id"])
    runs = state.get("runs") or []
    if runs:
        last = runs[-1]
        for k in ("run_id", "build_loop_id", "id"):
            if last.get(k):
                return str(last[k])
    return "unknown"


def _intent_one_line(intent_md: str) -> str | None:
    """Pull the one-line restatement out of intent.md."""
    if not intent_md:
        return None
    m = re.search(r"^## Restated intent.*?\n+([^\n]+)", intent_md, re.M | re.S)
    if m:
        return m.group(1).strip()
    # fall back to first markdown line under the title
    return None


def _derive_repo_slug(workdir: Path) -> str:
    """Best-effort repo slug: directory name. Caller may override."""
    return workdir.resolve().name


def run(
    workdir: Path,
    *,
    run_id: str | None = None,
    transcript: Path | None = None,
    memory_root: Path | None = None,
) -> dict[str, Any]:
    """Synthesize the retrospective for ``workdir``.

    Args:
        workdir:     the build-loop project directory.
        run_id:      override the derived run id.
        transcript:  override the located transcript JSONL.
        memory_root: override the build-loop-memory root for durable promotion.

    Returns:
        {
            "active_path":         str | None,
            "summary_path":        str | None,
            "durable_path":        str | None,
            "enforce_candidates":  list[str],   # file paths
            "status":              "ok" | "degraded" | "skipped",
            "reason":              str | None,
            "meta":                dict,        # from sections.meta
        }
    """
    workdir = Path(workdir).resolve()
    try:
        state = _load_state_json(workdir)
        intent_md = _load_md(workdir, "intent.md")
        plan_md = _load_md(workdir, "plan.md")
        rid = run_id or _derive_run_id(state)
        tx = transcript if transcript is not None else find_transcript_for_cwd(workdir)
        repo = _derive_repo_slug(workdir)
        intent_one = _intent_one_line(intent_md)

        sections = build_sections(tx, state, intent_md, plan_md, rid)

        active = write_active(workdir, rid, sections,
                              intent_one_line=intent_one, repo=repo)
        durable = promote_durable(workdir, rid, sections,
                                  intent_one_line=intent_one, repo=repo,
                                  memory_root=memory_root)
        enforce = write_enforce_candidates(workdir, rid,
                                            sections.get("enforce_candidates") or [])

        return {
            "active_path":        active.get("active_path"),
            "summary_path":       active.get("summary_path"),
            "durable_path":       durable.get("durable_path"),
            "enforce_candidates": enforce.get("paths", []),
            "status":             active.get("status", "ok"),
            "reason":             active.get("reason"),
            "meta":               sections.get("meta") or {},
        }
    except Exception as e:  # noqa: BLE001  — never raise from background dispatch
        return {
            "active_path":        None,
            "summary_path":       None,
            "durable_path":       None,
            "enforce_candidates": [],
            "status":             "degraded",
            "reason":             f"{type(e).__name__}: {e}",
            "meta":               {},
        }
