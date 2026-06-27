#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Pre-commit auto-regen for the living architecture diagram.

WHY THIS EXISTS
---------------
The diagram (``architecture/model.json``, ``architecture/ARCHITECTURE.md``,
``docs/build-loop-flow-mockup.html``) is generated from source — agents, skills,
scripts, ``hooks/hooks.json``, and the authored flow block in ARCHITECTURE.md.
The CI gate (``check.sh`` -> ``generate.py --check``) only *checks* freshness, so
EVERY structural commit that forgets to run ``generate.py`` by hand goes red.
That is a chronic, recurring failure class — not a one-off.

This hook makes the class impossible to recur: when a commit stages any
diagram-source file, it regenerates the outputs and stages them INTO the same
commit, so the committed diagram is fresh by construction and the gate stays
green without a manual step.

DESIGN (KISS / fail-open)
-------------------------
- Single source of truth for "what is a diagram source": ``generate`` itself
  (``_static_provenance()["auto_sources"]`` + the authored ARCHITECTURE.md).
  We never re-list the globs here — they're derived, so they can't drift.
- FAST SKIP: if no staged file matches a source, exit 0 immediately (the common
  case — most commits don't touch structure). No generate() call, no git churn.
- FAIL-OPEN: any internal error -> exit 0 (do not block the commit). The
  fail-CLOSED backstop is the CI gate; a flaky local hook must never wedge a
  commit. The worst case is a stale diagram caught by CI, same as today.
- Honours ``BL_ARCH_NO_REGEN=1`` to opt out (mirrors ``BL_ARCH_ADVISORY`` on
  the check side) and ``git commit --no-verify`` (skips all hooks natively).

CONTRACT
--------
Run from within the repo (the installed ``.git/hooks/pre-commit`` cd's to the
toplevel first). Exit code is ALWAYS 0 — this hook regenerates + stages, it does
not gate. Prints a one-line note to stderr only when it actually regenerated.

Usage (normally invoked by .git/hooks/pre-commit):
    python3 scripts/architecture_diagram/regen_hook.py [--repo PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = HERE.parent.parent


def _generated_outputs() -> tuple[str, ...]:
    """The generated outputs to stage after a regen — derived from generate.py
    (``generate.OUTPUTS``), the single source of truth, so a new/renamed output
    is staged without a second edit here. DRY mirror of ``_source_globs``."""
    sys.path.insert(0, str(HERE))
    import generate  # noqa: E402 — path set above; local module

    return tuple(generate.OUTPUTS)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


def _staged_files(repo: Path) -> list[str]:
    """Staged paths (added/copied/modified/renamed) relative to repo root."""
    out = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [line for line in out.splitlines() if line]


def _source_globs(repo: Path) -> list[str]:
    """Diagram-source globs, derived from generate.py (single source of truth).

    ``generate._static_provenance()["auto_sources"]`` lists the auto-discovered
    inventories; the authored flow lives in architecture/ARCHITECTURE.md. We add
    that one explicitly because it is BOTH a source (the flow block) and an
    output (the Components block) — editing the flow must trigger a regen.
    """
    sys.path.insert(0, str(HERE))
    import generate  # noqa: E402 — path set above; local module

    globs = list(generate._static_provenance()["auto_sources"])
    globs.append("architecture/ARCHITECTURE.md")
    return globs


def _glob_to_re(g: str) -> str:
    """Translate a path glob to an anchored regex with POSIX-shell semantics:
    ``*`` matches within a path segment (never ``/``); ``**`` matches across.

    Stdlib ``fnmatch``/``PurePath.match`` don't give both behaviours portably
    on py3.11 (``Path.full_match`` is 3.13+), so we translate once, explicitly.
    """
    out = ["^"]
    i = 0
    while i < len(g):
        if g.startswith("**/", i):
            out.append("(?:.*/)?")  # zero-or-more leading dirs
            i += 3
        elif g.startswith("**", i):
            out.append(".*")
            i += 2
        elif g[i] == "*":
            out.append("[^/]*")
            i += 1
        elif g[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(g[i]))
            i += 1
    out.append(r"\Z")
    return "".join(out)


def _matches_source(staged: list[str], globs: list[str]) -> list[str]:
    pats = [re.compile(_glob_to_re(g)) for g in globs]
    return [p for p in staged if any(rx.match(p) for rx in pats)]


def _generate(repo: Path) -> None:
    """Run generate.py against ``repo`` with the current interpreter.

    A separate process (not an in-proc ``generate.main()`` call) so a generate
    crash can't take down the commit's git process, and so this stays a thin,
    monkeypatchable seam for tests. We use ``sys.executable`` — the interpreter
    running this hook already imported ``yaml`` to derive the globs, so it can
    run generate too.
    """
    gen = HERE / "generate.py"
    subprocess.run(
        [sys.executable, str(gen), "--repo", str(repo)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run(repo: Path, *, dry_run: bool = False) -> dict:
    """Regenerate + stage iff a diagram source is staged. Returns a result dict.

    Never raises for an expected condition — callers treat any exception as a
    fail-open (exit 0).
    """
    if os.environ.get("BL_ARCH_NO_REGEN") == "1":
        return {"action": "skipped", "reason": "BL_ARCH_NO_REGEN=1"}

    staged = _staged_files(repo)
    if not staged:
        return {"action": "skipped", "reason": "no staged files"}

    globs = _source_globs(repo)
    hits = _matches_source(staged, globs)
    if not hits:
        return {"action": "skipped", "reason": "no diagram-source change", "globs": globs}

    if dry_run:
        return {"action": "would-regen", "triggered_by": hits}

    _generate(repo)

    # Stage only the generated outputs that exist (derived from generate.py).
    staged_outputs: list[str] = []
    for rel in _generated_outputs():
        if (repo / rel).exists():
            subprocess.run(["git", "-C", str(repo), "add", "--", rel], check=True)
            staged_outputs.append(rel)

    return {"action": "regenerated", "triggered_by": hits, "staged": staged_outputs}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=str(DEFAULT_REPO))
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would regenerate without writing or staging.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        repo = Path(args.repo).resolve()
        result = run(repo, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 — fail-open: never block a commit
        sys.stderr.write(f"[arch-regen] internal error — skipping regen: {exc!r}\n")
        return 0

    if args.json:
        import json
        sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    elif result.get("action") == "regenerated":
        sys.stderr.write(
            f"[arch-regen] diagram regenerated + staged "
            f"({len(result.get('staged', []))} files) — triggered by "
            f"{len(result.get('triggered_by', []))} source change(s)\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
