#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""extensions_paths.py — single owner of the ~/.build-loop-extensions layout."""
from __future__ import annotations
import os, re
from pathlib import Path

ENV = "BUILD_LOOP_EXTENSIONS_ROOT"

def root() -> Path:
    return Path(os.environ[ENV]) if os.environ.get(ENV) else Path.home() / ".build-loop-extensions"

def plugin_dir() -> Path: return root() / "plugin"
def pending_dir() -> Path: return root() / "pending"
def manifest_path() -> Path: return plugin_dir() / ".claude-plugin" / "plugin.json"
def graduated_path() -> Path: return root() / "graduated.json"

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

def safe_name(name: str) -> bool:
    """A single path segment with no separators, no '..', no leading dot."""
    return (
        bool(name)
        and name not in (".", "..")
        and not name.startswith(".")
        and "/" not in name
        and "\\" not in name
        and ".." not in name
        and _SAFE_NAME.match(name) is not None
    )
