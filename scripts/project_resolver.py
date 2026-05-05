#!/usr/bin/env python3
"""Resolve a current working directory to a project tag.

Reads ``<agent_memory_root>/.config/projects.yaml`` and returns the
project tag whose ``path:`` is the longest prefix match against the
given ``cwd``. Falls back to the YAML's ``default:`` key, which itself
defaults to ``_unscoped`` if absent.

Pure stdlib parser — projects.yaml is a small file we control, so we
parse just the subset we emit (top-level ``default:`` scalar plus a
``projects:`` list of ``- path: ...\\n  project: ...`` blocks).

Public API:
    resolve_project(cwd: Path) -> str
    load_projects_yaml(path: Path | None = None) -> dict
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
import sys
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from _paths import agent_memory_root  # type: ignore  # noqa: E402

DEFAULT_PROJECT_TAG = "_unscoped"


def _projects_yaml_path() -> Path:
    return agent_memory_root() / ".config" / "projects.yaml"


def load_projects_yaml(path: Path | None = None) -> dict[str, Any]:
    """Parse the small subset of YAML we emit.

    Returns ``{"default": <tag>, "projects": [{"path": <abs>, "project": <tag>}, ...]}``.
    Missing file → ``{"default": "_unscoped", "projects": []}``.
    """
    if path is None:
        path = _projects_yaml_path()
    if not path.exists():
        return {"default": DEFAULT_PROJECT_TAG, "projects": []}
    text = path.read_text(encoding="utf-8")
    return _parse_projects_yaml(text)


def _parse_projects_yaml(text: str) -> dict[str, Any]:
    """Parse the subset of YAML used in projects.yaml.

    Recognized shapes:
        default: <scalar>
        projects:
          - path: <scalar>
            project: <scalar>
    Lines starting with ``#`` and blank lines are ignored.
    """
    default_tag = DEFAULT_PROJECT_TAG
    projects: list[dict[str, str]] = []
    in_projects = False
    cur: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        # Strip inline comments (very simple: a `#` not inside quotes).
        # projects.yaml never quotes values, so this is safe.
        if "#" in line:
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            line = line.split("#", 1)[0].rstrip()
        if not line:
            continue
        # Top-level key
        if not line[0].isspace():
            if line.startswith("default:"):
                val = line.split(":", 1)[1].strip()
                if val:
                    default_tag = val
                in_projects = False
                cur = None
            elif line.startswith("projects:"):
                in_projects = True
                cur = None
            else:
                in_projects = False
                cur = None
            continue
        # Indented line under projects:
        if not in_projects:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            # Start of a new project entry. The "- " line might itself
            # carry the first key (e.g. "- path: ~/dev/foo").
            if cur is not None:
                projects.append(cur)
            cur = {}
            after = stripped[2:].strip()
            if ":" in after:
                k, _, v = after.partition(":")
                cur[k.strip()] = v.strip()
        elif ":" in stripped and cur is not None:
            k, _, v = stripped.partition(":")
            cur[k.strip()] = v.strip()
    if cur is not None:
        projects.append(cur)
    # Filter out incomplete entries.
    projects = [p for p in projects if "path" in p and "project" in p]
    return {"default": default_tag or DEFAULT_PROJECT_TAG, "projects": projects}


def _normalize(p: str | Path) -> str:
    """Expand ``~`` and resolve to absolute path string. Trailing slash dropped."""
    return os.path.normpath(os.path.expanduser(str(p)))


def resolve_project(cwd: Path | str) -> str:
    """Return the project tag for ``cwd``.

    Resolution order (per design §projects.yaml):
      1. Exact path match (longest wins among ties — only one can be exact).
      2. Path prefix match (longest wins).
      3. ``default:`` from the YAML, else ``_unscoped``.
    """
    data = load_projects_yaml()
    cwd_norm = _normalize(cwd)
    best_match: tuple[int, str] | None = None  # (path_length, project_tag)
    for entry in data["projects"]:
        path_norm = _normalize(entry["path"])
        # Exact match wins outright.
        if path_norm == cwd_norm:
            return entry["project"]
        # Prefix match: cwd must be path_norm OR start with path_norm + os.sep
        if cwd_norm.startswith(path_norm + os.sep):
            length = len(path_norm)
            if best_match is None or length > best_match[0]:
                best_match = (length, entry["project"])
    if best_match is not None:
        return best_match[1]
    return data["default"]


if __name__ == "__main__":  # pragma: no cover - manual smoke tool
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    print(resolve_project(target))
