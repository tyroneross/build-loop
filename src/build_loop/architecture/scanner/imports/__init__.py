# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Import extraction for Python (ast) and JS/TS (tree-sitter, regex fallback).

Python imports come from the stdlib ``ast`` walk. JS/TS imports come from a
lazily-bootstrapped tree-sitter parser; the recursive node visit is hoisted to
module-level ``_ts_walk_node`` (guard-clause early returns) so the collector is
flat rather than a 5-deep nested closure. A regex matcher backstops both the
no-parser and parse-error paths and fills any specifiers tree-sitter missed.

Behavior is byte-identical to the pre-split scanner: same emitted
``(specifier, line)`` tuples, in the same order.
"""

from __future__ import annotations

import ast
import re
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Tree-sitter lazy bootstrap
# ---------------------------------------------------------------------------

_TS_PARSER = None
_TSX_PARSER = None


def _ts_parsers() -> Tuple[object, object]:
    """Lazy-load tree-sitter TS/TSX parsers. Returns ``(ts, tsx)``."""
    global _TS_PARSER, _TSX_PARSER
    if _TS_PARSER is not None and _TSX_PARSER is not None:
        return _TS_PARSER, _TSX_PARSER

    try:
        from tree_sitter import Language, Parser
        import tree_sitter_typescript as tsts
    except Exception as e:  # pragma: no cover — import-time
        raise RuntimeError(
            "tree_sitter / tree_sitter_typescript not installed. "
            "Run `uv pip install tree-sitter tree-sitter-typescript`."
        ) from e

    ts_lang = Language(tsts.language_typescript())
    tsx_lang = Language(tsts.language_tsx())

    p_ts = Parser(ts_lang)
    p_tsx = Parser(tsx_lang)
    _TS_PARSER, _TSX_PARSER = p_ts, p_tsx
    return p_ts, p_tsx


# ---------------------------------------------------------------------------
# Python imports via ast
# ---------------------------------------------------------------------------

def _py_import_node(node: ast.Import, out: List[Tuple[str, int]]) -> None:
    """Emit one edge per ``import X, Y`` alias."""
    lineno = getattr(node, "lineno", 1)
    for alias in node.names:
        out.append((alias.name, lineno))


def _py_import_from_node(node: ast.ImportFrom, out: List[Tuple[str, int]]) -> None:
    """Emit per-name candidate edges + the package-level edge for ``from X``."""
    base = node.module or ""
    prefix = "." * (node.level or 0)
    lineno = getattr(node, "lineno", 1)
    # Emit one edge per imported name as a candidate submodule first.
    for alias in node.names:
        if alias.name == "*":
            continue
        full = prefix + base + "." + alias.name if base else prefix + alias.name
        out.append((full, lineno))
    # Also emit the package-level edge so star-imports + bare
    # ``from X import name`` where ``name`` is a symbol (not module)
    # still get attributed.
    pkg = prefix + base if base else prefix
    if pkg:
        out.append((pkg, lineno))


def _py_imports(source: str) -> List[Tuple[str, int]]:
    """Return list of (module_string, line_number) for top-level imports.

    For ``from X import a, b`` we emit BOTH the package-level edge (``X``) AND
    one candidate edge per name (``X.a``, ``X.b``). The resolver picks the most
    specific in-tree match, so ``from . import b`` resolves to ``pkg/b.py``
    rather than ``pkg/__init__.py``.
    """
    out: List[Tuple[str, int]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            _py_import_node(node, out)
        elif isinstance(node, ast.ImportFrom):
            _py_import_from_node(node, out)
    return out


# ---------------------------------------------------------------------------
# JS/TS imports via tree-sitter (with regex fallback)
# ---------------------------------------------------------------------------

_TS_IMPORT_RE = re.compile(
    r"""(?:^|\n)\s*
        (?:import\s+[^'"\n;]+\s+from\s+|
           import\s+|
           export\s+\*\s+from\s+|
           export\s+\{[^}]*\}\s+from\s+|
           import\s*\(|
           require\s*\()
        ['"]([^'"\n]+)['"]
    """,
    re.VERBOSE,
)


def _ts_imports_regex(source: str) -> List[Tuple[str, int]]:
    """Cheap fallback when tree-sitter isn't available."""
    out: List[Tuple[str, int]] = []
    for m in _TS_IMPORT_RE.finditer(source):
        spec = m.group(1)
        line = source[: m.start()].count("\n") + 1
        out.append((spec, line))
    return out


def _ts_collect_node(node, out: List[Tuple[str, int]]) -> None:
    """Collect import/require specifiers from a single tree-sitter node.

    Guard-clause shape: each statement form returns early after appending, so
    the visitor stays flat instead of nesting one branch inside the next.
    """
    # import ... from "X"  ->  import_statement with string child.
    if node.type in ("import_statement", "export_statement"):
        for child in node.children:
            if child.type == "string":
                spec = child.text.decode("utf-8").strip("'\"`")
                out.append((spec, child.start_point[0] + 1))
        return
    # require("X")  ->  call_expression w/ identifier 'require'.
    if node.type == "call_expression" and node.child_count >= 2:
        fn = node.child(0)
        if fn.type != "identifier" or fn.text != b"require":
            return
        args = node.child(1)
        for arg in args.children:
            if arg.type == "string":
                spec = arg.text.decode("utf-8").strip("'\"`")
                out.append((spec, arg.start_point[0] + 1))


def _ts_walk_node(node, out: List[Tuple[str, int]]) -> None:
    """Depth-first visit; collect on this node, then recurse into children."""
    _ts_collect_node(node, out)
    for child in node.children:
        _ts_walk_node(child, out)


def _ts_imports(source: str, is_tsx: bool) -> List[Tuple[str, int]]:
    try:
        p_ts, p_tsx = _ts_parsers()
        parser = p_tsx if is_tsx else p_ts
    except RuntimeError:
        return _ts_imports_regex(source)
    try:
        tree = parser.parse(source.encode("utf-8"))
    except Exception:
        return _ts_imports_regex(source)

    out: List[Tuple[str, int]] = []
    _ts_walk_node(tree.root_node, out)
    seen = set(out)
    for item in _ts_imports_regex(source):
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out
