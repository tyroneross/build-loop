#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Codex hook-trust check.

Fixtures are synthetic. The real finding this guards (28 of 46 hooks across 10
repos never trusted, 2026-08-23) is reproduced in miniature rather than by
reading the user's actual config.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_hook_trust_check as chk  # type: ignore  # noqa: E402


def _hooks(path: Path, stop_cmds: list[str], session_cmds: list[str] | None = None) -> Path:
    data: dict = {"hooks": {}}
    if session_cmds:
        data["hooks"]["SessionStart"] = [
            {"hooks": [{"type": "command", "command": c} for c in session_cmds]}
        ]
    if stop_cmds:
        data["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": c}]} for c in stop_cmds]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _config(path: Path, hooks_json: Path, suffixes: list[str]) -> Path:
    body = "".join(
        f'[hooks.state."{hooks_json.resolve()}:{s}"]\ntrusted_hash = "sha256:{i:064d}"\n\n'
        for i, s in enumerate(suffixes)
    )
    path.write_text("[features]\nhooks = true\n\n" + body, encoding="utf-8")
    return path


def test_untrusted_hooks_are_reported(tmp_path: Path) -> None:
    hooks = _hooks(tmp_path / "repo" / ".codex" / "hooks.json", ["a", "b", "c"])
    cfg = _config(tmp_path / "config.toml", hooks, ["stop:0:0"])
    result = chk.check(hooks, cfg, tmp_path / "fp.json", record=False)
    assert result["hooks_registered"] == 3
    assert result["trusted"] == 0
    assert result["recorded_grants"] == 1
    assert [u["key"] for u in result["untrusted"]] == ["stop:1:0", "stop:2:0"]
    assert result["ok"] is False


def test_recorded_grants_do_not_claim_native_verification(tmp_path: Path) -> None:
    """A recorded grant cannot establish current native content trust."""
    hooks = _hooks(tmp_path / "repo" / ".codex" / "hooks.json", ["a", "b"])
    cfg = _config(tmp_path / "config.toml", hooks, ["stop:0:0", "stop:1:0"])
    result = chk.check(hooks, cfg, tmp_path / "fp.json", record=False)
    assert result["ok"] is False
    assert result["verification"] == "unknown"
    assert len(result["unknown"]) == 2
    assert "unverified" in chk.render(result)


def test_ordinal_shift_on_a_trusted_key_is_detected(tmp_path: Path) -> None:
    """The hazard that position-only checking cannot see.

    Trust is keyed by position. Inserting a group ahead of a trusted one moves
    an accepted grant onto a DIFFERENT command, and nothing reports it. Two
    merges on 2026-08-23 shifted a group from index 4 to 5; indices 0-3 were
    untouched by luck of insertion order, not by design.
    """
    repo = tmp_path / "repo" / ".codex" / "hooks.json"
    fp = tmp_path / "fp.json"
    hooks = _hooks(repo, ["original-command"])
    cfg = _config(tmp_path / "config.toml", hooks, ["stop:0:0"])

    first = chk.check(hooks, cfg, fp, record=True)
    assert first["recorded_grants"] == 1
    assert first["verification"] == "unknown"

    # A merge inserts a new group ahead of it; stop:0:0 now holds other content.
    _hooks(repo, ["inserted-by-merge", "original-command"])
    after = chk.check(repo, cfg, fp, record=False)
    assert [d["key"] for d in after["position_drift"]] == ["stop:0:0"]
    assert "ordinal shift" in chk.render(after)


def test_fingerprints_only_record_trusted_keys(tmp_path: Path) -> None:
    hooks = _hooks(tmp_path / "repo" / ".codex" / "hooks.json", ["a", "b"])
    cfg = _config(tmp_path / "config.toml", hooks, ["stop:0:0"])
    fp = tmp_path / "fp.json"
    chk.check(hooks, cfg, fp, record=True)
    stored = json.loads(fp.read_text())
    assert len(stored) == 1
    assert all(k.endswith("stop:0:0") for k in stored)


def test_missing_config_reports_everything_untrusted_without_raising(tmp_path: Path) -> None:
    hooks = _hooks(tmp_path / "repo" / ".codex" / "hooks.json", ["a"])
    result = chk.check(hooks, tmp_path / "absent.toml", tmp_path / "fp.json", record=False)
    assert result["trusted"] == 0
    assert result["ok"] is False


def test_malformed_hooks_json_yields_no_entries(tmp_path: Path) -> None:
    bad = tmp_path / "repo" / ".codex" / "hooks.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")
    result = chk.check(bad, tmp_path / "config.toml", tmp_path / "fp.json", record=False)
    assert result["hooks_registered"] == 0
    assert result["ok"] is False
    assert result["errors"]
    assert "Cannot read hook definitions" in chk.render(result)


def test_worktrees_are_excluded_from_the_sweep(tmp_path: Path) -> None:
    """A worktree is a transient checkout of a repo already counted.

    Codex keys trust by absolute path, so each one reads as a fresh untrusted
    repo -- the first cut of this sweep reported my own worktree as a 15-hook
    gap.
    """
    root = tmp_path / "git-folder"
    _hooks(root / "repo" / ".codex" / "hooks.json", ["a"])
    _hooks(root / "repo.worktrees" / "slug" / ".codex" / "hooks.json", ["a"])
    _hooks(root / "repo" / ".build-loop" / "worktrees" / "x" / ".codex" / "hooks.json", ["a"])

    result = chk.sweep([root], tmp_path / "config.toml", tmp_path / "fp.json")
    assert result["repos_checked"] == 1
    assert [r["repo"] for r in result["repos"]] == ["repo"]


def test_sweep_totals_across_repos(tmp_path: Path) -> None:
    root = tmp_path / "git-folder"
    good = _hooks(root / "good" / ".codex" / "hooks.json", ["a"])
    _hooks(root / "dead" / ".codex" / "hooks.json", ["x", "y"])
    cfg = _config(tmp_path / "config.toml", good, ["stop:0:0"])

    result = chk.sweep([root], cfg, tmp_path / "fp.json")
    assert result["repos_checked"] == 2
    assert result["hooks_registered"] == 3
    assert result["hooks_untrusted"] == 2
    assert result["repos_fully_dead"] == ["dead"]


def test_wrong_native_hash_never_reports_ok(tmp_path: Path) -> None:
    hooks = _hooks(tmp_path / "repo/.codex/hooks.json", ["a"])
    cfg = _config(tmp_path / "config.toml", hooks, ["stop:0:0"])
    # Even a syntactically valid hash is not an oracle for Codex's algorithm.
    cfg.write_text(cfg.read_text().replace("0" * 64, "f" * 64))
    result = chk.check(hooks, cfg, tmp_path / "fp.json", record=False)
    assert result["ok"] is False
    assert result["trusted"] == 0
    assert result["verification"] == "unknown"
    assert not (tmp_path / "fp.json").exists()


def test_modern_content_hash_and_enabled_parse_independent_of_order(tmp_path: Path) -> None:
    hooks = _hooks(tmp_path / "repo/.codex/hooks.json", ["a", "b"])
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[hooks.state."{hooks.resolve()}:stop:0:0"]\n'
        'enabled = true\ntrusted_content_hash = "sha256:' + "a" * 64 + '"\n'
        f'[hooks.state."{hooks.resolve()}:stop:1:0"]\n'
        'enabled = false\n'
    )
    result = chk.check(hooks, cfg, tmp_path / "fp.json", record=False)
    assert result["recorded_grants"] == 1
    assert [item["key"] for item in result["disabled"]] == ["stop:1:0"]
    assert not result["untrusted"]
    assert result["verification"] == "unknown"


def test_all_disabled_hooks_need_no_trust(tmp_path: Path) -> None:
    hooks = _hooks(tmp_path / "repo/.codex/hooks.json", ["a"])
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[hooks.state."{hooks.resolve()}:stop:0:0"]\nenabled = false\n')
    result = chk.check(hooks, cfg, tmp_path / "fp.json", record=False)
    assert result["ok"] is True
    assert result["trusted"] == 0
    assert chk.render(result) == ""


def test_plugin_grants_use_plugin_identity_not_cache_absolute_path(tmp_path: Path) -> None:
    hooks = _hooks(tmp_path / ".codex/plugins/cache/vendor/demo/1.0/hooks/hooks.json", ["a"])
    cfg = tmp_path / "config.toml"
    cfg.write_text('[hooks.state."demo@vendor:hooks/hooks.json:stop:0:0"]\n'
                   'trusted_content_hash = "sha256:' + "b" * 64 + '"\n')
    result = chk.check(hooks, cfg, tmp_path / "fp.json", record=False)
    assert result["recorded_grants"] == 1
    assert result["verification"] == "unknown"
    assert not result["untrusted"]


def test_malformed_toml_and_invalid_grant_cannot_report_healthy(tmp_path: Path) -> None:
    hooks = _hooks(tmp_path / "repo/.codex/hooks.json", ["a"])
    cfg = tmp_path / "config.toml"
    cfg.write_text('[hooks.state.???')
    result = chk.check(hooks, cfg, tmp_path / "fp.json", record=False)
    assert result["errors"]
    assert result["ok"] is False
    cfg.write_text(f'[hooks.state."{hooks.resolve()}:stop:0:0"]\ntrusted_hash = "wrong"\n')
    result = chk.check(hooks, cfg, tmp_path / "fp.json", record=False)
    assert result["untrusted"][0]["reason"] == "invalid recorded grant"
    assert result["ok"] is False


def test_invalid_json_shape_cannot_report_healthy(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks.json"
    hooks.write_text('{"hooks": {"Stop": [{"hooks": [null]}]}}')
    result = chk.check(hooks, tmp_path / "missing", tmp_path / "fp.json", record=False)
    assert result["errors"]
    assert result["ok"] is False


def test_quiet_unknown_suppresses_text_but_preserves_json(tmp_path: Path, capsys) -> None:
    hooks = _hooks(tmp_path / "repo/.codex/hooks.json", ["a"])
    cfg = _config(tmp_path / "config.toml", hooks, ["stop:0:0"])
    args = ["--hooks-json", str(hooks), "--config", str(cfg), "--fingerprints",
            str(tmp_path / "fp.json"), "--no-record", "--quiet-unknown"]
    assert chk.main(args) == 0
    assert capsys.readouterr().out == ""
    assert chk.main(args + ["--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verification"] == "unknown"
    assert result["ok"] is False
    assert len(result["unknown"]) == 1


def test_quiet_unknown_keeps_missing_grants_errors_and_drift_visible(tmp_path: Path, capsys) -> None:
    hooks = _hooks(tmp_path / "repo/.codex/hooks.json", ["a", "b"])
    cfg = _config(tmp_path / "config.toml", hooks, ["stop:0:0"])
    fp = tmp_path / "fp.json"
    args = ["--hooks-json", str(hooks), "--config", str(cfg), "--fingerprints",
            str(fp), "--no-record", "--quiet-unknown"]
    assert chk.main(args) == 0
    assert "no recorded grant" in capsys.readouterr().out
    cfg.write_text("malformed = [")
    assert chk.main(args) == 0
    assert "Cannot read hook trust state" in capsys.readouterr().out
    _config(cfg, hooks, ["stop:0:0", "stop:1:0"])
    chk.check(hooks, cfg, fp, record=True)
    _hooks(hooks, ["changed", "b"])
    assert chk.main(args) == 0
    assert "ordinal shift" in capsys.readouterr().out


def test_quiet_unknown_applies_to_sweep_text_only(tmp_path: Path, capsys) -> None:
    root = tmp_path / "git-folder"
    hooks = _hooks(root / "repo/.codex/hooks.json", ["a"])
    cfg = _config(tmp_path / "config.toml", hooks, ["stop:0:0"])
    args = ["--sweep", str(root), "--config", str(cfg), "--fingerprints",
            str(tmp_path / "fp.json"), "--quiet-unknown"]
    assert chk.main(args) == 0
    assert capsys.readouterr().out == ""
    assert chk.main(args + ["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["repos"][0]["verification"] == "unknown"
