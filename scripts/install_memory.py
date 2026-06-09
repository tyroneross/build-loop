#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""install_memory.py — bootstrap the build-loop memory directory.

Creates the canonical build-loop-memory root and seeds it with template
`constitution.md` + `MEMORY.md` if either is missing. Idempotent — never
overwrites existing files.

The root is resolved by `_paths.memory_store_root()`: an env override
(`$BUILD_LOOP_MEMORY_STORE_ROOT` / `$BUILD_LOOP_MEMORY_ROOT` /
`$AGENT_MEMORY_ROOT`), else a pre-existing legacy
`~/dev/git-folder/build-loop-memory` on disk, else the neutral fresh-install
default `~/.build-loop-memory`.

Optional: link the directory to a private git repo so user-specific memory
content is versioned. The build-loop public repo ships ONLY the templates;
the user's actual lessons / patterns / decisions belong in a separate private
repo (see `docs/memory-setup.md`).

Usage:
  python3 scripts/install_memory.py                    # bootstrap + seed
  python3 scripts/install_memory.py --guided           # guided terminal install
  python3 scripts/install_memory.py --check            # report status, no writes
  python3 scripts/install_memory.py --validate-seed    # validate packaged public seed
  python3 scripts/install_memory.py --link-repo <url>  # bootstrap + clone private repo
  python3 scripts/install_memory.py --dest <path>      # override default location

Exit codes:
  0 — success (or check completed)
  1 — write failure
  2 — invalid arguments
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    from _paths import _safe_project_relpath, memory_store_root  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    memory_store_root = None  # type: ignore[assignment]
    _safe_project_relpath = None  # type: ignore[assignment]

# Resolve via memory_store_root() (env → legacy-if-present → neutral default).
# The import-failure fallback uses the neutral fresh-install default so a
# degraded run never re-encodes a personal directory layout.
DEFAULT_DEST = memory_store_root() if memory_store_root is not None else Path.home() / ".build-loop-memory"
TEMPLATE_DIR_RELATIVE = "templates/memory"
SEED_MANIFEST_FILENAME = "manifest.json"
TEMPLATES = [
    ("constitution.md.template", "constitution.md"),
    ("MEMORY.md.template", "MEMORY.md"),
]
PROJECT_RAW_SUBDIRS = (
    "documents",
    "data",
    "db",
    "runtime",
    "agent-artifacts",
    "artifacts",
    "files",
)
PROJECT_TOPIC_DIRS = frozenset({
    "apps",
    "assets",
    "architecture",
    "context",
    "decisions",
    "docs",
    "features",
    "formats",
    "indexes",
    "lessons",
    "plugins",
    "product",
    "prompts",
    "raw",
    "research",
    "semantic",
    "skills",
    "sources",
    "testing",
    "tradeoffs",
})
KEEP_FILENAME = ".gitkeep"


def script_dir() -> Path:
    return HERE


def template_dir() -> Path:
    # scripts/install_memory.py lives next to scripts/ at the repo root
    return script_dir().parent / TEMPLATE_DIR_RELATIVE


def seed_manifest_path(tpl_dir: Optional[Path] = None) -> Path:
    return (tpl_dir or template_dir()) / SEED_MANIFEST_FILENAME


def _load_seed_manifest(tpl_dir: Optional[Path] = None) -> tuple[Optional[dict], list[str]]:
    path = seed_manifest_path(tpl_dir)
    if not path.exists():
        return None, [f"seed manifest missing: {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"seed manifest invalid JSON: {exc}"]
    if not isinstance(data, dict):
        return None, ["seed manifest root must be an object"]
    issues: list[str] = []
    if data.get("kind") != "build-loop-memory-public-seed":
        issues.append("seed manifest kind must be build-loop-memory-public-seed")
    if not data.get("schema_version"):
        issues.append("seed manifest schema_version missing")
    if not isinstance(data.get("sources"), list):
        issues.append("seed manifest sources must be a list")
    privacy = data.get("privacy")
    if not isinstance(privacy, dict):
        issues.append("seed manifest privacy block missing")
    elif not isinstance(privacy.get("deny_patterns"), list):
        issues.append("seed manifest privacy.deny_patterns must be a list")
    return data, issues


def _manifest_source_rels(manifest: dict) -> tuple[set[str], list[str]]:
    rels: set[str] = set()
    issues: list[str] = []
    for entry in manifest.get("sources", []):
        if not isinstance(entry, dict):
            issues.append("seed manifest source entry must be an object")
            continue
        source = entry.get("source")
        if isinstance(source, str):
            rel_path = Path(source)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                issues.append(f"seed manifest source must stay inside template dir: {source}")
                continue
            rels.add(source)
        else:
            issues.append("seed manifest source entry missing source string")
    return rels, issues


def validate_public_seed(tpl_dir: Optional[Path] = None) -> dict:
    """Validate that the packaged memory seed is scaffold-only and allowlisted."""
    root = tpl_dir or template_dir()
    manifest, issues = _load_seed_manifest(root)
    files: list[str] = []
    seed_version = None
    privacy_classification = None
    if manifest is not None:
        seed_version = manifest.get("seed_version")
        privacy = manifest.get("privacy") if isinstance(manifest.get("privacy"), dict) else {}
        privacy_classification = privacy.get("classification") if isinstance(privacy, dict) else None
        allowed, source_issues = _manifest_source_rels(manifest)
        issues.extend(source_issues)
        if not allowed:
            issues.append("seed manifest has no source allowlist")
        for rel in sorted(allowed):
            if not (root / rel).is_file():
                issues.append(f"allowlisted seed file missing: {rel}")
        deny_patterns = privacy.get("deny_patterns", []) if isinstance(privacy, dict) else []
        compiled_patterns: list[tuple[str, re.Pattern[str]]] = []
        for pattern in deny_patterns:
            if not isinstance(pattern, str):
                issues.append("seed manifest deny pattern must be a string")
                continue
            try:
                compiled_patterns.append((pattern, re.compile(pattern)))
            except re.error as exc:
                issues.append(f"seed manifest deny pattern invalid: {pattern}: {exc}")

        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(root).as_posix()
            if rel == SEED_MANIFEST_FILENAME:
                continue
            files.append(rel)
            if rel not in allowed:
                issues.append(f"seed file not allowlisted in manifest: {rel}")
                continue
            try:
                body = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                issues.append(f"seed file is not UTF-8 text: {rel}")
                continue
            for pattern, compiled in compiled_patterns:
                if compiled.search(body):
                    issues.append(f"privacy deny pattern matched {rel}: {pattern}")
    return {
        "ok": not issues,
        "kind": "build-loop-memory-public-seed-validation",
        "manifest": str(seed_manifest_path(root)),
        "seed_version": seed_version,
        "privacy_classification": privacy_classification,
        "files": files,
        "issues": issues,
    }


def print_seed_validation(validation: dict) -> None:
    status = "ok" if validation.get("ok") else "failed"
    print(f"public seed: {status}")
    print(f"  manifest: {validation.get('manifest')}")
    print(f"  seed_version: {validation.get('seed_version') or 'unknown'}")
    print(f"  privacy: {validation.get('privacy_classification') or 'unknown'}")
    for rel in validation.get("files") or []:
        print(f"  - {rel}")
    for issue in validation.get("issues") or []:
        print(f"  ! {issue}")


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
    indexes_dir = dest / "indexes"
    status["indexes_dir_exists"] = indexes_dir.is_dir()
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
            raw_status = _raw_status(sub)
            per_project.append({
                "slug": sub.name,
                "md_files": md_count,
                "raw_exists": raw_status["exists"],
                "raw_missing": raw_status["missing"],
            })
            # Sub-components (e.g. workers/) — surface as <slug>/<sub>
            for nested in sorted(sub.iterdir()):
                if nested.is_dir() and not nested.name.startswith("_") and nested.name not in PROJECT_TOPIC_DIRS:
                    nested_count = sum(1 for _ in nested.glob("*.md"))
                    if nested_count:
                        per_project.append({
                            "slug": f"{sub.name}/{nested.name}",
                            "md_files": nested_count,
                        })
        status["projects"] = per_project
    status["public_seed"] = validate_public_seed()
    return status


def _raw_status(project_dir: Path) -> dict:
    """Return raw-lane presence for a project directory."""
    raw = project_dir / "raw"
    missing = [
        name for name in PROJECT_RAW_SUBDIRS
        if not (raw / name).is_dir()
    ]
    return {"exists": raw.is_dir() and not missing, "missing": missing}


def seed_template(src: Path, dest: Path, force: bool = False) -> tuple[bool, str]:
    """Copy a template if the destination doesn't exist (or force=True). Returns (wrote, reason)."""
    if dest.exists() and not force:
        return False, "already exists"
    if not src.exists():
        return False, f"template missing: {src}"
    shutil.copy2(src, dest)
    return True, "seeded"


def _project_relpath(project: str) -> Path:
    if _safe_project_relpath is None:
        raise ValueError("_paths._safe_project_relpath unavailable")
    return _safe_project_relpath(project)


def ensure_project_scaffold(dest: Path, project: str) -> Path:
    """Create the per-project raw-source scaffold and return the project dir."""
    project_dir = dest / "projects" / _project_relpath(project)
    raw_dir = project_dir / "raw"
    for subdir in PROJECT_RAW_SUBDIRS:
        leaf = raw_dir / subdir
        leaf.mkdir(parents=True, exist_ok=True)
        keep = leaf / KEEP_FILENAME
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    return project_dir


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
    p.add_argument("--guided", action="store_true",
                   help="Terminal-first guided install: validate packaged seed, bootstrap, and print next steps.")
    p.add_argument("--validate-seed", action="store_true",
                   help="Validate the packaged public memory seed without writing")
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON for --check or --validate-seed")
    p.add_argument("--link-repo", default=None,
                   help="Clone a private git repo into the dest (for versioned user content). "
                        "Skips template seeding — the repo provides content.")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing template files (dangerous; use only on fresh install)")
    p.add_argument("--ensure-project", action="append", default=[],
                   help="Create projects/<slug>/raw/ lane folders with .gitkeep files. "
                        "May be passed more than once.")
    args = p.parse_args(argv)

    dest = Path(args.dest).expanduser().resolve()

    if args.json and not (args.check or args.validate_seed):
        print("install_memory: --json is only supported with --check or --validate-seed", file=sys.stderr)
        return 2

    if args.guided and args.link_repo:
        print("install_memory: --guided and --link-repo are separate paths; run one at a time", file=sys.stderr)
        return 2

    if args.validate_seed:
        validation = validate_public_seed()
        if args.json:
            print(json.dumps(validation, indent=2, sort_keys=True))
        else:
            print_seed_validation(validation)
        return 0 if validation.get("ok") else 1

    if args.check:
        status = report(dest)
        if args.json:
            print(json.dumps(status, indent=2, sort_keys=True))
            return 0
        print(f"dest: {status['dest']}")
        print(f"  exists: {status['exists']}")
        print(f"  is_git_repo: {status['is_git_repo']}")
        for name, info in status["files"].items():
            present = "✓" if info["exists"] else "✗"
            print(f"  {present} {name} ({info['size_bytes']}B)")
        if "user_md_files" in status:
            print(f"  global content: {status['user_md_files']} .md files beyond templates")
        projects_present = "✓" if status.get("projects_dir_exists") else "✗"
        indexes_present = "✓" if status.get("indexes_dir_exists") else "✗"
        print(f"  {indexes_present} indexes/ subtree")
        print(f"  {projects_present} projects/ subtree")
        if status.get("projects"):
            for entry in status["projects"]:
                if "raw_exists" not in entry:
                    raw = "raw n/a"
                else:
                    raw = "raw ok" if entry.get("raw_exists") else (
                        "raw missing: " + ",".join(entry.get("raw_missing") or [])
                    )
                print(f"    - {entry['slug']}: {entry['md_files']} file(s), {raw}")
        elif status.get("projects_dir_exists"):
            print("    (no project subdirs yet — populated by migration script in PR 1.5)")
        print_seed_validation(status["public_seed"])
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

    seed_validation = validate_public_seed()
    if not seed_validation.get("ok"):
        print_seed_validation(seed_validation)
        print("install_memory: packaged public seed failed validation; refusing to install templates", file=sys.stderr)
        return 1

    if args.guided and not args.json:
        print("Build-loop memory guided install")
        print(f"  dest: {dest}")
        print("  source: packaged public seed only; no personal memory content is copied")
        print()
        print_seed_validation(seed_validation)
        print()

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

    indexes_dir = dest / "indexes"
    if not indexes_dir.exists():
        indexes_dir.mkdir(parents=True, exist_ok=True)
        print("  ✓ created: indexes/")
    else:
        print("  – skipped: indexes/ (already exists)")

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

    for project in args.ensure_project:
        try:
            project_dir = ensure_project_scaffold(dest, project)
        except ValueError as exc:
            print(f"install_memory: invalid project tag {project!r}: {exc}", file=sys.stderr)
            return 2
        print(f"  ✓ ensured: {project_dir.relative_to(dest)}/raw/")

    print()
    print("Done. Next steps:")
    print(f"  - Edit {dest / 'constitution.md'} with your invariants")
    print(f"  - Add lessons over time as feedback_*.md / pattern_*.md / reference_*.md")
    print("  - Optionally version this directory in a private git repo:")
    print(f"      cd {dest} && git init && git remote add origin <your-private-repo-url>")
    print(f"  - Project-scoped lessons: {dest / 'projects' / '<slug>'} (filled by migration when needed)")
    print(f"  - Raw source files: {dest / 'projects' / '<slug>' / 'raw'}")
    print("  - See docs/memory-setup.md for full setup guide")
    return 0


_PROJECTS_README_BODY = """# projects/ — project-scoped lessons

Per-project memory entries live in subdirectories of this folder:

```
projects/
├── build-loop/
│   ├── MEMORY.md             # project-specific index (optional)
│   ├── constitution.md       # project-specific overrides (optional)
│   ├── raw/                  # verbatim source material
│   │   ├── documents/        # PDFs, docs, notes, exported pages
│   │   ├── data/             # JSON, YAML, TOML, CSV, XML
│   │   ├── db/               # schemas, migrations, SQL
│   │   ├── agent-artifacts/  # .build-loop/.claude/.codex/.navgator context
│   │   ├── files/            # reusable source files copied for reference
│   │   └── artifacts/        # screenshots, traces, exports
│   └── feedback_*.md / pattern_*.md / reference_*.md
├── example-project/
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

1. Global tier — files at the root of the build-loop-memory store
2. Project tier — files at `projects/<slug>/` under that store

Later tiers OVERRIDE earlier ones on filename collision (project wins
over global).

Raw source material belongs under `projects/<slug>/raw/` and stays verbatim.
Use `documents/` for human-readable source documents, `data/` for structured
data/config, `db/` for schemas and migrations, `agent-artifacts/` for local
agent context, `files/` for reusable source files, and `artifacts/` for
screenshots, traces, and exports.

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
