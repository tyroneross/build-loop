#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""merged_worktree_sweep.py — archive merged linked worktrees, then free them.

Why this exists (2026-11-01): the SessionStart reaper only scans
`<repo>/.build-loop/worktrees`, so worktrees created anywhere else
(`~/dev/worktrees/*`, `~/.codex/worktrees/*`, sibling folders) were never
checked for being merged. Six merged worktrees held 9.65 GiB of cargo
`target/`. Owner rule (2026-11-01): merged worktrees are freed automatically,
or at least archived automatically and listed for deletion, with a patch saved.

For every linked worktree in `git worktree list --porcelain` (any location;
never the primary checkout):

  1. Skip and report locked, prunable and bare entries.
  2. MERGED when `git merge-base --is-ancestor <HEAD> <base>` holds for the
     default branch (local and remote-tracking: origin/HEAD's target, else
     main, else master; `--base` overrides). Anything else is UNMERGED and is
     only reported. A worktree whose HEAD reflog shows no work since creation
     (`fresh`) or whose git metadata changed within `--min-idle-minutes`
     (`recently_active`) is also left alone: its HEAD is an ancestor of the
     base only because nobody has committed there yet.
  3. A merged worktree with a process whose cwd is inside it (`lsof -a -d cwd
     -Fn`) is `busy` and skipped. No lsof, an lsof failure or an exhausted
     budget mark it `unchecked`, which is also skipped (fail closed).
  4. For each remaining merged worktree (not in --dry-run):
       a. tracked changes (`git diff HEAD --binary`, staged included) are saved
          to `<primary>/.build-loop/patches/<UTC>-<name>.patch` and verified
          with `git apply --check --reverse` in the worktree; a failure stops
          that worktree;
       b. `.build-loop/{backlog/items,pending-lessons,proposals,retrospectives}`
          files are copied into the primary checkout, never overwriting;
       c. `scripts/worktree_archive.py --path <wt>` archives what removal would
          delete and writes the receipt the removal guard requires; a refusal
          stops that worktree;
       d. --act only: the receipt is re-verified, then `git worktree remove
          --force`, `git worktree prune`, and `git branch -d` (plain -d, which
          refuses an unmerged branch; never -D).

Modes: --archive-only (default; steps a-c, nothing removed, archived
worktrees listed as ready to remove), --act (a-d), --dry-run (touches
nothing). Free space is read from os.statvfs before and after; `du` overstates
APFS clones.

CLI::

    python3 scripts/merged_worktree_sweep.py --repo <path>
        [--dry-run | --archive-only | --act] [--base <ref>] [--json]
        [--max-worktrees N] [--budget-s S] [--min-idle-minutes M] [--no-sizes]

Exit 0 = no step failed. Exit 1 = at least one worktree hit an error (patch
verification, archive refusal, removal failure). Exit 2 = --repo is not a git
repository.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

SCHEMA = "build-loop.merged-worktree-sweep/v1"
RECORD_DIRS = ("backlog/items", "pending-lessons", "proposals", "retrospectives")
PATCH_DIR_REL = Path(".build-loop") / "patches"
ARCHIVER = HERE / "worktree_archive.py"
DEFAULT_MAX_WORKTREES = 100
DEFAULT_MIN_IDLE_MINUTES = 60.0
# A never-committed worktree untouched this long is abandoned, not "fresh".
FRESH_ABANDONED_DAYS = 7.0
_GIT_TIMEOUT_S = 30
_ARCHIVE_TIMEOUT_S = 1800


# ── subprocess helpers ───────────────────────────────────────────────────────

def clean_env() -> dict[str, str]:
    """Inherited GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE (set by git hooks) would
    override `git -C` and aim every command at the wrong repository."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(cwd: Path, *args: str, timeout: float = _GIT_TIMEOUT_S, text: bool = True):
    return subprocess.run(["git", "--no-optional-locks", "-C", str(cwd), *args],
                          capture_output=True, text=text, check=False, timeout=timeout,
                          env=clean_env())


def _real(path: str | os.PathLike[str]) -> str:
    return os.path.realpath(os.path.expanduser(str(path)))


def _inside(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def free_bytes(path: Path) -> int | None:
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    return st.f_bavail * st.f_frsize


# ── discovery ────────────────────────────────────────────────────────────────

def parse_porcelain(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line.strip():
            cur = None
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            cur = {"path": value}
            entries.append(cur)
        elif cur is None:
            continue
        elif key == "HEAD":
            cur["head"] = value
        elif key == "branch":
            cur["branch"] = value[len("refs/heads/"):] if value.startswith("refs/heads/") else value
        elif key in ("bare", "detached"):
            cur[key] = True
        elif key in ("locked", "prunable"):
            cur[key] = value or True
    return entries


def resolve_base_refs(primary: Path, override: str | None) -> list[str]:
    """Refs a merged worktree's HEAD must be an ancestor of (any one suffices).

    Local and remote-tracking default branch both count: a branch merged into
    local main but not yet pushed, or merged on the remote but not yet pulled,
    keeps every commit reachable from a ref either way.
    """
    def exists(ref: str) -> bool:
        return _git(primary, "rev-parse", "--verify", "-q", ref + "^{commit}").returncode == 0

    if override:
        return [override] if exists(override) else []
    names: list[str] = []
    proc = _git(primary, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
    if proc.returncode == 0 and proc.stdout.strip().startswith("origin/"):
        names.append(proc.stdout.strip()[len("origin/"):])
    names += ["main", "master"]
    for name in names:
        refs = [r for r in (name, f"origin/{name}") if exists(r)]
        if refs:
            return refs
    return []


def merged_tips(primary: Path, bases: list[str]) -> dict[str, str]:
    """Branch tips reachable from any base ref -> that ref. Two git calls in
    total instead of one per worktree (SessionStart runs this under a budget)."""
    tips: dict[str, str] = {}
    for ref in bases:
        proc = _git(primary, "for-each-ref", f"--merged={ref}", "--format=%(objectname)", "refs/heads")
        for sha in proc.stdout.split() if proc.returncode == 0 else []:
            tips.setdefault(sha, ref)
    return tips


def is_merged(primary: Path, head: str, bases: list[str], tips: dict[str, str]) -> str | None:
    if head in tips:
        return tips[head]
    for ref in bases:  # detached HEAD, or a HEAD no branch points at
        if _git(primary, "merge-base", "--is-ancestor", head, ref).returncode == 0:
            return ref
    return None


def _admin_dir(wt: Path) -> Path | None:
    import worktree_archive  # noqa: PLC0415 — reads the gitlink, no subprocess

    return worktree_archive.linked_worktree_admin(wt)


MAX_DIRTY_FILES = 2000


def _dirty_files(wt: Path) -> list[Path]:
    """Changed and untracked (not ignored) files: an editor whose cwd is elsewhere
    still leaves its edits here, and they bump neither HEAD nor the root dir."""
    proc = _git(wt, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if proc.returncode != 0:
        return []
    out = []
    for item in proc.stdout.split("\0")[:MAX_DIRTY_FILES]:
        if len(item) > 3:
            out.append(wt / item[3:])
    return out


def last_activity(wt: Path, admin: Path | None) -> float:
    candidates = [wt, *_dirty_files(wt)]
    if admin is not None:
        candidates += [admin / "HEAD", admin / "index", admin / "logs" / "HEAD"]
    newest = 0.0
    for p in candidates:
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    return newest


def worked_since_creation(admin: Path | None) -> bool:
    """True when the worktree's own HEAD reflog records anything beyond its
    creation (`git worktree add` writes an empty-message entry plus `reset:
    moving to HEAD`). An unreadable reflog counts as no work (fail closed)."""
    if admin is None:
        return False
    try:
        lines = (admin / "logs" / "HEAD").read_bytes().splitlines()
    except OSError:
        return False
    for ln in lines:
        msg = ln.partition(b"\t")[2].strip()
        if msg and msg != b"reset: moving to HEAD":
            return True
    return False


_PROC_PIDVNODEPATHINFO = 9
_VNODEPATHINFO_SIZE = 2352  # struct proc_vnodepathinfo: two vnode_info_path (152 + 1024 each)
_CDIR_PATH_OFFSET = 152


def libproc_cwds() -> list[str] | None:
    """Real cwd of every process, read from macOS libproc. None = use lsof instead.

    A system-wide `lsof -d cwd` took 167 s on a machine with 3,800 processes
    (2026-11-09), past every budget this sweep has; libproc answers in under a
    second. A process that exited mid-scan (ESRCH) or belongs to another user
    (EPERM, which lsof cannot read either) is skipped. Any other failure
    returns None, because a process that was not inspected could be the one
    using a worktree.
    """
    if sys.platform != "darwin":
        return None
    try:
        import ctypes  # noqa: PLC0415 - only the macOS path needs it
        import errno  # noqa: PLC0415

        lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        count = lib.proc_listallpids(None, 0)
        if count <= 0:
            return None
        pids = (ctypes.c_int * (count + 256))()
        count = lib.proc_listallpids(pids, ctypes.sizeof(pids))
        if count <= 0:
            return None
        buf = ctypes.create_string_buffer(_VNODEPATHINFO_SIZE)
        cwds: list[str] = []
        for pid in pids[:count]:
            if pid <= 0:
                continue
            ctypes.set_errno(0)
            got = lib.proc_pidinfo(pid, _PROC_PIDVNODEPATHINFO, ctypes.c_uint64(0), buf, _VNODEPATHINFO_SIZE)
            if got != _VNODEPATHINFO_SIZE:
                if ctypes.get_errno() in (errno.ESRCH, errno.EPERM):
                    continue
                return None
            raw = buf.raw[_CDIR_PATH_OFFSET:_CDIR_PATH_OFFSET + 1024].split(b"\0", 1)[0]
            if raw.startswith(b"/"):
                cwds.append(_real(os.fsdecode(raw)))
        return cwds or None
    except (OSError, AttributeError, ValueError):
        return None


def lsof_cwds(timeout: float) -> tuple[list[str] | None, str | None]:
    """Real cwd of every process, or (None, reason) when it cannot be known."""
    override = os.environ.get("BUILD_LOOP_SWEEP_LSOF")
    if not override:
        cwds = libproc_cwds()
        if cwds is not None:
            return cwds, None
    exe = override or shutil.which("lsof")
    if not exe or not os.path.exists(exe):
        return None, "lsof unavailable"
    if timeout <= 0:
        return None, "time budget exhausted before lsof"
    try:
        proc = subprocess.run([exe, "-w", "-a", "-d", "cwd", "-Fn"], capture_output=True, text=True,
                              check=False, timeout=timeout, env=clean_env())
    except subprocess.TimeoutExpired:
        return None, "lsof did not finish within the time budget"
    except OSError as exc:
        return None, f"lsof failed: {exc}"
    # Fail closed: a nonzero exit means some process was not inspected, and that
    # process could be the one using a worktree. Partial output is not a clearance.
    if proc.returncode != 0:
        return None, f"lsof exited {proc.returncode}; process check incomplete"
    cwds = [_real(ln[1:]) for ln in proc.stdout.splitlines() if ln.startswith("n/")]
    if not cwds:
        return None, "lsof returned no process cwd records"
    return cwds, None


def allocated_bytes(root: Path, deadline: float | None) -> tuple[int, bool]:
    """(bytes allocated under root, complete). APFS clones may share blocks."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if deadline is not None and time.monotonic() > deadline:
            return total, False
        for name in dirnames + filenames:
            try:
                total += getattr(os.lstat(os.path.join(dirpath, name)), "st_blocks", 0) * 512
            except OSError:
                continue
    return total, True


# ── per-worktree steps ───────────────────────────────────────────────────────

def save_patch(wt: Path, primary: Path, now: datetime) -> tuple[str | None, str | None]:
    """(patch path or None when there are no tracked changes, error)."""
    diff = _git(wt, "diff", "HEAD", "--binary", text=False)
    if diff.returncode != 0:
        return None, "git diff HEAD failed: " + (diff.stderr or b"").decode("utf-8", "replace").strip()
    if not diff.stdout.strip():
        return None, None
    pdir = primary / PATCH_DIR_REL
    pdir.mkdir(parents=True, exist_ok=True)
    stem = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{wt.name}"
    for n in range(1000):
        path = pdir / (f"{stem}.patch" if n == 0 else f"{stem}-{n}.patch")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as fh:
            fh.write(diff.stdout)
        break
    else:
        return None, f"could not pick an unused patch name under {pdir}"
    check = _git(wt, "apply", "--check", "--reverse", str(path))
    if check.returncode != 0:
        return str(path), "patch verification failed (git apply --check --reverse): " + (
            check.stderr or check.stdout).strip()
    return str(path), None


def copy_records(wt: Path, primary: Path) -> tuple[list[str], list[str]]:
    """Copy durable .build-loop records into the primary checkout; never overwrite."""
    copied, kept_existing = [], []
    dst_root = Path(_real(primary / ".build-loop"))
    for sub in RECORD_DIRS:
        src_dir = wt / ".build-loop" / sub
        if not src_dir.is_dir() or _inside(_real(src_dir), str(dst_root)):
            continue  # absent, or a symlink into the primary's own records
        for dirpath, _dirnames, filenames in os.walk(src_dir):
            for name in filenames:
                src = Path(dirpath) / name
                if src.is_symlink():
                    continue
                rel = src.relative_to(wt / ".build-loop")
                dst = dst_root / rel
                if dst.exists() or dst.is_symlink():
                    kept_existing.append(str(rel))
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                except FileExistsError:
                    kept_existing.append(str(rel))
                    continue
                with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
                    shutil.copyfileobj(inp, out)
                shutil.copystat(src, dst)
                copied.append(str(rel))
    return copied, kept_existing


def run_archiver(wt: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        proc = subprocess.run([sys.executable, str(ARCHIVER), "--path", str(wt), "--json"],
                              capture_output=True, text=True, check=False,
                              timeout=_ARCHIVE_TIMEOUT_S, env=clean_env())
    except subprocess.TimeoutExpired:
        return None, "worktree_archive.py timed out"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout).strip() or f"worktree_archive.py exited {proc.returncode}"
    try:
        receipts = json.loads(proc.stdout)
        return receipts[0], None
    except (ValueError, IndexError, TypeError):
        return None, "worktree_archive.py printed no receipt"


def recheck(wt: Path, primary: Path, expected_head: str, bases: list[str], idle_s: float) -> str | None:
    """Re-verify eligibility immediately before removal; return why not, or None.
    Classification ran earlier, and a commit, an edit or a new process may have
    arrived since then."""
    admin = _admin_dir(wt)
    if admin is not None and (admin / "locked").exists():
        return "worktree was locked since classification"
    head = _git(wt, "rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != expected_head:
        return "HEAD moved since classification"
    if not any(_git(primary, "merge-base", "--is-ancestor", expected_head, ref).returncode == 0 for ref in bases):
        return "HEAD is no longer merged"
    if time.time() - last_activity(wt, _admin_dir(wt)) < idle_s:
        return "worktree became active since classification"
    cwds, why = lsof_cwds(30.0)
    if cwds is None:
        return why
    if any(_inside(c, str(wt)) for c in cwds):
        return "a process now has its cwd inside it"
    return None


def remove(wt: Path, primary: Path, branch: str | None, *, expected_head: str | None = None,
           bases: list[str] | None = None, idle_s: float = 0.0) -> dict[str, Any]:
    import worktree_archive  # noqa: PLC0415 — only --act needs the guard's verdict

    out: dict[str, Any] = {"removed": False, "branch_deleted": False}
    if expected_head is not None:
        why = recheck(wt, primary, expected_head, bases or [], idle_s)
        if why:
            out["error"] = "not removed: " + why
            return out
    verdict, detail = worktree_archive.receipt_verdict(wt)
    if verdict != "ok":
        out["error"] = f"archive receipt {verdict}: {detail}"
        return out
    proc = _git(primary, "worktree", "remove", "--force", str(wt), timeout=600)
    if proc.returncode != 0:
        out["error"] = "git worktree remove failed: " + (proc.stderr or proc.stdout).strip()
        return out
    out["removed"] = True
    # No repo-wide `git worktree prune`: `worktree remove` already deregisters this
    # worktree, and a global prune would delete admin dirs (HEAD, reflog) of other
    # worktrees skipped as prunable, unprotecting their unmerged commits.
    if branch:
        bd = _git(primary, "branch", "-d", branch)
        out["branch_deleted"] = bd.returncode == 0
        if bd.returncode != 0:
            out["branch_note"] = (bd.stderr or bd.stdout).strip()
    return out


# ── sweep ────────────────────────────────────────────────────────────────────

def sweep(repo: str | os.PathLike[str], *, mode: str = "archive-only", base: str | None = None,
          max_worktrees: int = DEFAULT_MAX_WORKTREES, budget_s: float = 0.0,
          min_idle_minutes: float = DEFAULT_MIN_IDLE_MINUTES, sizes: bool = True) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + budget_s if budget_s > 0 else None

    def remaining() -> float:
        return float("inf") if deadline is None else deadline - time.monotonic()

    repo_path = Path(_real(repo))
    listing = _git(repo_path, "worktree", "list", "--porcelain")
    if listing.returncode != 0:
        raise RuntimeError((listing.stderr or listing.stdout).strip() or f"{repo_path} is not a git repository")
    entries = parse_porcelain(listing.stdout)
    primary = Path(_real(entries[0]["path"])) if entries else repo_path
    bases = resolve_base_refs(primary, base)
    report: dict[str, Any] = {
        "schema": SCHEMA, "mode": mode, "repo": str(repo_path), "primary": str(primary),
        "base_refs": bases, "linked_total": max(0, len(entries) - 1), "inspected": 0,
        "skipped_locked": [], "skipped_prunable": [], "skipped_bare": [], "unmerged": [],
        "fresh": [], "recently_active": [], "busy": [], "unchecked": [], "ready_to_remove": [],
        "removed": [], "errors": [], "free_bytes_before": free_bytes(primary),
    }
    if not bases:
        report["errors"].append({"path": str(primary), "error": "no default branch found (origin/HEAD, main, master)"
                                 if not base else f"--base {base} does not resolve to a commit"})
        report["free_bytes_after"] = report["free_bytes_before"]
        report["freed_bytes"] = 0
        return report

    now = datetime.now(timezone.utc)
    idle_s = min_idle_minutes * 60
    tips = merged_tips(primary, bases)
    merged: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries[1:]):
        rec = {"path": entry["path"], "branch": entry.get("branch"), "head": entry.get("head")}
        if idx >= max_worktrees or remaining() <= 0:
            rec["reason"] = "inspection cap reached" if idx >= max_worktrees else "time budget exhausted"
            report["unchecked"].append(rec)
            continue
        report["inspected"] += 1
        if entry.get("bare"):
            report["skipped_bare"].append(rec)
            continue
        if entry.get("prunable") or not os.path.isdir(entry["path"]):
            rec["reason"] = entry.get("prunable") if isinstance(entry.get("prunable"), str) else "directory missing"
            report["skipped_prunable"].append(rec)
            continue
        if entry.get("locked"):
            rec["reason"] = entry["locked"] if isinstance(entry["locked"], str) else "locked"
            report["skipped_locked"].append(rec)
            continue
        wt = Path(_real(entry["path"]))
        if wt == primary or not rec["head"]:
            continue
        merged_into = is_merged(primary, rec["head"], bases, tips)
        if merged_into is None:
            report["unmerged"].append(rec)
            continue
        rec["merged_into"] = merged_into
        admin = _admin_dir(wt)
        idle_for = time.time() - last_activity(wt, admin)
        rec["idle_minutes"] = round(idle_for / 60, 1)
        if not worked_since_creation(admin) and idle_for < FRESH_ABANDONED_DAYS * 86400:
            rec["reason"] = "no commits since the worktree was created"
            report["fresh"].append(rec)
            continue
        if idle_for < idle_s:
            report["recently_active"].append(rec)
            continue
        rec["path"] = str(wt)
        merged.append(rec)

    if merged:
        cwds, why = lsof_cwds(min(30.0, remaining()))
        for rec in merged:
            if cwds is None:
                rec["reason"] = why
                report["unchecked"].append(rec)
                continue
            users = [c for c in cwds if _inside(c, rec["path"])]
            if users:
                rec["reason"] = f"{len(users)} process(es) have their cwd inside it"
                report["busy"].append(rec)
                continue
            if sizes:
                rec["allocated_bytes"], rec["size_complete"] = allocated_bytes(Path(rec["path"]), deadline)
            if mode == "dry-run":
                rec["action"] = "would archive, then be ready to remove"
                report["ready_to_remove"].append(rec)
                continue
            wt = Path(rec["path"])
            patch, err = save_patch(wt, primary, now)
            rec["patch"] = patch
            if err:
                rec["error"] = err
                report["errors"].append(rec)
                continue
            rec["records_copied"], rec["records_kept_existing"] = copy_records(wt, primary)
            receipt, err = run_archiver(wt)
            if err or receipt is None:
                rec["error"] = "archiver refused: " + (err or "no receipt")
                report["errors"].append(rec)
                continue
            rec["archive_path"] = receipt.get("archive_path")
            rec["receipt_path"] = receipt.get("receipt_path")
            if mode != "act":
                report["ready_to_remove"].append(rec)
                continue
            rec.update(remove(wt, primary, rec.get("branch"), expected_head=rec["head"],
                              bases=bases, idle_s=idle_s))
            (report["removed"] if rec["removed"] else report["errors"]).append(rec)

    report["free_bytes_after"] = free_bytes(primary)
    before, after = report["free_bytes_before"], report["free_bytes_after"]
    report["freed_bytes"] = (after - before) if before is not None and after is not None else None
    report["elapsed_s"] = round(time.monotonic() - started, 3)
    return report


# ── output ───────────────────────────────────────────────────────────────────

def _gib(n: int | None) -> str:
    return "?" if n is None else f"{n / 2**30:.2f} GiB"


def render_text(r: dict[str, Any]) -> str:
    lines = [f"Merged worktree sweep ({r['mode']}) — {r['primary']}",
             f"base: {', '.join(r['base_refs']) or 'none'}; linked worktrees: {r['linked_total']}, "
             f"inspected: {r['inspected']}"]

    def section(title: str, items: list[dict[str, Any]], fmt) -> None:
        if items:
            lines.append(f"\n{title} ({len(items)}):")
            lines.extend("  " + fmt(i) for i in items)

    def size(i: dict[str, Any]) -> str:
        if "allocated_bytes" not in i:
            return ""
        return f" [{_gib(i['allocated_bytes'])}{'' if i.get('size_complete') else '+, partial'}]"

    ready_title = "Ready to remove (dry-run, nothing archived yet)" if r["mode"] == "dry-run" else (
        "Ready to remove (archived; run with --act to remove)")
    section(ready_title, r["ready_to_remove"],
            lambda i: f"{i['path']} ({i.get('branch') or 'detached'}){size(i)}"
                      + (f" archive={i['archive_path']}" if i.get("archive_path") else "")
                      + (f" patch={i['patch']}" if i.get("patch") else ""))
    section("Removed", r["removed"],
            lambda i: f"{i['path']} ({i.get('branch') or 'detached'}){size(i)} archive={i.get('archive_path')}"
                      + (f" patch={i['patch']}" if i.get("patch") else "")
                      + ("" if i.get("branch_deleted") or not i.get("branch")
                         else f" branch kept: {i.get('branch_note', '')}"))
    section("Errors", r["errors"], lambda i: f"{i['path']}: {i.get('error')}")
    section("Busy (process cwd inside)", r["busy"], lambda i: f"{i['path']}: {i['reason']}")
    section("Merged, process check not run (left alone)", [i for i in r["unchecked"] if i.get("merged_into")],
            lambda i: f"{i['path']} ({i.get('branch') or 'detached'}): {i.get('reason')}")
    section("Not inspected", [i for i in r["unchecked"] if not i.get("merged_into")],
            lambda i: f"{i['path']}: {i.get('reason')}")
    section("Recently active (merged, left alone)", r["recently_active"],
            lambda i: f"{i['path']} idle {i['idle_minutes']} min")
    section("Fresh (no commits yet, left alone)", r["fresh"], lambda i: i["path"])
    section("Unmerged (report only)", r["unmerged"], lambda i: f"{i['path']} ({i.get('branch') or 'detached'})")
    section("Locked (skipped)", r["skipped_locked"], lambda i: f"{i['path']}: {i.get('reason')}")
    section("Prunable (skipped)", r["skipped_prunable"], lambda i: f"{i['path']}: {i.get('reason')}")
    section("Bare (skipped)", r["skipped_bare"], lambda i: i["path"])
    lines.append(f"\nFree space: {_gib(r['free_bytes_before'])} -> {_gib(r['free_bytes_after'])} "
                 f"(delta {_gib(r['freed_bytes'])}, statvfs)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Archive merged linked worktrees and free them.")
    p.add_argument("--repo", required=True, help="Any checkout of the repository")
    m = p.add_mutually_exclusive_group()
    m.add_argument("--dry-run", dest="mode", action="store_const", const="dry-run", help="Touch nothing")
    m.add_argument("--archive-only", dest="mode", action="store_const", const="archive-only",
                   help="Default: patch + copy records + archive; no removal")
    m.add_argument("--act", dest="mode", action="store_const", const="act", help="Also remove + delete branch")
    p.add_argument("--base", help="Base ref instead of the default branch")
    p.add_argument("--max-worktrees", type=int, default=DEFAULT_MAX_WORKTREES)
    p.add_argument("--budget-s", type=float, default=0.0, help="Overall time budget (0 = none)")
    p.add_argument("--min-idle-minutes", type=float, default=DEFAULT_MIN_IDLE_MINUTES)
    p.add_argument("--no-sizes", action="store_true", help="Skip per-worktree size walk")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    try:
        report = sweep(args.repo, mode=args.mode or "archive-only", base=args.base,
                       max_worktrees=args.max_worktrees, budget_s=args.budget_s,
                       min_idle_minutes=args.min_idle_minutes, sizes=not args.no_sizes)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
