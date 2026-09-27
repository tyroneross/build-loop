#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Fast, read-only checks for predictable build and deploy delays.

This is a triage probe, not a deployment gate. It reads a bounded set of local
files and never invokes package scripts, scans, network calls, or migrations.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

AUTO_DEPLOY_CLAIM = re.compile(
    r"(?:\bmain\b\W+auto.deploys?\b|\bpush is a deploy\b)", re.I,
)
SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".py", ".go", ".rs", ".swift", ".prisma", ".sql"}
SOURCE_MANIFESTS = {"package.json", "pnpm-lock.yaml", "package-lock.json", "pyproject.toml", "go.mod", "Cargo.toml", "Package.swift"}


def _json_file(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _head(workdir: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _source_worktree_changes(workdir: Path) -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(workdir), "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    paths: list[str] = []
    for line in result.stdout.splitlines():
        path = line[3:].split(" -> ")[-1].strip('"')
        if path.startswith((".build-loop/", ".navgator/", "node_modules/")):
            continue
        if Path(path).suffix in SOURCE_SUFFIXES or Path(path).name in SOURCE_MANIFESTS:
            paths.append(path)
    return paths


def _finding(id_: str, evidence: str, action: str) -> dict[str, str]:
    return {"id": id_, "severity": "warn", "evidence": evidence, "action": action}


def probe(workdir: Path, *, now_ms: int | None = None) -> dict[str, Any]:
    """Return bounded findings and commands available for early validation."""
    workdir = workdir.resolve()
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    findings: list[dict[str, str]] = []
    available_checks: list[str] = []

    package = _json_file(workdir / "package.json") or {}
    scripts = package.get("scripts") or {}
    if isinstance(scripts, dict):
        for name in ("deploy:db:gate", "deploy:preflight", "preflight"):
            if isinstance(scripts.get(name), str):
                available_checks.append(f"npm run {name}")
    else:
        scripts = {}

    index = _json_file(workdir / ".navgator/architecture/index.json")
    freshness = _json_file(workdir / ".navgator/architecture/freshness.json") or {}
    if index is not None:
        last_scan = index.get("last_scan")
        age_hours: float | None = None
        if isinstance(last_scan, (int, float)) and 0 < last_scan <= now_ms:
            age_hours = (now_ms - last_scan) / 3_600_000
        else:
            findings.append(_finding(
                "architecture_index_unverifiable",
                "NavGator index has no usable last_scan timestamp",
                "Refresh the graph before relying on impact results.",
            ))
        head = _head(workdir)
        scan_sha = freshness.get("commit_sha")
        comparable_sha = scan_sha.strip() if isinstance(scan_sha, str) else ""
        if comparable_sha and head and not head.startswith(comparable_sha):
            findings.append(_finding(
                "architecture_index_other_revision",
                f"NavGator graph records {comparable_sha}; checkout is {head[:12]}"
                + (f"; scan age {age_hours:.1f} hours" if age_hours is not None else ""),
                "Refresh the graph in this checkout before using its impact list.",
            ))
        elif not comparable_sha or not head:
            findings.append(_finding(
                "architecture_index_unpinned",
                "NavGator graph has no comparable checkout SHA"
                + (f"; scan age {age_hours:.1f} hours" if age_hours is not None else ""),
                "Refresh the graph before relying on impact results; verify suggested paths in source.",
            ))
        elif comparable_sha and head:
            changes = _source_worktree_changes(workdir)
            if changes is None:
                findings.append(_finding(
                    "architecture_index_worktree_unchecked",
                    "Git status could not verify source changes against the saved graph",
                    "Verify current source or refresh the graph before using its impact list.",
                ))
            elif changes:
                findings.append(_finding(
                    "architecture_index_uncommitted_changes",
                    f"{len(changes)} source or manifest files differ from the scanned checkout"
                    + f" (for example {changes[0]})",
                    "Refresh or verify impact against current source before using the saved graph.",
                ))
        dirty_count = freshness.get("dirty_count")
        if isinstance(dirty_count, int) and dirty_count > 0:
            findings.append(_finding(
                "architecture_index_dirty",
                f"NavGator freshness ledger has {dirty_count} dirty files",
                "Refresh the graph before using its impact list.",
            ))

    dependencies = package.get("dependencies") or {}
    dev_dependencies = package.get("devDependencies") or {}
    if not isinstance(dependencies, dict):
        dependencies = {}
    if not isinstance(dev_dependencies, dict):
        dev_dependencies = {}
    dependencies = {**dependencies, **dev_dependencies}
    node_modules = workdir / "node_modules"
    if "next" in dependencies and node_modules.is_symlink():
        target = node_modules.resolve()
        try:
            target.relative_to(workdir)
        except ValueError:
            findings.append(_finding(
                "external_node_modules",
                f"Next.js node_modules points outside this checkout: {target}",
                "Install dependencies in this checkout before relying on a production build.",
            ))

    vercel = _json_file(workdir / "vercel.json") or {}
    git_config = vercel.get("git") or {}
    if not isinstance(git_config, dict):
        git_config = {}
    deployment_enabled = git_config.get("deploymentEnabled") or {}
    if isinstance(deployment_enabled, dict) and deployment_enabled.get("main") is False:
        workflow_dir = workdir / ".github/workflows"
        staged = False
        if workflow_dir.is_dir():
            for path in (*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")):
                try:
                    if "--skip-domain" in path.read_text(encoding="utf-8"):
                        staged = True
                        break
                except OSError:
                    continue
        if staged:
            for rel in ("AGENTS.md", "CLAUDE.md", "docs/AGENT-ROUTING.md"):
                path = workdir / rel
                try:
                    claim = AUTO_DEPLOY_CLAIM.search(path.read_text(encoding="utf-8"))
                except OSError:
                    continue
                if claim:
                    findings.append(_finding(
                        "deployment_doc_conflict",
                        f"{rel} says '{claim.group(0)}' while main Git deployment is disabled and staging uses --skip-domain",
                        "Read the live workflow and correct the doc before planning promotion.",
                    ))

    if "deploy:preflight" in scripts and "deploy:db:gate" in scripts:
        preflight = workdir / "scripts/deploy-preflight.sh"
        if preflight.is_file():
            try:
                body = preflight.read_text(encoding="utf-8")
            except OSError:
                body = ""
            build_pos = body.find("npm run build")
            gate_pos = body.find("npm run deploy:db:gate")
            if build_pos >= 0 and gate_pos > build_pos:
                findings.append(_finding(
                    "migration_gate_after_build",
                    "deploy-preflight.sh runs the database gate after the production build",
                    "Inspect the database gate; if read-only, run it before the build so migration blockers fail quickly.",
                ))

    return {
        "status": "issues_found" if findings else "clear",
        "workdir": str(workdir),
        "findings": findings,
        "available_checks": available_checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = probe(args.workdir)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for finding in result["findings"]:
            print(f"[{finding['id']}] {finding['evidence']} — {finding['action']}")
        if not result["findings"]:
            print("early risk probe: clear")
    return 0  # Findings inform plan order; this read-only probe never blocks.


if __name__ == "__main__":
    raise SystemExit(main())
