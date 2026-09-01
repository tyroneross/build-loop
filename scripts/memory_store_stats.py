#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""One re-runnable command for every headline figure about the memory store.

WHY THIS EXISTS
---------------
An architecture proposal in this repo rested on "8,839 files modified in
August". The number was filesystem mtime, quoted without saying so; version
history showed 4,503 committed. Both are true of different things, and neither
was reproducible, so the disagreement surfaced only when a reviewer happened to
re-measure it.

The defect was not the number. It was that a hand-produced figure entered a
design argument with no path to re-run it. This makes the next architecture
premise a DIFF rather than an assertion.

Every figure here is labelled with what it measures. Where two defensible
definitions exist (files touched by mtime versus by commit), BOTH are printed,
because picking one silently is how the original error happened.

TIER SEPARATION IS NOT OPTIONAL
-------------------------------
40,843 of 41,128 read rows are schema 1.0, predate the `source` field, and are
dominated by test fixtures. Any rate over the whole ledger measures the test
suite. Rates are therefore reported per tier and never blended, matching
`memory_health.py`.

Exit codes: 0 always. Observability, never a gate.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _paths import memory_store_root  # noqa: E402

SKIP_PARTS = {".git", "node_modules", "archive", "raw-originals", "indexes",
              ".venv", "__pycache__", ".build-loop", ".rally"}
READ, WRITE, USE, EFFECT = "memory-read", "memory-write", "memory-use", "memory-effect"


def tier_of(row: dict) -> str:
    """Same taxonomy as memory_health.py. Kept identical on purpose: two tools
    that disagree about what 'clean' means produce two irreconcilable numbers."""
    sv = str(row.get("schema_version") or "1.0")
    if sv == "1.0" or "source" not in row:
        return "legacy"
    return "clean" if row.get("source") == "runtime" else "non_runtime"


def _iter_rows(store: Path):
    for lane in sorted(store.rglob("TELEMETRY.jsonl")):
        try:
            fh = lane.open(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue


def corpus_stats(store: Path, since: str | None) -> dict:
    total = 0
    indexable = 0
    touched_mtime = 0
    cutoff = 0.0
    if since:
        try:
            cutoff = datetime.strptime(since, "%Y-%m-%d").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            cutoff = 0.0
    for p in store.rglob("*.md"):
        total += 1
        if not any(part in SKIP_PARTS for part in p.parts):
            indexable += 1
        if cutoff:
            try:
                if p.stat().st_mtime >= cutoff:
                    touched_mtime += 1
            except OSError:
                pass
    out = {"markdown_files": total, "indexable_files": indexable}
    if since:
        out["since"] = since
        out["files_touched_by_mtime"] = touched_mtime
        out["files_touched_by_commit"] = _committed_since(store, since)
        out["_note"] = ("mtime counts regenerated and uncommitted churn; commit "
                        "counts only what landed. Neither is wrong; they answer "
                        "different questions.")
    return out


def _committed_since(store: Path, since: str) -> int | None:
    try:
        proc = subprocess.run(
            ["git", "log", f"--since={since}", "--name-only", "--pretty=format:"],
            cwd=str(store), capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return len({ln for ln in proc.stdout.splitlines() if ln.strip().endswith(".md")})


def telemetry_stats(store: Path) -> dict:
    tiers: dict[str, dict[str, Any]] = {}
    kinds = Counter()
    for row in _iter_rows(store):
        kind = row.get("kind", "?")
        kinds[kind] += 1
        if kind != READ:
            continue
        t = tiers.setdefault(tier_of(row), {
            "reads": 0, "with_results": 0, "with_paths": 0,
            "with_session": 0, "with_ranks": 0, "readers": Counter()})
        t["reads"] += 1
        t["readers"][row.get("reader_or_writer", "?")] += 1
        if row.get("memory_ids_seen"):
            t["with_results"] += 1
        if row.get("returned_paths"):
            t["with_paths"] += 1
        if row.get("session_id"):
            t["with_session"] += 1
        if row.get("ranks"):
            t["with_ranks"] += 1

    out: dict[str, Any] = {"kinds": dict(kinds), "tiers": {}}
    for name, t in sorted(tiers.items()):
        n = t["reads"]
        out["tiers"][name] = {
            "trustworthy": name == "clean",
            "reads": n,
            "hit_rate": round(t["with_results"] / n, 4) if n else None,
            "zero_result_rate": round(1 - t["with_results"] / n, 4) if n else None,
            "joinable_rate": round(t["with_paths"] / n, 4) if n else None,
            "session_rate": round(t["with_session"] / n, 4) if n else None,
            "exposure_rate": round(t["with_ranks"] / n, 4) if n else None,
            "top_readers": dict(t["readers"].most_common(4)),
        }
    out["loop_closed"] = bool(kinds.get(USE, 0) or kinds.get(EFFECT, 0))
    out["use_rows"] = kinds.get(USE, 0)
    return out


def join_stats(store: Path) -> dict | None:
    """Match coverage per strategy. Optional: skipped if the reconciler is absent."""
    try:
        import memory_reconcile as mrc  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    try:
        reads = mrc.load_reads(store)
        opens = mrc.load_opens(list(mrc.DEFAULT_TRACE_ROOTS), store=store)
        return mrc.summarize(reads, opens, mrc.DEFAULT_WINDOW_S)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:120]}


def collect(store: Path, since: str | None, with_join: bool) -> dict:
    return {
        "store": str(store),
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "corpus": corpus_stats(store, since),
        "telemetry": telemetry_stats(store),
        "join": join_stats(store) if with_join else None,
    }


def render(s: dict) -> str:
    L = [f"Memory store stats  {s['as_of']}", f"  {s['store']}", ""]
    c = s["corpus"]
    L.append("CORPUS")
    L.append(f"  markdown files      : {c['markdown_files']}")
    L.append(f"  indexable           : {c['indexable_files']}  (skip-list applied)")
    if "since" in c:
        L.append(f"  touched since {c['since']}:")
        L.append(f"     by mtime         : {c['files_touched_by_mtime']}")
        L.append(f"     by commit        : {c['files_touched_by_commit']}")
        L.append(f"     {c['_note']}")
    t = s["telemetry"]
    L += ["", "TELEMETRY", f"  event kinds         : {t['kinds']}"]
    for name, d in t["tiers"].items():
        mark = "TRUSTWORTHY" if d["trustworthy"] else "not trustworthy for rates"
        L.append(f"  [{name}] {mark}  reads={d['reads']}")
        pct = lambda v: "-" if v is None else f"{100*v:.1f}%"  # noqa: E731
        L.append(f"     hit {pct(d['hit_rate'])}   zero-result {pct(d['zero_result_rate'])}"
                 f"   joinable {pct(d['joinable_rate'])}"
                 f"   session {pct(d['session_rate'])}"
                 f"   exposure {pct(d['exposure_rate'])}")
        L.append(f"     readers: {d['top_readers']}")
    L.append(f"  loop closed         : {t['loop_closed']}  (use rows: {t['use_rows']})")
    j = s.get("join")
    if j and "by_strategy" in j:
        L += ["", "JOIN COVERAGE"]
        L.append(f"  reads with paths    : {j['reads_with_paths']}"
                 f"   opens: {j['opens']}"
                 f"   ({j['opens_explicit']} explicit / {j['opens_inferred']} inferred)")
        for name, d in j["by_strategy"].items():
            rate = "-" if d["match_rate"] is None else f"{100*d['match_rate']:.1f}%"
            L.append(f"     {name:22s} {d['matched_reads']:5d} reads  "
                     f"{d['matched_memories']:5d} memories  {rate}")
    elif j:
        L.append(f"\nJOIN COVERAGE unavailable: {j.get('error')}")
    L += ["", "Rates are never blended across tiers: legacy rows are dominated by",
          "test fixtures, so a blended figure measures the test suite."]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=None)
    ap.add_argument("--since", default=None, help="YYYY-MM-DD for churn figures")
    ap.add_argument("--no-join", action="store_true", help="skip join coverage (faster)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    store = Path(a.store).expanduser() if a.store else memory_store_root()
    if not store.is_dir():
        print(f"memory store not found: {store}", file=sys.stderr)
        return 0
    s = collect(store, a.since, not a.no_join)
    print(json.dumps(s, indent=2) if a.json else render(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
