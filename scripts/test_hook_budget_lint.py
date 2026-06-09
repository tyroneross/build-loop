#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for hook_budget_lint — inner hook timeouts must fit under the outer budget."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_budget_lint as hbl  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _write(tmp_path: Path, doc: dict) -> Path:
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "hooks").mkdir(exist_ok=True)
    p = tmp_path / "hooks" / "hooks.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_self_test_passes():
    assert hbl._self_test() == 0


def test_inline_inner_timeout_over_budget_flags_hb001(tmp_path):
    doc = {"hooks": {"Stop": [{"matcher": "", "hooks": [
        {"type": "command", "command": "timeout 30s python3 -c x", "timeout": 5000},
    ]}]}}
    findings = hbl.lint_hooks(_write(tmp_path, doc), repo_root=tmp_path)
    assert any(f["rule_id"] == "HB001" for f in findings)


def test_inner_below_budget_is_clean(tmp_path):
    doc = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "timeout 1 git status", "timeout": 2000},
    ]}]}}
    findings = hbl.lint_hooks(_write(tmp_path, doc), repo_root=tmp_path)
    assert findings == []


def test_missing_timeout_flags_hb002(tmp_path):
    doc = {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "echo hi"},
    ]}]}}
    findings = hbl.lint_hooks(_write(tmp_path, doc), repo_root=tmp_path)
    assert any(f["rule_id"] == "HB002" for f in findings)


def test_referenced_script_inner_timeout_flags(tmp_path):
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "slow.py").write_text(
        "import subprocess\nsubprocess.run(['x'], timeout=6)\n", encoding="utf-8"
    )
    doc = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command",
         "command": 'python3 "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/scripts/slow.py"',
         "timeout": 2000},
    ]}]}}
    findings = hbl.lint_hooks(_write(tmp_path, doc), repo_root=tmp_path)
    assert any(f["rule_id"] == "HB001" and f["script_path"] == "scripts/slow.py" for f in findings)


def test_backgrounded_inner_timeout_is_exempt(tmp_path):
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "bg.py").write_text(
        "import subprocess\nsubprocess.run(['x'], timeout=60)\n", encoding="utf-8"
    )
    doc = {"hooks": {"Stop": [{"matcher": "", "hooks": [
        {"type": "command",
         "command": 'nohup python3 "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/scripts/bg.py" >/dev/null 2>&1 & printf "{}"',
         "timeout": 5000},
    ]}]}}
    findings = hbl.lint_hooks(_write(tmp_path, doc), repo_root=tmp_path)
    assert not any(f["rule_id"] == "HB001" for f in findings)


def test_redirect_not_misread_as_background(tmp_path):
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "fg.py").write_text(
        "import subprocess\nsubprocess.run(['x'], timeout=9)\n", encoding="utf-8"
    )
    doc = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command",
         "command": 'python3 "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/scripts/fg.py" 2>&1',
         "timeout": 5000},
    ]}]}}
    findings = hbl.lint_hooks(_write(tmp_path, doc), repo_root=tmp_path)
    assert any(f["rule_id"] == "HB001" for f in findings)


def test_budget_derived_timeout_not_flagged(tmp_path):
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "budget.py").write_text(
        "from rally_point import hook_budget\n"
        "subprocess.run(['x'], timeout=hook_budget.inner_timeout_seconds())\n",
        encoding="utf-8",
    )
    doc = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command",
         "command": 'python3 "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/scripts/budget.py"',
         "timeout": 3000},
    ]}]}}
    findings = hbl.lint_hooks(_write(tmp_path, doc), repo_root=tmp_path)
    assert not any(f["rule_id"] == "HB001" for f in findings)


def test_shipped_hooks_json_passes_the_lint():
    """The repo's own hooks.json must be clean — this is the regression guard
    that keeps the timeout-inversion class from re-entering the shipped hooks."""
    findings = hbl.lint_hooks(REPO / "hooks" / "hooks.json")
    assert findings == [], (
        "hooks/hooks.json has timeout-budget findings:\n"
        + "\n".join(f"  [{f['rule_id']}] {f['event']} "
                    f"{f['script_path'] or '(inline)'}: {f['message']}" for f in findings)
    )
