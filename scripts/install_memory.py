#!/usr/bin/env python3
"""install_memory.py — bootstrap the build-loop memory directory.

Creates `~/.build-loop/memory/` (the canonical global memory store) and seeds
it with template `constitution.md` + `MEMORY.md` if either is missing. Idempotent — never overwrites existing files.

Optional: link the directory to a private git repo so user-specific memory
content is versioned. The build-loop public repo ships ONLY the templates;
the user's actual lessons / patterns / decisions belong in a separate private
repo (see `docs/memory-setup.md`).

Usage:
  python3 scripts/install_memory.py                    # bootstrap + seed
  python3 scripts/install_memory.py --check            # report status, no writes
  python3 scripts/install_memory.py --link-repo <url>  # bootstrap + clone private repo
  python3 scripts/install_memory.py --dest <path>      # override default location

Exit codes:
  0 — success (or check completed)
  1 — write failure
  2 — invalid arguments
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

DEFAULT_DEST = Path.home() / ".build-loop" / "memory"
TEMPLATE_DIR_RELATIVE = "templates/memory"
TEMPLATES = [
    ("constitution.md.template", "constitution.md"),
    ("MEMORY.md.template", "MEMORY.md"),
]


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def template_dir() -> Path:
    # scripts/install_memory.py lives next to scripts/ at the repo root
    return script_dir().parent / TEMPLATE_DIR_RELATIVE


def report(dest: Path) -> dict:
    """Inspect the destination and report what's present / missing."""
    status = {
        "dest": str(dest),
        "exists": dest.exists(),
        "is_git_repo": (dest / ".git").exists() if dest.exists() else False,
        "files": {},
    }
    for _, target_name in TEMPLATES:
        target = dest / target_name
        status["files"][target_name] = {
            "exists": target.exists(),
            "size_bytes": target.stat().st_size if target.exists() else 0,
        }
    # Count user content
    if dest.exists():
        user_files = [
            p for p in dest.glob("*.md")
            if p.name not in {t[1] for t in TEMPLATES}
        ]
        status["user_md_files"] = len(user_files)
    return status


def seed_template(src: Path, dest: Path, force: bool = False) -> tuple[bool, str]:
    """Copy a template if the destination doesn't exist (or force=True). Returns (wrote, reason)."""
    if dest.exists() and not force:
        return False, "already exists"
    if not src.exists():
        return False, f"template missing: {src}"
    shutil.copy2(src, dest)
    return True, "seeded"


def link_private_repo(dest: Path, repo_url: str) -> tuple[bool, str]:
    """Clone a private repo INTO the dest path. Requires dest to be empty or non-existent.

    The repo is expected to contain user-specific memory content (feedback_*.md, pattern_*.md, etc.).
    Templates are NOT seeded when linking — the repo provides the actual content.
    """
    if dest.exists() and any(dest.iterdir()):
        return False, (
            f"{dest} exists and is non-empty. Move existing content out, then re-run with --link-repo."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", repo_url, str(dest)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        return False, f"git clone failed: {e.stderr.decode().strip() or e}"
    return True, f"cloned {repo_url} → {dest}"


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dest", default=str(DEFAULT_DEST),
                   help=f"Memory directory location (default: {DEFAULT_DEST})")
    p.add_argument("--check", action="store_true",
                   help="Report status without writing")
    p.add_argument("--link-repo", default=None,
                   help="Clone a private git repo into the dest (for versioned user content). "
                        "Skips template seeding — the repo provides content.")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing template files (dangerous; use only on fresh install)")
    args = p.parse_args(argv)

    dest = Path(args.dest).expanduser().resolve()

    if args.check:
        status = report(dest)
        print(f"dest: {status['dest']}")
        print(f"  exists: {status['exists']}")
        print(f"  is_git_repo: {status['is_git_repo']}")
        for name, info in status["files"].items():
            present = "✓" if info["exists"] else "✗"
            print(f"  {present} {name} ({info['size_bytes']}B)")
        if "user_md_files" in status:
            print(f"  user content: {status['user_md_files']} .md files beyond templates")
        return 0

    # Link-repo path: clone instead of seeding
    if args.link_repo:
        ok, reason = link_private_repo(dest, args.link_repo)
        if not ok:
            print(f"install_memory: {reason}", file=sys.stderr)
            return 1
        print(f"install_memory: {reason}")
        # Verify templates landed via the repo (warn if not)
        for _, target_name in TEMPLATES:
            if not (dest / target_name).exists():
                print(f"  ⚠️  expected file not in cloned repo: {target_name}", file=sys.stderr)
        return 0

    # Standard bootstrap: mkdir + seed templates
    dest.mkdir(parents=True, exist_ok=True)
    print(f"install_memory: dest = {dest}")
    tpl_dir = template_dir()
    if not tpl_dir.exists():
        print(f"install_memory: template directory missing: {tpl_dir}", file=sys.stderr)
        return 1

    for template_name, target_name in TEMPLATES:
        src = tpl_dir / template_name
        target = dest / target_name
        wrote, reason = seed_template(src, target, force=args.force)
        status = "✓ seeded" if wrote else f"– skipped ({reason})"
        print(f"  {status}: {target_name}")

    print()
    print("Done. Next steps:")
    print(f"  - Edit {dest / 'constitution.md'} with your invariants")
    print(f"  - Add lessons over time as feedback_*.md / pattern_*.md / reference_*.md")
    print("  - Optionally version this directory in a private git repo:")
    print(f"      cd {dest} && git init && git remote add origin <your-private-repo-url>")
    print("  - See docs/memory-setup.md for full setup guide")
    return 0


if __name__ == "__main__":
    sys.exit(main())
