#!/usr/bin/env python3
"""Read-only structural inventory for a repository or arbitrary directory."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_EXCLUDES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "coverage",
    "target",
}

SOURCE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".html",
    ".java", ".js", ".jsx", ".kt", ".kts", ".m", ".mm", ".php", ".py",
    ".rb", ".rs", ".sh", ".sql", ".swift", ".ts", ".tsx", ".vue",
}
DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".txt", ".adoc"}
TEST_MARKERS = {"test", "tests", "__tests__", "spec", "specs"}
TEXT_LINE_LIMIT = 2 * 1024 * 1024

ANCHOR_NAMES = {
    "governance": {
        "AGENTS.md", "CLAUDE.md", "CODEOWNERS", "CONTRIBUTING.md",
        "GOVERNANCE.md",
    },
    "orientation": {
        "README", "README.md", "README.rst", "ARCHITECTURE.md", "VISION.md",
        "PRINCIPLES.md", "PRODUCT.md",
    },
    "security": {"SECURITY.md", "THREAT_MODEL.md"},
    "license": {
        "LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "NOTICE",
    },
    "manifest": {
        "package.json", "pyproject.toml", "Cargo.toml", "go.mod", "pom.xml",
        "build.gradle", "build.gradle.kts", "Gemfile", "composer.json",
        "Package.swift", "requirements.txt",
    },
    "ci": {
        "Jenkinsfile", "azure-pipelines.yml", ".gitlab-ci.yml",
    },
}

ENTRYPOINT_NAMES = {
    "main.py", "__main__.py", "app.py", "server.py", "cli.py", "index.js",
    "index.ts", "main.js", "main.ts", "main.rs", "main.go", "Program.cs",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory a local repository or directory without executing it."
    )
    parser.add_argument("root", help="Local repository or directory")
    parser.add_argument(
        "--seed", action="append", default=[],
        help="Relative path supplied by the user; repeat for multiple paths",
    )
    parser.add_argument(
        "--exclude", action="append", default=[],
        help="Additional directory basename to exclude; repeat as needed",
    )
    parser.add_argument(
        "--hotspots", type=int, default=20,
        help="Maximum largest-source-file records (default: 20)",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent JSON output")
    return parser.parse_args()


def git_value(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = (result.stdout or "").strip()
    return value if result.returncode == 0 and value else None


def is_test_path(relative: Path) -> bool:
    lowered = [part.lower() for part in relative.parts]
    name = relative.name.lower()
    return (
        any(part in TEST_MARKERS for part in lowered)
        or name.startswith(("test_", "spec_"))
        or ".test." in name
        or ".spec." in name
    )


def line_count(path: Path, size: int) -> int | None:
    if size > TEXT_LINE_LIMIT:
        return None
    try:
        return path.read_bytes().count(b"\n") + 1
    except OSError:
        return None


def profile_record() -> dict[str, int]:
    return {
        "files": 0,
        "bytes": 0,
        "source_files": 0,
        "test_files": 0,
        "doc_files": 0,
    }


def add_to_profiles(
    profiles: dict[str, dict[str, int]],
    relative: Path,
    size: int,
    suffix: str,
) -> None:
    parents = [Path(".")]
    parents.extend(reversed(relative.parents[:-1]))
    for parent in parents:
        key = "." if str(parent) == "." else parent.as_posix()
        record = profiles.setdefault(key, profile_record())
        record["files"] += 1
        record["bytes"] += size
        if suffix in SOURCE_EXTENSIONS:
            record["source_files"] += 1
        if suffix in DOC_EXTENSIONS:
            record["doc_files"] += 1
        if is_test_path(relative):
            record["test_files"] += 1


def classify_anchors(relative: Path) -> list[str]:
    categories = [
        category
        for category, names in ANCHOR_NAMES.items()
        if relative.name in names
    ]
    if relative.parts[:2] == (".github", "workflows"):
        categories.append("ci")
    if relative.parts and relative.parts[0].lower() in {"test", "tests", "__tests__"}:
        categories.append("tests")
    if relative.name in ENTRYPOINT_NAMES:
        categories.append("entrypoints")
    return categories


def walk(root: Path, excludes: set[str], hotspot_limit: int) -> dict:
    extensions: collections.Counter[str] = collections.Counter()
    profiles: dict[str, dict[str, int]] = {}
    anchors: dict[str, list[str]] = collections.defaultdict(list)
    hotspots: list[dict] = []
    excluded_seen: set[str] = set()
    warnings: list[str] = []
    directory_count = 0
    symlink_count = 0

    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_dirs = []
        for name in sorted(dirs):
            child = current_path / name
            if name in excludes:
                excluded_seen.add(name)
            elif child.is_symlink():
                symlink_count += 1
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        directory_count += len(kept_dirs)

        for name in sorted(files):
            path = current_path / name
            relative = path.relative_to(root)
            if path.is_symlink():
                symlink_count += 1
                continue
            try:
                size = path.stat().st_size
            except OSError as exc:
                warnings.append(f"{relative.as_posix()}: {exc}")
                continue
            suffix = path.suffix.lower() or "[no-extension]"
            extensions[suffix] += 1
            add_to_profiles(profiles, relative, size, suffix)
            for category in classify_anchors(relative):
                if len(anchors[category]) < 100:
                    anchors[category].append(relative.as_posix())
            if suffix in SOURCE_EXTENSIONS:
                hotspots.append(
                    {
                        "path": relative.as_posix(),
                        "bytes": size,
                        "lines": line_count(path, size),
                    }
                )

    hotspots.sort(key=lambda item: (-item["bytes"], item["path"]))
    sorted_profiles = sorted(
        (
            {"path": path, **values}
            for path, values in profiles.items()
            if path != "."
        ),
        key=lambda item: (-item["files"], item["path"]),
    )
    return {
        "summary": {
            **profiles.get(".", profile_record()),
            "directories": directory_count,
            "symlinks_skipped": symlink_count,
        },
        "extensions": [
            {"extension": ext, "files": count}
            for ext, count in extensions.most_common()
        ],
        "directory_profiles": sorted_profiles[:100],
        "anchors": {key: sorted(value) for key, value in sorted(anchors.items())},
        "hotspots": hotspots[: max(0, hotspot_limit)],
        "excluded": sorted(excluded_seen),
        "warnings": warnings[:100],
    }


def seed_record(root: Path, seed: str, excludes: set[str]) -> dict:
    raw = Path(seed).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    try:
        lexical = Path(os.path.abspath(candidate))
        lexical.relative_to(root)
    except (OSError, ValueError):
        return {
            "seed": seed,
            "exists": False,
            "error": "seed resolves outside the inventory root",
        }
    if lexical.is_symlink():
        return {
            "seed": seed,
            "path": lexical.relative_to(root).as_posix(),
            "exists": True,
            "type": "symlink",
            "followed": False,
        }
    try:
        resolved = lexical.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return {
            "seed": seed,
            "exists": False,
            "error": "seed resolves outside the inventory root",
        }
    if not resolved.exists():
        return {"seed": seed, "path": str(resolved), "exists": False}
    if resolved.is_file():
        return {
            "seed": seed,
            "path": resolved.relative_to(root).as_posix(),
            "exists": True,
            "type": "file",
            "bytes": resolved.stat().st_size,
        }
    nested = walk(resolved, excludes, hotspot_limit=5)
    return {
        "seed": seed,
        "path": resolved.relative_to(root).as_posix() or ".",
        "exists": True,
        "type": "directory",
        "summary": nested["summary"],
        "anchors": nested["anchors"],
        "hotspots": nested["hotspots"],
    }


def git_metadata(root: Path) -> dict:
    top = git_value(root, "rev-parse", "--show-toplevel")
    if not top:
        return {"is_repository": False}
    return {
        "is_repository": True,
        "top_level": top,
        "commit": git_value(root, "rev-parse", "HEAD"),
        "branch": git_value(root, "branch", "--show-current"),
        "remote": git_value(root, "remote", "get-url", "origin"),
        "status": git_value(root, "status", "--short", "--branch"),
        "shallow": git_value(root, "rev-parse", "--is-shallow-repository") == "true",
    }


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"repository_inventory: not a directory: {root}", file=sys.stderr)
        return 2
    excludes = DEFAULT_EXCLUDES | set(args.exclude)
    result = {
        "schema": "repository-intelligence.inventory.v1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "root": str(root),
        "contract": {
            "read_only": True,
            "follows_symlinks": False,
            "executes_project_code": False,
        },
        "git": git_metadata(root),
        **walk(root, excludes, args.hotspots),
        "seeds": [seed_record(root, seed, excludes) for seed in args.seed],
    }
    json.dump(result, sys.stdout, indent=2 if args.pretty else None, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
