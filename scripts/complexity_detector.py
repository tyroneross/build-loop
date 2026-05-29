#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""complexity_detector.py — stdlib-`ast` detector that finds code to optimize.

Diff-scoped detector for build-loop Phase 4 Review Sub-step E *deep mode*.
Walks the changed Python files passed via ``--changed-files`` and emits a
ranked hotspot envelope. Detects only *obvious*, mechanically-decidable
signals; when a signal is borderline it is emitted at ``advisory`` severity
so the deep-mode pass never auto-applies an uncertain rewrite.

Zero third-party dependencies — standard library only (ADR-001).

Envelope (``--json``)::

    {
      "hotspots":      [{"file","line","kind","reason","severity","score"}],
      "scanned_files": ["..."],
      "skipped":       [{"file","reason"}]
    }

Detected kinds:
  - high_complexity      : cyclomatic AND cognitive proxy both over threshold
  - deep_nesting         : a statement nested beyond DEPTH_THRESHOLD
  - accidental_quadratic : nested loop / membership test over the SAME iterable
  - redundant_multipass  : 2+ separate top-level loops over the SAME iterable
  - needless_indirection : private module fn, one in-scope call site, tiny body

Exit codes:
  0 — ran successfully (hotspots may or may not be present)
  2 — usage error (no --changed-files)
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

# --- thresholds (conservative; borderline -> advisory, never auto-applied) ---
CYCLOMATIC_THRESHOLD = 10
COGNITIVE_THRESHOLD = 12
DEPTH_THRESHOLD = 4
SMALL_BODY_STMT_MAX = 4
SMALL_BODY_LINE_MAX = 8

KINDS = (
    "high_complexity",
    "deep_nesting",
    "accidental_quadratic",
    "redundant_multipass",
    "needless_indirection",
)

_BRANCH_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With,
                 ast.AsyncWith)
_LOOP_NODES = (ast.For, ast.AsyncFor, ast.While)


def _iter_name(node: ast.AST) -> str | None:
    """Return the simple name of a for-loop / comprehension iterable, else None.

    Only bare ``Name`` iterables count — ``for x in obj.attr`` and
    ``for x in f()`` are intentionally NOT treated as the "same iterable"
    because reasoning about their identity is not mechanically safe.
    """
    if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        it = node.iter
        if isinstance(it, ast.Name):
            return it.id
    return None


class _FuncAnalyzer:
    """Per-function analysis. One instance per FunctionDef/AsyncFunctionDef."""

    def __init__(self, fn: ast.AST, file: str):
        self.fn = fn
        self.file = file
        self.hotspots: list[dict] = []

    def run(self) -> list[dict]:
        self._check_complexity()
        self._check_deep_nesting()
        self._check_accidental_quadratic()
        self._check_redundant_multipass()
        return self.hotspots

    def _add(self, line: int, kind: str, reason: str, severity: str,
             score: float) -> None:
        self.hotspots.append({
            "file": self.file, "line": line, "kind": kind,
            "reason": reason, "severity": severity, "score": round(score, 2),
        })

    # -- high_complexity: cyclomatic AND cognitive proxy both over threshold --
    def _check_complexity(self) -> None:
        cyclomatic = 1
        cognitive = 0
        for node, depth in self._walk_with_depth(self.fn):
            if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While)):
                cyclomatic += 1
                cognitive += 1 + depth  # nesting-weighted (cognitive proxy)
            elif isinstance(node, ast.ExceptHandler):
                cyclomatic += 1
                cognitive += 1 + depth
            elif isinstance(node, ast.BoolOp):
                cyclomatic += len(node.values) - 1
        if cyclomatic > CYCLOMATIC_THRESHOLD and cognitive > COGNITIVE_THRESHOLD:
            score = (cyclomatic / CYCLOMATIC_THRESHOLD) + (
                cognitive / COGNITIVE_THRESHOLD)
            self._add(self.fn.lineno, "high_complexity",
                      f"cyclomatic={cyclomatic} (>{CYCLOMATIC_THRESHOLD}) and "
                      f"cognitive={cognitive} (>{COGNITIVE_THRESHOLD}) in "
                      f"'{self.fn.name}'", "high", score)

    def _check_deep_nesting(self) -> None:
        worst = 0
        worst_line = self.fn.lineno
        for node, depth in self._walk_with_depth(self.fn):
            if isinstance(node, _BRANCH_NODES) and depth > worst:
                worst = depth
                worst_line = getattr(node, "lineno", worst_line)
        if worst > DEPTH_THRESHOLD:
            sev = "high" if worst >= DEPTH_THRESHOLD + 1 else "advisory"
            self._add(worst_line, "deep_nesting",
                      f"block nested {worst} deep (>{DEPTH_THRESHOLD}) in "
                      f"'{self.fn.name}'", sev, float(worst))

    # -- accidental_quadratic: inner loop / `in` over the SAME iterable name --
    @staticmethod
    def _quadratic_kind(child: ast.AST, outer: ast.AST, name: str) -> str | None:
        """Classify one inner node as a same-iterable O(n^2) signal, or None.

        Guard-style: each branch returns immediately, so nesting stays shallow.
        """
        if child is outer:
            return None
        if (isinstance(child, (ast.For, ast.AsyncFor, ast.comprehension))
                and _iter_name(child) == name):
            return "loop nested over same iterable"
        if isinstance(child, ast.Compare) and any(
                isinstance(o, (ast.In, ast.NotIn)) for o in child.ops):
            if any(isinstance(c, ast.Name) and c.id == name
                   for c in child.comparators):
                return "membership test over same iterable"
        if (isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "count"
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == name):
            return f"'{name}.count()' over same iterable"
        return None

    def _check_accidental_quadratic(self) -> None:
        for outer in ast.walk(self.fn):
            outer_name = _iter_name(outer)
            if outer_name is None:
                continue
            for child in ast.walk(outer):
                kind = self._quadratic_kind(child, outer, outer_name)
                if kind is None:
                    continue
                self._add(getattr(child, "lineno", outer.lineno),
                          "accidental_quadratic",
                          f"{kind} '{outer_name}' inside loop in "
                          f"'{self.fn.name}' (O(n^2))", "high", 2.0)
                break

    # -- redundant_multipass: 2+ top-level loops over the SAME iterable name --
    def _check_redundant_multipass(self) -> None:
        body = getattr(self.fn, "body", [])
        seen: dict[str, int] = {}
        for stmt in body:
            if isinstance(stmt, (ast.For, ast.AsyncFor)):
                name = _iter_name(stmt)
                if name is None:
                    continue
                if name in seen:
                    self._add(stmt.lineno, "redundant_multipass",
                              f"second top-level loop over '{name}' in "
                              f"'{self.fn.name}' is collapsible to one pass",
                              "advisory", 1.5)
                else:
                    seen[name] = stmt.lineno

    @staticmethod
    def _walk_with_depth(fn: ast.AST):
        """Yield (node, nesting_depth) for nodes inside the function body.

        Depth counts enclosing branch/loop blocks; the function itself is 0.
        """
        stack = [(child, 1) for child in getattr(fn, "body", [])]
        while stack:
            node, depth = stack.pop()
            yield node, depth
            child_depth = depth + 1 if isinstance(node, _BRANCH_NODES) else depth
            for child in ast.iter_child_nodes(node):
                stack.append((child, child_depth))


class _ModuleScanner(ast.NodeVisitor):
    """Collects top-level functions + call counts for needless_indirection."""

    def __init__(self, file: str):
        self.file = file
        self.module_funcs: dict[str, ast.AST] = {}
        self.call_counts: dict[str, int] = {}
        self.public: set[str] = set()

    def visit_Module(self, node: ast.Module) -> None:
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.module_funcs[stmt.name] = stmt
            elif isinstance(stmt, ast.Assign):
                self._capture_all(stmt)
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                n = call.func.id
                self.call_counts[n] = self.call_counts.get(n, 0) + 1

    def _capture_all(self, stmt: ast.Assign) -> None:
        is_all = any(isinstance(t, ast.Name) and t.id == "__all__"
                     for t in stmt.targets)
        if not is_all or not isinstance(stmt.value, (ast.List, ast.Tuple)):
            return
        for elt in stmt.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                self.public.add(elt.value)

    def needless_indirection(self) -> list[dict]:
        out: list[dict] = []
        for name, fn in self.module_funcs.items():
            if name in self.public or name.startswith("__"):
                continue
            if not name.startswith("_"):
                continue  # only flag clearly-private "just in case" helpers
            if getattr(fn, "decorator_list", None):
                continue
            if self.call_counts.get(name, 0) != 1:
                continue
            stmt_count = sum(isinstance(node, ast.stmt) for node in ast.walk(fn)) - 1
            line_count = (getattr(fn, "end_lineno", fn.lineno) or fn.lineno) - fn.lineno + 1
            if stmt_count > SMALL_BODY_STMT_MAX or line_count > SMALL_BODY_LINE_MAX:
                continue
            out.append({
                "file": self.file, "line": fn.lineno,
                "kind": "needless_indirection",
                "reason": (f"private helper '{name}' has one in-scope call "
                           f"site and a tiny body — inline candidate"),
                "severity": "advisory", "score": 1.0,
            })
        return out


def analyze_source(src: str, file: str) -> list[dict]:
    tree = ast.parse(src, filename=file)
    hotspots: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            hotspots.extend(_FuncAnalyzer(node, file).run())
    scanner = _ModuleScanner(file)
    scanner.visit(tree)
    hotspots.extend(scanner.needless_indirection())
    return hotspots


def scan(changed_files: list[str]) -> dict:
    hotspots: list[dict] = []
    scanned: list[str] = []
    skipped: list[dict] = []
    for raw in changed_files:
        p = Path(raw)
        if p.suffix != ".py":
            skipped.append({"file": raw, "reason": "not a .py file"})
            continue
        if not p.exists():
            skipped.append({"file": raw, "reason": "path does not exist"})
            continue
        try:
            src = p.read_text(encoding="utf-8")
            file_hotspots = analyze_source(src, raw)
        except SyntaxError as exc:
            skipped.append({"file": raw, "reason": f"syntax error: {exc.msg}"})
            continue
        except (UnicodeDecodeError, OSError) as exc:
            skipped.append({"file": raw, "reason": f"unreadable: {exc}"})
            continue
        scanned.append(raw)
        hotspots.extend(file_hotspots)
    severity_rank = {"high": 0, "advisory": 1}
    hotspots.sort(key=lambda h: (severity_rank.get(h["severity"], 9),
                                 -h["score"], h["file"], h["line"]))
    return {"hotspots": hotspots, "scanned_files": scanned,
            "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="stdlib-ast complexity/inefficiency detector "
                    "(build-loop Sub-step E deep mode)")
    parser.add_argument("--changed-files", nargs="+", required=True,
                        help="diff-scoped paths to analyze (.py only)")
    parser.add_argument("--json", action="store_true",
                        help="emit the envelope as JSON")
    args = parser.parse_args(argv)
    envelope = scan(args.changed_files)
    if args.json:
        print(json.dumps(envelope, indent=2))
    else:
        for h in envelope["hotspots"]:
            print(f"[{h['severity']}] {h['file']}:{h['line']} "
                  f"{h['kind']} — {h['reason']}")
        if not envelope["hotspots"]:
            print("no hotspots in scope")
    return 0


if __name__ == "__main__":
    sys.exit(main())
