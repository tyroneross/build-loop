# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for comprehension-safe complexity scanning."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from complexity_detector import _FuncAnalyzer  # noqa: E402


def test_comprehension_outer_loop_has_a_safe_fallback_line() -> None:
    inner = ast.comprehension(
        target=ast.Name(id="inner", ctx=ast.Store()),
        iter=ast.Name(id="items", ctx=ast.Load()),
        ifs=[],
        is_async=0,
    )
    outer = ast.comprehension(
        target=ast.Name(id="outer", ctx=ast.Store()),
        iter=ast.Name(id="items", ctx=ast.Load()),
        ifs=[ast.GeneratorExp(elt=ast.Name(id="inner", ctx=ast.Load()), generators=[inner])],
        is_async=0,
    )
    function = ast.FunctionDef(
        name="duplicate_ids",
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[ast.Expr(value=ast.ListComp(elt=ast.Name(id="outer", ctx=ast.Load()), generators=[outer]))],
        decorator_list=[],
        lineno=1,
    )

    hotspots = _FuncAnalyzer(function, "fixture.py").run()

    assert any(hotspot["kind"] == "accidental_quadratic" for hotspot in hotspots)
    assert all(isinstance(hotspot["line"], int) for hotspot in hotspots)
