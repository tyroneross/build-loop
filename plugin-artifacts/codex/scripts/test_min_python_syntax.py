#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Every shipped .py must PARSE on the minimum Python declared in pyproject.

Why this exists (a named, observed failure — not a hypothetical):
``scripts/detect_plugin_distribution.py:186`` used a nested same-type quote
inside an f-string::

    f"  shape {r['shape']}  (source: {r["install_sources"] or "undetermined"})"

That is PEP 701, accepted only on Python 3.12+. ``pyproject.toml`` declares
``requires-python = ">=3.11"`` and CI runs the floor, so on CI it was a
SyntaxError at IMPORT — which means pytest died at COLLECTION and ran zero
tests. CI was red from 2026-07-25 until 2026-07-30 and every gate in this repo
was silently not executing. The local dev interpreter (3.14) accepted it, so
nothing caught it here.

Why not ``ast.parse(feature_version=(3, 11))``: measured, it does NOT reject
this. ``feature_version`` gates AST-level features; PEP 701 is a tokenizer
change, so the nested-quote form parses clean under that flag. The only
faithful oracle is the real floor interpreter.

Degradation contract: when the floor interpreter is not installed the test
SKIPS with an explicit reason rather than passing quietly. It is a real gate on
CI (which provisions the floor) and an honest no-op on a dev box that lacks it.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOTS = ("scripts", "src", "hooks", "tests", "cli")
# Generated, vendored, or transient trees: not ours to hold to the floor.
EXCLUDED_PARTS = {
    "__pycache__",
    ".build",
    "worktrees",
    "plugin-artifacts",
    "vendor",
    "node_modules",
}

_REQUIRES_RE = re.compile(r'requires-python\s*=\s*["\']>=\s*(\d+)\.(\d+)')


def min_python() -> tuple[int, int]:
    """The (major, minor) floor declared in pyproject.toml."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = _REQUIRES_RE.search(text)
    if match is None:  # pragma: no cover - pyproject always declares it today
        raise AssertionError("pyproject.toml declares no `requires-python = \">=X.Y\"`")
    return int(match.group(1)), int(match.group(2))


def shipped_python_files() -> list[Path]:
    out: list[Path] = []
    for root in SCAN_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if EXCLUDED_PARTS & set(path.relative_to(REPO_ROOT).parts):
                continue
            out.append(path)
    return out


class MinPythonSyntaxTests(unittest.TestCase):
    def test_every_shipped_file_parses_on_the_declared_floor(self) -> None:
        major, minor = min_python()
        interpreter = shutil.which(f"python{major}.{minor}")
        if interpreter is None:
            self.skipTest(
                f"python{major}.{minor} (the pyproject floor) is not installed; "
                "this gate is a real check on CI, which provisions it. Install it "
                f"locally with `brew install python@{major}.{minor}` to run it here."
            )

        files = shipped_python_files()
        self.assertGreater(len(files), 100, "scan found suspiciously few files")

        # One subprocess for the whole tree: per-file spawn is ~800 processes.
        program = (
            "import ast,sys\n"
            "bad=[]\n"
            "for p in sys.argv[1:]:\n"
            "    try:\n"
            "        ast.parse(open(p, encoding='utf-8', errors='ignore').read())\n"
            "    except SyntaxError as e:\n"
            "        bad.append(f'{p}:{e.lineno}: {e.msg}')\n"
            "print('\\n'.join(bad))\n"
            "sys.exit(1 if bad else 0)\n"
        )
        result = subprocess.run(
            [interpreter, "-c", program, *[str(f) for f in files]],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"{len(files)} files scanned; these do not parse on "
            f"python{major}.{minor} (CI's interpreter) and would fail pytest at "
            f"COLLECTION, running zero tests:\n{result.stdout}{result.stderr}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
