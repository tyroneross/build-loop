# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Routing helpers for the deprecated ``app_pulse`` alias package."""
from __future__ import annotations

import importlib
import sys
import warnings
from pathlib import Path
from types import ModuleType

_ALIAS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _ALIAS_DIR.parent
_REPO_ROOT = _SCRIPTS_DIR.parent

MODULES = (
    "changes",
    "channel_paths",
    "checkpoint",
    "inbox",
    "install_git_hook",
    "lifecycle",
    "mece_gate",
    "post",
    "presence",
    "rally",
    "revision",
)

for _path in (str(_REPO_ROOT), str(_SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def warn_deprecated(name: str) -> None:
    warnings.warn(
        f"{name} is deprecated; use rally_point instead.",
        DeprecationWarning,
        stacklevel=3,
    )


def _import_target(module_name: str, *, prefer_scripts_package: bool) -> ModuleType:
    prefixes = (
        ("scripts.rally_point", "rally_point")
        if prefer_scripts_package
        else ("rally_point", "scripts.rally_point")
    )
    for prefix in prefixes:
        target = f"{prefix}.{module_name}"
        try:
            return importlib.import_module(target)
        except ModuleNotFoundError as exc:
            if exc.name not in {target, prefix, prefix.split(".", 1)[0]}:
                raise
            continue
    raise ModuleNotFoundError(
        f"Could not route app_pulse.{module_name} to rally_point.{module_name}"
    )


def route_module(alias_name: str, module_name: str) -> ModuleType:
    warn_deprecated(alias_name)
    module = _import_target(
        module_name,
        prefer_scripts_package=alias_name.startswith("scripts.app_pulse"),
    )
    sys.modules[alias_name] = module
    return module
