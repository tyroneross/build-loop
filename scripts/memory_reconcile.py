#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Join memory retrievals to the file opens that followed them.

WHAT THIS ANSWERS
-----------------
The store logged 41,128 retrievals and zero records of which memory helped. Both
halves of that signal already existed and could not see each other: a retrieval
row carries memory IDS and (now) PATHS; a tool-trace span carries a PATH and a
SESSION. This joins them and emits `memory-use`.

WHAT IT DELIBERATELY DOES NOT CLAIM
-----------------------------------
An open is evidence a memory was **inspected**, never that it **helped**. Every
row emitted here says "opened at rank N" and leaves `effect` unset. An agent can
open a memory and reject it, or act on a title without opening anything.

A panel of six architects reached one conclusion independently and without
prompting: **an open must never feed the ranker or the pruner.** Lower-ranked
items are examined less regardless of merit, so feeding opens back builds a
system that promotes whatever it already promoted. That is why every emitted row
carries the `rank` it was shown at: so a later consumer can correct for position
instead of trusting the raw count. Nothing here writes to a ranker.

BUILT FOR REPLACEMENT, NOT FOR CORRECTNESS-ON-DAY-ONE
-----------------------------------------------------
Two things are known to be wrong at the start and are expected to change, so both
are registries rather than hardcoded logic:

- **Extractors** turn a tool-call span into the paths it touched. `Read` is
  trivial; `Bash` needs a regex over the command line, which is how one agent
  runtime reads files at all. Adding a runtime means adding an extractor, not
  editing the join.
- **Strategies** decide when a retrieval and an open belong together. Retrieval
  rows do not carry a session id yet, so the strict join is not yet available.
  Start loose, measure, tighten. Switching is a flag, not a rewrite.

Every strategy reports what it matched AND what the looser strategies would have
matched, so tightening the join can never quietly flatter the numbers.

Exit codes: 0 always. This is observability and must never gate anything.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import memory_telemetry as mt  # noqa: E402
from _paths import memory_store_root  # noqa: E402

TRACE_REL = Path(".build-loop/telemetry/tool-traces.jsonl")
TRACE_GLOB = "*/.build-loop/telemetry/tool-traces.jsonl"

# The trace hook writes to $CLAUDE_PROJECT_DIR/.build-loop/telemetry/, and that
# directory is NOT always a repo under a single parent. A session whose project
# dir is the home directory writes to ~/.build-loop/telemetry/ -- 10 MB and
# 1,180 spans of it, entirely invisible to a glob rooted at the repo folder.
# Discovery is therefore a LIST, and --trace-root repeats.
DEFAULT_TRACE_ROOTS = (
    Path.home(),                       # sessions whose project dir is $HOME
    Path.home() / "dev" / "git-folder",  # per-repo sessions
)


def discover_traces(roots: Sequence[Path]) -> list[Path]:
    """Every tool-trace file under any given root, deduped.

    Checks both `<root>/.build-loop/...` (root IS the project dir) and
    `<root>/*/.build-loop/...` (root CONTAINS project dirs), because both
    layouts occur and assuming one silently drops the other.
    """
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        root = Path(root).expanduser()
        candidates = [root / TRACE_REL, *sorted(root.glob(TRACE_GLOB))]
        for c in candidates:
            key = str(c.resolve()) if c.exists() else str(c)
            if c.is_file() and key not in seen:
                seen.add(key)
                found.append(c)
    return found

# A path-shaped token in free shell text. Deliberately conservative: absolute,
# and ending in a file extension, so `cd /tmp` or a bare directory does not
# register as reading a document.
_SHELL_PATH_RE = re.compile(r"(/(?:[\w.\-+@]+/)+[\w.\-+@]+\.[A-Za-z0-9]{1,8})")


# --------------------------------------------------------------------------
# Extractors: span -> paths touched.  Registry so a new runtime is additive.
# --------------------------------------------------------------------------

Extractor = Callable[[dict], list[str]]
_EXTRACTORS: dict[str, Extractor] = {}


def extractor(*tool_names: str) -> Callable[[Extractor], Extractor]:
    def register(fn: Extractor) -> Extractor:
        for name in tool_names:
            _EXTRACTORS[name] = fn
        return fn
    return register


def _preview(attrs: dict) -> Any:
    """Parse the recorded call arguments. Returns dict, str, or None.

    The preview is truncated for large calls (`...arguments.truncated`), so a
    parse failure is expected and is not an error: fall back to raw text so the
    shell extractor can still find paths in what survived.
    """
    raw = attrs.get("gen_ai.tool.call.arguments.preview")
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


@extractor("Read", "Edit", "Write", "NotebookEdit")
def _extract_file_path(attrs: dict) -> list[str]:
    """Tools that name their target explicitly."""
    p = _preview(attrs)
    if isinstance(p, dict):
        for key in ("file_path", "path", "notebook_path"):
            v = p.get(key)
            if isinstance(v, str) and v.startswith("/"):
                return [v]
    if isinstance(p, str):
        return _SHELL_PATH_RE.findall(p)
    return []


@extractor("Bash", "exec", "shell", "run_command")
def _extract_shell_paths(attrs: dict) -> list[str]:
    """Paths named inside a shell command.

    This is the coverage that makes non-Claude runtimes visible at all: one agent
    runtime reads files through shell (`sed -n`, `cat`, `rg`) rather than a Read
    tool, so a Read-only matcher would silently report on one runtime and call it
    a system-wide number.

    Necessarily lossy. A command can name a path it never reads (`ls`, `rm`), and
    a heredoc can contain a path as text. Treated as weaker evidence than an
    explicit Read: `confidence` on the emitted row records which extractor fired.
    """
    p = _preview(attrs)
    text = ""
    if isinstance(p, dict):
        text = " ".join(str(v) for v in p.values())
    elif isinstance(p, str):
        text = p
    return list(dict.fromkeys(_SHELL_PATH_RE.findall(text)))


@extractor("Grep", "Glob")
def _extract_search_paths(attrs: dict) -> list[str]:
    """Search tools name a directory, not a document. Only an explicit
    file-shaped `path` counts; a bare pattern does not."""
    p = _preview(attrs)
    if isinstance(p, dict):
        v = p.get("path")
        if isinstance(v, str) and v.startswith("/") and "." in Path(v).name:
            return [v]
    return []


EXPLICIT_TOOLS = frozenset({"Read", "Edit", "Write", "NotebookEdit", "Grep", "Glob"})


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

@dataclass
class Open:
    path: str
    session: str | None
    ts: float
    tool: str
    run_id: str | None = None

    @property
    def confidence(self) -> str:
        """`explicit` when the tool named the file; `inferred` from shell text."""
        return "explicit" if self.tool in EXPLICIT_TOOLS else "inferred"


@dataclass
class Read:
    correlation_id: str
    ts: float
    query: str
    ids: list[str]
    paths: list[str]
    ranks: list[int]
    scores: list[float | None]
    session: str | None
    reader: str
    source: str

    def rank_of(self, index: int) -> int:
        return self.ranks[index] if index < len(self.ranks) else index

    def score_of(self, index: int) -> float | None:
        return self.scores[index] if index < len(self.scores) else None


@dataclass
class Match:
    read: Read
    opened: list[tuple[str, str, int, float | None, Open]] = field(default_factory=list)
    #                  id    path  rank  score          open


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def normalize_path(raw: str, store: Path) -> str:
    """Canonicalise a path so both sides of the join can meet.

    Retrieval rows and tool-trace spans disagree on path form, and this defeated
    the join entirely on the first real run: 247 read paths against 2,345 open
    paths produced ZERO intersection, purely because backends emit store-relative
    paths (`projects/x/decisions/y.md`) while spans record absolute ones.

    Normalising here rather than in each backend is deliberate: it fixes the
    historical rows too, and a new backend that gets path form wrong cannot
    silently zero the signal again.
    """
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute():
        path = store / path
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _epoch(ts: str) -> float:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


def load_opens(trace_roots: Sequence[Path] | Path, since: float = 0.0,
               store: Path | None = None) -> list[Open]:
    if isinstance(trace_roots, (str, Path)):
        trace_roots = [Path(trace_roots)]
    out: list[Open] = []
    for trace in discover_traces(trace_roots):
        try:
            fh = trace.open(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    span = json.loads(line)
                except json.JSONDecodeError:
                    continue
                attrs = span.get("attributes") or {}
                tool = attrs.get("gen_ai.tool.name")
                fn = _EXTRACTORS.get(tool or "")
                if fn is None:
                    continue
                nanos = span.get("end_time_unix_nano") or span.get("start_time_unix_nano") or 0
                ts = float(nanos) / 1e9 if nanos else 0.0
                if ts < since:
                    continue
                for path in fn(attrs):
                    if store is not None:
                        path = normalize_path(path, store)
                    out.append(Open(path=path, session=attrs.get("session.id"),
                                    ts=ts, tool=tool or "?",
                                    run_id=attrs.get("build_loop.run_id")))
    out.sort(key=lambda o: o.ts)
    return out


def load_reads(store: Path, since: float = 0.0, runtime_only: bool = True) -> list[Read]:
    out: list[Read] = []
    for lane in sorted(store.rglob("TELEMETRY.jsonl")):
        try:
            fh = lane.open(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("kind") != mt.KIND_READ:
                    continue
                # Legacy rows are dominated by test fixtures and carry no source
                # field, so including them would measure the test suite.
                if runtime_only and row.get("source") != "runtime":
                    continue
                paths = [normalize_path(p, store)
                         for p in (row.get("returned_paths") or [])]
                paths = [p for p in paths if p]
                if not paths:
                    continue  # nothing to join on
                ts = _epoch(row.get("ts", ""))
                if ts < since:
                    continue
                out.append(Read(
                    correlation_id=row.get("correlation_id") or "",
                    ts=ts,
                    query=row.get("query") or "",
                    ids=list(row.get("memory_ids_seen") or []),
                    paths=list(paths),
                    ranks=list(row.get("ranks") or []),
                    scores=list(row.get("scores") or []),
                    session=row.get("session_id"),
                    reader=row.get("reader_or_writer") or "?",
                    source=row.get("source") or "?",
                ))
    out.sort(key=lambda r: r.ts)
    return out


# --------------------------------------------------------------------------
# Strategies: when does an open belong to a read?  Registry, not a constant.
# --------------------------------------------------------------------------

Strategy = Callable[[Read, Open, float], bool]
_STRATEGIES: dict[str, Strategy] = {}
STRATEGY_DOCS: dict[str, str] = {}


def strategy(name: str, doc: str) -> Callable[[Strategy], Strategy]:
    def register(fn: Strategy) -> Strategy:
        _STRATEGIES[name] = fn
        STRATEGY_DOCS[name] = doc
        return fn
    return register


@strategy("path", "Path match only. No session, no time bound. Loosest; will "
                  "cross-attribute between concurrent agents. Use as the upper "
                  "bound, never as the reported figure.")
def _s_path(read: Read, op: Open, window: float) -> bool:
    return True  # path equality is checked by the caller's index


@strategy("path-window", "Path match, and the open happened AFTER the read and "
                         "within the window. The default: available today, "
                         "since retrieval rows carry no session id yet.")
def _s_path_window(read: Read, op: Open, window: float) -> bool:
    return read.ts <= op.ts <= read.ts + window


@strategy("session-path-window", "Path match, same session, open after the read "
                                 "and within the window. The correct join. "
                                 "Yields nothing until retrieval rows carry a "
                                 "session id, which is the point: it fails "
                                 "visibly rather than silently guessing.")
def _s_session_path_window(read: Read, op: Open, window: float) -> bool:
    if not read.session or not op.session:
        return False
    return read.session == op.session and read.ts <= op.ts <= read.ts + window


DEFAULT_STRATEGY = "path-window"
DEFAULT_WINDOW_S = 1800.0


# --------------------------------------------------------------------------
# Reconcile
# --------------------------------------------------------------------------

def reconcile(reads: Sequence[Read], opens: Sequence[Open], *,
              strategy_name: str = DEFAULT_STRATEGY,
              window: float = DEFAULT_WINDOW_S) -> list[Match]:
    fn = _STRATEGIES[strategy_name]
    by_path: dict[str, list[Open]] = {}
    for op in opens:
        by_path.setdefault(op.path, []).append(op)

    matches: list[Match] = []
    for read in reads:
        m = Match(read=read)
        for index, path in enumerate(read.paths):
            for op in by_path.get(path, ()):
                if fn(read, op, window):
                    mem_id = read.ids[index] if index < len(read.ids) else path
                    m.opened.append((mem_id, path, read.rank_of(index),
                                     read.score_of(index), op))
                    break  # first qualifying open is enough
        if m.opened:
            matches.append(m)
    return matches


def emit(matches: Iterable[Match], *, strategy_name: str,
         telemetry_path: Path | None = None, source: str | None = None) -> int:
    """Write one `memory-use` row per matched read. Never sets `effect`."""
    count = 0
    for m in matches:
        ids = [mid for mid, _p, _r, _s, _o in m.opened]
        paths = [p for _mid, p, _r, _s, _o in m.opened]
        detail = "; ".join(
            f"{mid}@rank{rank}"
            f"{'' if score is None else f'/score{score:.3f}'}"
            f" via {op.tool}({op.confidence})"
            for mid, _p, rank, score, op in m.opened
        )
        mt.emit_use(
            correlation_id=m.read.correlation_id,
            memory_ids_used=ids,
            files_read=paths,
            # No effect. An open proves the memory was INSPECTED. Assigning
            # "informed_decision" here would be the same overclaim this codebase
            # already refuses to make in the negative direction.
            effect=None,
            reason=f"opened [{strategy_name}] {detail}",
            telemetry_path=telemetry_path,
            source=source,
        )
        count += 1
    return count


def summarize(reads: Sequence[Read], opens: Sequence[Open], window: float) -> dict:
    """Report EVERY strategy, so tightening the join cannot flatter the number."""
    out: dict[str, Any] = {
        "reads_with_paths": len(reads),
        "opens": len(opens),
        "opens_explicit": sum(1 for o in opens if o.confidence == "explicit"),
        "opens_inferred": sum(1 for o in opens if o.confidence == "inferred"),
        "sessions_in_opens": len({o.session for o in opens if o.session}),
        "reads_with_session": sum(1 for r in reads if r.session),
        "window_s": window,
        "by_strategy": {},
    }
    for name in _STRATEGIES:
        ms = reconcile(reads, opens, strategy_name=name, window=window)
        out["by_strategy"][name] = {
            "matched_reads": len(ms),
            "matched_memories": sum(len(m.opened) for m in ms),
            "match_rate": round(len(ms) / len(reads), 4) if reads else None,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=None, help="memory store root")
    ap.add_argument("--trace-root", action="append", default=None,
                    help="repeatable; a project dir OR a directory of project "
                         "dirs. Defaults to $HOME and ~/dev/git-folder.")
    ap.add_argument("--strategy", default=DEFAULT_STRATEGY, choices=sorted(_STRATEGIES),
                    help="join strategy")
    ap.add_argument("--window", type=float, default=DEFAULT_WINDOW_S,
                    help="seconds after a read that an open may still count")
    ap.add_argument("--since", default=None, help="ISO date lower bound, e.g. 2026-08-01")
    ap.add_argument("--include-legacy", action="store_true",
                    help="include non-runtime rows (dominated by test fixtures)")
    ap.add_argument("--emit", action="store_true",
                    help="write memory-use rows (default is dry-run)")
    ap.add_argument("--telemetry-path", default=None)
    ap.add_argument("--source", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true", help="print each match")
    a = ap.parse_args(argv)

    since = 0.0
    if a.since:
        try:
            since = datetime.strptime(a.since, "%Y-%m-%d").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            print(f"bad --since {a.since!r}; expected YYYY-MM-DD", file=sys.stderr)
            return 0

    store = Path(a.store).expanduser() if a.store else memory_store_root()
    reads = load_reads(store, since, runtime_only=not a.include_legacy)
    roots = [Path(r) for r in a.trace_root] if a.trace_root else list(DEFAULT_TRACE_ROOTS)
    opens = load_opens(roots, since, store=store)
    summary = summarize(reads, opens, a.window)

    matches = reconcile(reads, opens, strategy_name=a.strategy, window=a.window)
    summary["strategy"] = a.strategy
    summary["emitted"] = 0
    if a.emit:
        summary["emitted"] = emit(
            matches, strategy_name=a.strategy,
            telemetry_path=Path(a.telemetry_path) if a.telemetry_path else None,
            source=a.source)

    if a.json:
        if a.explain:
            summary["matches"] = [
                {"correlation_id": m.read.correlation_id, "query": m.read.query[:80],
                 "opened": [{"id": mid, "rank": rank, "score": score,
                             "tool": op.tool, "confidence": op.confidence}
                            for mid, _p, rank, score, op in m.opened]}
                for m in matches]
        print(json.dumps(summary, indent=2))
        return 0

    print(f"trace files      : {len(discover_traces(roots))}")
    print(f"reads with paths : {summary['reads_with_paths']}"
          f"   (with session id: {summary['reads_with_session']})")
    print(f"file opens       : {summary['opens']}"
          f"   explicit {summary['opens_explicit']} / inferred {summary['opens_inferred']}"
          f"   across {summary['sessions_in_opens']} sessions")
    print(f"window           : {summary['window_s']:.0f}s")
    print()
    print(f"{'strategy':22s} {'reads matched':>14s} {'memories':>9s} {'rate':>7s}")
    print("-" * 56)
    for name, s in summary["by_strategy"].items():
        mark = " <-" if name == a.strategy else ""
        rate = "-" if s["match_rate"] is None else f"{100*s['match_rate']:.1f}%"
        print(f"{name:22s} {s['matched_reads']:14d} {s['matched_memories']:9d} {rate:>7s}{mark}")
    print()
    if a.emit:
        print(f"emitted {summary['emitted']} memory-use row(s), effect unset")
    else:
        print("dry-run. Pass --emit to write memory-use rows.")
    if a.explain:
        for m in matches[:20]:
            print(f"\n  {m.read.correlation_id}  {m.read.query[:60]!r}")
            for mid, _p, rank, score, op in m.opened:
                sc = "-" if score is None else f"{score:.3f}"
                print(f"     rank {rank:<3d} score {sc:>6s}  {op.tool}({op.confidence})  {mid[:56]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
