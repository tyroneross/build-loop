#!/usr/bin/env python3
"""SessionEnd retrospective sweep — the auto-actuator for "don't make me ask".

Fixes the standing, ≥6×-recurring, multi-host gap (filed as
`bl-memory-closeout-enforcement`): every retrospective actuator
(`retrospective-synthesizer`, `recursive-retrospective`, build-loop Stop
closeout) is bound to a build-loop RUN. A plain interactive / Rally / Codex
session has no run, so the learning loop is dark unless the user asks. This
script is the missing host-level actuator.

Design (cost-first, deterministic — ZERO LLM / ZERO tokens in this path):
  1. Gate on a NON-TRIVIAL session (enough tool activity or a commit). Trivial
     sessions exit silently — no marker, no noise.
  2. For a real project session (has `.build-loop/` or `.git`) that never opened
     a build-loop run, AUTO-FIRE the deterministic rich retrospective via
     `python3 -m retrospective` — the same synthesizer the Phase 4 dispatch uses,
     run headlessly (sections.py emits captured signals verbatim, no LLM). This
     writes the 9+2-section file AND promotes a durable copy to build-loop-memory.
     Closes the "rich retro only fires on formal runs" gap for interactive /
     Codex / Rally sessions.
  3. Run the deterministic `transcript-pattern-miner` (pure stdlib, no network,
     no LLM) over the recent window.
  4. Split candidates:
       - skill/workflow proposals  → only when REPEATED (session_count high
         enough that a skill would save time+tokens vs re-deriving the ritual).
       - lesson candidates         → recurring (session_count >= 2) signals.
  5. Write a digest to a retro-staging dir AND drop a `needs-attention` marker.
     The marker is surfaced at the NEXT SessionStart by the existing
     `session-start-surface-markers.sh`, where a cheap in-context agent ratifies
     the recurring signals into well-formed canonical memory + proposed skills,
     and MAY enrich the auto-written retro with LLM narration. The expensive
     `recursive-retrospective` deep dive stays ON-DEMAND (`/retro`).

Deterministic-first / AI-narrates: step 2 writes the objective section signals
for free; the ONLY LLM cost is the optional, on-demand enrichment at the next
SessionStart. The miner (step 3) emits pattern SIGNALS ("Bash,Bash,Read ×17"),
not lesson prose, so its promotion-to-canonical-memory stays a cheap in-context
step — capture is automatic, promotion-to-quality is one step away.

Contract: fail-open. ANY error → exit 0, silent. Never blocks session end.
Invoked fire-and-forget (nohup &) by the host SessionEnd hook.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# --- Tunable gates -----------------------------------------------------------
MIN_TOOL_USES = 8          # below this (and no commit) the session is trivial
SKILL_MIN_SESSIONS = 3     # a workflow must recur in >=3 sessions to be worth a skill
LESSON_MIN_SESSIONS = 2    # never n=1 (matches feedback_mine_manual_steps_for_skills_scripts)
MINER_WINDOW_DAYS = 1      # mine the recent window, not all history (cheap)

HOME = Path(os.path.expanduser("~"))
MARKER_DIR = HOME / ".claude" / "cache-telemetry" / "needs-attention"
RETRO_STAGING = HOME / ".claude" / "cache-telemetry" / "retro-staging"
BUILD_LOOP_CANDIDATES = [
    HOME / "dev" / "git-folder" / "build-loop",
    HOME / "Desktop" / "git-folder" / "build-loop",
]


def _log_and_exit() -> None:
    """Fail-open sentinel: anything unexpected → exit 0 silently."""
    sys.exit(0)


def find_build_loop() -> Path | None:
    env = os.environ.get("BUILD_LOOP_DIR")
    if env and (Path(env) / "scripts" / "transcript-pattern-miner.py").exists():
        return Path(env)
    for cand in BUILD_LOOP_CANDIDATES:
        if (cand / "scripts" / "transcript-pattern-miner.py").exists():
            return cand
    return None


def session_is_trivial(transcript: Path) -> bool:
    """Count tool_use blocks; a commit or enough tool activity = non-trivial."""
    tool_uses = 0
    saw_commit = False
    try:
        with transcript.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if '"tool_use"' not in line and "git commit" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message") or {}
                content = msg.get("content")
                if isinstance(content, list):
                    for blk in content:
                        if isinstance(blk, dict) and blk.get("type") == "tool_use":
                            tool_uses += 1
                            inp = blk.get("input") or {}
                            cmd = inp.get("command", "") if isinstance(inp, dict) else ""
                            if isinstance(cmd, str) and "git commit" in cmd:
                                saw_commit = True
    except Exception:
        return True  # unreadable → treat as trivial (do nothing)
    return not (saw_commit or tool_uses >= MIN_TOOL_USES)


def resolve_project_cwd(transcript: Path) -> Path | None:
    """Read the session's working directory from the transcript.

    Claude Code stamps a top-level ``cwd`` field on transcript records. We take
    the first one we can parse. Returns None when unreadable (fail-open)."""
    try:
        with transcript.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if '"cwd"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                cwd = obj.get("cwd")
                if isinstance(cwd, str) and cwd:
                    return Path(cwd)
    except Exception:
        return None
    return None


def is_project_dir(cwd: Path) -> bool:
    """Only synthesize a session retro for a REAL project checkout — one with a
    ``.build-loop/`` state dir or a ``.git`` repo. A bare $HOME / scratch cwd is
    skipped so we don't scatter retrospectives into non-project directories."""
    try:
        if cwd == Path(os.path.expanduser("~")):
            return False
        return (cwd / ".build-loop").is_dir() or (cwd / ".git").exists()
    except Exception:
        return False


def formal_run_retro_exists(cwd: Path) -> bool:
    """True when a formal build-loop RUN already wrote its retrospective TODAY.

    The session retro is for sessions with NO build-loop run (spec: "non-run
    sessions"). If Phase 4 already dispatched the retrospective-synthesizer for
    this session's run, a `<run-id>.md` sits in today's retrospectives dir — so
    firing again would duplicate it. We key on the run id from state.json AND
    today's date dir, so a stale run from a PRIOR day never suppresses a genuine
    run-less session today. Fail-open: any error → False (fire the retro)."""
    try:
        state_p = cwd / ".build-loop" / "state.json"
        if not state_p.is_file():
            return False
        state = json.loads(state_p.read_text(encoding="utf-8"))
        rid = (state.get("execution") or {}).get("build_loop_id")
        if not rid:
            runs = state.get("runs") or []
            for key in ("run_id", "build_loop_id", "id"):
                if runs and runs[-1].get(key):
                    rid = runs[-1][key]
                    break
        if not rid:
            return False
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return (cwd / ".build-loop" / "retrospectives" / today / f"{rid}.md").is_file()
    except Exception:
        return False


def run_session_retro(build_loop: Path, transcript: Path, cwd: Path) -> None:
    """Fire the DETERMINISTIC rich retrospective for a non-run session.

    This is the auto-actuator for "make the rich retro fire automatically" on
    interactive / Codex / Rally sessions that never open a build-loop run. It
    shells to ``python3 -m retrospective`` which is ZERO-LLM (sections.py emits
    captured signals verbatim), writes the 9+2-section file, and promotes a
    durable copy to build-loop-memory. The expensive LLM enrichment stays
    on-demand (the marker invites `/retro`). Fail-open: any error is swallowed.
    """
    session_key = transcript.stem or "session"
    scripts_dir = build_loop / "scripts"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(scripts_dir) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        subprocess.run(
            ["python3", "-m", "retrospective",
             "--workdir", str(cwd),
             "--run-id", f"session-{session_key}",
             "--transcript", str(transcript),
             "--json"],
            check=False, capture_output=True, timeout=120, env=env, cwd=str(scripts_dir),
        )
    except Exception:
        return


def run_miner(build_loop: Path, out_dir: Path) -> dict:
    miner = build_loop / "scripts" / "transcript-pattern-miner.py"
    try:
        subprocess.run(
            ["python3", str(miner), "--days", str(MINER_WINDOW_DAYS),
             "--out-dir", str(out_dir)],
            check=False, capture_output=True, timeout=120,
        )
    except Exception:
        return {}
    cand = out_dir / ".candidates.json"
    if not cand.exists():
        return {}
    try:
        return json.loads(cand.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Core-tool steps carry no reusable semantic content: a sequence made only of
# these is the universal edit-test loop and recurs in EVERY coding session, so
# proposing it as a skill is permanent noise (observed 2026-07-04: the same two
# ×24-session `Edit→Bash` candidates re-marked across 6 consecutive sessions).
GENERIC_TOOL_PREFIXES = {
    "Bash", "Edit", "MultiEdit", "Read", "Write", "Grep", "Glob",
    "Task", "Agent", "TodoWrite", "NotebookEdit", "LS",
}


def sequence_is_generic(c: dict) -> bool:
    """True when a repeated_tool_sequence has only core-tool steps (e.g.
    `Edit:replace_all → Bash:command`). Sequences with a step outside the core
    toolset (Skill, MCP tool, slash command) keep their candidacy; shapes that
    carry content elsewhere (manual_command_ritual) are never gated here."""
    if c.get("shape") != "repeated_tool_sequence":
        return False
    seq = c.get("sequence") or []
    if not seq:
        return False
    return all(str(s).split(":", 1)[0] in GENERIC_TOOL_PREFIXES for s in seq)


def split_candidates(candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    """skill proposals (repeated workflow, worth the token/time savings) vs lessons."""
    skills, lessons = [], []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        if sequence_is_generic(c):
            continue  # universal edit-test loop — noise, not a skill or lesson
        n = c.get("session_count", 0) or 0
        shape = c.get("shape", "")
        kind = c.get("kind", "")
        is_workflow = shape in ("repeated_tool_sequence", "manual_command_ritual") \
            or kind == "skill_or_workflow_candidate"
        if is_workflow and n >= SKILL_MIN_SESSIONS:
            skills.append(c)
        elif n >= LESSON_MIN_SESSIONS:
            lessons.append(c)
    return skills, lessons


def write_marker(skills: list[dict], lessons: list[dict], staging_path: Path,
                 session_key: str) -> None:
    if not skills and not lessons:
        return  # nothing worth surfacing — stay silent
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    # Session-keyed filename: SessionEnd can fire more than once for a session
    # (e.g. /clear then continue) — overwrite rather than accumulate duplicates.
    marker = MARKER_DIR / f"retro-closeout-{session_key}.md"
    lines = [
        f"# 🔁 Session retrospective closeout ({ts})",
        "",
        "_Auto-captured by the SessionEnd retro sweep (deterministic; no LLM)._",
        "Ratify in-context now: promote the recurring items below to canonical",
        "memory (`memory_writer.py`) and create any proposed skill, then resolve",
        "this marker. For a deep dive run `/retro` (recursive-retrospective).",
        "",
    ]
    if skills:
        lines.append(f"## Proposed skills — {len(skills)} repeated workflow(s) (saves time+tokens)")
        for s in skills:
            seq = " → ".join(s.get("sequence", [])) or s.get("rationale", "")
            lines.append(f"- **×{s.get('session_count','?')} sessions** · `{seq}` — {s.get('rationale','')}")
        lines.append("")
    if lessons:
        lines.append(f"## Recurring lesson candidates — {len(lessons)} (n≥{LESSON_MIN_SESSIONS})")
        for l in lessons:
            lines.append(f"- ×{l.get('session_count','?')} · {l.get('rationale', l.get('kind',''))}")
        lines.append("")
    lines.append(f"_Full digest: `{staging_path}`_")
    lines.append("")
    lines.append("---")
    lines.append("_To resolve: ratify the items, then "
                 "`mv <marker> ~/.claude/cache-telemetry/needs-attention/resolved/`_")
    marker.write_text("\n".join(lines), encoding="utf-8")


def write_workdir_digest(cwd: Path | None, payload: dict, ts: str) -> Path | None:
    """ALSO persist the miner digest into the workdir's ``.build-loop/learn/pending/``
    lane (EC-01 coord).

    The digest was previously written only to the GLOBAL, GC-eligible
    ``~/.claude/cache-telemetry/retro-staging`` dir, so the mining signal was
    disconnected from the project's own ``.build-loop/``. This drops a durable,
    project-scoped copy alongside the deterministic retrospective this same sweep
    already writes to ``.build-loop/retrospectives/``, so a later Phase-6 accruing
    pass working in the workdir can consult it.

    Scope note: Phase-6 accruing today reads ``state.json.runs[]`` +
    ``.build-loop/proposals/enforce-from-retro/`` (references/phase-6-learn.md §Detect);
    this lane is the durable workdir-local capture, not yet an automated detector
    input — teaching the detector to fold these candidates in is a follow-up.

    Fail-open: only fires for a real project cwd; any error → None (never blocks)."""
    if cwd is None or not is_project_dir(cwd):
        return None
    try:
        lane = cwd / ".build-loop" / "learn" / "pending"
        lane.mkdir(parents=True, exist_ok=True)
        out = lane / f"{ts}-digest.json"
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return out
    except Exception:
        return None


def resolve_transcript() -> str:
    """Transcript path comes from the SessionEnd stdin JSON `transcript_path`
    field (Claude Code hooks reference) — there is NO `CLAUDE_TRANSCRIPT_PATH`
    env var. The wiring hook extracts it from stdin and passes it as argv[1].
    Fall back to argv, then the (non-standard) env var for manual/test runs."""
    if len(sys.argv) > 1 and sys.argv[1]:
        return sys.argv[1]
    return os.environ.get("CLAUDE_TRANSCRIPT_PATH", "")


def main() -> None:
    transcript_env = resolve_transcript()
    if not transcript_env:
        _log_and_exit()
    transcript = Path(transcript_env)
    if not transcript.exists() or session_is_trivial(transcript):
        _log_and_exit()

    build_loop = find_build_loop()
    if build_loop is None:
        _log_and_exit()

    # Auto-fire the deterministic rich retrospective for real project sessions
    # that never opened a build-loop run. Independent of miner candidates — a
    # session with issues/tool-usage/automation signal but no repeated ritual
    # still deserves its retro + memory write. Zero-LLM, fail-open.
    cwd = resolve_project_cwd(transcript)
    if cwd is not None and is_project_dir(cwd) and not formal_run_retro_exists(cwd):
        run_session_retro(build_loop, transcript, cwd)

    with tempfile.TemporaryDirectory() as tmp:
        data = run_miner(build_loop, Path(tmp))
        candidates = data.get("candidates", []) if isinstance(data, dict) else []
        skills, lessons = split_candidates(candidates)
        if not skills and not lessons:
            _log_and_exit()

        RETRO_STAGING.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        payload = {
            "generated_at": data.get("generated_at"),
            "window": data.get("window_label"),
            "skills": skills, "lessons": lessons,
            "transcript": str(transcript),
        }
        staging = RETRO_STAGING / f"{ts}-digest.json"
        staging.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # ALSO drop a durable, project-scoped copy into the workdir's learn lane
        # (EC-01 coord) so the signal is not stranded in the global cache.
        write_workdir_digest(cwd, payload, ts)
        session_key = transcript.stem or ts
        write_marker(skills, lessons, staging, session_key)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail-open: never block session end
    sys.exit(0)
