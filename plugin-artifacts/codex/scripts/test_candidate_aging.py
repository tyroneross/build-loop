# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/candidate_aging.py (Sol audit finding 3 aging surface)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import candidate_aging as ca  # noqa: E402


def _cand(workdir: Path, name: str, body: str) -> Path:
    d = workdir / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.md"
    p.write_text(body, encoding="utf-8")
    return p


TODAY = date(2026, 7, 11)


def test_aged_open_candidate_flagged(tmp_path):
    _cand(tmp_path, "old-open",
          "---\nproposal_id: old-open\nstatus: proposed\ndate: 2026-06-01\n---\n# x\n")
    res = ca.scan(tmp_path, older_than_days=14, now=TODAY)
    assert res["undisposed"] == 1
    assert len(res["aged_undisposed"]) == 1
    c = res["aged_undisposed"][0]
    assert c["id"] == "old-open"
    assert c["age_days"] == 40


def test_terminal_status_not_flagged(tmp_path):
    _cand(tmp_path, "adopted",
          "---\nproposal_id: adopted\nstatus: adopted\ndate: 2026-01-01\n---\n# x\n")
    res = ca.scan(tmp_path, older_than_days=14, now=TODAY)
    assert res["undisposed"] == 0
    assert res["aged_undisposed"] == []


def test_recent_open_not_flagged(tmp_path):
    _cand(tmp_path, "fresh",
          "---\nproposal_id: fresh\nstatus: proposed\ndate: 2026-07-10\n---\n# x\n")
    res = ca.scan(tmp_path, older_than_days=14, now=TODAY)
    assert res["undisposed"] == 1  # counted as undisposed
    assert res["aged_undisposed"] == []  # but not aged


def test_checked_disposition_box_counts_as_disposed(tmp_path):
    # No frontmatter status, but a checked box → disposed (write_enforce_candidates format).
    _cand(tmp_path, "boxed",
          "# Enforce candidate\n\n## Disposition\n\n- [x] Adopt as default\n- [ ] Reject\n")
    res = ca.scan(tmp_path, older_than_days=0, now=TODAY)
    assert res["undisposed"] == 0


def test_unchecked_boxes_are_undisposed(tmp_path):
    p = _cand(tmp_path, "unboxed",
              "# Enforce candidate\n\n## Disposition\n\n- [ ] Adopt\n- [ ] Reject\n")
    import os
    old = os.stat(p).st_mtime
    os.utime(p, (old, __import__("time").mktime(date(2026, 5, 1).timetuple())))
    res = ca.scan(tmp_path, older_than_days=14, now=TODAY)
    assert res["undisposed"] == 1
    assert len(res["aged_undisposed"]) == 1  # mtime fallback dates it old


def test_report_line_zero_and_nonzero(tmp_path):
    empty = ca.scan(tmp_path, now=TODAY)
    assert "0 aged undisposed" in ca.report_line(empty)
    _cand(tmp_path, "old-open",
          "---\nproposal_id: old-open\nstatus: proposed\ndate: 2026-06-01\n---\n# x\n")
    res = ca.scan(tmp_path, older_than_days=14, now=TODAY)
    line = ca.report_line(res)
    assert "1 aged undisposed" in line
    assert "old-open (40d)" in line


def test_missing_dir_is_clean(tmp_path):
    res = ca.scan(tmp_path, now=TODAY)
    assert res["total_candidates"] == 0
    assert res["aged_undisposed"] == []
