#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""run_registration_gate.py — warn, at commit time, that this run was never registered.

WHY THIS EXISTS
---------------
2026-08-28, TruePace. A session loaded the build-loop skill, ran a five-lane
assessment, dispatched nine implementer chunks, landed two commits, and captured
nine owner decisions — and wrote NO durable run record. ``intent.md`` was five
weeks stale, ``state.json`` sat at 22 runs with ``phase: None``, and
``run_close_lint.py`` returned ``status: missing``. The USER noticed, not the
system.

``run_close_lint.py`` was already correct and already documented this exact class
("a gate that lives in the script cannot fire when nobody runs the script"). Its
header names three designed callers: Review-G self-assert, the dispatching parent
via ``verify-dispatch.md`` step 6, and **fail-open hook callers with
``--advisory``**. The session was none of them — it acted as the orchestrator
INLINE, so there was no parent to run step 6 and no Review-G to self-assert from.

Caller #3 was specified and never wired. This is that wiring.

WHY HERE, SPECIFICALLY
----------------------
``pre_bash_dispatch.sh`` is a PreToolUse Bash hook. It fires on the TOOL CALL, not
on build-loop's protocol, so it runs whether or not the protocol was entered — it
ran on both of the commits above. ``git_command_classifier.py`` already separates a
genuine ``git commit`` from prose and from paths containing "git", and the
dispatcher deliberately passes sub-gate stderr through to the running session. A
warning written here lands in the operator's context at the moment the first commit
is attempted, which is exactly where it was missing.

SCOPE — WHAT THIS IS AND IS NOT
-------------------------------
This is the LOCAL, ADVISORY half of a two-layer control, and it is deliberately
weak on its own:

- Client-side git-adjacent checks are bypassable and the consensus across the git
  ecosystem is unambiguous — anything that blocks gets ``--no-verify``'d or
  uninstalled the first time it is slow or wrong. So this NEVER blocks: it always
  exits 0 and always emits ``{}``.
- The observed failure was OMISSION, not evasion. Nobody bypassed a check; there
  was no check. An advisory warning is sufficient for that case and is the whole
  point: it converts a silent nothing into a visible something.
- The authoritative half belongs somewhere the operator cannot route around — a
  freshness check at the START of the next run, which compares commits since the
  last recorded run entry and reports the gap as a positive finding. That is
  tracked separately; this gate does not pretend to cover it.

FAIL-CLOSED IN JUDGEMENT, FAIL-OPEN IN EFFECT
---------------------------------------------
Watchdog principle: silence is evidence of a problem, not absence of evidence. A
missing run entry for a repo that has build-loop state is a POSITIVE finding and is
reported as one. But reporting is all it does — the process is never wedged.

NOT FIRING IS THE DEFAULT
-------------------------
Three separate conditions must all hold before a single line is written, because a
gate that cries wolf is worse than no gate (a noisy check gets muted, and a muted
check is the failure this file exists to prevent):

1. The repo has ``.build-loop/state.json``. No build-loop state means this is not a
   build-loop-managed repo and the question does not apply.
2. ``run_close_lint`` reports ``missing``. Its ``skipped`` status already covers
   "nothing ran here", and a fresh repo with no runs yet must not be nagged — the
   Prometheus ``absent()`` lesson, where an absence alert without a settling clause
   false-fires before the first legitimate sample exists.
3. This repo has not already been warned about in this session. One line per repo
   per session; a warning repeated on every commit is a warning that gets ignored.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# The lint lives two directories up from scripts/hooks/.
_HOOK_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _HOOK_DIR.parent
_LINT = _SCRIPTS_DIR / "run_close_lint.py"

# How far back a run entry still counts as covering this commit. Generous on
# purpose: a long session that registered its run hours ago is fine, and the cost
# of a false negative here (one missed warning) is far lower than the cost of a
# false positive (a warning the operator learns to skip past).
_WINDOW_MINUTES = 720

_MARKER_PREFIX = "buildloop-runreg-"


def _repo_root(start: str) -> Path | None:
    """Nearest ancestor containing .build-loop/state.json, or None."""
    try:
        here = Path(start).resolve()
    except Exception:
        return None
    if not here.is_dir():
        here = here.parent
    for candidate in [here, *here.parents]:
        if (candidate / ".build-loop" / "state.json").is_file():
            return candidate
    return None


def _session_marker(session_id: str, repo: Path) -> Path:
    """One marker per (session, repo) so the warning fires at most once."""
    key = hashlib.sha256(f"{session_id}|{repo}".encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"{_MARKER_PREFIX}{key}"


def _lint_status(repo: Path) -> str | None:
    """Run the lint in advisory mode. Returns its status, or None if unusable."""
    if not _LINT.is_file():
        return None
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(_LINT),
                "--workdir",
                str(repo),
                "--expect-recent-minutes",
                str(_WINDOW_MINUTES),
                "--advisory",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    try:
        return str(json.loads(proc.stdout).get("status") or "") or None
    except Exception:
        return None


def _message(repo: Path) -> str:
    return (
        "[build-loop] This run has no durable record.\n\n"
        f"  repo    {repo}\n"
        "  status  run_close_lint: missing — commits are landing, but "
        "state.json.runs[] has no entry covering them.\n\n"
        "A run that closes without a record is invisible to Phase 6 Learn, to the\n"
        "next session's context bootstrap, and to you. Register it:\n\n"
        '  python3 "$BUILD_LOOP_ROOT/scripts/write_run_entry/__main__.py" \\\n'
        f'      --workdir "{repo}" \\\n'
        '      --goal "<what this run is doing>" \\\n'
        "      --outcome partial --scope build\n\n"
        "If this genuinely is not a build-loop run — a typo fix, a hook-only\n"
        "commit — ignore this. It is advisory and will not fire again this session\n"
        "for this repo.\n"
    )


def evaluate(cwd: str, session_id: str, *, mark: bool = True) -> str | None:
    """Return the warning text, or None to stay silent.

    Split out from main() so the tests can drive it directly without
    constructing hook payloads or capturing stderr.
    """
    repo = _repo_root(cwd)
    if repo is None:
        return None  # not a build-loop-managed repo

    marker = _session_marker(session_id, repo)
    if marker.exists():
        return None  # already said this once

    if _lint_status(repo) != "missing":
        return None  # recorded, skipped, or the lint could not speak

    if mark:
        try:
            marker.touch()
        except Exception:
            pass  # an unwritable temp dir means we may warn twice; harmless
    return _message(repo)


def main() -> int:
    # Every failure path below prints '{}' and returns 0. This gate must never
    # be the reason a commit does not happen.
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    try:
        if not isinstance(payload, dict):
            payload = {}
        cwd = str(payload.get("cwd") or os.getcwd())
        session_id = str(payload.get("session_id") or "nosession")
        warning = evaluate(cwd, session_id)
    except Exception:
        print("{}")
        return 0

    print("{}")
    if warning:
        sys.stderr.write(warning)
    return 0  # advisory, always


if __name__ == "__main__":
    raise SystemExit(main())
