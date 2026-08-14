#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""schema_consistency_lint.py — one schema per artifact, one name per module.

Named, observed failure this control earns its place against (2026-08-12): the
backlog had TWO live frontmatter schemas and nobody noticed.

    scripts/backlog.py         FIELD_ORDER -> id, priority, type, gated, area,
                               entities, provenance, evidence, ...
    scripts/backlog/assess.py  build_item  -> classify, effort, repo, branch,
                               source, product_impacting, impact
    templates/backlog-item.md  documents the SECOND one

49 items in this repo carry the first shape; 17 in another repo carry the
second with every `classify:` blank, because the template told the author to
fill a field the CLI never writes. Both populations look fine in isolation.
Neither writer knows the other exists.

The same read also surfaced a name collision: `scripts/backlog.py` (module) and
`scripts/backlog/` (package) sit in one directory, so `import backlog` binds the
PACKAGE and the CLI module is unreachable by import. Nothing depends on it
today, which is exactly why it would be discovered the confusing way.

Both are the same defect class — a contract asserted in two places that drift
apart silently — so one lint covers both. WARN-only by default: it reports and
exits 0 unless --strict. Per the noisy-gate lesson, a check earns hard-blocking
authority by demonstrating precision first, not by being written.

CLI::

    python3 scripts/schema_consistency_lint.py [--workdir .] [--json] [--strict]

Exit: 0 always (WARN mode) / 1 when --strict and any finding is HIGH.
Pure stdlib. Python 3.11+.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:", re.MULTILINE)


def _template_keys(path: Path) -> set[str] | None:
    """Frontmatter keys a human copying this template would fill in."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    return set(KEY_RE.findall(m.group(1)))


def _writer_keys(path: Path, name: str) -> set[str] | None:
    """Read a module-level tuple/list of string literals without importing.

    Importing would execute the module and, for `scripts/backlog.py`, would hit
    the very package-shadowing bug this lint reports. AST keeps the check honest
    about a repo whose imports are broken.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name not in targets:
            continue
        if not isinstance(node.value, (ast.Tuple, ast.List)):
            return None
        return {
            el.value for el in node.value.elts
            if isinstance(el, ast.Constant) and isinstance(el.value, str)
        }
    return None


def _docstring_keys(path: Path, func: str) -> set[str]:
    """Frontmatter-ish keys a function's docstring promises to emit.

    `build_item` documents its shape in prose rather than a constant, so the
    keys it writes are only discoverable from the rendered body. Falls back to
    scanning the whole function source for `"key":` / `key:` line starts.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        src = path.read_text(encoding="utf-8").splitlines()
    except (OSError, SyntaxError):
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            body = "\n".join(src[node.lineno - 1: (node.end_lineno or node.lineno)])
            # Keys rendered into markdown frontmatter appear as `key: {…}` or
            # `key: <literal>` at the start of an f-string line.
            # Match only keys inside a string literal — `f"repo: {repo}"`.
            # Emitted frontmatter is always quoted, while the two things that
            # mimic it are not: the signature line (`deferral: dict[str, Any],`)
            # and the docstring's Args block. Filtering on the quote is more
            # honest than subtracting parameter names, which would also discard
            # `repo` and `branch` — both parameters AND genuinely emitted.
            return set(re.findall(r"""["']([a-z_]+):\s""", body))
    return set()


def _shadowed_modules(scripts_dir: Path) -> list[tuple[str, Path, Path]]:
    """A `<name>.py` and a `<name>/__init__.py` in one directory. Package wins."""
    out: list[tuple[str, Path, Path]] = []
    for mod in sorted(scripts_dir.glob("*.py")):
        pkg_init = scripts_dir / mod.stem / "__init__.py"
        if pkg_init.is_file():
            out.append((mod.stem, mod, pkg_init))
    return out


def scan(workdir: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    scripts = workdir / "scripts"

    # --- 1. backlog template vs the CLI writer ------------------------------
    tmpl = workdir / "templates" / "backlog-item.md"
    cli = scripts / "backlog.py"
    tmpl_keys = _template_keys(tmpl)
    cli_keys = _writer_keys(cli, "FIELD_ORDER")

    if tmpl_keys is not None and cli_keys is not None:
        only_tmpl = sorted(tmpl_keys - cli_keys)
        only_cli = sorted(cli_keys - tmpl_keys)
        if only_tmpl or only_cli:
            findings.append({
                "kind": "schema_template_writer_divergence",
                "signal": "templates/backlog-item.md disagrees with backlog.py FIELD_ORDER",
                "evidence": (
                    f"template_only={only_tmpl!r} writer_only={only_cli!r}"
                ),
                "suggested_action": (
                    "Pick one canonical field set and derive the other from it. "
                    "A field only the template documents is a field every "
                    "hand-authored item fills in and no code reads."
                ),
                "severity": "HIGH",
            })

    # --- 2. a second writer emitting a different shape ----------------------
    assess = scripts / "backlog" / "assess.py"
    if assess.is_file() and cli_keys is not None:
        emitted = _docstring_keys(assess, "build_item")
        # Only flag keys that look like frontmatter, not local variables.
        candidate = {k for k in emitted if k.islower() and "_" not in k or k in {
            "product_impacting", "review_by", "schema_version",
        }}
        foreign = sorted(candidate - cli_keys - {"args", "returns", "raises"})
        if foreign:
            findings.append({
                "kind": "schema_second_writer",
                "signal": "scripts/backlog/assess.py:build_item writes keys the CLI schema lacks",
                "evidence": f"foreign_keys={foreign!r} vs FIELD_ORDER={sorted(cli_keys)!r}",
                "suggested_action": (
                    "Route both writers through one renderer, or add the keys to "
                    "FIELD_ORDER so the tolerant reader defaults them. Two writers "
                    "producing two shapes into one directory is the defect."
                ),
                "severity": "HIGH",
            })

    # --- 3. module shadowed by a same-named package -------------------------
    for stem, mod, pkg in _shadowed_modules(scripts):
        findings.append({
            "kind": "module_shadowed_by_package",
            "signal": f"`import {stem}` binds the package, not {mod.name}",
            "evidence": f"module={mod.relative_to(workdir)} package={pkg.relative_to(workdir)}",
            "suggested_action": (
                f"Rename one of them. Today `import {stem}` silently resolves to "
                f"the package, so {mod.name}'s functions are reachable only by "
                "running it as a script path — an ImportError waiting to look "
                "like a missing function."
            ),
            "severity": "MEDIUM",
        })

    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 on any HIGH finding (default: report and exit 0)")
    args = ap.parse_args(argv)

    findings = scan(Path(args.workdir).resolve())

    if args.json:
        print(json.dumps({"findings": findings, "count": len(findings)}, indent=2))
    elif not findings:
        print("schema_consistency_lint: no divergence found")
    else:
        for f in findings:
            print(f"[{f['severity']}] {f['kind']}: {f['signal']}")
            print(f"        {f['evidence']}")

    if args.strict and any(f["severity"] == "HIGH" for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
