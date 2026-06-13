#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""extensions_route.py — consumer default: place a drafted skill into pending/ (never loads until approved)."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
from extensions_init import ensure_scaffold  # noqa: E402
from extensions_paths import pending_dir  # noqa: E402

def route_draft(name: str, skill_md_text: str) -> str:
    ensure_scaffold(git_init=False)
    d = pending_dir() / "skills" / name; d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(skill_md_text)
    return str(d / "SKILL.md")

def main(argv=None) -> int:
    p = argparse.ArgumentParser(); p.add_argument("--name", required=True); p.add_argument("--file", required=True)
    a = p.parse_args(argv); print(route_draft(a.name, Path(a.file).read_text())); return 0

if __name__ == "__main__":
    raise SystemExit(main())
