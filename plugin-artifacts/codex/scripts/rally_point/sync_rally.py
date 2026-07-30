#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
Drift-detection for build-loop's vendored Rally Point substrate copied from upstream
agent-rally-point.

Reads the provenance manifest (``scripts/rally_point/_provenance.json``), which maps each
vendored ``scripts/rally_point/<name>.py`` to its upstream counterpart path (relative to the
agent-rally-point repo root) and the SHA-256 of that UPSTREAM file at last reconcile. For
each tracked file it recomputes the CURRENT upstream hash and reports drift.

The build-loop copies have intentionally diverged from upstream (added repo-local
normalization, the fact.v1 emitter, etc.), so this detector does NOT compare the local copy
to upstream. It compares the RECORDED baseline (upstream's hash at last reconcile) to the
CURRENT upstream hash — answering "did upstream move since we last looked?" so the maintainer
can review whether to port the upstream change into the diverged build-loop copy.

``source: null`` entries are build-loop-original files with no upstream counterpart; they are
skipped cleanly (mirrors sync_skills.py's "not a synced skill, skip silently").

Read-only. Never auto-updates a vendored file. Exit code signals whether review is needed.

Exit codes:
    0 — all tracked files clean (upstream unchanged since baseline)
    1 — drift detected (upstream moved) or upstream source missing
    2 — internal error (unreadable manifest, malformed JSON)

Usage:
    python3 scripts/rally_point/sync_rally.py [--json]
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from typing import Optional


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
HOME = pathlib.Path.home()
MANIFEST = pathlib.Path(__file__).resolve().parent / "_provenance.json"
UPSTREAM_REPO_DIRNAME = "agent-rally-point"


def load_manifest() -> dict:
    """Return the parsed provenance manifest. Raises SystemExit(2) on failure."""
    try:
        text = MANIFEST.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(2) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(2) from exc
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise SystemExit(2)
    return data


def find_upstream_root(repo_dirname: str) -> Optional[pathlib.Path]:
    """Search common parent dirs for the upstream agent-rally-point repo root."""
    candidates = [
        HOME / "dev" / "git-folder",
        REPO_ROOT.parent,
        pathlib.Path.cwd(),
    ]
    for parent in candidates:
        path = parent / repo_dirname
        if path.is_dir():
            return path
    return None


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv

    manifest = load_manifest()
    files: dict = manifest["files"]
    repo_dirname = manifest.get("_upstream_repo", UPSTREAM_REPO_DIRNAME)

    upstream_root = find_upstream_root(repo_dirname)
    drift: list[dict] = []
    checked = 0
    skipped = 0

    for vendored_name, entry in files.items():
        if not isinstance(entry, dict):
            continue  # defensive: skip any non-object value (e.g. stray meta key)
        source = entry.get("source")
        expected = entry.get("source_hash")
        if not source or not expected:
            skipped += 1  # build-loop-original — no upstream counterpart
            continue
        checked += 1
        if upstream_root is None:
            drift.append({
                "file": vendored_name,
                "kind": "MISSING",
                "detail": f"upstream repo {repo_dirname!r} not found in search roots",
                "expected_hash": expected,
                "actual_hash": None,
                "source": source,
            })
            continue
        upstream_path = upstream_root / source
        if not upstream_path.exists():
            drift.append({
                "file": vendored_name,
                "kind": "MISSING",
                "detail": f"upstream source not found: {source}",
                "expected_hash": expected,
                "actual_hash": None,
                "source": source,
            })
            continue
        try:
            actual = hashlib.sha256(upstream_path.read_bytes()).hexdigest()
        except OSError as exc:
            drift.append({
                "file": vendored_name,
                "kind": "MISSING",
                "detail": f"upstream source unreadable: {exc}",
                "expected_hash": expected,
                "actual_hash": None,
                "source": source,
            })
            continue
        if actual != expected:
            drift.append({
                "file": vendored_name,
                "kind": "DRIFT",
                "detail": f"upstream moved: baseline {expected[:12]} -> current {actual[:12]}",
                "expected_hash": expected,
                "actual_hash": actual,
                "source": source,
            })

    if as_json:
        print(json.dumps({
            "checked": checked,
            "skipped_build_loop_original": skipped,
            "upstream_root": str(upstream_root) if upstream_root else None,
            "drift_count": len(drift),
            "drift": drift,
        }, indent=2))
    else:
        print(f"Checked: {checked} tracked files ({skipped} build-loop-original skipped)")
        print(f"Upstream root: {upstream_root if upstream_root else '(not found)'}")
        if not drift:
            print("Status: clean — upstream unchanged since baseline")
        else:
            print(f"Status: {len(drift)} drift(s)")
            for entry in drift:
                print(f"  [{entry['kind']}] {entry['file']}")
                print(f"          {entry['detail']}")
            print()
            print("Refresh: review the upstream change at the noted source path; if porting it")
            print("  into the (diverged) build-loop copy, apply the relevant delta by hand, then")
            print("  update the baseline source_hash in scripts/rally_point/_provenance.json and")
            print("  re-run scripts/rally_point/sync_rally.py to confirm.")

    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
