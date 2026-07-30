# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Deprecated alias package for ``rally_point``.

Canonical code lives in ``scripts/rally_point``. This package is a segmented
compatibility boundary for one release cycle so older imports like
``scripts.app_pulse.post`` and ``app_pulse.post`` route to the Rally Point
modules without duplicating implementation.
"""
from __future__ import annotations

from ._alias import MODULES, route_module, warn_deprecated

warn_deprecated(__name__)

for _module_name in MODULES:
    globals()[_module_name] = route_module(f"{__name__}.{_module_name}", _module_name)

__all__ = list(MODULES)
