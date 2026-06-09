#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the version-controlled marketplace_autoupdate compensator.

Covers the deterministic, host-independent surface: version parsing/compare,
catalog source-kind classification, drift detection, and manifest reading.
The fetch/registry-write paths touch ~/.claude and are exercised against
tmp_path fixtures only where safe.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import marketplace_autoupdate as ma  # noqa: E402


# --- version compare ------------------------------------------------------

def test_version_lt_basic():
    assert ma.version_lt("0.8.1", "0.8.2")
    assert ma.version_lt("0.8.1", "0.31.0")  # numeric, not lexical (8 < 31)
    assert not ma.version_lt("0.31.0", "0.8.1")
    assert not ma.version_lt("1.0.0", "1.0.0")


def test_version_lt_strips_v_prefix():
    assert ma.version_lt("v1.2.0", "v1.3.0")
    assert not ma.version_lt("v2.0.0", "v1.9.9")


def test_version_eq():
    assert ma.version_eq("0.30.0", "0.30.0")
    assert ma.version_eq("v0.30.0", "0.30.0")
    assert not ma.version_eq("0.30.0", "0.30.1")


# --- catalog source kind --------------------------------------------------

def test_catalog_source_kind():
    assert ma._catalog_source_kind({"source": {"source": "github", "repo": "x/y"}}) == "github"
    assert ma._catalog_source_kind({"source": {"source": "directory", "path": "/x"}}) == "directory"
    # String-form (official-marketplace relative path) is out of scope.
    assert ma._catalog_source_kind({"source": "./rel/path"}) is None
    assert ma._catalog_source_kind({}) is None


# --- manifest reading -----------------------------------------------------

def test_read_install_path_version_ok(tmp_path):
    p = tmp_path / "plug"
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "0.31.0"}), encoding="utf-8")
    v, status = ma.read_install_path_version(str(p))
    assert v == "0.31.0" and status == "ok"


def test_read_install_path_version_missing_dir(tmp_path):
    v, status = ma.read_install_path_version(str(tmp_path / "nope"))
    assert v is None and status == "missing-dir"


def test_read_install_path_version_no_version_field(tmp_path):
    p = tmp_path / "plug"
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    v, status = ma.read_install_path_version(str(p))
    assert v is None and status == "no-version-field"


# --- drift detection ------------------------------------------------------

def _make_entry(tmp_path, on_disk_version, registry_version):
    p = tmp_path / "plug"
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": on_disk_version}), encoding="utf-8"
    )
    return {"installPath": str(p), "version": registry_version}


def test_detect_drift_on_disk_behind(tmp_path):
    entry = _make_entry(tmp_path, "0.8.1", "0.8.1")
    is_drift, eff, reason = ma.detect_drift(entry, "x@m", "0.8.2")
    assert is_drift and eff == "0.8.1"


def test_detect_drift_current(tmp_path):
    entry = _make_entry(tmp_path, "0.8.2", "0.8.2")
    is_drift, eff, reason = ma.detect_drift(entry, "x@m", "0.8.2")
    assert not is_drift


def test_detect_drift_registry_stale_but_disk_current(tmp_path):
    # on-disk is current, registry field is stale → registry-only drift.
    entry = _make_entry(tmp_path, "0.8.2", "0.8.1")
    is_drift, eff, reason = ma.detect_drift(entry, "x@m", "0.8.2")
    assert is_drift and "registry-version-stale" in reason


def test_detect_drift_missing_install_path():
    entry = {"installPath": "/does/not/exist", "version": "0.8.1"}
    is_drift, eff, reason = ma.detect_drift(entry, "x@m", "0.8.2")
    assert is_drift and "missing" in reason


# --- kill switch / dry-run never write ------------------------------------

def test_dry_run_main_is_safe(monkeypatch, tmp_path):
    # Point everything at a tmp tree so a stray write can't touch ~/.claude.
    monkeypatch.setattr(ma, "REGISTRY_PATH", tmp_path / "installed_plugins.json")
    monkeypatch.setattr(ma, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(ma, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(ma, "LOG_FILE", tmp_path / "logs" / "x.log")
    monkeypatch.setattr(ma, "KILL_SWITCH", tmp_path / "killswitch")
    # No registry present → graceful exit 0.
    assert ma.main(["--dry-run"]) == 0
