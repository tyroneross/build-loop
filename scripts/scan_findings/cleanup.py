#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""One-time cleanup for auto-finding-sweep backlog noise.

Before the junk-title gate (``detect.junk_title_reason``) and the P0-requires-
evidence gate existed, the Stop-hook sweep filed a batch of rubric/template
fragments and near-duplicate restatements straight into the backlog, all at P0
(severity critical). This module finds and closes exactly that noise —
existing open items whose ``provenance.source`` starts with
``auto-finding-sweep`` — without touching anything a human or another producer
created.

Two closure reasons, applied to the SAME open+auto-finding-sweep population:

  * ``junk-title:<reason>``   — ``detect.junk_title_reason(title)`` is non-None
                                 (severity-enum-template or too-short).
  * ``duplicate-of:<id>``     — among the survivors, items whose
                                 ``_normalize_core(title)`` collide; the oldest
                                 (by ``created``, then ``id``) is kept, the rest
                                 are closed.

Closing means ``status: dropped`` (backlog.py has no separate "waived" status)
plus an appended ``## Waived`` body section recording the reason and date. A
single ``backlog.py sync`` per repo (after all writes) moves the dropped items
to ``archive/`` and regenerates the INDEX — never a per-item sync.

Default is a DRY RUN: it reports what it *would* close and writes nothing.
Never deletes a file; never touches an item whose ``provenance.source`` isn't
``auto-finding-sweep`` (or a ``auto-finding-sweep:<agent>`` variant).

Usage:
  PYTHONPATH=scripts python3 -m scan_findings.cleanup --repo <path> [--repo <path> ...] \\
      [--apply] [--today YYYY-MM-DD] [--no-mirror] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = HERE.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Reuse the loader that resolves `scripts/backlog.py` BY PATH — a bare
# `import backlog` resolves to the unrelated `scripts/backlog/` package.
from scan_findings.__main__ import _load_backlog_module  # noqa: E402
from scan_findings.detect import _normalize_core, junk_title_reason  # noqa: E402

PROVENANCE_SOURCE_BASE = "auto-finding-sweep"


def _is_open_auto_finding(item: dict[str, Any]) -> bool:
    if str(item.get("status")) != "open":
        return False
    prov = item.get("provenance")
    source = prov.get("source") if isinstance(prov, dict) else None
    return isinstance(source, str) and source.startswith(PROVENANCE_SOURCE_BASE)


def _waived_section(today: str, reason: str) -> str:
    return (
        "\n## Waived\n"
        f"Closed by scan_findings.cleanup on {today}: {reason}. "
        "Auto-finding-sweep noise, not a verified finding.\n"
    )


def plan_repo(repo: Path) -> dict[str, Any]:
    """Compute (never write) the closures for one repo.

    Returns ``{repo, scanned, to_close: [{item, reason}]}`` where ``item`` is
    the raw loaded-item dict (carries ``_path``/``_body`` for the writer) and
    ``reason`` is the exact closure reason string.
    """
    backlog = _load_backlog_module()
    items = backlog.load_items(repo, include_archive=False)
    candidates = [it for it in items if _is_open_auto_finding(it)]

    to_close: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for it in candidates:
        reason = junk_title_reason(str(it.get("title", "")))
        if reason is not None:
            to_close.append({"item": it, "reason": f"junk-title:{reason}"})
        else:
            remaining.append(it)

    # Duplicate grouping among the survivors: same normalized title, keep the
    # oldest (created, then id), close the rest.
    groups: dict[str, list[dict[str, Any]]] = {}
    for it in remaining:
        key = _normalize_core(str(it.get("title", "")))
        if not key:
            continue
        groups.setdefault(key, []).append(it)
    for group in groups.values():
        if len(group) < 2:
            continue
        group_sorted = sorted(
            group, key=lambda x: (str(x.get("created") or ""), str(x.get("id") or ""))
        )
        kept = group_sorted[0]
        for dupe in group_sorted[1:]:
            to_close.append({"item": dupe, "reason": f"duplicate-of:{kept.get('id')}"})

    return {"repo": str(repo), "scanned": len(candidates), "to_close": to_close}


def _close_item(backlog: Any, item: dict[str, Any], today: str, reason: str) -> None:
    """Set ``status: dropped`` + append the Waived section, write in place.

    Mirrors the write shape ``backlog.py``'s ``cmd_update`` uses (frontmatter
    dict -> ``render_frontmatter`` -> ``_atomic_write_text``, with ``updated``
    stamped to ``today``) rather than going through the CLI — cleanup needs to
    append a body section ``update`` has no flag for, and batches ONE
    ``cmd_sync`` per repo afterward instead of update's per-call sync.
    """
    path = Path(str(item["_path"]))
    body = str(item.get("_body", ""))
    data = {k: v for k, v in item.items() if not k.startswith("_")}
    data["status"] = "dropped"
    data["updated"] = today
    new_body = body.rstrip("\n") + "\n" + _waived_section(today, reason)
    doc = backlog.render_frontmatter(data) + "\n" + new_body
    if not doc.endswith("\n"):
        doc += "\n"
    backlog._atomic_write_text(path, doc)


def run_repo(repo: Path, *, apply: bool, today: str, no_mirror: bool) -> dict[str, Any]:
    backlog = _load_backlog_module()
    plan = plan_repo(repo)
    to_close = plan["to_close"]

    closed: list[dict[str, Any]] = []
    for entry in to_close:
        it = entry["item"]
        if apply:
            _close_item(backlog, it, today, entry["reason"])
        closed.append({"id": it.get("id"), "title": it.get("title"), "reason": entry["reason"]})

    if apply and to_close:
        # One sync per repo: consolidate() archives every just-dropped item and
        # write_index()/mirror_to_memory() regenerate INDEX + the personal mirror.
        backlog.cmd_sync(backlog._NS_sync(
            repo=str(repo), today=today, no_mirror=no_mirror, prune=False,
        ))

    junk_count = sum(1 for e in to_close if e["reason"].startswith("junk-title:"))
    dup_count = sum(1 for e in to_close if e["reason"].startswith("duplicate-of:"))
    return {
        "repo": str(repo),
        "scanned": plan["scanned"],
        "junk": junk_count,
        "duplicate": dup_count,
        "closed": closed,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="scan_findings.cleanup", description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", action="append", required=True,
                    help="Repo path to clean up (repeatable).")
    ap.add_argument("--apply", action="store_true",
                    help="Write changes. Default is a dry run that changes nothing.")
    ap.add_argument("--today", default=None, help="Forwarded to backlog.py (deterministic tests).")
    ap.add_argument("--no-mirror", action="store_true", dest="no_mirror",
                    help="Skip the build-loop-memory mirror on sync.")
    ap.add_argument("--json", action="store_true", dest="print_json")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    backlog = _load_backlog_module()
    today = backlog.resolve_today(args.today)

    reports = []
    for raw_repo in args.repo:
        repo = backlog.normalize_repo(raw_repo)
        reports.append(run_repo(repo, apply=args.apply, today=today, no_mirror=args.no_mirror))

    if args.print_json:
        print(json.dumps(reports, indent=2))
    else:
        mode = "APPLY" if args.apply else "DRY RUN"
        for r in reports:
            print(f"[{mode}] {r['repo']}: scanned={r['scanned']} "
                  f"junk={r['junk']} duplicate={r['duplicate']}")
            for c in r["closed"]:
                print(f"  - {c['id']}: {c['title']!r} ({c['reason']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
