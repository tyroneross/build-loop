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
  2. Run the deterministic `transcript-pattern-miner` (pure stdlib, no network,
     no LLM) over the recent window.
  3. Split candidates:
       - skill/workflow proposals  → only when REPEATED (session_count high
         enough that a skill would save time+tokens vs re-deriving the ritual).
       - lesson candidates         → recurring (session_count >= 2) signals.
  4. Write a digest to a retro-staging dir AND drop a `needs-attention` marker.
     The marker is surfaced at the NEXT SessionStart by the existing
     `session-start-surface-markers.sh`, where a cheap in-context agent ratifies
     the recurring signals into well-formed canonical memory + proposed skills.
     The expensive LLM `recursive-retrospective` stays ON-DEMAND (`/retro`).

Why not auto-write canonical memory here: the miner emits pattern SIGNALS
("Bash,Bash,Read ×17"), not lesson prose. Unattended deterministic writes to the
curated store would be noise (violates the user's memory-discipline). Capture is
automatic; promotion-to-quality is one cheap in-context step away.

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


def split_candidates(candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    """skill proposals (repeated workflow, worth the token/time savings) vs lessons."""
    skills, lessons = [], []
    for c in candidates:
        if not isinstance(c, dict):
            continue
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

    with tempfile.TemporaryDirectory() as tmp:
        data = run_miner(build_loop, Path(tmp))
        candidates = data.get("candidates", []) if isinstance(data, dict) else []
        skills, lessons = split_candidates(candidates)
        if not skills and not lessons:
            _log_and_exit()

        RETRO_STAGING.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        staging = RETRO_STAGING / f"{ts}-digest.json"
        staging.write_text(json.dumps(
            {"generated_at": data.get("generated_at"),
             "window": data.get("window_label"),
             "skills": skills, "lessons": lessons,
             "transcript": str(transcript)}, indent=2), encoding="utf-8")
        session_key = transcript.stem or ts
        write_marker(skills, lessons, staging, session_key)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail-open: never block session end
    sys.exit(0)
