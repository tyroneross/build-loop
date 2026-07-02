#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""readme_currency_check.py — Phase 4 Review-G gate: did a run that changed
user-facing surface also update the README?

The gap this closes: build-loop verifies code, tests, and (for plugin repos)
README<->cache SYNC — but nothing checks that a run which added/removed/renamed a
user-facing surface (a command, skill, agent, CLI flag) actually DOCUMENTED it in
the README. So features ship undocumented and the README silently drifts stale.

Contract (build-loop hook charter):
  * Deterministic, zero-dependency, stdlib only. Python 3.11+.
  * Advisory + WARN-first: exit 0 ALWAYS; never blocks a build.
  * Generic: works in any consumer repo, not just plugin repos. Surface globs +
    README paths are configurable via .build-loop/config.json > readmeCurrency.
  * Self-gating: no diff range / no surface change / disabled → skips cleanly.

Logic: over the run's diff range (base..head), if any changed file matches a
`surfaceGlobs` entry AND no changed file matches a `readmePaths` entry, emit a
WARN listing the surface changes. Otherwise pass.

Config (.build-loop/config.json):
  {"readmeCurrency": {
      "enabled": true,
      "surfaceGlobs": ["commands/**", "skills/**/SKILL.md", "agents/**", "cli/**", "bin/**"],
      "readmePaths":  ["README.md", "README.*", "readme.md", "docs/README*", "AGENTS.md"]
  }}

Usage:
  python3 scripts/readme_currency_check.py --workdir . --diff-range <base>..<head> [--json]
  python3 scripts/readme_currency_check.py --workdir . --base-sha <sha> [--json]   # head defaults to HEAD
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_DEFAULT_SURFACE = ["commands/**", "skills/**/SKILL.md", "agents/**", "cli/**", "bin/**"]
_DEFAULT_README = ["README.md", "README.*", "readme.md", "docs/README*", "AGENTS.md"]


def _glob_to_re(glob: str) -> re.Pattern:
    """Tiny glob → regex supporting ** (any depth, incl. /) and * (one segment)."""
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        if glob[i:i + 2] == "**":
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _matches_any(path: str, globs: list[str]) -> bool:
    base = path.rsplit("/", 1)[-1]
    for g in globs:
        pat = _glob_to_re(g)
        # match on full path OR (for basename-style globs with no slash) the basename
        if pat.match(path) or ("/" not in g and pat.match(base)):
            return True
    return False


def _load_config(workdir: Path) -> dict:
    cfg_path = workdir / ".build-loop" / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        rc = cfg.get("readmeCurrency", {}) if isinstance(cfg, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        rc = {}
    return {
        "enabled": rc.get("enabled", True),
        "surfaceGlobs": rc.get("surfaceGlobs", _DEFAULT_SURFACE),
        "readmePaths": rc.get("readmePaths", _DEFAULT_README),
    }


def _changed_files(workdir: Path, diff_range: str) -> list[str]:
    r = subprocess.run(
        ["git", "-C", str(workdir), "diff", "--name-only", diff_range],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return []
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def evaluate(changed: list[str], cfg: dict) -> dict:
    if not cfg["enabled"]:
        return {"verdict": "skipped", "reason": "readmeCurrency disabled", "surface_changes": [], "readme_touched": False}
    surface = [f for f in changed if _matches_any(f, cfg["surfaceGlobs"])]
    readme_touched = any(_matches_any(f, cfg["readmePaths"]) for f in changed)
    if not surface:
        return {"verdict": "skipped", "reason": "no user-facing surface changed", "surface_changes": [], "readme_touched": readme_touched}
    if readme_touched:
        return {"verdict": "ok", "reason": "surface changed and a README was updated", "surface_changes": surface, "readme_touched": True}
    return {
        "verdict": "warn",
        "reason": f"{len(surface)} user-facing surface change(s) but no README/AGENTS.md update in this run",
        "surface_changes": surface,
        "readme_touched": False,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Phase-4G gate: README currency vs user-facing surface changes.")
    p.add_argument("--workdir", default=".")
    p.add_argument("--diff-range", default="", help="git diff range, e.g. <base>..<head>")
    p.add_argument("--base-sha", default="", help="base sha; head defaults to HEAD (used if --diff-range absent)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    workdir = Path(args.workdir).expanduser().resolve()
    diff_range = args.diff_range or (f"{args.base_sha}..HEAD" if args.base_sha else "")
    if not diff_range:
        # try state.json preBuildSha as the base
        try:
            st = json.loads((workdir / ".build-loop" / "state.json").read_text())
            base = st.get("preBuildSha")
            diff_range = f"{base}..HEAD" if base else ""
        except (FileNotFoundError, json.JSONDecodeError):
            diff_range = ""
    result = {"verdict": "skipped", "reason": "no diff range (pass --diff-range/--base-sha or set preBuildSha)",
              "surface_changes": [], "readme_touched": False} if not diff_range \
        else evaluate(_changed_files(workdir, diff_range), _load_config(workdir))

    if args.json:
        print(json.dumps(result))
    else:
        v = result["verdict"]
        print(f"readme_currency: {v.upper()} — {result['reason']}")
        for f in result["surface_changes"]:
            print(f"  surface: {f}")
        if v == "warn":
            print("  → update README.md / AGENTS.md to document the change, or set readmeCurrency.enabled=false if intentional.")
    return 0  # advisory: never blocks


if __name__ == "__main__":
    raise SystemExit(main())
