# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Deprecated alias for ``rally_point.post``."""
try:
    from ._alias import route_module
except ImportError:
    from _alias import route_module  # type: ignore

route_module(__name__, "post")
