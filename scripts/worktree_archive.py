#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""worktree_archive.py — archive what a worktree removal would delete, then
write the receipt the removal guard requires.

Why this exists (2026-10-23): an agent removed 83 "merged, clean" Easy Terminal
worktrees after `git status --porcelain -uall` came back empty. That check omits
GITIGNORED files, so `.build-loop/`, `.bookmark/`, `.designdoc/` and `.ibr/`
state in 55 of them — including a Groundwork design project a live server was
serving — was deleted with no copy. `worktree_inventory.py` could already list
ignored files; nothing forced anyone to run it. This script is the one step the
guard (`scripts/hooks/worktree_removal_guard.py`) demands before any removal.

What it archives, per worktree:
  - tracked changes (modified/added files still on disk),
  - untracked files,
  - every ignored entry EXCEPT pure caches (tool caches, dependency trees, build
    output — the classes `worktree_inventory.classify` already names), plus any
    credential/database/patch-shaped file found inside such a cache directory.
Caches are LISTED in the receipt, never archived.

Outputs:
  - archive: `<repo-parent>/<repo>.worktrees/_preserved/<name>-<UTC>.tar.gz`
    (override with --dest); omitted when there is nothing to archive;
  - receipt: `~/.build-loop/worktree-removal-receipts/<sha1(realpath)>.json`
    (override with $BUILD_LOOP_WORKTREE_RECEIPTS_DIR), and a copy next to the
    archive so the evidence survives the receipts directory.

CLI::

    python3 scripts/worktree_archive.py --path <worktree> [--path <worktree> ...]
        [--dest <dir>] [--json]

Exit 0 = every path archived and receipted. Exit 1 = at least one path failed
(not a linked worktree, git error, write error); nothing is claimed for it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import worktree_inventory  # noqa: E402

# Classes that are reproducible from source + declared dependencies. Logs are
# deliberately NOT here: they are evidence, usually small, and not regenerable.
CACHE_CLASSES = frozenset({"tool_cache", "dependency", "build_output"})
# Maximum age, in seconds, of a receipt the guard accepts.
RECEIPT_TTL_S = 30 * 60
_GIT_TIMEOUT_S = 30
# File-list cap per receipt; the archive itself is never truncated.
_MAX_LISTED_FILES = 20_000
SCHEMA = "build-loop.worktree-removal-receipt/v1"


# ── receipts (shared with the guard) ─────────────────────────────────────────

def receipts_dir() -> Path:
    override = os.environ.get("BUILD_LOOP_WORKTREE_RECEIPTS_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".build-loop" / "worktree-removal-receipts"


def canonical(path: str | os.PathLike[str]) -> str:
    return os.path.realpath(os.path.expanduser(str(path)))


def receipt_path_for(path: str | os.PathLike[str]) -> Path:
    key = hashlib.sha1(canonical(path).encode("utf-8")).hexdigest()
    return receipts_dir() / f"{key}.json"


def load_receipt(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    try:
        data = json.loads(receipt_path_for(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def archive_command(paths: list[str], plugin_root: Path | None = None) -> str:
    """The exact command an agent runs to satisfy the guard."""
    script = (plugin_root / "scripts" / "worktree_archive.py") if plugin_root else Path(__file__).resolve()
    quoted = " ".join(f"--path {_shq(p)}" for p in paths)
    return f"python3 {_shq(str(script))} {quoted}"


def _shq(text: str) -> str:
    import shlex
    return shlex.quote(text)


# ── git helpers ──────────────────────────────────────────────────────────────

def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(path), *args],
        capture_output=True, text=True, check=False, timeout=_GIT_TIMEOUT_S,
    )


def head_of(path: str | os.PathLike[str]) -> tuple[str | None, str | None]:
    """Return (HEAD sha, error)."""
    try:
        proc = _git(Path(path), "rev-parse", "--verify", "-q", "HEAD")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"git rev-parse failed: {exc}"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout).strip() or "git rev-parse HEAD failed"
    return proc.stdout.strip(), None


def linked_worktree_admin(path: str | os.PathLike[str]) -> Path | None:
    """Return the admin dir if `path` is the root of a REGISTERED linked worktree.

    Registration is what `git worktree list` reads: `<path>/.git` is a gitlink
    file naming `<common>/worktrees/<id>`, and that admin dir's `gitdir` file
    points back at `<path>/.git`. No subprocess, so the guard stays fast.
    """
    root = Path(canonical(path))
    gitfile = root / ".git"
    try:
        if not gitfile.is_file():
            return None
        line = gitfile.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    admin = Path(line[len("gitdir:"):].strip())
    if not admin.is_absolute():
        admin = (root / admin)
    admin = Path(canonical(admin))
    try:
        back = (admin / "gitdir").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    back_path = Path(back)
    if not back_path.is_absolute():
        back_path = admin / back_path
    if canonical(back_path) != canonical(gitfile):
        return None
    if admin.parent.name != "worktrees":
        return None
    return admin


# ── archive ──────────────────────────────────────────────────────────────────

def _default_dest(wt: Path) -> Path:
    proc = _git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or "git rev-parse --git-common-dir failed")
    common = Path(proc.stdout.strip())
    if common.name == ".git":
        repo_dir = common.parent
        repo_name = repo_dir.name
    else:  # bare repository, e.g. foo.git
        repo_dir = common
        repo_name = common.name[:-4] if common.name.endswith(".git") else common.name
    return repo_dir.parent / f"{repo_name}.worktrees" / "_preserved"


def _branch_of(wt: Path) -> str | None:
    proc = _git(wt, "symbolic-ref", "-q", "--short", "HEAD")
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _walk_files(root: Path, rel_dir: str) -> tuple[list[str], list[str]]:
    """Return (files_to_archive, cache_paths_skipped) under one directory entry."""
    keep: list[str] = []
    skipped: list[str] = []
    base = root / rel_dir.rstrip("/")
    if base.is_symlink() or base.is_file():
        return [rel_dir.rstrip("/")], []
    for dirpath, dirnames, filenames in os.walk(base):
        kept_dirs = []
        for d in sorted(dirnames):
            full = Path(dirpath) / d
            rel = full.relative_to(root).as_posix()
            if full.is_symlink():
                keep.append(rel)  # archived as a link, never followed
                continue
            if worktree_inventory.classify(rel) in CACHE_CLASSES:
                skipped.append(rel + "/")
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs
        for name in sorted(filenames):
            rel = (Path(dirpath) / name).relative_to(root).as_posix()
            if worktree_inventory.classify(rel) in CACHE_CLASSES:
                skipped.append(rel)
            else:
                keep.append(rel)
    return keep, skipped


def _collect(wt: Path) -> dict[str, Any]:
    inv = worktree_inventory.inventory(wt, matching=False)
    if not inv["ok"]:
        raise RuntimeError(inv["error"] or "inventory failed")
    files: dict[str, str] = {}  # rel path -> kind
    caches: list[str] = []

    for entry in inv["tracked_changes"]:
        code, _, rel = entry.partition(" ")
        rel = rel.strip()
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        if rel and ((wt / rel).exists() or (wt / rel).is_symlink()):
            files.setdefault(rel, "tracked_change")
    for rel in inv["untracked"]:
        kept, skipped = _walk_files(wt, rel)
        for k in kept:
            files.setdefault(k, "untracked")
        caches.extend(skipped)
    for rel in inv["ignored"]:
        cls = worktree_inventory.classify(rel)
        if cls in CACHE_CLASSES:
            caches.append(rel)
            continue
        kept, skipped = _walk_files(wt, rel)
        for k in kept:
            files.setdefault(k, "ignored")
        caches.extend(skipped)
    # Credential/database/patch-shaped files the inventory found inside cache dirs.
    for rel in inv["non_reproducible_ignored"]:
        if (wt / rel).is_file() and not rel.endswith("/"):
            files.setdefault(rel, "ignored")
    return {"inventory": inv, "files": files, "caches": sorted(set(caches))}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def archive(path: str | os.PathLike[str], *, dest: str | os.PathLike[str] | None = None,
            plugin_root: Path | None = None) -> dict[str, Any]:
    """Archive one worktree and write its receipt. Raises on any failure."""
    wt = Path(canonical(path))
    if not wt.is_dir():
        raise RuntimeError(f"{wt} is not a directory")
    if linked_worktree_admin(wt) is None:
        raise RuntimeError(f"{wt} is not the root of a registered linked worktree")
    head, err = head_of(wt)
    if err:
        raise RuntimeError(err)
    collected = _collect(wt)
    files: dict[str, str] = collected["files"]
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    listed = []
    total = 0
    for rel in sorted(files):
        full = wt / rel
        try:
            size = full.lstat().st_size
        except OSError:
            continue
        total += size
        listed.append({"path": rel, "kind": files[rel], "bytes": size})

    archive_path: Path | None = None
    digest = None
    archive_bytes = 0
    dest_dir = Path(dest).expanduser() if dest else _default_dest(wt)
    if listed:
        dest_dir.mkdir(parents=True, exist_ok=True)
        archive_path = dest_dir / f"{wt.name}-{stamp}.tar.gz"
        tmp = archive_path.with_suffix(".tmp")
        with tarfile.open(tmp, "w:gz") as tar:
            for item in listed:
                tar.add(str(wt / item["path"]), arcname=f"{wt.name}/{item['path']}", recursive=False)
        os.replace(tmp, archive_path)
        digest = _sha256(archive_path)
        archive_bytes = archive_path.stat().st_size

    inv = collected["inventory"]
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "path": str(wt),
        "head": head,
        "branch": _branch_of(wt),
        "created_at": now.isoformat(timespec="seconds"),
        "created_at_epoch": now.timestamp(),
        "archive_path": str(archive_path) if archive_path else None,
        "archive_sha256": digest,
        "archive_bytes": archive_bytes,
        "archived_file_count": len(listed),
        "archived_source_bytes": total,
        "files": listed[:_MAX_LISTED_FILES],
        "files_truncated": len(listed) > _MAX_LISTED_FILES,
        "caches_listed_not_archived": collected["caches"],
        "uninspected_ignored_directories": inv["uninspected_ignored_directories"],
        "status_warnings": inv["status_warnings"],
        "characterization": inv["characterization"],
        "restore": (f"tar -xzf {_shq(str(archive_path))} -C <parent-dir>" if archive_path else None),
    }
    rpath = receipt_path_for(wt)
    rpath.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(receipt, indent=2)
    tmp_r = rpath.with_suffix(".tmp")
    tmp_r.write_text(text, encoding="utf-8")
    os.replace(tmp_r, rpath)
    if archive_path:
        archive_path.with_name(archive_path.name[: -len(".tar.gz")] + ".receipt.json").write_text(text, encoding="utf-8")
    receipt["receipt_path"] = str(rpath)
    return receipt


def receipt_verdict(path: str | os.PathLike[str], *, now: float | None = None) -> tuple[str, str]:
    """Judge whether a removal of `path` is covered by a receipt.

    Returns (verdict, detail) with verdict one of:
      ok        — fresh receipt, HEAD matches, archive present;
      missing   — no receipt;
      stale     — receipt older than RECEIPT_TTL_S, HEAD moved, or archive gone;
      git_error — receipt exists but current HEAD could not be read (fail open).
    """
    receipt = load_receipt(path)
    if receipt is None:
        return "missing", "no archive receipt exists for this worktree"
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    try:
        age = now - float(receipt.get("created_at_epoch", 0))
    except (TypeError, ValueError):
        age = float("inf")
    if age > RECEIPT_TTL_S or age < -60:
        return "stale", f"the archive receipt is {int(age // 60)} minutes old (limit {RECEIPT_TTL_S // 60})"
    archive_path = receipt.get("archive_path")
    if archive_path and not Path(archive_path).is_file():
        return "stale", f"the receipt's archive {archive_path} no longer exists"
    head, err = head_of(path)
    if err:
        return "git_error", err
    if head != receipt.get("head"):
        return "stale", f"HEAD moved since the archive ({str(receipt.get('head'))[:12]} -> {str(head)[:12]})"
    return "ok", str(receipt.get("archive_path") or "nothing to archive")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Archive a worktree's untracked + ignored files before removal.")
    p.add_argument("--path", action="append", required=True, help="Worktree root (repeatable)")
    p.add_argument("--dest", help="Archive directory (default <repo>.worktrees/_preserved)")
    p.add_argument("--json", action="store_true", help="Emit receipts as JSON")
    args = p.parse_args(argv)
    failed = 0
    out = []
    for raw in args.path:
        try:
            receipt = archive(raw, dest=args.dest)
        except Exception as exc:  # noqa: BLE001 — report per path, never claim success
            failed += 1
            print(f"error: {canonical(raw)}: {exc}", file=sys.stderr)
            continue
        out.append(receipt)
        if not args.json:
            where = receipt["archive_path"] or "nothing to archive"
            print(
                f"archived {receipt['archived_file_count']} file(s) from {receipt['path']} -> {where}; "
                f"{len(receipt['caches_listed_not_archived'])} cache entr(ies) listed, not archived; "
                f"receipt {receipt['receipt_path']} (valid {RECEIPT_TTL_S // 60} min at HEAD {receipt['head'][:12]})"
            )
    if args.json:
        print(json.dumps(out, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
