#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Close the memory read->effect loop at the commit boundary.

WHY THIS EXISTS
---------------
`memory_telemetry.py` has defined four event kinds since day one --
``memory-read``, ``memory-write``, ``memory-effect``, ``memory-use`` -- with a
six-value effect vocabulary, and tests cover all four. But measured across every
lane on 2026-08-31: 41,128 read rows, 794 write rows, and **zero** rows
populating ``memory_ids_used`` or ``effect``. The store records what it looked
at and never what helped, so nothing can rank memory by usefulness.

`context_bootstrap.py` carries a comment explaining that read telemetry was
added because the ledger held only writes -- "measure usage before removing had
no read data". That fix closed the write->read gap. This closes the read->effect
gap one layer up.

WHAT IT MEASURES
----------------
The deterministic analogue of Perplexity Brain's "agent touched primary
evidence" metric: of the memories surfaced to an agent before a commit, which
ones does that commit actually reference?

A memory counts as USED when its id, or the stem of the file that carries it,
appears literally in the commit message or in the lines the commit ADDED. Memory
ids are long unique slugs (`decision-project-build-loop-...-20260524-001`), so a
literal match is high precision by construction.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It never emits a negative. A memory that was surfaced and not referenced is
recorded only in the denominator, never labelled ``ignored``. An agent can be
influenced by a lesson without citing it, so auto-labelling silence as "ignored"
would poison the dataset this exists to create. Precision first: emit only the
provable positive, keep the denominator honest, and let the ignored/stale labels
stay a human or judge call.

ATTRIBUTION LIMIT (measured, not theoretical)
---------------------------------------------
Every read in this store lands in ONE lane (`indexes/TELEMETRY.jsonl`) with no
project field, while Tyrone runs several agents against several repos at once.
A pure time window therefore attributes another workstream's reads to this
commit. Measured on build-loop-memory HEAD~15..HEAD: 180 memories "surfaced",
0 referenced -- and manual inspection confirmed the surfaced ids belonged to a different project
audit records against a commit about Apple notification constraints. Different
project entirely. The denominator was wrong, not the numerator.

`--project SLUG` fixes the denominator by keeping only reads whose surfaced ids
or returned paths mention that project. Both the scoped and unscoped
denominators are always reported, so the filter can never quietly flatter the
rate. The durable fix is a `project` field on `emit_read`; this is the
retrofit that works on rows already written.

Exit codes: 0 always (observability, never a gate).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import memory_telemetry as mt  # type: ignore  # noqa: E402
from _paths import memory_store_root  # type: ignore  # noqa: E402

# A surfaced id must be at least this long to be matched literally. Guards
# against a short or generic id (e.g. "test") matching incidental prose.
MIN_ID_LEN = 12


def _run(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout


def commit_window(repo: Path, sha: str) -> tuple[float, float]:
    """Return (start_epoch, end_epoch) bounding the reads that fed this commit.

    Start is the PARENT commit's timestamp -- the reads that happened while this
    unit of work was being done. End is this commit's timestamp. A root commit
    (no parent) falls back to a 24h lookback so the first commit is not silently
    unmeasurable.
    """
    end = _run(["git", "show", "-s", "--format=%ct", sha], repo).strip()
    end_epoch = float(end) if end else 0.0
    parent = _run(["git", "rev-parse", f"{sha}^"], repo).strip()
    if parent:
        start = _run(["git", "show", "-s", "--format=%ct", parent], repo).strip()
        start_epoch = float(start) if start else end_epoch - 86400.0
    else:
        start_epoch = end_epoch - 86400.0
    return start_epoch, end_epoch


def commit_text(repo: Path, sha: str) -> str:
    """Commit message plus only the ADDED lines of its diff.

    Added lines only: a memory id that appears in a REMOVED line is evidence the
    work moved away from it, not that the work used it.
    """
    msg = _run(["git", "show", "-s", "--format=%B", sha], repo)
    diff = _run(["git", "show", "--format=", "--unified=0", sha], repo)
    added = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    paths = _run(["git", "show", "--format=", "--name-only", sha], repo)
    return "\n".join([msg, added, paths])


def _iter_telemetry(store: Path):
    """Yield (path, row) for every telemetry row across every lane."""
    for path in sorted(store.rglob("TELEMETRY.jsonl")):
        try:
            with path.open(encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield path, json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue


def _epoch(ts: str) -> float:
    try:
        return (
            datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
    except (ValueError, TypeError):
        return 0.0


def read_mentions_project(row: dict, project: str) -> bool:
    """Does this read plausibly belong to `project`?

    Checked against the surfaced ids and any returned paths, which are the only
    project-bearing fields a schema-1.0/1.1 read row carries.
    """
    if not project:
        return True
    hay = " ".join(
        [str(x) for x in (row.get("memory_ids_seen") or [])]
        + [str(x) for x in (row.get("returned_paths") or [])]
    )
    return project in hay


class Ledger:
    """One pass over every lane, reused across commits.

    Re-scanning per commit made this O(commits x ledger): 41k rows x 25 commits
    timed out on a large repo. Load once, index by epoch, sort, then bisect.
    """

    def __init__(self, store: Path) -> None:
        self.reads: list[tuple[float, Path, dict]] = []
        self.effect_reasons: list[tuple[str, str]] = []  # (reason, correlation_id)
        for path, row in _iter_telemetry(store):
            kind = row.get("kind")
            if kind == mt.KIND_READ and row.get("memory_ids_seen"):
                self.reads.append((_epoch(row.get("ts", "")), path, row))
            elif kind in (mt.KIND_USE, mt.KIND_EFFECT):
                cid = row.get("correlation_id")
                if cid:
                    self.effect_reasons.append((row.get("reason") or "", cid))
        self.reads.sort(key=lambda t: t[0])
        self._epochs = [t[0] for t in self.reads]
        # Rows emitted during THIS run. A cached ledger is a snapshot taken
        # before any writes, so without this a second commit in the same range
        # would re-credit a correlation_id the first commit already emitted.
        self._emitted_this_run: set[str] = set()

    def note_emitted(self, correlation_id: str) -> None:
        self._emitted_this_run.add(correlation_id)

    def reads_in_window(self, start: float, end: float) -> list[tuple[Path, dict]]:
        import bisect
        lo = bisect.bisect_left(self._epochs, start)
        hi = bisect.bisect_right(self._epochs, end)
        return [(p, r) for _e, p, r in self.reads[lo:hi]]

    def already_emitted(self, sha: str) -> set[str]:
        key = sha[:12]
        return {cid for reason, cid in self.effect_reasons
                if key in reason} | self._emitted_this_run


def reads_in_window(store: Path, start: float, end: float) -> list[tuple[Path, dict]]:
    """Kept for direct callers/tests; prefer Ledger for multi-commit runs."""
    return Ledger(store).reads_in_window(start, end)


def already_emitted(store: Path, sha: str) -> set[str]:
    """Correlation ids that already carry a use row citing this commit.

    Makes the script idempotent -- re-running over the same range must not
    double-count a memory as used.
    """
    seen: set[str] = set()
    for _path, row in _iter_telemetry(store):
        if row.get("kind") not in (mt.KIND_USE, mt.KIND_EFFECT):
            continue
        if sha[:12] in (row.get("reason") or ""):
            cid = row.get("correlation_id")
            if cid:
                seen.add(cid)
    return seen


def matched_ids(surfaced: list[str], haystack: str) -> list[str]:
    """Surfaced ids that literally appear in the commit text.

    Also matches on the id's own filename stem, since a commit usually cites the
    path rather than the frontmatter id.
    """
    hits = []
    for mid in surfaced:
        if not isinstance(mid, str) or len(mid) < MIN_ID_LEN:
            continue
        stem = Path(mid).stem
        if mid in haystack or (len(stem) >= MIN_ID_LEN and stem in haystack):
            hits.append(mid)
    return hits


def analyze_commit(repo: Path, store: Path, sha: str, *, emit: bool,
                   telemetry_path: Path | None = None,
                   source: str | None = None,
                   project: str = "",
                   ledger: "Ledger | None" = None) -> dict:
    led = ledger if ledger is not None else Ledger(store)
    start, end = commit_window(repo, sha)
    text = commit_text(repo, sha)
    all_rows = led.reads_in_window(start, end)
    rows = [(p, r) for (p, r) in all_rows if read_mentions_project(r, project)]
    unscoped_surfaced = sum(len(r.get("memory_ids_seen") or []) for _p, r in all_rows)
    done = led.already_emitted(sha)

    surfaced_total = 0
    used_total = 0
    emitted = 0
    details = []
    for _path, row in rows:
        surfaced = [m for m in (row.get("memory_ids_seen") or []) if isinstance(m, str)]
        surfaced_total += len(surfaced)
        hits = matched_ids(surfaced, text)
        if not hits:
            continue
        used_total += len(hits)
        cid = row.get("correlation_id") or ""
        details.append({"correlation_id": cid, "used": hits,
                        "reader": row.get("reader_or_writer")})
        if emit and cid and cid not in done:
            mt.emit_use(
                correlation_id=cid,
                memory_ids_used=hits,
                files_read=[],
                # NO effect label. A literal citation proves the memory was
                # REFERENCED; it does not prove it informed the decision. The
                # effect vocabulary has no "referenced" value, and picking the
                # nearest one ("informed_decision") asserts a causal claim the
                # evidence cannot carry -- the same overclaim this module
                # already refuses to make in the negative direction by never
                # emitting "ignored". Effect stays for a human or a judge.
                effect=None,
                reason=f"referenced in commit {sha[:12]}",
                telemetry_path=telemetry_path,
                source=source,
            )
            led.note_emitted(cid)
            emitted += 1

    return {
        "commit": sha[:12],
        "project_filter": project or None,
        "reads_in_window": len(rows),
        "reads_in_window_unscoped": len(all_rows),
        "memories_surfaced": surfaced_total,
        "memories_surfaced_unscoped": unscoped_surfaced,
        "memories_referenced": used_total,
        "reference_rate": round(used_total / surfaced_total, 4) if surfaced_total else None,
        "use_rows_emitted": emitted,
        "already_emitted_skipped": len([d for d in details
                                        if d["correlation_id"] in done]),
        "details": details,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--range", default="HEAD~1..HEAD",
                    help="git rev range to analyze (default: HEAD~1..HEAD)")
    ap.add_argument("--repo", default=".", help="repository to read commits from")
    ap.add_argument("--store", default=None,
                    help="memory store root (default: resolved build-loop-memory root)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be emitted without writing rows")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--telemetry-path", default=None,
                    help="explicit telemetry file (tests)")
    ap.add_argument("--source", default=None, help="telemetry source label")
    ap.add_argument("--project", default=None,
                    help="restrict reads to those mentioning this project slug "
                         "(default: the repo directory name; pass '' to disable)")
    args = ap.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    store = Path(args.store).expanduser() if args.store else memory_store_root()
    if not store.is_dir():
        print(f"memory store not found: {store}", file=sys.stderr)
        return 0

    revs = _run(["git", "rev-list", "--reverse", args.range], repo).split()
    if not revs:
        single = _run(["git", "rev-parse", args.range], repo).strip()
        revs = [single] if single else []
    if not revs:
        print(f"no commits in range {args.range}", file=sys.stderr)
        return 0

    tpath = Path(args.telemetry_path) if args.telemetry_path else None
    project = repo.name if args.project is None else args.project
    ledger = Ledger(store)
    results = [
        analyze_commit(repo, store, sha, emit=not args.dry_run,
                       telemetry_path=tpath, source=args.source, project=project,
                       ledger=ledger)
        for sha in revs
    ]

    surfaced = sum(r["memories_surfaced"] for r in results)
    referenced = sum(r["memories_referenced"] for r in results)
    unscoped = sum(r["memories_surfaced_unscoped"] for r in results)
    summary = {
        "range": args.range,
        "project_filter": project or None,
        "commits": len(results),
        "memories_surfaced_unscoped": unscoped,
        "memories_surfaced": surfaced,
        "memories_referenced": referenced,
        "reference_rate": round(referenced / surfaced, 4) if surfaced else None,
        "use_rows_emitted": sum(r["use_rows_emitted"] for r in results),
        "dry_run": bool(args.dry_run),
        "commits_detail": results,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"range {args.range}: {len(results)} commit(s)"
              + (f"  project={project}" if project else "  project=<unfiltered>"))
        print(f"  memories surfaced   : {surfaced}"
              + (f"  (unscoped: {unscoped})" if unscoped != surfaced else ""))
        print(f"  memories referenced : {referenced}"
              + (f" ({100*referenced/surfaced:.1f}%)" if surfaced else ""))
        print(f"  use rows emitted    : {summary['use_rows_emitted']}"
              + ("  [dry-run]" if args.dry_run else ""))
        if surfaced and not referenced:
            print("  note: nothing referenced. Not labelled 'ignored' -- "
                  "silence is not evidence of non-use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
