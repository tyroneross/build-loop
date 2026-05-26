#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Validate the embedded agent-rally plugin boundary manifest.

This is intentionally small and stdlib-only. The manifest is a local
extraction contract: it names which files belong to agent-rally-point,
which files belong to agent-rally-watcher, and which build-loop files are
only compatibility adapters.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

MANIFEST_NAME = "plugin_boundary.json"
PATH_FIELDS = (
    "skill_entrypoints",
    "owns",
    "compatibility_entrypoints",
    "build_loop_adapters",
    "docs",
    "grouped_dependencies",
    "tests",
)


def repo_root_from_here() -> Path:
    return Path(__file__).resolve().parents[2]


def manifest_path(repo: Path | None = None) -> Path:
    root = (repo or repo_root_from_here()).expanduser().resolve()
    return root / "scripts" / "rally_point" / MANIFEST_NAME


def load_manifest(repo: Path | None = None) -> dict[str, Any]:
    path = manifest_path(repo)
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _expand_path(root: Path, pattern: str) -> list[Path]:
    if any(ch in pattern for ch in "*?["):
        return [p for p in root.glob(pattern) if p.exists()]
    p = root / pattern
    return [p] if p.exists() else []


def _python_files(root: Path, patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        for path in _expand_path(root, pattern):
            if path.is_dir():
                files.extend(
                    p for p in path.rglob("*.py")
                    if "__pycache__" not in p.parts
                )
            elif path.suffix == ".py":
                files.append(path)
    return sorted(set(files))


def _forbidden_module_names(paths: list[str]) -> list[str]:
    names: list[str] = []
    for value in paths:
        p = Path(value)
        if p.suffix == ".py":
            names.append(p.stem)
            names.append(".".join(p.with_suffix("").parts))
    return sorted(set(names))


def _imports_forbidden_module(source: str, module_name: str) -> bool:
    escaped = re.escape(module_name)
    patterns = (
        rf"^\s*import\s+{escaped}(\s|,|$)",
        rf"^\s*from\s+{escaped}\s+import\s+",
    )
    return any(re.search(pattern, source, re.MULTILINE) for pattern in patterns)


def validate_manifest(repo: Path | None = None) -> dict[str, Any]:
    root = (repo or repo_root_from_here()).expanduser().resolve()
    data = load_manifest(root)
    findings: list[dict[str, str]] = []
    plugins = data.get("plugins")
    if not isinstance(plugins, dict) or not plugins:
        findings.append({
            "severity": "error",
            "path": "plugins",
            "message": "manifest must define at least one plugin boundary",
        })
        plugins = {}

    for plugin_name, spec in plugins.items():
        if not isinstance(spec, dict):
            findings.append({
                "severity": "error",
                "path": f"plugins.{plugin_name}",
                "message": "plugin spec must be an object",
            })
            continue
        if not spec.get("purpose"):
            findings.append({
                "severity": "error",
                "path": f"plugins.{plugin_name}.purpose",
                "message": "purpose is required",
            })
        if not spec.get("skill_entrypoints"):
            findings.append({
                "severity": "error",
                "path": f"plugins.{plugin_name}.skill_entrypoints",
                "message": "native build-loop skill entrypoint is required",
            })
        for field in PATH_FIELDS:
            values = spec.get(field, [])
            if not isinstance(values, list):
                findings.append({
                    "severity": "error",
                    "path": f"plugins.{plugin_name}.{field}",
                    "message": "field must be a list of repo-relative paths",
                })
                continue
            for pattern in values:
                if not isinstance(pattern, str) or not pattern.strip():
                    findings.append({
                        "severity": "error",
                        "path": f"plugins.{plugin_name}.{field}",
                        "message": "path entries must be non-empty strings",
                    })
                    continue
                matches = _expand_path(root, pattern)
                if not matches:
                    findings.append({
                        "severity": "error",
                        "path": f"plugins.{plugin_name}.{field}",
                        "message": f"no files matched {pattern}",
                    })

        forbidden = spec.get("forbidden_parent_imports", [])
        owns = spec.get("owns", [])
        if isinstance(forbidden, list) and isinstance(owns, list):
            module_names = _forbidden_module_names([
                value for value in forbidden if isinstance(value, str)
            ])
            for py_file in _python_files(root, [
                value for value in owns if isinstance(value, str)
            ]):
                try:
                    source = py_file.read_text(encoding="utf-8")
                except OSError:
                    continue
                for module_name in module_names:
                    if _imports_forbidden_module(source, module_name):
                        findings.append({
                            "severity": "error",
                            "path": str(py_file.relative_to(root)),
                            "message": (
                                "extractable namespace imports forbidden "
                                f"parent module {module_name}"
                            ),
                        })

    return {
        "schema_version": data.get("schema_version"),
        "manifest": str(manifest_path(root)),
        "plugins": sorted(plugins.keys()),
        "skill_entrypoints": {
            name: spec.get("skill_entrypoints", [])
            for name, spec in sorted(plugins.items())
            if isinstance(spec, dict)
        },
        "ok": not any(f["severity"] == "error" for f in findings),
        "findings": findings,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", default=None, help="Repo root; defaults to this checkout")
    p.add_argument("--check", action="store_true", help="Exit non-zero on validation errors")
    p.add_argument("--json", action="store_true", help="Emit JSON envelope")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = Path(args.repo).expanduser().resolve() if args.repo else None
    result = validate_manifest(repo)
    if args.json or not sys.stdout.isatty():
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        status = "ok" if result["ok"] else "failed"
        print(f"agent-rally boundary: {status} ({', '.join(result['plugins'])})")
        for finding in result["findings"]:
            print(f"{finding['severity']}: {finding['path']}: {finding['message']}")
    if args.check and not result["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
