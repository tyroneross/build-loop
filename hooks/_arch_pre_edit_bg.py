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


def classify(payload_text: str, workdir: Path, file_map_path: Path) -> list:
    """Return the edit's targets: "MANIFEST" and/or repo-relative tracked paths.

    A multi-file edit (``edits[]``, apply_patch) names several paths; each is
    classified on its own (scripts/hook_payload.py owns payload parsing).
    """
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from hook_payload import edited_paths
    except ImportError:
        return []
    workdir = workdir.resolve()
    file_map = None
    targets = []
    for fp in edited_paths(payload):
        absolute = (workdir / fp).resolve()
        try:
            rel = absolute.relative_to(workdir).as_posix()
        except ValueError:
            continue
        if absolute.name in MANIFESTS:
            targets.append("MANIFEST")
            continue
        if absolute.suffix.lower() not in ALLOWED_EXTS:
            continue
        if file_map is None:
            try:
                file_map = json.loads(file_map_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return targets
        if rel in (file_map.get("files") or {}):
            targets.append(rel)
    return list(dict.fromkeys(targets))


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
    # The freshness script ships with the plugin; a project copy is only a fallback.
    freshness = here.parent / "scripts" / "architecture_freshness.py"
    if not freshness.is_file():
        freshness = workdir / "scripts" / "architecture_freshness.py"
    worker = here / "_arch_scan_bg.py"

    targets = classify(payload_text, workdir, arch_dir / "file_map.json")
    if "MANIFEST" in targets:
        try:
            (arch_dir / ".enrich-needed").touch()
            os.truncate(arch_dir / ".enrich-needed", 0)
        except OSError:
            pass
    sources = [t for t in targets if t != "MANIFEST"]
    if not sources:
        return 0

    # Order matters: stale must be marked before the scan can mark fresh.
    for rel in sources:
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
