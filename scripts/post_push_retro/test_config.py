# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Config defaults, override merge, opt-out, and budget-guard (acceptance #5)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from post_push_retro import config  # noqa: E402


def _write_cfg(tmp: Path, obj) -> Path:
    (tmp / ".build-loop").mkdir(parents=True, exist_ok=True)
    (tmp / ".build-loop" / "config.json").write_text(json.dumps(obj), encoding="utf-8")
    return tmp


def test_default_is_enabled(tmp_path):
    cfg = config.load(tmp_path)  # no config file
    assert config.is_enabled(cfg) is True
    assert cfg["thresholds"]["substantial_commits"] == 10


def test_missing_config_returns_defaults(tmp_path):
    cfg = config.load(tmp_path)
    assert cfg["autoAfterPush"] is True
    assert cfg["optOut"] is False


def test_opt_out_disables(tmp_path):
    _write_cfg(tmp_path, {"retrospective": {"optOut": True}})
    cfg = config.load(tmp_path)
    assert config.is_enabled(cfg) is False


def test_auto_after_push_false_disables(tmp_path):
    _write_cfg(tmp_path, {"retrospective": {"autoAfterPush": False}})
    assert config.is_enabled(config.load(tmp_path)) is False


def test_threshold_override_merges(tmp_path):
    _write_cfg(tmp_path, {"retrospective": {"thresholds": {"substantial_commits": 3}}})
    cfg = config.load(tmp_path)
    assert cfg["thresholds"]["substantial_commits"] == 3
    # untouched defaults survive the deep-merge
    assert cfg["thresholds"]["substantial_repos"] == 2
    assert "risk_surface_globs" in cfg["thresholds"]


def test_malformed_json_falls_back_to_defaults(tmp_path):
    (tmp_path / ".build-loop").mkdir(parents=True)
    (tmp_path / ".build-loop" / "config.json").write_text("{not json", encoding="utf-8")
    cfg = config.load(tmp_path)
    assert cfg["autoAfterPush"] is True


def test_non_dict_root_falls_back(tmp_path):
    (tmp_path / ".build-loop").mkdir(parents=True)
    (tmp_path / ".build-loop" / "config.json").write_text("[1,2,3]", encoding="utf-8")
    assert config.load(tmp_path)["autoAfterPush"] is True


def test_budget_guard_default_no_cap(tmp_path):
    cfg = config.load(tmp_path)
    assert config.budget_guard_exceeded(tmp_path, cfg) is False


def test_budget_guard_injected_true(tmp_path):
    cfg = config.load(tmp_path)
    assert config.budget_guard_exceeded(tmp_path, cfg, budget_fn=lambda w, c: True) is True


def test_budget_guard_injected_raise_is_false(tmp_path):
    # a broken budget probe must NEVER spuriously skip the retro.
    def boom(w, c):
        raise RuntimeError("probe down")
    cfg = config.load(tmp_path)
    assert config.budget_guard_exceeded(tmp_path, cfg, budget_fn=boom) is False
