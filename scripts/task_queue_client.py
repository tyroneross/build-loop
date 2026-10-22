#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""task_queue_client.py — the one place build-loop reads the shared task queue.

Two jobs, both backend-agnostic:

1. **Lookup before filing or fixing.** `find_duplicate()` matches a finding
   against the queue's OPEN cards by normalized title + repo, so an agent
   attaches to the existing card instead of filing a second one.
2. **Close-on-fix.** A commit that addresses a card carries the trailer
   ``Task: <id>``; the queue's landing check closes the card when that commit
   reaches main. ``OC-Task: <id>`` is accepted as a legacy alias on parse.

The queue backend sits behind `TaskQueueClient` (two methods: `list_open`,
`attach`). Today the only backend is the RossLabs Operations Center
(`RossLabsOCClient`, read via its MCP `list_tasks` tool, attach via its
`receipt` CLI). Easy Terminal's `et task` store can replace it by implementing
the same two methods — see the module docstring section "Swapping to et task".

Dedupe is deliberately EXACT after normalization (no fuzzy similarity). A
false positive swallows a distinct finding into someone else's card, which is
worse than a duplicate card the queue's own intake dedupe can still catch.

Swapping to et task: a backend needs (a) a JSON list of open cards carrying
id, title, status and the repo each card targets, (b) an append-note call on a
card id, (c) a landing check that closes a card when a ``Task: <id>`` trailer
lands on the target repo's main. `et task list` returns JSON and `et task note`
appends; the card's repo field (`--repo-root` on create) must be returned by
`list` for (a), and (c) does not exist yet in et-operationsd.

CLI::

    task_queue_client.py lookup  --repo <repo> --title <title> [--json]
    task_queue_client.py trailer --task-id <id-or-prefix>       # prints "Task: <full-id>"
    task_queue_client.py parse   --message <commit message>     # ids from Task:/OC-Task: trailers

Exit codes: 0 ok (lookup: match or no match), 1 backend unavailable / id not
an open card, 2 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Protocol

OPEN_STATUSES = ("todo", "working", "needs_input", "review")
TRAILER_KEY = "Task"
LEGACY_TRAILER_KEYS = ("OC-Task",)

# Leading conventional-commit / tag prefixes that do not change what the finding is:
# "fix: x", "fix(scope): x", "[bug] x", "bug: x", and a bare leading "Fix " ("Fix x"
# and "fix: x" name the same defect). Other leading verbs stay: "Add x" != "Remove x".
_PREFIX_RE = re.compile(
    r"^\s*(?:\[[^\]]{1,20}\]\s*|(?:fix|bug|feat|chore|docs|refactor|perf|test|ops)(?:\([^)]{0,40}\))?!?:\s*|fix\s+)+",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
_TRAILER_RE = re.compile(
    r"^(?:%s)\s*:\s*([0-9A-Za-z][0-9A-Za-z_-]{3,})\s*$"
    % "|".join(re.escape(k) for k in (TRAILER_KEY, *LEGACY_TRAILER_KEYS)),
    re.MULTILINE,
)
# Worktree layouts that live inside or beside the canonical repo.
_WORKTREE_MARKERS = ("/.build-loop/worktrees/", "/.claude/worktrees/", "/.rally/worktrees/")


def normalize_title(title: str | None) -> str:
    """Casefold, drop conventional prefixes, collapse punctuation/whitespace."""
    t = (title or "").strip()
    t = _PREFIX_RE.sub("", t)
    return _NON_ALNUM_RE.sub(" ", t.casefold()).strip()


def repo_key(repo: str | None) -> str:
    """Stable repo identity: the canonical checkout's directory name, casefolded.

    Cards store either an absolute path or a bare repo name, and agents often
    run from a linked worktree, so a raw string compare would miss. Worktree
    paths resolve to their parent repo: `<repo>.worktrees/<x>`,
    `<repo>/.build-loop/worktrees/<x>`, `<repo>/.claude/worktrees/<x>`.
    """
    r = (repo or "").strip().rstrip("/")
    if not r:
        return ""
    for marker in _WORKTREE_MARKERS:
        if marker in r:
            r = r.split(marker, 1)[0]
            break
    parts = [p for p in r.split("/") if p]
    for i, part in enumerate(parts):
        if part.endswith(".worktrees") and i + 1 < len(parts):
            return part[: -len(".worktrees")].casefold()
    return (parts[-1] if parts else r).casefold()


def find_duplicate(cards: Iterable[dict], *, title: str, repo: str) -> dict | None:
    """Return the first OPEN card whose normalized title AND repo match, else None.

    An empty normalized title never matches (it would match every blank card).
    """
    want_t, want_r = normalize_title(title), repo_key(repo)
    if not want_t or not want_r:
        return None
    for card in cards:
        status = str(card.get("status") or "").lower()
        if status and status not in OPEN_STATUSES:
            continue
        if normalize_title(card.get("title")) == want_t and repo_key(card.get("target_repo")) == want_r:
            return card
    return None


def trailer(task_id: str) -> str:
    return f"{TRAILER_KEY}: {task_id}"


def parse_trailer_ids(message: str) -> list[str]:
    """Task ids named by `Task:` or legacy `OC-Task:` trailers, in order, deduped."""
    seen: list[str] = []
    for m in _TRAILER_RE.finditer(message or ""):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def resolve_open_id(cards: Iterable[dict], id_or_prefix: str) -> dict | None:
    """Resolve a full id or unambiguous prefix to one OPEN card, else None."""
    p = (id_or_prefix or "").strip().lower()
    if len(p) < 4:
        return None
    hits = [c for c in cards
            if str(c.get("id") or "").lower().startswith(p)
            and str(c.get("status") or "").lower() in OPEN_STATUSES]
    return hits[0] if len(hits) == 1 else None


class QueueUnavailable(RuntimeError):
    """The backend could not be read. Callers degrade (file anyway / skip trailer)."""


class TaskQueueClient(Protocol):
    def list_open(self) -> list[dict]:
        """Open cards as dicts with at least id, title, status, target_repo."""

    def attach(self, task_id: str, note: str) -> None:
        """Append a note to an existing card (the dedupe 'attach' action)."""


class RossLabsOCClient:
    """RossLabs Operations Center backend: MCP `list_tasks` read, CLI `receipt` attach."""

    def __init__(self, binary: str, db: str | None = None, agent: str = "build-loop",
                 timeout: float = 20.0) -> None:
        self.binary, self.db, self.agent, self.timeout = binary, db, agent, timeout

    def _base(self) -> list[str]:
        return [self.binary] + (["--db", self.db] if self.db else [])

    def list_open(self) -> list[dict]:
        msgs = [
            {"jsonrpc": "2.0", "id": 0, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "build-loop", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ]
        msgs += [{"jsonrpc": "2.0", "id": i + 1, "method": "tools/call",
                  "params": {"name": "list_tasks", "arguments": {"status": s}}}
                 for i, s in enumerate(OPEN_STATUSES)]
        try:
            proc = subprocess.Popen(self._base() + ["mcp"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError as exc:
            raise QueueUnavailable(f"cannot start MCP server: {exc}") from exc
        cards: list[dict] = []
        try:
            assert proc.stdin and proc.stdout
            for m in msgs:
                proc.stdin.write(json.dumps(m) + "\n")
            proc.stdin.flush()
            # rmcp answers in order; read until every tools/call id has replied.
            pending = set(range(1, len(OPEN_STATUSES) + 1))
            while pending:
                line = proc.stdout.readline()
                if not line:
                    raise QueueUnavailable("MCP server closed before answering list_tasks")
                try:
                    reply = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise QueueUnavailable(f"MCP reply was not JSON: {line[:120]!r}") from exc
                rid = reply.get("id")
                if rid not in pending:
                    continue
                pending.discard(rid)
                if "error" in reply or reply.get("result", {}).get("isError"):
                    raise QueueUnavailable(f"list_tasks failed: {str(reply)[:200]}")
                text = reply["result"]["content"][0]["text"]
                rows = json.loads(text)
                if isinstance(rows, list):
                    cards.extend(r for r in rows if isinstance(r, dict))
        except (KeyError, IndexError, TypeError, ValueError, OSError) as exc:
            raise QueueUnavailable(f"unexpected MCP reply: {exc}") from exc
        finally:
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=self.timeout)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
        return cards

    def attach(self, task_id: str, note: str) -> None:
        argv = self._base() + ["receipt", task_id, "AGENT STATUS", "--body", note, "--agent", self.agent]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout)
        if r.returncode != 0:
            raise QueueUnavailable(f"receipt exited {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")


def default_client(oc_bin: str | None = None, db: str | None = None,
                   workdir: Path | None = None) -> TaskQueueClient | None:
    """The configured backend, or None when no queue binary is reachable."""
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    from file_to_operations_center import find_oc_binary  # one discovery path

    binary = find_oc_binary(oc_bin, workdir)
    return RossLabsOCClient(str(binary), db=db) if binary else None


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--oc-bin", default=os.environ.get("OC_BIN"))
    p.add_argument("--db", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    lk = sub.add_parser("lookup")
    lk.add_argument("--repo", required=True)
    lk.add_argument("--title", required=True)
    lk.add_argument("--json", action="store_true")
    tr = sub.add_parser("trailer")
    tr.add_argument("--task-id", required=True)
    pa = sub.add_parser("parse")
    pa.add_argument("--message", required=True)
    args = p.parse_args(argv)

    if args.cmd == "parse":
        print(json.dumps({"task_ids": parse_trailer_ids(args.message)}))
        return 0

    client = default_client(args.oc_bin, args.db)
    if client is None:
        print(json.dumps({"error": "task queue backend not found"}))
        return 1
    try:
        cards = client.list_open()
    except QueueUnavailable as exc:
        print(json.dumps({"error": str(exc)}))
        return 1

    if args.cmd == "lookup":
        hit = find_duplicate(cards, title=args.title, repo=args.repo)
        out = {"match": None if hit is None else
               {k: hit.get(k) for k in ("id", "title", "status", "target_repo")},
               "trailer": trailer(hit["id"]) if hit else None}
        print(json.dumps(out, indent=2))
        return 0

    card = resolve_open_id(cards, args.task_id)
    if card is None:
        print(f"no single open card matches {args.task_id!r}; no trailer emitted", file=sys.stderr)
        return 1
    print(trailer(card["id"]))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
