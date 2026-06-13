#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""extensions_approve.py — list pending drafts; approve one (checks -> move into plugin/)."""
from __future__ import annotations
import argparse, json, shutil, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extensions_paths import pending_dir, plugin_dir  # noqa: E402
from extensions_check import check_skill  # noqa: E402

def list_pending() -> list[str]:
    d = pending_dir() / "skills"
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []

def approve(name: str, core_descriptions: list[str] | None = None) -> dict:
    src = pending_dir() / "skills" / name
    if not (src / "SKILL.md").exists():
        return {"approved": False, "issues": [{"code": "missing", "detail": f"no pending skill {name!r}"}]}
    issues = check_skill(src / "SKILL.md", core_descriptions or [])
    if issues:
        return {"approved": False, "issues": issues}
    dst = plugin_dir() / "skills" / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"approved": True, "moved_to": str(dst)}

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--list", action="store_true"); p.add_argument("name", nargs="?")
    a = p.parse_args(argv)
    if a.list or not a.name:
        print(json.dumps({"pending": list_pending()}, indent=2)); return 0
    res = approve(a.name)
    print(json.dumps(res, indent=2)); return 0 if res.get("approved") else 3

if __name__ == "__main__":
    raise SystemExit(main())
