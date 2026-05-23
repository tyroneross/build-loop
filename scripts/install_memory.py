#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
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
    # Count user content (root level, excluding templates and the projects/ tree)
    if dest.exists():
        template_names = {t[1] for t in TEMPLATES}
        user_files = [
            p for p in dest.glob("*.md")
            if p.name not in template_names and p.name != "README.md"
        ]
        status["user_md_files"] = len(user_files)
    # Project segmentation (NEW PR 1)
    projects_dir = dest / "projects"
    status["projects_dir_exists"] = projects_dir.is_dir()
    if projects_dir.is_dir():
        per_project: list[dict] = []
        for sub in sorted(projects_dir.iterdir()):
            if not sub.is_dir():
                continue
            # Count .md files at top level of the project subdir (sub-component
            # dirs like workers/ contribute their own row).
            md_count = sum(1 for _ in sub.glob("*.md"))
            per_project.append({
                "slug": sub.name,
                "md_files": md_count,
            })
            # Sub-components (e.g. workers/) — surface as <slug>/<sub>
            for nested in sorted(sub.iterdir()):
                if nested.is_dir() and not nested.name.startswith("_"):
                    nested_count = sum(1 for _ in nested.glob("*.md"))
                    if nested_count:
                        per_project.append({
                            "slug": f"{sub.name}/{nested.name}",
                            "md_files": nested_count,
                        })
        status["projects"] = per_project
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
            print(f"  global content: {status['user_md_files']} .md files beyond templates")
        projects_present = "✓" if status.get("projects_dir_exists") else "✗"
        print(f"  {projects_present} projects/ subtree")
        if status.get("projects"):
            for entry in status["projects"]:
                print(f"    - {entry['slug']}: {entry['md_files']} file(s)")
        elif status.get("projects_dir_exists"):
            print("    (no project subdirs yet — populated by migration script in PR 1.5)")
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

    # Bootstrap the project-segmentation subtree (PR 1).
    projects_dir = dest / "projects"
    projects_readme = projects_dir / "README.md"
    if not projects_dir.exists():
        projects_dir.mkdir(parents=True, exist_ok=True)
        print("  ✓ created: projects/")
    else:
        print("  – skipped: projects/ (already exists)")
    if not projects_readme.exists():
        projects_readme.write_text(_PROJECTS_README_BODY, encoding="utf-8")
        print("  ✓ seeded: projects/README.md")
    else:
        print("  – skipped: projects/README.md (already exists)")

    print()
    print("Done. Next steps:")
    print(f"  - Edit {dest / 'constitution.md'} with your invariants")
    print(f"  - Add lessons over time as feedback_*.md / pattern_*.md / reference_*.md")
    print("  - Optionally version this directory in a private git repo:")
    print(f"      cd {dest} && git init && git remote add origin <your-private-repo-url>")
    print("  - Project-scoped lessons: ~/.build-loop/memory/projects/<slug>/ (filled by migration in PR 1.5)")
    print("  - See docs/memory-setup.md for full setup guide")
    return 0


_PROJECTS_README_BODY = """# projects/ — project-scoped lessons

Per-project memory entries live in subdirectories of this folder:

```
projects/
├── build-loop/
│   ├── MEMORY.md             # project-specific index (optional)
│   ├── constitution.md       # project-specific overrides (optional)
│   └── feedback_*.md / pattern_*.md / reference_*.md
├── decision-doctor-cc/
│   └── workers/              # sub-component memory (deliberate hierarchy)
└── _archive/
    └── <retired-project>/    # historical memory, still queryable
```

## Slug derivation

The slug for the current working directory is derived by
`scripts/derive_slug_from_cwd` in build-loop:

1. Resolve symlinks (`Path.resolve()`)
2. Walk up looking for a `.git` directory
3. Slug = `basename(repo_root)` normalized (lowercase, non-safe chars → `-`)
4. If `cwd` is under `<repo_root>/workers/...`, append `/workers` to the slug
5. No `.git` ancestor → `_unscoped`

This is filesystem-driven; no hand-maintained registry. The legacy
`projects.yaml` (in build-loop-memory repo) remains as a fallback for
explicit aliases only.

## Precedence

When the orchestrator loads memory at Phase 1 Assess:

1. Global tier — files at the root of `~/.build-loop/memory/`
2. Project tier — files at `~/.build-loop/memory/projects/<slug>/`

Later tiers OVERRIDE earlier ones on filename collision (project wins
over global).

Historical: a legacy per-repo tier (`<repo>/.build-loop/memory/`) was
read during the PR 1/2 transition. PR 3 (2026-05-13) removed that
read-shim; the consolidated tree is now the only readable location.

## Privacy

Same as the root: this directory should be in a private git repo. The
build-loop public repo ships templates + the projects/ scaffolding only,
not the user-specific content.
"""


if __name__ == "__main__":
    sys.exit(main())
