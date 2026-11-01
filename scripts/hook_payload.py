# SPDX-License-Identifier: Apache-2.0
"""Normalize file changes from Claude, Codex and the generic JSON hook contract."""
from __future__ import annotations

import re
from typing import Any


def edited_paths(payload: dict[str, Any]) -> list[str]:
    data = payload.get("tool_input") or {}
    if not isinstance(data, dict):
        return []
    paths = [data.get(k) for k in ("file_path", "path", "notebook_path")]
    if payload.get("tool_name", payload.get("tool")) == "apply_patch":
        patch = data.get("command", "")
        if isinstance(patch, str):
            paths.extend(re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", patch, re.M))
    for edit in data.get("edits", []) if isinstance(data.get("edits"), list) else []:
        if isinstance(edit, dict):
            paths.append(edit.get("file_path") or edit.get("path"))
    return list(dict.fromkeys(p for p in paths if isinstance(p, str) and p and "\n" not in p and "\r" not in p))
