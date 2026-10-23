#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""worktree_archive.py — archive what a worktree removal would delete, then
write the receipt the removal guard requires.

Why this exists (2026-10-23): an agent removed 83 "merged, clean" Easy Terminal
worktrees after `git status --porcelain -uall` came back empty. That check omits
GITIGNORED files, so `.build-loop/`, `.bookmark/`, `.designdoc/` and `.ibr/`
state in 55 of them — including a Groundwork design project a live server was
serving — was deleted with no copy. The guard
(`scripts/hooks/worktree_removal_guard.py`) demands this script's receipt
before any removal.

Contract:
  - Everything a removal would delete is archived EXCEPT regenerable cache roots
    recognised by name AND place (`node_modules/` beside a package.json,
    `target/` beside a Cargo.toml, a venv holding pyvenv.cfg, `__pycache__/`,
    ...; see CACHE_ROOTS). A directory merely NAMED like a cache (`build/`,
    `vendor/`, `dist/`) is archived. Credential/database/patch-shaped files
    found inside a cache root are archived too.
  - The index survives: `git diff --cached --binary` and `git diff --binary`
    go into the tar beside the files, so staged and unstaged states restore.
  - An incomplete inventory issues NO receipt: git warnings, unreadable
    directories, or a cache root too large to search all refuse, naming why.
    `--include-caches` archives cache roots whole instead of searching them.
  - The receipt binds to content: a fingerprint over every archived path's
    size + mtime_ns, git's status codes, and the cache roots. The guard
    recomputes it, so editing or adding a file after archiving voids it.
  - Archive names never collide (full-path hash + microsecond UTC, created
    O_EXCL) and the receipt records size + sha256, which the guard checks.

Outputs: archive `<repo-parent>/<repo>.worktrees/_preserved/<name>-<hash>-<UTC>.tar.gz`
(`--dest` overrides; never inside the worktree), receipt
`~/.build-loop/worktree-removal-receipts/<sha1(realpath)>.json`
($BUILD_LOOP_WORKTREE_RECEIPTS_DIR overrides), plus a copy beside the archive.

CLI::

    python3 scripts/worktree_archive.py --path <wt> [--path <wt> ...]
        [--dest <dir>] [--include-caches] [--json]

Exit 0 = every path archived and receipted. Exit 1 = at least one refused or
failed; no receipt exists for it.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import os
import shlex
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import worktree_inventory  # noqa: E402

SCHEMA = "build-loop.worktree-removal-receipt/v2"
RECEIPT_TTL_S = 30 * 60
_GIT_TIMEOUT_S = 30
_MAX_LISTED_FILES = 20_000
# Entries searched inside cache roots for credential-shaped files. Exceeding it
# refuses the receipt (an unsearched directory is never certified).
CACHE_SCAN_BUDGET = 500_000
# Archives up to this size are fully re-hashed by the guard; larger ones are
# checked by size, mtime and a head+tail sample hash to stay inside its budget.
FULL_HASH_MAX_BYTES = 32 * 1024 * 1024
_SAMPLE_BYTES = 1024 * 1024

# Regenerable cache roots: name -> None (always a cache) or the marker that must
# sit BESIDE it ("../x") or INSIDE it ("x"). A name without its marker is
# ordinary data and gets archived.
CACHE_ROOTS: dict[str, tuple[str, ...] | None] = {
    "node_modules": ("../package.json",),
    "target": ("../Cargo.toml",),
    ".build": ("../Package.swift",),
    "Pods": ("../Podfile",),
    ".next": ("../package.json",),
    ".venv": ("pyvenv.cfg",),
    "venv": ("pyvenv.cfg",),
    "__pycache__": None,
    ".pytest_cache": None,
    ".mypy_cache": None,
    ".ruff_cache": None,
    ".tox": None,
    ".gradle": None,
    ".turbo": None,
    ".parcel-cache": None,
    "DerivedData": None,
}
CACHE_FILES = ("*.pyc", "*.pyo", ".DS_Store")


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
    script = (plugin_root / "scripts" / "worktree_archive.py") if plugin_root else Path(__file__).resolve()
    return f"python3 {shlex.quote(str(script))} " + " ".join(f"--path {shlex.quote(p)}" for p in paths)


# ── git helpers ──────────────────────────────────────────────────────────────

def _git(path: Path, *args: str, timeout: float = _GIT_TIMEOUT_S, text: bool = True):
    return subprocess.run(["git", "--no-optional-locks", "-C", str(path), *args],
                          capture_output=True, text=text, check=False, timeout=timeout)


def head_of(path: str | os.PathLike[str], timeout: float = _GIT_TIMEOUT_S) -> tuple[str | None, str | None]:
    try:
        proc = _git(Path(path), "rev-parse", "--verify", "-q", "HEAD", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout: git rev-parse HEAD did not finish"
    except OSError as exc:
        return None, f"git rev-parse failed: {exc}"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout).strip() or "git rev-parse HEAD failed"
    return proc.stdout.strip(), None


def linked_worktree_admin(path: str | os.PathLike[str]) -> Path | None:
    """Admin dir when `path` is the root of a REGISTERED linked worktree.

    Same evidence `git worktree list` reads: `<path>/.git` is a gitlink naming
    `<common>/worktrees/<id>`, whose `gitdir` file points back. No subprocess.
    """
    root = Path(os.path.abspath(os.path.expanduser(str(path))))
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
        admin = root / admin
    admin = Path(canonical(admin))
    try:
        back = Path((admin / "gitdir").read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return None
    if not back.is_absolute():
        back = admin / back
    if canonical(back) != canonical(gitfile) or admin.parent.name != "worktrees":
        return None
    return admin


# ── snapshot: what removal would delete ──────────────────────────────────────

def is_cache_root(directory: Path) -> bool:
    if directory.name not in CACHE_ROOTS or directory.is_symlink():
        return False
    markers = CACHE_ROOTS[directory.name]
    if markers is None:
        return True
    for marker in markers:
        target = directory.parent / marker[3:] if marker.startswith("../") else directory / marker
        if target.exists():
            return True
    return False


def is_cache_file(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in CACHE_FILES)


def _status(wt: Path, timeout: float) -> tuple[list[tuple[str, str]], list[str]]:
    """NUL-delimited porcelain v1, renames disabled. Returns (entries, warnings)."""
    proc = _git(wt, "-c", "status.showUntrackedFiles=normal", "status", "--porcelain=v1", "-z",
                "--no-renames", "--ignored=traditional", "--untracked-files=normal",
                timeout=timeout, text=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace").strip() or "git status failed")
    warnings = [ln.strip() for ln in (proc.stderr or b"").decode("utf-8", "replace").splitlines() if ln.strip()]
    entries = []
    for rec in proc.stdout.split(b"\0"):
        if len(rec) < 4:
            continue
        entries.append((rec[:2].decode("ascii", "replace"), os.fsdecode(rec[3:])))
    return entries, warnings


class BudgetExceeded(Exception):
    pass


def _index_info(wt: Path, timeout: float) -> tuple[str, list[str], list[str]]:
    """(digest of staged entries, assume-unchanged/skip-worktree paths, submodule paths).

    `git status` and `git diff` hide edits to files marked assume-unchanged
    (lowercase tag) or skip-worktree ('S'), so those files are kept outright.
    Hashing the stage listing binds the receipt to the index: an index-only
    edit after archiving changes the digest.
    """
    proc = _git(wt, "ls-files", "-s", "-v", "-z", timeout=timeout, text=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace").strip() or "git ls-files failed")
    hidden, subs = [], []
    for rec in proc.stdout.split(b"\0"):
        if not rec:
            continue
        meta, _, raw = rec.partition(b"\t")
        parts = meta.split(b" ")
        rel = os.fsdecode(raw)
        tag = parts[0].decode("ascii", "replace") if parts else ""
        if tag.islower() or tag == "S":
            hidden.append(rel)
        if len(parts) > 1 and parts[1] == b"160000":
            subs.append(rel)
    return hashlib.sha256(proc.stdout).hexdigest(), hidden, subs


class Snapshot:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}        # rel -> kind (tracked_change|untracked|ignored)
        self.codes: dict[str, str] = {}        # tracked rel -> XY (includes deletions)
        self.caches: list[str] = []            # cache roots/files listed, not archived
        self.cache_valuable: list[str] = []    # credential-shaped files inside cache roots
        self.incomplete: list[str] = []        # reasons the inventory is not whole
        self.index_digest = ""                 # sha256 of `git ls-files -s -v` (staged blobs)


def snapshot(wt: Path, *, scan_caches: bool, include_caches: bool = False,
             deadline: float | None = None, timeout: float = _GIT_TIMEOUT_S, _depth: int = 0) -> Snapshot:
    snap = Snapshot()
    entries, warnings = _status(wt, timeout)
    snap.incomplete += [f"git warned: {w}" for w in warnings]
    snap.index_digest, hidden, submodules = _index_info(wt, timeout)
    subset = set(submodules)
    budget = [CACHE_SCAN_BUDGET]

    def over_time() -> bool:
        return deadline is not None and time.monotonic() > deadline

    def scan_cache(root: Path, rel_root: str) -> None:
        snap.caches.append(rel_root + "/")
        if include_caches:
            walk(root, "ignored", caches_too=True)
            return
        if not scan_caches:
            return
        errors: list[OSError] = []
        for dirpath, dirnames, filenames in os.walk(root, onerror=errors.append):
            if over_time():
                snap.incomplete.append("time budget exhausted while listing files")
                return
            budget[0] -= len(dirnames) + len(filenames)
            if budget[0] < 0:
                snap.incomplete.append(f"{rel_root}/ is too large to search for credential-shaped "
                                       f"files; re-run with --include-caches or delete it first")
                return
            for name in filenames:
                rel = (Path(dirpath) / name).relative_to(wt).as_posix()
                if worktree_inventory.classify(rel) == "potentially_valuable":
                    snap.cache_valuable.append(rel)
        if errors:
            snap.incomplete.append(f"could not read part of {rel_root}/ ({errors[0]})")

    def walk(base: Path, kind: str, caches_too: bool = False) -> None:
        if base.is_symlink() or not base.is_dir():
            snap.files.setdefault(base.relative_to(wt).as_posix(), kind)
            return
        if not caches_too and is_cache_root(base):
            scan_cache(base, base.relative_to(wt).as_posix())
            return
        errors: list[OSError] = []
        for dirpath, dirnames, filenames in os.walk(base, onerror=errors.append):
            if over_time():
                snap.incomplete.append("time budget exhausted while listing files")
                return
            keep = []
            for d in sorted(dirnames):
                full = Path(dirpath) / d
                if full.is_symlink():
                    snap.files.setdefault(full.relative_to(wt).as_posix(), kind)
                elif not caches_too and is_cache_root(full):
                    scan_cache(full, full.relative_to(wt).as_posix())
                else:
                    keep.append(d)
            dirnames[:] = keep
            for name in sorted(filenames):
                rel = (Path(dirpath) / name).relative_to(wt).as_posix()
                if not caches_too and is_cache_file(name):
                    snap.caches.append(rel)
                else:
                    snap.files.setdefault(rel, kind)
            if not filenames and not keep and dirpath != str(base):
                snap.files.setdefault(Path(dirpath).relative_to(wt).as_posix() + "/", kind)
        if errors:
            snap.incomplete.append(f"could not read {errors[0].filename} ({errors[0].strerror})")

    for code, rel in entries:
        if rel.rstrip("/") in subset:
            continue  # submodule: its own contents are snapshotted below
        if code == "??" or code == "!!":
            kind = "untracked" if code == "??" else "ignored"
            path = wt / rel.rstrip("/")
            if not rel.endswith("/") and is_cache_file(path.name) and not include_caches:
                snap.caches.append(rel)
                continue
            walk(path, kind, caches_too=include_caches)
        else:
            snap.codes[rel] = code
            path = wt / rel
            if path.is_symlink() or path.exists():
                snap.files.setdefault(rel, "tracked_change")
    for rel in hidden:
        if (wt / rel).is_symlink() or (wt / rel).is_file():
            snap.files.setdefault(rel, "tracked_hidden")
    # Submodules: superproject status reports a dirty submodule as one path and
    # never lists its ignored files, so each checked-out submodule is
    # snapshotted in full and merged under its prefix.
    for sub in submodules:
        sub_wt = wt / sub
        if not (sub_wt / ".git").exists():
            continue
        if _depth >= 4:
            snap.incomplete.append(f"submodule nesting too deep at {sub}")
            continue
        try:
            inner = snapshot(sub_wt, scan_caches=scan_caches, include_caches=include_caches,
                             deadline=deadline, timeout=timeout, _depth=_depth + 1)
        except RuntimeError as exc:
            snap.incomplete.append(f"could not inventory submodule {sub} ({exc})")
            continue
        pre = sub.rstrip("/") + "/"
        for rel, kind in inner.files.items():
            snap.files.setdefault(pre + rel, kind)
        snap.codes.update({pre + r: c for r, c in inner.codes.items()})
        snap.caches += [pre + c for c in inner.caches]
        snap.cache_valuable += [pre + c for c in inner.cache_valuable]
        snap.incomplete += [f"submodule {sub}: {r}" for r in inner.incomplete]
        snap.index_digest = hashlib.sha256((snap.index_digest + sub + inner.index_digest).encode()).hexdigest()
    snap.caches = sorted(set(snap.caches))
    snap.cache_valuable = sorted(set(snap.cache_valuable))
    return snap


def fingerprint(wt: Path, snap: Snapshot, cache_valuable: list[str]) -> str:
    """Content binding: sizes + mtimes of everything kept, status codes, index."""
    lines = []
    for rel in sorted(set(snap.files) | set(cache_valuable)):
        try:
            st = (wt / rel.rstrip("/")).lstat()
            lines.append(f"f\0{rel}\0{st.st_size}\0{st.st_mtime_ns}")
        except OSError:
            lines.append(f"f\0{rel}\0missing")
    lines += [f"s\0{rel}\0{code}" for rel, code in sorted(snap.codes.items())]
    # Cache roots are deliberately NOT bound: they are regenerable, and a new
    # `__pycache__/` from merely importing a module would void every receipt.
    lines.append(f"i\0{snap.index_digest}")
    return hashlib.sha256("\n".join(lines).encode("utf-8", "surrogateescape")).hexdigest()


# ── archive ──────────────────────────────────────────────────────────────────

def _default_dest(wt: Path) -> Path:
    proc = _git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or "git rev-parse --git-common-dir failed")
    common = Path(proc.stdout.strip())
    if common.name == ".git":
        repo_dir, repo_name = common.parent, common.parent.name
    else:
        repo_dir, repo_name = common, (common.name[:-4] if common.name.endswith(".git") else common.name)
    return repo_dir.parent / f"{repo_name}.worktrees" / "_preserved"


def _branch_of(wt: Path) -> str | None:
    proc = _git(wt, "symbolic-ref", "-q", "--short", "HEAD")
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _hash_file(path: Path, deadline: float | None = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            if deadline is not None and time.monotonic() > deadline:
                raise BudgetExceeded("hashing the archive")
            h.update(chunk)
    return h.hexdigest()


def _sample_hash(path: Path, size: int) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        h.update(fh.read(_SAMPLE_BYTES))
        if size > _SAMPLE_BYTES:
            fh.seek(max(size - _SAMPLE_BYTES, _SAMPLE_BYTES))
            h.update(fh.read(_SAMPLE_BYTES))
    return h.hexdigest()


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    import io
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(time.time())
    tar.addfile(info, io.BytesIO(data))


def archive(path: str | os.PathLike[str], *, dest: str | os.PathLike[str] | None = None,
            include_caches: bool = False) -> dict[str, Any]:
    """Archive one worktree and write its receipt. Raises (no receipt) on any gap."""
    wt = Path(canonical(path))
    if not wt.is_dir():
        raise RuntimeError(f"{wt} is not a directory")
    if linked_worktree_admin(wt) is None:
        raise RuntimeError(f"{wt} is not the root of a registered linked worktree")
    dest_dir = Path(canonical(dest)) if dest else _default_dest(wt)
    if (str(dest_dir) + "/").startswith(str(wt) + "/"):
        raise RuntimeError(f"--dest {dest_dir} is inside the worktree; removing it would delete the archive")
    head, err = head_of(wt)
    if err:
        raise RuntimeError(err)
    snap = snapshot(wt, scan_caches=True, include_caches=include_caches)
    if snap.incomplete:
        raise RuntimeError("inventory incomplete, no receipt issued: " + "; ".join(snap.incomplete))
    fp_before = fingerprint(wt, snap, snap.cache_valuable)
    staged = _git(wt, "diff", "--cached", "--binary", text=False)
    unstaged = _git(wt, "diff", "--binary", text=False)
    if staged.returncode != 0 or unstaged.returncode != 0:
        raise RuntimeError("git diff failed; index state not captured, no receipt issued")

    kept = dict(snap.files)
    for rel in snap.cache_valuable:
        kept.setdefault(rel, "ignored")
    listed, total = [], 0
    for rel in sorted(kept):
        try:
            size = (wt / rel.rstrip("/")).lstat().st_size
        except OSError as exc:
            raise RuntimeError(f"{rel} vanished while archiving ({exc}); no receipt issued") from exc
        total += size
        listed.append({"path": rel, "kind": kept[rel], "bytes": size})

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%S.%fZ")
    key = hashlib.sha1(str(wt).encode("utf-8")).hexdigest()[:10]
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive_path = dest_dir / f"{wt.name}-{key}-{stamp}.tar.gz"
    fd = os.open(archive_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=fh, mode="w:gz") as tar:
            _tar_files(tar, wt, listed)
            _add_bytes(tar, "__worktree_archive__/staged.patch", staged.stdout)
            _add_bytes(tar, "__worktree_archive__/unstaged.patch", unstaged.stdout)
            _add_bytes(tar, "__worktree_archive__/status.json",
                       json.dumps(snap.codes, indent=2, sort_keys=True).encode())
        # Close the archive-time race: files edited while tar ran would bind
        # their new metadata to old archived bytes.
        after = snapshot(wt, scan_caches=False, include_caches=include_caches)
        if after.incomplete or fingerprint(wt, after, snap.cache_valuable) != fp_before:
            raise RuntimeError("files changed while the archive was being written; no receipt issued, re-run")
    except BaseException:
        archive_path.unlink(missing_ok=True)
        raise
    st = archive_path.stat()

    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "path": str(wt),
        "head": head,
        "branch": _branch_of(wt),
        "created_at": now.isoformat(timespec="microseconds"),
        "created_at_epoch": now.timestamp(),
        "fingerprint": fp_before,
        "archive_path": str(archive_path),
        "archive_bytes": st.st_size,
        "archive_mtime_ns": st.st_mtime_ns,
        "archive_sha256": _hash_file(archive_path),
        "archive_sample_sha256": _sample_hash(archive_path, st.st_size),
        "archived_file_count": len(listed),
        "archived_source_bytes": total,
        "files": listed[:_MAX_LISTED_FILES],
        "files_truncated": len(listed) > _MAX_LISTED_FILES,
        "cache_valuable_files": snap.cache_valuable,
        "caches_listed_not_archived": snap.caches,
        "include_caches": include_caches,
        "restore": (f"tar -xzf {shlex.quote(str(archive_path))} -C <dir> restores the working files; "
                    f"recreate the worktree at HEAD {head} and run `git apply --cached "
                    "__worktree_archive__/staged.patch` to rebuild the index"),
    }
    text = json.dumps(receipt, indent=2)
    rpath = receipt_path_for(wt)
    rpath.parent.mkdir(parents=True, exist_ok=True)
    tmp_r = rpath.with_name(f".{rpath.name}.{os.getpid()}.tmp")
    tmp_r.write_text(text, encoding="utf-8")
    os.replace(tmp_r, rpath)
    archive_path.with_name(archive_path.name[: -len(".tar.gz")] + ".receipt.json").write_text(text, encoding="utf-8")
    receipt["receipt_path"] = str(rpath)
    return receipt


def _tar_files(tar: tarfile.TarFile, wt: Path, listed: list[dict[str, Any]]) -> None:
    for item in listed:
        rel = item["path"].rstrip("/")
        tar.add(str(wt / rel), arcname=f"{wt.name}/{rel}", recursive=False)


def _finite(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def receipt_verdict(path: str | os.PathLike[str], *, now: float | None = None,
                    deadline: float | None = None) -> tuple[str, str]:
    """Judge whether a removal of `path` is covered by a receipt.

    Verdicts: ok · missing · stale (expired, HEAD moved, files changed, archive
    altered) · invalid (malformed receipt) · incomplete (the worktree could not
    be listed in full) · git_error · timeout. Only ok permits a removal.
    """
    receipt = load_receipt(path)
    if receipt is None:
        return "missing", "no archive receipt exists for this worktree"
    created = _finite(receipt.get("created_at_epoch"))
    if created is None or not isinstance(receipt.get("fingerprint"), str):
        return "invalid", "the archive receipt is malformed (bad timestamp or no fingerprint)"
    now = time.time() if now is None else now
    age = now - created
    if age > RECEIPT_TTL_S or age < -60:
        return "stale", f"the archive receipt is {int(age // 60)} minutes old (limit {RECEIPT_TTL_S // 60})"

    def remaining() -> float:
        return float("inf") if deadline is None else deadline - time.monotonic()

    ap = receipt.get("archive_path")
    if not isinstance(ap, str) or not ap:
        return "invalid", "the archive receipt names no archive"
    archive_path = Path(ap)
    try:
        st = archive_path.stat()
    except OSError:
        return "stale", f"the receipt's archive {ap} no longer exists"
    if st.st_size != receipt.get("archive_bytes"):
        return "stale", f"the receipt's archive {ap} changed size since it was written"
    if st.st_size <= FULL_HASH_MAX_BYTES:
        try:
            digest = _hash_file(archive_path, deadline)
        except BudgetExceeded:
            return "timeout", "receipt not verified within the guard's time budget"
        if digest != receipt.get("archive_sha256"):
            return "stale", f"the receipt's archive {ap} no longer matches its sha256"
    elif (st.st_mtime_ns != receipt.get("archive_mtime_ns")
          or _sample_hash(archive_path, st.st_size) != receipt.get("archive_sample_sha256")):
        return "stale", f"the receipt's archive {ap} was modified since it was written"
    if remaining() <= 0:
        return "timeout", "receipt not verified within the guard's time budget"

    head, err = head_of(path, timeout=max(0.1, min(5.0, remaining())))
    if err:
        return ("timeout" if err.startswith("timeout") else "git_error"), err
    if head != receipt.get("head"):
        return "stale", f"HEAD moved since the archive ({str(receipt.get('head'))[:12]} -> {str(head)[:12]})"
    wt = Path(canonical(path))
    try:
        snap = snapshot(wt, scan_caches=False, include_caches=bool(receipt.get("include_caches")),
                        deadline=deadline, timeout=max(0.1, min(5.0, remaining())))
    except subprocess.TimeoutExpired:
        return "timeout", "receipt not verified within the guard's time budget"
    except (OSError, RuntimeError) as exc:
        return "git_error", f"could not list the worktree's files ({exc})"
    if any("time budget" in r for r in snap.incomplete):
        return "timeout", "receipt not verified within the guard's time budget"
    if snap.incomplete:
        return "incomplete", "the worktree listing is incomplete now: " + "; ".join(snap.incomplete[:3])
    cache_valuable = [r for r in receipt.get("cache_valuable_files") or [] if isinstance(r, str)]
    changed = fingerprint(wt, snap, cache_valuable) != receipt["fingerprint"]
    if remaining() <= 0:
        return "timeout", "receipt not verified within the guard's time budget"
    if changed:
        return "stale", "files or the index changed since the archive was made (edited, added, removed, or staged)"
    return "ok", ap


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Archive a worktree's untracked + ignored files before removal.")
    p.add_argument("--path", action="append", required=True, help="Worktree root (repeatable)")
    p.add_argument("--dest", help="Archive directory (default <repo>.worktrees/_preserved; not inside the worktree)")
    p.add_argument("--include-caches", action="store_true",
                   help="Archive cache roots whole instead of searching them for credential-shaped files")
    p.add_argument("--json", action="store_true", help="Emit receipts as JSON")
    args = p.parse_args(argv)
    failed, out = 0, []
    for raw in args.path:
        try:
            receipt = archive(raw, dest=args.dest, include_caches=args.include_caches)
        except Exception as exc:  # noqa: BLE001 — report per path, never claim success
            failed += 1
            print(f"error: {canonical(raw)}: {exc}", file=sys.stderr)
            continue
        out.append(receipt)
        if not args.json:
            print(f"archived {receipt['archived_file_count']} file(s) from {receipt['path']} -> "
                  f"{receipt['archive_path']}; {len(receipt['caches_listed_not_archived'])} cache entr(ies) "
                  f"listed, not archived; receipt {receipt['receipt_path']} (valid {RECEIPT_TTL_S // 60} min "
                  f"while HEAD and files stay unchanged)")
    if args.json:
        print(json.dumps(out, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
