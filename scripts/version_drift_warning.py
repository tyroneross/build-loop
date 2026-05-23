#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Detect drift between source manifest version and installed plugin version.

Self-recursive plugin-developer signal: working-copy commits may be ahead of
the installed runtime. Schema (always valid JSON, never raises): drift_detected,
plugin_name, manifest_version, installed_version, installed_sha, head_sha,
commits_ahead (int|null), warning_message, skip_reason (null|
plugin_not_installed|no_git|no_manifest). Pure stdlib. Python 3.11+.
"""
from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path


def _git(workdir: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(workdir), *args], capture_output=True,
                             text=True, timeout=5, check=False)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _commits_ahead(workdir: Path, mver: str | None, sha: str | None) -> int | None:
    tries = [("rev-list", "--count", f"{r}..HEAD") for r in ((f"v{mver}", mver) if mver else ()) if r]
    if sha:
        tries.append(("rev-list", "--count", "HEAD", f"^{sha}"))
    for spec in tries:
        n = _git(workdir, *spec)
        if n and n.isdigit():
            return int(n)
    return None


def _load_json(path: Path):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError): return None


def detect(workdir: Path, installed_path: Path | None = None) -> dict:
    installed_path = installed_path or (Path.home() / ".claude" / "plugins" / "installed_plugins.json")
    r = {"drift_detected": False, "plugin_name": None, "manifest_version": None,
         "installed_version": None, "installed_sha": None, "head_sha": None,
         "commits_ahead": 0, "warning_message": None, "skip_reason": None}
    m = _load_json(workdir / ".claude-plugin" / "plugin.json") or {}
    name = m.get("name") if isinstance(m.get("name"), str) and m.get("name") else None
    if name is None:
        r["skip_reason"] = "no_manifest"; return r
    r["plugin_name"] = name
    r["manifest_version"] = m.get("version") if isinstance(m.get("version"), str) else None
    if not (workdir / ".git").exists():
        r["skip_reason"] = "no_git"; return r
    r["head_sha"] = _git(workdir, "rev-parse", "HEAD")
    entry = next((e[0] for k, e in ((_load_json(installed_path) or {}).get("plugins") or {}).items()
                  if isinstance(k, str) and k.startswith(name + "@")
                  and isinstance(e, list) and e and isinstance(e[0], dict)), None)
    if entry is None:
        r["skip_reason"] = "plugin_not_installed"; return r
    r["installed_version"] = entry.get("version") if isinstance(entry.get("version"), str) else None
    r["installed_sha"] = entry.get("gitCommitSha") if isinstance(entry.get("gitCommitSha"), str) else None
    ahead = _commits_ahead(workdir, r["installed_version"], r["installed_sha"])
    if ahead is None:
        r["commits_ahead"] = None
        r["warning_message"] = "drift detection unavailable (missing tag or shallow clone)"
        return r
    r["commits_ahead"] = ahead
    if ahead > 0:
        ref = r["installed_version"] or "(unknown)"
        sp = f" (sha {r['installed_sha'][:7]})" if r["installed_sha"] else ""
        r["drift_detected"] = True
        r["warning_message"] = (f"Source has {ahead} commit{'s' if ahead != 1 else ''} "
                                f"beyond installed v{ref}{sp}. Consider bumping plugin.json version.")
    return r


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Detect plugin manifest version drift.")
    p.add_argument("--workdir", required=True, type=Path)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    r = detect(a.workdir.expanduser())
    if a.json:
        print(json.dumps(r, indent=2)); return 0
    print(f"drift_detected: {'yes' if r['drift_detected'] else 'no'}")
    for k in ("plugin_name", "manifest_version", "installed_version",
              "commits_ahead", "warning_message", "skip_reason"):
        if r[k] is not None: print(f"{k}: {r[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
