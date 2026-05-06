#!/usr/bin/env python3
"""Echo the working-copy branch + SHA when the runtime is self-recursive.

Self-recursive plugin developer signal: surface which branch the dogfooding
session is actually running off of. Schema (always valid JSON, never raises):
branch (str|null), head_sha (str|null), dirty_files (int|null), message
(str|null), skip_reason (null|no_git|not_self_recursive). Pure stdlib. 3.11+.
"""
from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path


def _git(workdir: Path, *args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(workdir), *args], capture_output=True,
                           text=True, timeout=5, check=False)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def detect(workdir: Path, *, self_recursive: bool = True) -> dict:
    r = {"branch": None, "head_sha": None, "dirty_files": None,
         "message": None, "skip_reason": None}
    if _git(workdir, "rev-parse", "--git-dir")[0] != 0:
        r["skip_reason"] = "no_git"; return r
    rc, out = _git(workdir, "branch", "--show-current")
    r["branch"] = out.strip() if rc == 0 and out.strip() else None
    rc, out = _git(workdir, "rev-parse", "--short", "HEAD")
    r["head_sha"] = out.strip() if rc == 0 and out.strip() else None
    rc, out = _git(workdir, "status", "--porcelain")
    r["dirty_files"] = sum(1 for ln in out.splitlines() if ln.strip()) if rc == 0 else None
    if not self_recursive:
        r["skip_reason"] = "not_self_recursive"; return r
    where = f"`{r['branch']}`" if r["branch"] else "(detached HEAD)"
    suffix = f"{r['dirty_files']} dirty file{'s' if r['dirty_files'] != 1 else ''}"
    r["message"] = f"🔁 Self-recursive runtime — working copy on {where} @ {r['head_sha']}, {suffix}"
    return r


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Echo working-copy branch for self-recursive builds.")
    p.add_argument("--workdir", required=True, type=Path)
    p.add_argument("--json", action="store_true")
    p.add_argument("--not-self-recursive", action="store_true",
                   help="Suppress message; emit skip_reason=not_self_recursive.")
    a = p.parse_args(argv)
    r = detect(a.workdir.expanduser(), self_recursive=not a.not_self_recursive)
    if a.json:
        print(json.dumps(r, indent=2)); return 0
    for k in ("branch", "head_sha", "dirty_files", "message", "skip_reason"):
        if r[k] is not None: print(f"{k}: {r[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
