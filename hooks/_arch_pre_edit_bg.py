#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Detached worker for hooks/pre-edit-architecture.sh.

The PreToolUse hook only reads stdin to a file and spawns this process in its
own process group (stdin/stdout/stderr on /dev/null). Everything the hook used
to do inline happens here, in the same order:

  1. classify the edited path (manifest / tracked source file / bail)
  2. manifest  -> touch ``.build-loop/architecture/.enrich-needed``
  3. source    -> ``architecture_freshness.py --mark-stale --file REL``, then
                  ``_arch_scan_bg.py`` (its own flock keeps one scan at a time)

Deadline: ``signal.alarm`` (DEADLINE_S). On SIGALRM or SIGTERM this process
sends SIGTERM to its whole process group (worker, ``uv`` children) and exits.
Never SIGKILL. Never raises; always exits 0.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

DEADLINE_S = 300  # worker budget is 120 + 120 + 10 s; this bounds the lot

ALLOWED_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs"}
MANIFESTS = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "requirements.txt",
    "pyproject.toml", "uv.lock", "Cargo.toml", "Cargo.lock", "go.mod", "Gemfile",
}


_child = None


def _terminate_group(*_args) -> None:
    """SIGTERM the whole process group, except ourselves, then exit."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        if os.getpgrp() == os.getpid():  # we lead our own group (hook's set -m)
            os.killpg(os.getpgrp(), signal.SIGTERM)
        elif _child is not None:  # run directly from a caller's group: only our child
            _child.terminate()
    except OSError:
        pass
    os._exit(0)


def classify(payload_text: str, workdir: Path, file_map_path: Path) -> str:
    """Return "MANIFEST", a repo-relative tracked path, or "" (bail)."""
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return ""
    ti = payload.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("path") or ti.get("filename")
    if not fp:
        return ""
    if Path(fp).name in MANIFESTS:
        return "MANIFEST"
    if Path(fp).suffix.lower() not in ALLOWED_EXTS:
        return ""
    workdir = workdir.resolve()
    abs_path = (workdir / fp).resolve() if not os.path.isabs(fp) else Path(fp).resolve()
    try:
        rel = abs_path.relative_to(workdir).as_posix()
    except ValueError:
        return ""
    try:
        file_map = json.loads(file_map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return rel if rel in (file_map.get("files") or {}) else ""


def run_to_completion(cmd: list) -> None:
    """Run cmd to exit; the process-group SIGTERM from the alarm stops it."""
    global _child
    try:
        _child = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        _child.wait()
    except OSError:
        pass
    finally:
        _child = None


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workdir", required=True)
    p.add_argument("--payload", required=True, help="file holding the hook stdin JSON; deleted after read")
    args = p.parse_args(argv)

    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, _terminate_group)
    signal.signal(signal.SIGALRM, _terminate_group)
    signal.alarm(DEADLINE_S)

    try:
        payload_text = Path(args.payload).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    finally:
        try:
            os.unlink(args.payload)
        except OSError:
            pass

    workdir = Path(args.workdir)
    arch_dir = workdir / ".build-loop" / "architecture"
    here = Path(__file__).resolve().parent
    freshness = workdir / "scripts" / "architecture_freshness.py"
    worker = here / "_arch_scan_bg.py"

    rel = classify(payload_text, workdir, arch_dir / "file_map.json")
    if not rel:
        return 0
    if rel == "MANIFEST":
        try:
            (arch_dir / ".enrich-needed").touch()
            os.truncate(arch_dir / ".enrich-needed", 0)
        except OSError:
            pass
        return 0

    # Order matters: stale must be marked before the scan can mark fresh.
    run_to_completion([sys.executable, str(freshness), "--mark-stale", "--file", rel,
                       "--workdir", str(workdir)])
    run_to_completion([sys.executable, str(worker), "--workdir", str(workdir)])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        raise SystemExit(0)
