#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""extensions_init.py — scaffold ~/.build-loop-extensions (idempotent) + skills-dir registration."""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extensions_paths import root, plugin_dir, pending_dir, manifest_path, graduated_path  # noqa: E402

MANIFEST = {"name": "build-loop-extensions", "version": "0.1.0",
            "description": "Per-user learned build-loop skills/agents. Loads only approved (active) artifacts."}

def ensure_scaffold(git_init: bool = True) -> dict:
    for d in (plugin_dir() / "skills", plugin_dir() / "agents", plugin_dir() / "config", pending_dir() / "skills"):
        d.mkdir(parents=True, exist_ok=True)
    mp = manifest_path(); mp.parent.mkdir(parents=True, exist_ok=True)
    if not mp.exists():
        mp.write_text(json.dumps(MANIFEST, indent=2) + "\n")
    gp = graduated_path()
    if not gp.exists():
        gp.write_text(json.dumps({"absorbed": []}, indent=2) + "\n")
    if git_init and not (root() / ".git").exists():
        subprocess.run(["git", "init", "-q", str(root())], check=False)
    return {"root": str(root()), "ok": True}

def register_skills_dir() -> dict:
    """Durably register plugin/ as a Claude Code skills-dir plugin (loads in place).
    Idempotent symlink ~/.claude/skills/build-loop-extensions -> <root>/plugin (plugin root ONLY;
    pending/ is a sibling and never linked, so it cannot load)."""
    link = Path.home() / ".claude" / "skills" / "build-loop-extensions"
    link.parent.mkdir(parents=True, exist_ok=True)
    target = plugin_dir()
    if link.is_symlink() or link.exists():
        if link.is_symlink() and link.resolve() == target.resolve():
            return {"registered": True, "link": str(link), "noop": True}
        return {"registered": False, "error": f"{link} exists and is not our symlink"}
    link.symlink_to(target)
    return {"registered": True, "link": str(link)}

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--register", action="store_true", help="Also register plugin/ as a skills-dir plugin.")
    a = p.parse_args(argv)
    out = ensure_scaffold()
    if a.register:
        out["registration"] = register_skills_dir()
    print(json.dumps(out, indent=2)); return 0

if __name__ == "__main__":
    raise SystemExit(main())
