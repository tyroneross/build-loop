# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""write.py — atomic active write + best-effort durable promotion.

Active path: ``<workdir>/.build-loop/retrospectives/<YYYY-MM-DD>/<run-id>.md``
Summary    : ``<workdir>/.build-loop/retrospectives/<YYYY-MM-DD>/<run-id>.summary.md``
Durable    : ``<build-loop-memory>/projects/<slug>/retrospectives/<YYYY-MM-DD>/<run-id>.md``

The summary is ≤5 non-blank lines and is surfaced inline to the user in the
run report. The active full file is the agent-readable record; the durable
copy is promoted for cross-run learning.

Writes are atomic (write-to-tmp, ``os.replace``). Errors return a status dict;
the function never raises so the non-gating dispatch never crashes the run.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path
from typing import Any

# mktemp / scratch directory names — never a real project slug. Covers both
# shell `mktemp -d` (`tmp.XXXX`, with a dot) AND Python tempfile.mkdtemp
# (`tmpXXXXXXXX`, no separator) — the latter was missed by an earlier `tmp\d`
# form. `tmp<6+ alnum>` matches tempfile output without catching short real
# names like `tmpl` or `tmux`. Also guards pytest tmpdirs and scratch dirs.
_SCRATCH_SLUG_RE = re.compile(
    r"^(tmp[._-]|tmp[a-z0-9]{6,}$|\.tmp|pytest-|scratchpad$|mktemp|run[-_][0-9]+$)"
)

from retrospective.sections import SECTION_KEYS, SECTION_TITLES

# scripts/ is already on sys.path (the `retrospective` package import above
# requires it), so `_paths` — the canonical, env-overridable build-loop-memory
# resolver shared with memory_writer/recall — imports the same way.
from _paths import build_loop_memory_root  # noqa: E402


# ---------------------------------------------------------------------------
# Rendering helpers.
# ---------------------------------------------------------------------------

def _today_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def render_full_markdown(
    sections: dict[str, Any],
    *,
    run_id: str,
    repo: str = "",
    intent_one_line: str | None = None,
) -> str:
    """Render the full retrospective markdown body from a sections dict.

    Args:
        sections: dict produced by ``sections.build`` (must have all 9
                  SECTION_KEYS plus ``meta`` and ``enforce_candidates``).
        run_id:   the run identifier.
        repo:     optional repo slug for the header.
        intent_one_line: optional one-line intent restatement for the header.

    Returns:
        The complete markdown body, frontmatter-less (markdown only).
    """
    today = _today_iso()
    meta = sections.get("meta") or {}
    lines: list[str] = [
        f"# Retrospective — {run_id}",
        "",
        f"_Date: {today} · Repo: {repo or '(unset)'}_",
    ]
    if intent_one_line:
        lines.append(f"_Intent: {intent_one_line}_")
    lines.append("")
    lines.append(
        f"_Prompts: {meta.get('prompt_count', 0)} · "
        f"Repeated clusters: {meta.get('cluster_count', 0)} · "
        f"Transcript present: {meta.get('transcript_present', False)}_"
    )
    lines.append("")
    for key in SECTION_KEYS:
        lines.append(f"## {SECTION_TITLES[key]}")
        lines.append("")
        body = sections.get(key) or "_(empty)_"
        if not isinstance(body, str):
            body = str(body)
        lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_summary(sections: dict[str, Any], *, run_id: str) -> str:
    """Render a ≤5 non-blank-line inline summary.

    Lines:
      1. one-line headline (run_id + outcome)
      2. takeaways count + lesson count
      3. issues count + enforce-candidate count
      4. user-prompt count + repeated-cluster count
      5. pointer to the full retrospective
    """
    meta = sections.get("meta") or {}
    enforce = sections.get("enforce_candidates") or []
    # Heuristic counts derived from rendered text — keep it deterministic.
    def _count_bullets(text: str) -> int:
        if not text or text.startswith("_"):
            return 0
        return sum(1 for ln in text.splitlines() if ln.lstrip().startswith("-"))
    lesson_n = _count_bullets(sections.get("lessons_learned", ""))
    issue_n = _count_bullets(sections.get("issues_with_causal_tree", ""))
    take_n = _count_bullets(sections.get("key_takeaways", ""))
    return (
        f"Retrospective {run_id} written ({_today_iso()}).\n"
        f"  takeaways: {take_n} · lessons: {lesson_n}\n"
        f"  issues: {issue_n} · enforce-candidates: {len(enforce)}\n"
        f"  user-prompts: {meta.get('prompt_count', 0)} · "
        f"repeated-clusters: {meta.get('cluster_count', 0)}\n"
        f"  full file: .build-loop/retrospectives/{_today_iso()}/{run_id}.md\n"
    )


# ---------------------------------------------------------------------------
# Atomic write.
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, text: str) -> None:
    """Write text to path atomically (tmp → os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------

def write_active(
    workdir: Path,
    run_id: str,
    sections: dict[str, Any],
    *,
    intent_one_line: str | None = None,
    repo: str = "",
) -> dict[str, Any]:
    """Write the active retrospective + summary atomically.

    Returns ``{"active_path": str, "summary_path": str, "status": "ok"}``.
    On IO error returns ``{"active_path": None, "summary_path": None,
    "status": "degraded", "reason": <str>}``.
    """
    try:
        date = _today_iso()
        outdir = workdir / ".build-loop" / "retrospectives" / date
        active = outdir / f"{run_id}.md"
        summary = outdir / f"{run_id}.summary.md"
        _atomic_write(active, render_full_markdown(
            sections, run_id=run_id, repo=repo, intent_one_line=intent_one_line,
        ))
        _atomic_write(summary, render_summary(sections, run_id=run_id))
        return {
            "active_path": str(active),
            "summary_path": str(summary),
            "status": "ok",
        }
    except OSError as e:
        return {
            "active_path": None,
            "summary_path": None,
            "status": "degraded",
            "reason": f"OSError: {e}",
        }


def promote_durable(
    workdir: Path,
    run_id: str,
    sections: dict[str, Any],
    *,
    intent_one_line: str | None = None,
    repo: str = "",
    memory_root: Path | None = None,
) -> dict[str, Any]:
    """Best-effort durable promotion to build-loop-memory.

    Default ``memory_root`` resolves via ``_paths.build_loop_memory_root()``,
    which honors ``$BUILD_LOOP_MEMORY_ROOT`` / ``$BUILD_LOOP_MEMORY_STORE_ROOT``
    / ``$AGENT_MEMORY_ROOT`` and falls back to ``~/dev/git-folder/build-loop-memory``.
    Routing through the shared resolver (instead of a hardcoded path) lets tests
    and sandboxes redirect durable writes via env — production behavior is
    unchanged. When the directory is unreachable (not present, not writable),
    returns ``{"durable_path": None, "status": "skipped", "reason": <str>}``.
    """
    try:
        if memory_root is None:
            memory_root = build_loop_memory_root()
        slug = repo or workdir.name
        # Guard against scratch pollution: a mktemp workdir (`tmp.XXXX`) must
        # never create a `projects/tmp.XXXX/` dir in the curated store. The
        # SessionEnd auto-fire already gates on is_project_dir; this backstops
        # every OTHER caller (manual runs, tests, future callers). Observed
        # 2026-07-08: a smoke test with a mktemp workdir leaked a real durable
        # write into build-loop-memory.
        if not slug or _SCRATCH_SLUG_RE.match(slug):
            return {"durable_path": None, "status": "skipped",
                    "reason": f"non-project slug refused: {slug!r}"}
        # FIX-2 (2026-07-11): a peer-held store must QUEUE the durable promotion
        # into the consumer repo, not silently skip it. Replaces the observed
        # "point --memory-root at scratch to skip" data-loss pattern.
        try:
            import sys as _sys
            _scripts = str(Path(__file__).resolve().parent.parent)
            if _scripts not in _sys.path:
                _sys.path.insert(0, _scripts)
            import promotion_queue as _pq  # noqa: PLC0415
            if _pq.store_busy(memory_root):
                env = _pq.enqueue(
                    workdir,
                    kind="retro-durable",
                    payload={"sections": sections, "intent_one_line": intent_one_line,
                             "repo": repo},
                    reason="store peer-held — retro durable promotion queued",
                    run_id=run_id,
                )
                return {"durable_path": None, "status": "queued",
                        "reason": env.get("reason"), "queue_id": env.get("id")}
        except Exception as _exc:  # noqa: BLE001 — queueing is best-effort; fall through to write
            pass
        date = _today_iso()
        outdir = memory_root / "projects" / slug / "retrospectives" / date
        if not memory_root.exists():
            return {"durable_path": None, "status": "skipped",
                    "reason": f"memory_root absent: {memory_root}"}
        durable = outdir / f"{run_id}.md"
        _atomic_write(durable, render_full_markdown(
            sections, run_id=run_id, repo=repo, intent_one_line=intent_one_line,
        ))
        return {"durable_path": str(durable), "status": "ok"}
    except OSError as e:
        return {"durable_path": None, "status": "degraded",
                "reason": f"OSError: {e}"}


def write_enforce_candidates(
    workdir: Path,
    run_id: str,
    candidates: list[str],
) -> dict[str, Any]:
    """Write each enforce-candidate as a separate file in
    ``.build-loop/proposals/enforce-from-retro/<run-id>-<NN>.md``.

    Returns ``{"paths": [str], "status": "ok"|"skipped"}``. Empty list →
    ``skipped``. Never raises.
    """
    if not candidates:
        return {"paths": [], "status": "skipped"}
    try:
        outdir = workdir / ".build-loop" / "proposals" / "enforce-from-retro"
        outdir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        for i, text in enumerate(candidates, start=1):
            p = outdir / f"{run_id}-{i:02d}.md"
            body = (
                f"# Enforce candidate — {run_id} #{i}\n\n"
                f"_Source: post-push retrospective ({_today_iso()})_\n\n"
                f"## Candidate\n\n{text}\n\n"
                f"## Disposition\n\n"
                f"- [ ] Adopt as default in build-loop\n"
                f"- [ ] Route to Phase 6 Learn as A/B experiment\n"
                f"- [ ] Reject — note reason below\n"
            )
            _atomic_write(p, body)
            paths.append(str(p))
        return {"paths": paths, "status": "ok"}
    except OSError as e:
        return {"paths": [], "status": "degraded", "reason": f"OSError: {e}"}
