#!/usr/bin/env python3
"""Read-only inventory for repository maintenance, structure, and closeout."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_ARTIFACT_PATTERNS = (
    "build",
    "build-*",
    "build_*",
    "build-rust",
    "target",
    ".build",
    "DerivedData",
    "dist",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)

DEFAULT_PROTECTED_ARTIFACTS = frozenset({"build", "build-rust"})
DISCOVERY_PRUNE_NAMES = frozenset(
    {".git", ".nox", ".tox", ".venv", "node_modules", "site-packages", "venv"}
)
RELEASE_ARTIFACT_SUFFIXES = (
    ".aab",
    ".apk",
    ".appx",
    ".deb",
    ".dmg",
    ".exe",
    ".ipa",
    ".msi",
    ".msix",
    ".pkg",
    ".rpm",
    ".tar.gz",
    ".xcarchive",
    ".zip",
)
RELEASE_CONTAINER_NAMES = frozenset({"artifacts", "dist", "release", "releases"})
RELEASE_BUNDLE_SUFFIXES = (".app",)
RELEASE_EVIDENCE_LIMIT = 20

LANGUAGE_EXTENSIONS = {
    ".c": "C/C++",
    ".cc": "C/C++",
    ".cpp": "C/C++",
    ".cs": "C#",
    ".dart": "Dart",
    ".go": "Go",
    ".h": "C/C++",
    ".hpp": "C/C++",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".m": "Objective-C",
    ".mm": "Objective-C",
    ".php": "PHP",
    ".py": "Python",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".sh": "Shell",
    ".swift": "Swift",
    ".tf": "Terraform",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
}

PROFILE_EVIDENCE_LIMIT = 20
MANIFEST_CONTENT_SCAN_LIMIT = 200


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def resolve_root(repo: Path) -> Path:
    result = run_git(repo, "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip()).resolve()


def ref_exists(repo: Path, ref: str) -> bool:
    commit_ref = f"{ref}^{{commit}}"
    return run_git(repo, "rev-parse", "--verify", "--quiet", commit_ref, check=False).returncode == 0


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return run_git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0


def ahead_behind(repo: Path, left: str, right: str) -> tuple[int, int]:
    result = run_git(repo, "rev-list", "--left-right", "--count", f"{left}...{right}")
    left_only, right_only = result.stdout.split()
    return int(left_only), int(right_only)


def parse_worktrees(repo: Path) -> list[dict[str, Any]]:
    output = run_git(repo, "worktree", "list", "--porcelain").stdout
    records: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in output.splitlines() + [""]:
        if not line:
            if current:
                branch = current.get("branch")
                if isinstance(branch, str) and branch.startswith("refs/heads/"):
                    current["branch"] = branch.removeprefix("refs/heads/")
                path = Path(str(current["worktree"]))
                current["exists"] = path.exists()
                current["dirty_paths"] = parse_status(repo=path) if path.exists() else []
                current["dirty"] = bool(current["dirty_paths"])
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value if value else True
    return records


def parse_status(repo: Path) -> list[str]:
    output = run_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    fields = output.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        status = field[:2]
        paths.append(field[3:])
        if "R" in status or "C" in status:
            index += 1
    return paths


def branch_inventory(repo: Path, base: str, worktrees: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checked_out = {str(w.get("branch")): str(w["worktree"]) for w in worktrees if w.get("branch")}
    fmt = "%(refname:short)%00%(objectname)%00%(upstream:short)"
    output = run_git(repo, "for-each-ref", f"--format={fmt}", "refs/heads").stdout
    branches: list[dict[str, Any]] = []
    for line in output.splitlines():
        name, head, upstream = line.split("\0")
        item: dict[str, Any] = {
            "name": name,
            "head": head,
            "upstream": upstream or None,
            "worktree": checked_out.get(name),
        }
        if ref_exists(repo, base):
            behind, ahead = ahead_behind(repo, base, name)
            item.update(
                {
                    "behind_base": behind,
                    "ahead_base": ahead,
                    "merged_into_base": is_ancestor(repo, name, base),
                }
            )
            if name != base and ahead:
                cherry = run_git(repo, "cherry", base, name, check=False)
                item["cherry"] = cherry.stdout.splitlines() if cherry.returncode == 0 else []
        if upstream and ref_exists(repo, upstream):
            upstream_behind, upstream_ahead = ahead_behind(repo, upstream, name)
            item["behind_upstream"] = upstream_behind
            item["ahead_upstream"] = upstream_ahead
        branches.append(item)
    return branches


def stash_inventory(repo: Path) -> list[dict[str, Any]]:
    output = run_git(repo, "stash", "list", "--format=%gd%x00%H%x00%gs").stdout
    stashes: list[dict[str, Any]] = []
    for line in output.splitlines():
        ref, commit, subject = line.split("\0", 2)
        parents = run_git(repo, "show", "-s", "--format=%P", commit).stdout.split()
        names = run_git(repo, "stash", "show", "--include-untracked", "--name-only", ref).stdout.splitlines()
        stashes.append(
            {
                "ref": ref,
                "commit": commit,
                "subject": subject,
                "parents": parents,
                "has_untracked_parent": len(parents) >= 3,
                "paths": names,
            }
        )
    return stashes


def operation_state(repo: Path) -> list[str]:
    git_dir = Path(run_git(repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    markers = {
        "merge": "MERGE_HEAD",
        "cherry-pick": "CHERRY_PICK_HEAD",
        "revert": "REVERT_HEAD",
        "rebase-merge": "rebase-merge",
        "rebase-apply": "rebase-apply",
        "bisect": "BISECT_LOG",
    }
    return [name for name, marker in markers.items() if (git_dir / marker).exists()]


def tracked_structure(repo: Path) -> dict[str, Any]:
    paths = run_git(repo, "ls-files", "-z").stdout.split("\0")
    paths = [path for path in paths if path]
    top_level: dict[str, int] = {}
    for path in paths:
        root = path.split("/", 1)[0]
        top_level[root] = top_level.get(root, 0) + 1
    nested_git_roots = [
        child.name
        for child in repo.iterdir()
        if child.is_dir() and not child.is_symlink() and (child / ".git").exists()
    ]
    return {
        "tracked_file_count": len(paths),
        "tracked_files_by_top_level": dict(sorted(top_level.items())),
        "nested_git_roots": sorted(nested_git_roots),
    }


def tracked_paths_at_ref(repo: Path, ref: str) -> list[str]:
    output = run_git(repo, "ls-tree", "-r", "--name-only", "-z", ref).stdout
    return sorted(path for path in output.split("\0") if path)


def read_text_at_ref(repo: Path, ref: str, path: str) -> str | None:
    result = run_git(repo, "show", f"{ref}:{path}", check=False)
    return result.stdout if result.returncode == 0 else None


def limited_evidence(paths: list[str] | set[str] | tuple[str, ...]) -> list[str]:
    return sorted(set(paths))[:PROFILE_EVIDENCE_LIMIT]


def package_metadata(
    repo: Path, ref: str, paths: list[str]
) -> tuple[set[str], list[str], list[str], dict[str, Any]]:
    dependencies: set[str] = set()
    workspace_manifests: list[str] = []
    script_manifests: list[str] = []
    manifests = sorted(
        (item for item in paths if Path(item).name == "package.json"),
        key=lambda item: (item.count("/"), item),
    )
    scanned = manifests[:MANIFEST_CONTENT_SCAN_LIMIT]
    for path in scanned:
        raw = read_text_at_ref(repo, ref, path)
        if raw is None:
            continue
        try:
            manifest = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(manifest, dict):
            continue
        if manifest.get("workspaces"):
            workspace_manifests.append(path)
        if manifest.get("scripts"):
            script_manifests.append(path)
        for field in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            values = manifest.get(field, {})
            if isinstance(values, dict):
                dependencies.update(str(name) for name in values)
    return (
        dependencies,
        workspace_manifests,
        script_manifests,
        {
            "package_manifests_found": len(manifests),
            "package_manifests_scanned": len(scanned),
            "package_manifest_scan_truncated": len(manifests) > len(scanned),
        },
    )


def detect_profile_signals(repo: Path, ref: str) -> dict[str, Any]:
    """Return evidence signals without claiming a final architecture classification."""

    if not ref_exists(repo, ref):
        return {
            "source_ref": None,
            "inference_policy": (
                "Signals are evidence, not a final application, topology, deployment, or ownership decision."
            ),
            "languages": [],
            "build_systems": [],
            "application_signals": [],
            "composition_signals": [],
            "layout_roots": [],
            "scan_summary": {
                "tracked_paths": 0,
                "package_manifests_found": 0,
                "package_manifests_scanned": 0,
                "package_manifest_scan_truncated": False,
                "cargo_manifests_found": 0,
                "cargo_manifests_scanned": 0,
                "cargo_manifest_scan_truncated": False,
            },
        }

    paths = tracked_paths_at_ref(repo, ref)
    language_paths: dict[str, list[str]] = {}
    for path in paths:
        language = LANGUAGE_EXTENSIONS.get(Path(path).suffix.lower())
        if language:
            language_paths.setdefault(language, []).append(path)
    languages = [
        {
            "name": name,
            "file_count": len(language_paths[name]),
            "sample_paths": limited_evidence(language_paths[name])[:5],
        }
        for name in sorted(language_paths, key=lambda item: (-len(language_paths[item]), item))
    ]

    by_name: dict[str, list[str]] = {}
    for path in paths:
        by_name.setdefault(Path(path).name, []).append(path)

    build_systems: list[dict[str, Any]] = []

    def add_build(name: str, role: str, evidence: list[str]) -> None:
        if evidence:
            build_systems.append(
                {"name": name, "role": role, "evidence": limited_evidence(evidence)}
            )

    cargo_manifests = by_name.get("Cargo.toml", [])
    swift_manifests = by_name.get("Package.swift", [])
    xcode_projects = [path for path in paths if path.endswith(".xcodeproj/project.pbxproj")]
    xcodegen_manifests = by_name.get("project.yml", [])
    package_manifests = by_name.get("package.json", [])
    gradle_manifests = [
        path
        for path in paths
        if Path(path).name
        in {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}
    ]
    dockerfiles = [path for path in paths if Path(path).name.startswith("Dockerfile")]
    terraform_files = [path for path in paths if path.endswith(".tf")]
    (
        dependencies,
        node_workspace_manifests,
        node_script_manifests,
        scan_summary,
    ) = package_metadata(repo, ref, paths)

    add_build("cargo", "language-package-build", cargo_manifests)
    add_build("swiftpm", "language-package-build", swift_manifests)
    add_build("xcodegen", "project-generator", xcodegen_manifests)
    add_build("xcodebuild", "platform-build", xcode_projects or xcodegen_manifests)
    add_build("gradle", "language-package-build", gradle_manifests)
    add_build("maven", "language-package-build", by_name.get("pom.xml", []))
    add_build("go", "language-package-build", by_name.get("go.mod", []))
    add_build("python-build", "language-package-build", by_name.get("pyproject.toml", []))
    add_build(
        "msbuild",
        "language-package-build",
        [path for path in paths if path.endswith((".csproj", ".sln"))],
    )
    add_build("cmake", "project-generator", by_name.get("CMakeLists.txt", []))
    add_build("nextjs", "framework-compiler", package_manifests if "next" in dependencies else [])
    add_build("vite", "framework-bundler", package_manifests if "vite" in dependencies else [])
    add_build("webpack", "framework-bundler", package_manifests if "webpack" in dependencies else [])
    add_build("turborepo", "workspace-coordinator", by_name.get("turbo.json", []))
    add_build("nx", "workspace-coordinator", by_name.get("nx.json", []))
    add_build("pnpm", "package-manager", by_name.get("pnpm-lock.yaml", []))
    add_build("yarn", "package-manager", by_name.get("yarn.lock", []))
    add_build("npm", "package-manager", by_name.get("package-lock.json", []))
    add_build(
        "node-scripts",
        "task-orchestration",
        node_script_manifests,
    )
    add_build("make", "task-orchestration", by_name.get("Makefile", []))
    add_build("shell-build", "task-orchestration", by_name.get("build.sh", []))
    add_build("docker", "container-package", dockerfiles)
    add_build("terraform", "infrastructure", terraform_files)
    add_build(
        "github-actions",
        "ci-release-automation",
        [path for path in paths if path.startswith(".github/workflows/")],
    )

    root_cargo_workspace = []
    cargo_scanned = sorted(
        cargo_manifests, key=lambda item: (item.count("/"), item)
    )[:MANIFEST_CONTENT_SCAN_LIMIT]
    for path in cargo_scanned:
        content = read_text_at_ref(repo, ref, path) or ""
        if "[workspace]" in content:
            root_cargo_workspace.append(path)
    workspace_evidence = [
        *root_cargo_workspace,
        *by_name.get("pnpm-workspace.yaml", []),
        *node_workspace_manifests,
        *by_name.get("turbo.json", []),
        *by_name.get("nx.json", []),
    ]

    layout_names = {
        "apps",
        "clients",
        "crates",
        "daemon",
        "deploy",
        "infrastructure",
        "libs",
        "modules",
        "packages",
        "plugins",
        "services",
        "Sources",
        "src",
        "tools",
        "workers",
    }
    top_level = {path.split("/", 1)[0] for path in paths}
    layout_roots = sorted(top_level & layout_names)

    application_signals: list[dict[str, Any]] = []

    def add_application(
        name: str,
        evidence: list[str],
        *,
        confidence: str,
        interpretation: str,
    ) -> None:
        if evidence:
            application_signals.append(
                {
                    "name": name,
                    "confidence": confidence,
                    "evidence": limited_evidence(evidence),
                    "interpretation": interpretation,
                }
            )

    swift_evidence = language_paths.get("Swift", [])
    rust_evidence = language_paths.get("Rust", [])
    android_manifests = by_name.get("AndroidManifest.xml", [])
    web_frameworks = dependencies & {
        "@angular/core",
        "next",
        "nuxt",
        "react",
        "remix",
        "svelte",
        "vue",
    }
    runtime_roots = [
        root for root in ("daemon", "services", "workers") if root in top_level
    ]
    runtime_evidence = [
        path
        for path in paths
        if any(path == root or path.startswith(f"{root}/") for root in runtime_roots)
        and Path(path).name
        in {
            "Cargo.toml",
            "Dockerfile",
            "go.mod",
            "main.py",
            "main.rs",
            "package.json",
            "pyproject.toml",
        }
    ]
    rust_component_evidence = [
        path for path in paths if path.endswith(("/src/main.rs", "/src/lib.rs"))
    ]
    plugin_evidence = [
        path
        for path in paths
        if path.endswith(".claude-plugin/plugin.json")
        or path == ".claude-plugin/plugin.json"
        or path.endswith("/manifest.json") and "extension" in path.lower()
    ]
    agent_overlay_evidence = [
        path
        for path in paths
        if path.startswith((".claude/agents/", ".codex/skills/"))
        or path.endswith("/SKILL.md")
    ]
    data_ml_evidence = [
        path
        for path in paths
        if path.endswith(".ipynb")
        or path.startswith(("models/", "training/", "evals/", "notebooks/"))
    ]
    infra_evidence = [
        *terraform_files,
        *[path for path in paths if path.startswith(("infrastructure/", "deploy/", "charts/"))],
    ]

    add_application(
        "apple-native",
        [*xcode_projects, *xcodegen_manifests, *swift_evidence[:5]]
        if swift_evidence and (xcode_projects or xcodegen_manifests)
        else [],
        confidence="high",
        interpretation="Native Apple source and project/build configuration are present.",
    )
    add_application(
        "android-native",
        [*android_manifests, *gradle_manifests] if android_manifests else [],
        confidence="high" if gradle_manifests else "medium",
        interpretation="Android manifests and Gradle project evidence are present.",
    )
    add_application(
        "web-application",
        package_manifests if web_frameworks else [],
        confidence="medium",
        interpretation=f"Web framework dependencies detected: {', '.join(sorted(web_frameworks))}.",
    )
    add_application(
        "service-or-daemon",
        runtime_evidence or runtime_roots,
        confidence="medium",
        interpretation="Long-lived runtime layout is present; confirm independent deployment from release configuration.",
    )
    add_application(
        "rust-cli-or-library-components",
        [*cargo_manifests, *rust_component_evidence] if rust_evidence and rust_component_evidence else [],
        confidence="medium",
        interpretation="Rust binary and/or library component entrypoints are present.",
    )
    add_application(
        "plugin-or-extension",
        plugin_evidence,
        confidence="high",
        interpretation="A host-discovered plugin or extension manifest is tracked.",
    )
    add_application(
        "agent-tooling-overlay",
        agent_overlay_evidence,
        confidence="high",
        interpretation="Repository-local agent configuration exists; this is tooling, not product architecture.",
    )
    add_application(
        "data-or-ml",
        data_ml_evidence,
        confidence="medium",
        interpretation="Notebook, model, training, or evaluation paths are present.",
    )
    add_application(
        "infrastructure",
        infra_evidence,
        confidence="medium",
        interpretation="Infrastructure or deployment definitions are tracked.",
    )
    if swift_evidence and rust_evidence and (xcode_projects or xcodegen_manifests) and runtime_roots:
        add_application(
            "mixed-native-product",
            [*xcodegen_manifests, *xcode_projects, *cargo_manifests, *runtime_roots],
            confidence="high",
            interpretation="Native Apple application and Rust runtime components coexist in one repository.",
        )

    composition_signals: list[dict[str, Any]] = []

    def add_composition(name: str, evidence: list[str], interpretation: str) -> None:
        if evidence:
            composition_signals.append(
                {
                    "name": name,
                    "evidence": limited_evidence(evidence),
                    "interpretation": interpretation,
                }
            )

    package_roots = [root for root in ("packages", "crates", "modules", "libs") if root in top_level]
    app_roots = [root for root in ("apps", "clients") if root in top_level]
    add_composition(
        "workspace",
        workspace_evidence,
        "One or more build tools coordinate multiple packages in this checkout.",
    )
    add_composition(
        "package-family-layout",
        package_roots,
        "Top-level package/module collection exists; confirm enforceable package boundaries.",
    )
    add_composition(
        "multi-app-layout",
        app_roots,
        "Top-level application/client collection exists; confirm the number of runnable applications.",
    )
    add_composition(
        "runtime-layout",
        runtime_roots,
        "Service, worker, or daemon roots exist; confirm deployment independence.",
    )
    substantial_languages = [
        item["name"] for item in languages if item["name"] not in {"Shell", "Terraform"}
    ]
    add_composition(
        "mixed-language",
        [sample for name in substantial_languages for sample in language_paths[name][:1]]
        if len(substantial_languages) > 1
        else [],
        f"Multiple implementation languages are tracked: {', '.join(substantial_languages)}.",
    )

    return {
        "source_ref": ref,
        "inference_policy": (
            "Signals are evidence, not a final application, topology, deployment, or ownership decision."
        ),
        "languages": languages,
        "build_systems": build_systems,
        "application_signals": application_signals,
        "composition_signals": composition_signals,
        "layout_roots": layout_roots,
        "scan_summary": {
            "tracked_paths": len(paths),
            **scan_summary,
            "cargo_manifests_found": len(cargo_manifests),
            "cargo_manifests_scanned": len(cargo_scanned),
            "cargo_manifest_scan_truncated": len(cargo_manifests) > len(cargo_scanned),
        },
    }


def process_snapshot() -> list[tuple[int, str]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return []
    processes: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_text, _, command = line.partition(" ")
        if pid_text.isdigit():
            processes.append((int(pid_text), command.strip()))
    return processes


def matches_artifact_pattern(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def discover_artifact_paths(repo: Path, patterns: tuple[str, ...]) -> list[Path]:
    """Find artifact roots recursively without counting descendants twice."""
    discovered: list[Path] = []
    for current_root, directories, _files in os.walk(repo, followlinks=False):
        current = Path(current_root)
        retained: list[str] = []
        for name in sorted(directories):
            candidate = current / name
            if (
                candidate.is_symlink()
                or name in DISCOVERY_PRUNE_NAMES
                or (candidate / "pyvenv.cfg").is_file()
            ):
                continue
            if matches_artifact_pattern(name, patterns):
                discovered.append(candidate)
                continue
            retained.append(name)
        directories[:] = retained
    return sorted(discovered, key=lambda path: path.relative_to(repo).as_posix())


def command_references_path(command: str, path: Path) -> bool:
    target = str(path)
    start = 0
    while True:
        index = command.find(target, start)
        if index < 0:
            return False
        end = index + len(target)
        before = command[index - 1] if index else " "
        after = command[end] if end < len(command) else " "
        if before in " \t\"'=:([" and after in " \t\"'/)]":
            return True
        start = index + 1


def command_artifact_paths(
    repo: Path, command: str, patterns: tuple[str, ...]
) -> set[Path]:
    """Extract artifact-root prefixes under repo from a process command."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    repo_prefix = f"{repo}{os.sep}"
    roots: set[Path] = set()
    for token in tokens:
        index = token.find(repo_prefix)
        if index < 0:
            continue
        path_text = token[index:].rstrip("\"'`,;:)]}")
        try:
            relative = Path(path_text).relative_to(repo)
        except ValueError:
            continue
        current = repo
        for part in relative.parts:
            current /= part
            if matches_artifact_pattern(part, patterns):
                roots.add(current)
                break
    return roots


def active_missing_artifacts(
    repo: Path,
    patterns: tuple[str, ...],
    processes: list[tuple[int, str]],
) -> list[dict[str, Any]]:
    refs: dict[str, list[dict[str, Any]]] = {}
    for pid, command in processes:
        for path in command_artifact_paths(repo, command, patterns):
            if path.exists():
                continue
            relative = path.relative_to(repo).as_posix()
            refs.setdefault(relative, []).append(
                {"pid": pid, "command": command[:300]}
            )
    return [
        {
            "path": relative,
            "exists": False,
            "disposition": "active-missing-artifact",
            "active_process_refs": process_refs,
        }
        for relative, process_refs in sorted(refs.items())
    ]


def directory_stats(path: Path) -> tuple[int, int, int, float]:
    allocated_bytes = 0
    apparent_bytes = 0
    file_count = 0
    latest_mtime = path.stat().st_mtime
    for current_root, directories, files in os.walk(path, followlinks=False):
        directories[:] = [
            name for name in directories if not (Path(current_root) / name).is_symlink()
        ]
        for name in files:
            candidate = Path(current_root) / name
            try:
                stat = candidate.stat(follow_symlinks=False)
            except OSError:
                continue
            apparent_bytes += stat.st_size
            allocated_bytes += getattr(stat, "st_blocks", 0) * 512
            file_count += 1
            latest_mtime = max(latest_mtime, stat.st_mtime)
    return allocated_bytes, apparent_bytes, file_count, latest_mtime


def release_artifact_evidence(repo: Path, path: Path) -> list[str]:
    evidence: list[str] = []
    release_container = (
        path.name.lower() in RELEASE_CONTAINER_NAMES
        or path.name.lower().endswith(RELEASE_ARTIFACT_SUFFIXES)
        or path.name.lower().endswith(RELEASE_BUNDLE_SUFFIXES)
    )
    for current_root, directories, files in os.walk(path, followlinks=False):
        current = Path(current_root)
        directories[:] = [
            name
            for name in directories
            if not (current / name).is_symlink()
        ]
        for name in [*directories, *files]:
            lower_name = name.lower()
            high_confidence = lower_name.endswith(RELEASE_ARTIFACT_SUFFIXES)
            bundle_in_release_container = (
                release_container and lower_name.endswith(RELEASE_BUNDLE_SUFFIXES)
            )
            if not high_confidence and not bundle_in_release_container:
                continue
            evidence.append((current / name).relative_to(repo).as_posix())
            if len(evidence) >= RELEASE_EVIDENCE_LIMIT:
                return evidence
    return evidence


def artifact_inventory(
    repo: Path,
    patterns: tuple[str, ...],
    protected: set[str],
    stale_days: float,
    *,
    use_default_protections: bool = True,
) -> dict[str, Any]:
    processes = process_snapshot()
    now = time.time()
    paths = discover_artifact_paths(repo, patterns)
    artifacts: list[dict[str, Any]] = []
    for path in paths:
        relative = path.relative_to(repo).as_posix()
        allocated_bytes, apparent_bytes, file_count, latest_mtime = directory_stats(path)
        active_refs = [
            {"pid": pid, "command": command[:300]}
            for pid, command in processes
            if command_references_path(command, path)
        ]
        ignored = run_git(repo, "check-ignore", "-q", "--", relative, check=False).returncode == 0
        age_days = max(0.0, (now - latest_mtime) / 86_400)
        default_protected = use_default_protections and relative in DEFAULT_PROTECTED_ARTIFACTS
        is_protected = default_protected or relative in protected or path.name in protected
        release_evidence = release_artifact_evidence(repo, path)
        contains_release_artifact = bool(release_evidence)
        stale = age_days >= stale_days
        cleanup_candidate = (
            ignored
            and stale
            and not is_protected
            and not active_refs
            and not contains_release_artifact
        )
        if is_protected:
            disposition = "protected"
        elif active_refs:
            disposition = "active"
        elif contains_release_artifact:
            disposition = "release-artifact"
        elif not ignored:
            disposition = "review-tracked-or-unignored"
        elif not stale:
            disposition = "recent-cache"
        else:
            disposition = "cleanup-candidate"
        artifacts.append(
            {
                "path": relative,
                "bytes": allocated_bytes,
                "allocated_bytes": allocated_bytes,
                "apparent_bytes": apparent_bytes,
                "file_count": file_count,
                "latest_mtime": latest_mtime,
                "age_days": round(age_days, 2),
                "ignored_by_git": ignored,
                "protected": is_protected,
                "default_protected": default_protected,
                "active_process_refs": active_refs,
                "contains_release_artifact": contains_release_artifact,
                "release_artifact_evidence": release_evidence,
                "stale": stale,
                "cleanup_candidate": cleanup_candidate,
                "disposition": disposition,
            }
        )
    return {
        "patterns": list(patterns),
        "default_protected": (
            sorted(DEFAULT_PROTECTED_ARTIFACTS) if use_default_protections else []
        ),
        "protected": sorted(set(protected) | (
            set(DEFAULT_PROTECTED_ARTIFACTS) if use_default_protections else set()
        )),
        "stale_days": stale_days,
        "total_bytes": sum(item["bytes"] for item in artifacts),
        "total_apparent_bytes": sum(item["apparent_bytes"] for item in artifacts),
        "cleanup_candidate_bytes": sum(
            item["bytes"] for item in artifacts if item["cleanup_candidate"]
        ),
        "cleanup_candidate_apparent_bytes": sum(
            item["apparent_bytes"] for item in artifacts if item["cleanup_candidate"]
        ),
        "active_missing_artifacts": active_missing_artifacts(repo, patterns, processes),
        "artifacts": artifacts,
    }


def tree_entries(repo: Path, ref: str, prefix: str | None = None) -> dict[str, dict[str, str]]:
    args = ["ls-tree", "-r", "-z", ref]
    normalized_prefix = prefix.strip("/") if prefix else None
    if normalized_prefix:
        args.extend(["--", normalized_prefix])
    output = run_git(repo, *args).stdout
    entries: dict[str, dict[str, str]] = {}
    for record in output.split("\0"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        mode, kind, object_id = metadata.split(" ", 2)
        if normalized_prefix:
            prefix_with_slash = f"{normalized_prefix}/"
            if path.startswith(prefix_with_slash):
                path = path.removeprefix(prefix_with_slash)
        entries[path] = {"mode": mode, "kind": kind, "object": object_id}
    return entries


def compare_source_tree(
    repo: Path,
    base: str,
    source_repo: Path,
    source_ref: str,
    target_prefix: str,
) -> dict[str, Any]:
    source_root = resolve_root(source_repo)
    source_head = run_git(source_root, "rev-parse", source_ref).stdout.strip()
    source_entries = tree_entries(source_root, source_ref)
    target_entries = tree_entries(repo, base, target_prefix)
    source_paths = set(source_entries)
    target_paths = set(target_entries)
    common = source_paths & target_paths
    changed = sorted(path for path in common if source_entries[path] != target_entries[path])
    source_only = sorted(source_paths - target_paths)
    target_only = sorted(target_paths - source_paths)
    source_head_known = ref_exists(repo, source_head)
    source_head_ancestor = source_head_known and is_ancestor(repo, source_head, base)
    return {
        "source_repo": str(source_root),
        "source_ref": source_ref,
        "source_head": source_head,
        "target_ref": base,
        "target_prefix": target_prefix.strip("/"),
        "source_tracked_paths": len(source_paths),
        "target_tracked_paths": len(target_paths),
        "matching_paths": len(common) - len(changed),
        "changed_paths": changed,
        "source_only_paths": source_only,
        "target_only_paths": target_only,
        "exact_tree_match": bool(source_entries) and source_entries == target_entries,
        "source_head_known_to_target_repo": source_head_known,
        "source_head_ancestor_of_target_ref": source_head_ancestor,
    }


def audit(
    repo: Path,
    base: str = "main",
    *,
    include_artifacts: bool = False,
    artifact_patterns: tuple[str, ...] = DEFAULT_ARTIFACT_PATTERNS,
    protected_artifacts: set[str] | None = None,
    use_default_artifact_protections: bool = True,
    stale_days: float = 7,
    compare_repo: Path | None = None,
    compare_prefix: str | None = None,
    compare_ref: str = "HEAD",
) -> dict[str, Any]:
    root = resolve_root(repo)
    base_exists = ref_exists(root, base)
    worktrees = parse_worktrees(root)
    branches = branch_inventory(root, base, worktrees) if base_exists else []
    current_branch = run_git(root, "branch", "--show-current").stdout.strip() or None
    base_upstream_result = run_git(root, "rev-parse", "--abbrev-ref", f"{base}@{{upstream}}", check=False)
    base_upstream = base_upstream_result.stdout.strip() if base_upstream_result.returncode == 0 else None
    upstream_state = None
    if base_upstream and ref_exists(root, base_upstream):
        behind, ahead = ahead_behind(root, base_upstream, base)
        upstream_state = {"ref": base_upstream, "behind": behind, "ahead": ahead}
    candidates = [
        branch["name"]
        for branch in branches
        if branch["name"] != base and not branch.get("merged_into_base", False)
    ]
    removable = [
        branch["name"]
        for branch in branches
        if branch["name"] != base and branch.get("merged_into_base", False)
    ]
    report: dict[str, Any] = {
        "schema_version": 4,
        "repo_root": str(root),
        "base": base,
        "base_exists": base_exists,
        "base_head": run_git(root, "rev-parse", base).stdout.strip() if base_exists else None,
        "base_upstream": upstream_state,
        "current_branch": current_branch,
        "canonical_dirty_paths": parse_status(root),
        "operations_in_progress": operation_state(root),
        "worktrees": worktrees,
        "missing_worktrees": [
            str(worktree["worktree"])
            for worktree in worktrees
            if not worktree.get("exists", True)
        ],
        "branches": branches,
        "stashes": stash_inventory(root),
        "unmerged_candidates": candidates,
        "merged_branch_candidates": removable,
        "archive_tags": run_git(root, "tag", "-l", "archive/pre-closeout-*").stdout.splitlines(),
        "structure": tracked_structure(root),
        "profile_signals": detect_profile_signals(root, base if base_exists else "HEAD"),
    }
    if include_artifacts:
        report["artifacts"] = artifact_inventory(
            root,
            artifact_patterns,
            protected_artifacts or set(),
            stale_days,
            use_default_protections=use_default_artifact_protections,
        )
    if compare_repo is not None or compare_prefix is not None:
        if compare_repo is None or not compare_prefix:
            raise ValueError("--compare-repo and --compare-prefix must be used together")
        report["source_comparison"] = compare_source_tree(
            root,
            base,
            compare_repo,
            compare_ref,
            compare_prefix,
        )
    return report


def render_text(report: dict[str, Any]) -> str:
    upstream = report.get("base_upstream")
    upstream_text = "none"
    if upstream:
        upstream_text = f"{upstream['ref']} ahead={upstream['ahead']} behind={upstream['behind']}"
    lines = [
        f"repo: {report['repo_root']}",
        f"base: {report['base']} {report['base_head'] or 'missing'}",
        f"upstream: {upstream_text}",
        f"canonical dirty paths: {len(report['canonical_dirty_paths'])}",
        f"worktrees: {len(report['worktrees'])}",
        f"missing worktrees: {', '.join(report['missing_worktrees']) or 'none'}",
        f"local branches: {len(report['branches'])}",
        f"unmerged candidates: {', '.join(report['unmerged_candidates']) or 'none'}",
        f"merged branch candidates: {', '.join(report['merged_branch_candidates']) or 'none'}",
        f"stashes: {len(report['stashes'])}",
        f"operations in progress: {', '.join(report['operations_in_progress']) or 'none'}",
        f"archive tags: {len(report['archive_tags'])}",
        f"tracked files: {report['structure']['tracked_file_count']}",
    ]
    profile = report["profile_signals"]
    lines.extend(
        [
            "languages: "
            + (", ".join(item["name"] for item in profile["languages"]) or "none"),
            "build systems: "
            + (", ".join(item["name"] for item in profile["build_systems"]) or "none"),
            "application signals: "
            + (", ".join(item["name"] for item in profile["application_signals"]) or "none"),
            "composition signals: "
            + (", ".join(item["name"] for item in profile["composition_signals"]) or "none"),
        ]
    )
    artifacts = report.get("artifacts")
    if artifacts:
        candidates = [
            item["path"] for item in artifacts["artifacts"] if item["cleanup_candidate"]
        ]
        lines.extend(
            [
                f"artifact directories: {len(artifacts['artifacts'])}",
                f"artifact bytes: {artifacts['total_bytes']}",
                f"artifact cleanup candidates: {', '.join(candidates) or 'none'}",
                "active missing artifacts: "
                + (
                    ", ".join(item["path"] for item in artifacts["active_missing_artifacts"])
                    or "none"
                ),
            ]
        )
    comparison = report.get("source_comparison")
    if comparison:
        lines.extend(
            [
                f"source comparison: {comparison['source_repo']} -> {comparison['target_prefix']}",
                f"source head ancestor of {comparison['target_ref']}: "
                f"{comparison['source_head_ancestor_of_target_ref']}",
                f"source tree exact match: {comparison['exact_tree_match']}",
                f"source/changed/target-only paths: "
                f"{len(comparison['source_only_paths'])}/"
                f"{len(comparison['changed_paths'])}/"
                f"{len(comparison['target_only_paths'])}",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Path inside the Git repository")
    parser.add_argument("--base", default="main", help="Local integration branch")
    parser.add_argument(
        "--artifacts",
        action="store_true",
        help="Inventory common build, cache, and generated directories recursively",
    )
    parser.add_argument(
        "--artifact-pattern",
        action="append",
        default=[],
        help="Additional artifact-directory glob (repeatable)",
    )
    parser.add_argument(
        "--protect-artifact",
        action="append",
        default=[],
        help="Artifact path or name that must not be a cleanup candidate (repeatable)",
    )
    parser.add_argument(
        "--no-default-artifact-protection",
        action="store_true",
        help="Do not automatically protect canonical top-level build and build-rust roots",
    )
    parser.add_argument(
        "--stale-days",
        type=float,
        default=7,
        help="Minimum age for an ignored artifact cleanup candidate (default: 7)",
    )
    parser.add_argument("--compare-repo", help="Sibling/source Git repository to compare")
    parser.add_argument(
        "--compare-prefix",
        help="Path inside --base that should represent --compare-repo",
    )
    parser.add_argument("--compare-ref", default="HEAD", help="Source repository ref")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()
    try:
        patterns = tuple(dict.fromkeys((*DEFAULT_ARTIFACT_PATTERNS, *args.artifact_pattern)))
        report = audit(
            Path(args.repo),
            base=args.base,
            include_artifacts=args.artifacts,
            artifact_patterns=patterns,
            protected_artifacts=set(args.protect_artifact),
            use_default_artifact_protections=not args.no_default_artifact_protection,
            stale_days=args.stale_days,
            compare_repo=Path(args.compare_repo) if args.compare_repo else None,
            compare_prefix=args.compare_prefix,
            compare_ref=args.compare_ref,
        )
    except (RuntimeError, OSError, ValueError) as error:
        print(f"repository maintenance audit failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
