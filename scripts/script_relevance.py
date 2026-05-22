#!/usr/bin/env python3
# capability:
#   purpose: Cross-reference scripts vs run history + git to flag stale and orphaned scripts.
#   application: meta
#   status: active
"""Script relevance / staleness detector (G5).

The capability registry tells the orchestrator what each script *is*; this
detector tells it which scripts are still *worth routing to*. A registry
polluted with completed one-shot migrations and unreferenced orphans routes
agents to dead code — a coordination failure.

For each top-level `scripts/*.py` (excluding `_`-private, `test_`, and
`_attic/`), the detector cross-references three signals:

  1. Authored status   — the `# capability:` header's `status` field
                         (active|deprecated|oneshot-complete|experimental|
                         unknown). `oneshot-complete` and `deprecated` are
                         _attic candidates regardless of other signals.
  2. Git last-touched  — `git log -1 --format=%ct` for the file. A script
                         untouched for longer than --stale-days is "cold".
  3. Reference scan    — whether any other tracked repo file mentions the
                         script's name (an orphan is referenced nowhere).

It emits a JSON report: per-script verdict plus an `attic_candidates` list
(scripts safe to move to `scripts/_attic/`). Read-only — never moves a file.

Verdicts:
  keep        — status active/experimental AND referenced
  review      — cold (untouched > stale-days) OR status unknown
  attic       — status deprecated/oneshot-complete, OR orphan + cold

Stdlib only. Exit 0 always (a report, not a gate).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]
_VALID_STATUSES = ("active", "deprecated", "oneshot-complete", "experimental")
_CAP_HEADER_KEY_RE = re.compile(r"^#\s*(purpose|application|status)\s*:\s*(.+?)\s*$")


def _parse_capability_header(text: str) -> dict[str, str]:
    """Extract an authored `# capability:` header (mirror of registry parser)."""
    out: dict[str, str] = {}
    in_block = False
    for line in text.splitlines()[:60]:
        stripped = line.strip()
        if re.match(r"^#\s*capability\s*:\s*$", stripped):
            in_block = True
            continue
        if in_block:
            m = _CAP_HEADER_KEY_RE.match(stripped)
            if m:
                out[m.group(1)] = m.group(2).strip()
                continue
            break
    return out


def _git_last_touched(repo: Path, rel: str) -> int | None:
    """Unix timestamp of the last commit touching `rel`, or None."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%ct", "--", rel],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return int(out.stdout.strip())
    except ValueError:
        return None


def _run_history_names(repo: Path) -> set[str]:
    """Script basenames referenced anywhere in .build-loop/state.json.runs[].

    Best-effort: state.json is free-form, so we scan its raw text for any
    `scripts/<name>.py` mention rather than assuming a fixed schema.
    """
    state = repo / ".build-loop" / "state.json"
    try:
        raw = state.read_text(encoding="utf-8")
    except OSError:
        return set()
    return set(re.findall(r"scripts/([A-Za-z0-9_]+)\.py", raw))


def _referenced_elsewhere(repo: Path, stem: str, self_rel: str) -> bool:
    """True when any tracked repo file (other than the script itself)
    mentions the script's basename."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "grep", "-l", "-F", f"{stem}.py"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return True  # cannot tell -> assume referenced (conservative)
    if out.returncode not in (0, 1):
        return True
    hits = [h for h in out.stdout.splitlines() if h and h != self_rel]
    return bool(hits)


def analyze(repo: Path, stale_days: int) -> dict[str, Any]:
    scripts_dir = repo / "scripts"
    now = time.time()
    stale_cutoff = now - stale_days * 86400
    run_names = _run_history_names(repo)
    results: list[dict[str, Any]] = []

    for p in sorted(scripts_dir.glob("*.py")):
        if p.name.startswith("_") or p.name.startswith("test_"):
            continue
        rel = p.relative_to(repo).as_posix()
        stem = p.stem
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            text = ""
        header = _parse_capability_header(text)
        status = header.get("status", "").strip().lower()
        if status not in _VALID_STATUSES:
            status = "unknown"

        last_touched = _git_last_touched(repo, rel)
        cold = last_touched is not None and last_touched < stale_cutoff
        referenced = _referenced_elsewhere(repo, stem, rel)
        in_run_history = stem in run_names

        # Verdict.
        if status in ("deprecated", "oneshot-complete"):
            verdict, reason = "attic", f"authored status={status}"
        elif not referenced and cold and not in_run_history:
            verdict, reason = "attic", "orphan + cold + no run-history use"
        elif cold or status == "unknown":
            bits = []
            if cold:
                bits.append("cold (untouched > stale-days)")
            if status == "unknown":
                bits.append("no authored capability header")
            verdict, reason = "review", "; ".join(bits)
        else:
            verdict, reason = "keep", "active/experimental + referenced"

        results.append({
            "script": rel,
            "status": status,
            "verdict": verdict,
            "reason": reason,
            "referenced_elsewhere": referenced,
            "in_run_history": in_run_history,
            "last_touched_epoch": last_touched,
            "cold": cold,
        })

    attic = [r["script"] for r in results if r["verdict"] == "attic"]
    review = [r["script"] for r in results if r["verdict"] == "review"]
    return {
        "schema_version": "1.0",
        "generator": "script_relevance.py",
        "repo_root": str(repo),
        "stale_days": stale_days,
        "total_scripts": len(results),
        "counts_by_verdict": {
            "keep": sum(1 for r in results if r["verdict"] == "keep"),
            "review": len(review),
            "attic": len(attic),
        },
        "attic_candidates": attic,
        "review_candidates": review,
        "scripts": results,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=str(REPO_ROOT_DEFAULT))
    p.add_argument("--stale-days", type=int, default=120,
                   help="A script untouched longer than this is 'cold'.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    repo = Path(args.workdir).expanduser().resolve()
    if not (repo / "scripts").is_dir():
        print(f"no scripts/ dir under {repo}", file=sys.stderr)
        return 1

    report = analyze(repo, args.stale_days)
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        c = report["counts_by_verdict"]
        print(f"{report['total_scripts']} scripts: "
              f"keep={c['keep']} review={c['review']} attic={c['attic']}")
        for s in report["attic_candidates"]:
            print(f"  attic-candidate: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
