# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the one-time auto-finding-sweep backlog cleanup (scan_findings.cleanup)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from scan_findings import cleanup  # noqa: E402

BACKLOG_SCRIPT = _SCRIPTS_DIR / "backlog.py"


def _new_item(repo: Path, title: str, *, today: str, priority: str = "P2",
              provenance_source: str = "auto-finding-sweep") -> str:
    args = [
        sys.executable, str(BACKLOG_SCRIPT), "new",
        "--repo", str(repo),
        "--area", "audit",
        "--type", "fix",
        "--title", title,
        "--priority", priority,
        "--provenance-source", provenance_source,
        "--provenance-ref", f"finding-hash:{abs(hash(title)) % 10**8:08x}",
        "--today", today,
    ]
    cp = subprocess.run(args, capture_output=True, text=True, check=True)
    return json.loads(cp.stdout)["id"]


def _items_dir(repo: Path) -> Path:
    return repo / ".build-loop" / "backlog" / "items"


def _archive_dir(repo: Path) -> Path:
    return repo / ".build-loop" / "backlog" / "archive"


def _seed(tmp_path: Path) -> dict[str, str]:
    """Five items: one junk auto-finding item, two duplicate-titled auto-finding
    items (different `created` dates so "oldest wins" is unambiguous), one real
    auto-finding item, and one non-auto-finding item with a junk-shaped title
    that must be left untouched."""
    ids = {}
    ids["junk"] = _new_item(
        tmp_path, "high, medium, or low", today="2026-06-20", priority="P0",
    )
    ids["dup_old"] = _new_item(
        tmp_path, "The cache key collides across tenants", today="2026-06-21",
    )
    ids["dup_new"] = _new_item(
        tmp_path, "The cache key collides across tenants", today="2026-06-25",
    )
    ids["real"] = _new_item(
        tmp_path, "Command injection in verify-install.yml shell step", today="2026-06-22",
    )
    ids["not_auto_finding"] = _new_item(
        tmp_path, "high, medium, or low", today="2026-06-23",
        provenance_source="manual-audit",
    )
    return ids


def test_dry_run_changes_nothing(tmp_path):
    ids = _seed(tmp_path)
    before = sorted(p.name for p in _items_dir(tmp_path).glob("*.md"))

    report = cleanup.run_repo(tmp_path, apply=False, today="2026-06-28", no_mirror=True)

    after = sorted(p.name for p in _items_dir(tmp_path).glob("*.md"))
    assert before == after                     # nothing written
    assert list(_archive_dir(tmp_path).glob("*.md")) == []

    assert report["scanned"] == 4                # excludes the non-auto-finding item
    assert report["junk"] == 1
    assert report["duplicate"] == 1
    closed_ids = {c["id"] for c in report["closed"]}
    assert closed_ids == {ids["junk"], ids["dup_new"]}


def test_apply_drops_exactly_junk_and_newer_duplicate(tmp_path):
    ids = _seed(tmp_path)

    report = cleanup.run_repo(tmp_path, apply=True, today="2026-06-28", no_mirror=True)

    assert report["junk"] == 1
    assert report["duplicate"] == 1
    closed_ids = {c["id"] for c in report["closed"]}
    assert closed_ids == {ids["junk"], ids["dup_new"]}

    remaining_open = {p.stem for p in _items_dir(tmp_path).glob("*.md")}
    assert remaining_open == {ids["dup_old"], ids["real"], ids["not_auto_finding"]}

    archived = {p.stem: p for p in _archive_dir(tmp_path).glob("*.md")}
    assert set(archived) == {ids["junk"], ids["dup_new"]}
    for path in archived.values():
        text = path.read_text(encoding="utf-8")
        assert "## Waived" in text
        assert "status: dropped" in text


def test_apply_is_idempotent_on_rerun(tmp_path):
    _seed(tmp_path)
    cleanup.run_repo(tmp_path, apply=True, today="2026-06-28", no_mirror=True)
    second = cleanup.run_repo(tmp_path, apply=True, today="2026-06-29", no_mirror=True)
    # Everything auto-finding-sweep + junk/duplicate was already closed (status
    # no longer "open"), so a rerun finds nothing left to do.
    assert second["scanned"] == 2   # dup_old (open, unique now) + real
    assert second["junk"] == 0
    assert second["duplicate"] == 0
    assert second["closed"] == []


def test_never_touches_non_auto_finding_items(tmp_path):
    ids = _seed(tmp_path)
    cleanup.run_repo(tmp_path, apply=True, today="2026-06-28", no_mirror=True)
    still_open = _items_dir(tmp_path) / f"{ids['not_auto_finding']}.md"
    assert still_open.exists()
    text = still_open.read_text(encoding="utf-8")
    assert "status: open" in text
    assert "## Waived" not in text


def test_cli_entrypoint_dry_run_then_apply(tmp_path):
    ids = _seed(tmp_path)
    rc = cleanup.main([
        "--repo", str(tmp_path), "--today", "2026-06-28", "--no-mirror", "--json",
    ])
    assert rc == 0
    # dry run: nothing on disk changed
    assert (_items_dir(tmp_path) / f"{ids['junk']}.md").exists()

    rc = cleanup.main([
        "--repo", str(tmp_path), "--apply", "--today", "2026-06-28", "--no-mirror", "--json",
    ])
    assert rc == 0
    assert not (_items_dir(tmp_path) / f"{ids['junk']}.md").exists()
    assert (_archive_dir(tmp_path) / f"{ids['junk']}.md").exists()
