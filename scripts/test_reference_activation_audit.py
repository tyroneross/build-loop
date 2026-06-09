#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for reference_activation_audit — synthetic fixture trees, one per detector."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "reference_activation_audit", Path(__file__).with_name("reference_activation_audit.py")
)
raa = importlib.util.module_from_spec(_spec)
# Register before exec so @dataclass can resolve cls.__module__ during class creation.
sys.modules["reference_activation_audit"] = raa
_spec.loader.exec_module(raa)


def _mk(root: Path, rel: str, content: str = "x\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _clean_repo(root: Path) -> None:
    """A minimal tree where every reference is reachable."""
    _mk(root, "references/INDEX.md", "# Index\n- [keep.md](keep.md)\n")
    _mk(root, "references/keep.md", "canonical doc\n")
    _mk(root, "skills/foo/SKILL.md", "Load references/local.md when needed\n")
    _mk(root, "skills/foo/references/local.md", "skill-local doc\n")


def _rules(findings) -> set[str]:
    return {f.rule for f in findings}


def test_clean_tree_passes(tmp_path: Path):
    _clean_repo(tmp_path)
    findings = raa.run_audit(tmp_path)
    assert findings == [], f"expected clean, got {[f.detail for f in findings]}"


def test_main_exit_codes(tmp_path: Path):
    _clean_repo(tmp_path)
    assert raa.main(["--root", str(tmp_path)]) == 0
    # plant an error-severity orphan → exit 1
    _mk(tmp_path, "references/orphan.md", "nobody loads me\n")
    assert raa.main(["--root", str(tmp_path)]) == 1


def test_dup_drift_detected(tmp_path: Path):
    _clean_repo(tmp_path)
    _mk(tmp_path, "references/dup.md", "version A\n")
    _mk(tmp_path, "skills/foo/references/dup.md", "version B DIFFERENT\n")
    # make both reachable so only dup_drift fires
    _mk(tmp_path, "skills/foo/SKILL.md", "Load references/local.md and references/dup.md and dup.md\n")
    (tmp_path / "references/INDEX.md").write_text("# Index\n- dup.md\n- keep.md\n", encoding="utf-8")
    findings = raa.run_audit(tmp_path)
    assert "dup_drift" in _rules(findings)


def test_identical_dup_not_flagged(tmp_path: Path):
    _clean_repo(tmp_path)
    same = "identical body\n"
    _mk(tmp_path, "references/twin.md", same)
    _mk(tmp_path, "skills/foo/references/twin.md", same)
    _mk(tmp_path, "skills/foo/SKILL.md", "Load references/local.md and twin.md\n")
    (tmp_path / "references/INDEX.md").write_text("# Index\n- twin.md\n", encoding="utf-8")
    findings = raa.run_audit(tmp_path)
    assert "dup_drift" not in _rules(findings)


def test_orphan_detected(tmp_path: Path):
    _clean_repo(tmp_path)
    _mk(tmp_path, "references/lonely.md", "no loader names me\n")
    findings = raa.run_audit(tmp_path)
    assert "orphan" in _rules(findings)
    assert any("lonely.md" in f.path for f in findings if f.rule == "orphan")


def test_excluded_dirs_ignored(tmp_path: Path):
    _clean_repo(tmp_path)
    # mirror copy + archived copy must NOT trigger dup_drift or orphan
    _mk(tmp_path, "plugin-artifacts/codex/references/keep.md", "mirror DIFFERENT\n")
    _mk(tmp_path, "archive/references/old.md", "archived\n")
    findings = raa.run_audit(tmp_path)
    assert "dup_drift" not in _rules(findings)
    assert not any("plugin-artifacts" in f.path or "archive" in f.path for f in findings)


def test_ds_store_detected(tmp_path: Path):
    _clean_repo(tmp_path)
    _mk(tmp_path, "skills/foo/references/.DS_Store", "\x00")
    findings = raa.run_audit(tmp_path)
    assert "ds_store" in _rules(findings)


def test_skill_local_unmentioned(tmp_path: Path):
    _clean_repo(tmp_path)
    # present in INDEX (so not an orphan) but NOT named by its owning SKILL.md
    _mk(tmp_path, "skills/foo/references/secret.md", "skill doc\n")
    (tmp_path / "references/INDEX.md").write_text("# Index\n- keep.md\n- secret.md\n", encoding="utf-8")
    findings = raa.run_audit(tmp_path)
    assert "skill_local_unmentioned" in _rules(findings)


def test_shim_without_expiry(tmp_path: Path):
    _clean_repo(tmp_path)
    _mk(tmp_path, "references/old.alt.md", "DEPRECATED: use the new doc\n")
    (tmp_path / "references/INDEX.md").write_text("# Index\n- keep.md\n- old.alt.md\n", encoding="utf-8")
    findings = raa.run_audit(tmp_path)
    assert "shim_no_expiry" in _rules(findings)


def test_shim_with_expiry_ok(tmp_path: Path):
    _clean_repo(tmp_path)
    _mk(tmp_path, "references/old2.alt.md", "DEPRECATED shim. remove-after: 2026-09-01\n")
    (tmp_path / "references/INDEX.md").write_text("# Index\n- keep.md\n- old2.alt.md\n", encoding="utf-8")
    findings = raa.run_audit(tmp_path)
    assert "shim_no_expiry" not in _rules(findings)


def test_oversized_without_index(tmp_path: Path):
    _clean_repo(tmp_path)
    big = "line\n" * 50
    _mk(tmp_path, "skills/foo/references/big.md", big)
    _mk(tmp_path, "skills/foo/SKILL.md", "Load references/local.md and references/big.md\n")
    findings = raa.run_audit(tmp_path, max_lines=10)
    assert "oversized_no_index" in _rules(findings)


def test_oversized_with_index_ok(tmp_path: Path):
    _clean_repo(tmp_path)
    big = "line\n" * 50
    _mk(tmp_path, "skills/foo/references/big2.md", big)
    _mk(tmp_path, "skills/foo/SKILL.md", "Load references/local.md and references/big2.md\n")
    (tmp_path / "references/INDEX.md").write_text("# Index\n- keep.md\n- big2.md\n", encoding="utf-8")
    findings = raa.run_audit(tmp_path, max_lines=10)
    assert "oversized_no_index" not in _rules(findings)
