#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
marketplace_autoupdate.py — CANONICAL, version-controlled copy.

This is the source of truth for the marketplace-autoupdate workaround. The
host install at ``~/.claude/scripts/hooks/marketplace-autoupdate.py`` SHOULD be
a thin pure-exec shim that runs THIS file (per the hooks-hygiene lesson: loose
wrappers desync silently, so the shim must be a bare exec, not a copy). Once the
shim is installed, edit here and the shim picks up changes automatically.

Install (or re-install) the host shim — copy this canonical's repo-relative
path into a runpy exec wrapper. From the build-loop repo root:

    python3 scripts/install_marketplace_shim.py

(or `--print` to dry-run the shim body). The installer is idempotent and
fail-open: it refuses to clobber a host file that ISN'T already a shim unless
``--force`` is given, so a hand-edited host copy is never silently lost.

NOTE: until the shim is installed, the host file may still be a FULL COPY of an
older revision of this script — verify with
``head -12 ~/.claude/scripts/hooks/marketplace-autoupdate.py`` (a shim is ~15
lines and contains ``runpy.run_path``; a stale copy is ~800 lines).

Colocated test: test_marketplace_autoupdate.py.

Compensates for Claude Code's broken `autoUpdate: true` flag on extra
known marketplaces. Reads the registry at
~/.claude/plugins/installed_plugins.json, detects drift between each
installed entry and the marketplace catalog's declared version, fetches
the new version into a fresh `cache/<marketplace>/<plugin>/<version>/`
directory, then atomically updates the registry to point at it.

Diagnosis date: 2026-05-03. Underlying gap: settings.json declares
`autoUpdate: true` for a marketplace. The marketplace's local git
checkout under ~/.claude/plugins/marketplaces/<name>/ does refresh, but
no runtime path actually re-installs the per-plugin files into the
registry's `installPath`. This script fills that gap.

Safety properties:
- Atomic registry write (tmp file -> roundtrip-parse -> os.rename)
- Per-entry transaction (failed clone leaves registry pointing at old dir)
- Old versioned cache dirs retained (--gc not implemented; rollback safety)
- Lock file prevents concurrent runs
- plugin.json version verification before registry swap
- Symlink-installed entries skipped (local-dev workflow)
- Settings.json never written by this script
- Always exits 0 (never blocks session start)

Entry points:
  python3 marketplace_autoupdate.py            # session-start mode
  python3 marketplace_autoupdate.py --dry-run  # report only, no fetches

Kill switch: touch ~/.claude/settings.json.disable-marketplace-autoupdate
Lock file:   ~/.claude/plugins/.marketplace-autoupdate.lock
Log:         ~/.claude/logs/marketplace-autoupdate.log
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

CLAUDE_HOME = Path.home() / ".claude"
SETTINGS_PATH = CLAUDE_HOME / "settings.json"
KILL_SWITCH = CLAUDE_HOME / "settings.json.disable-marketplace-autoupdate"
PLUGINS_DIR = CLAUDE_HOME / "plugins"
MARKETPLACES_DIR = PLUGINS_DIR / "marketplaces"
REGISTRY_PATH = PLUGINS_DIR / "installed_plugins.json"
LOCK_FILE = PLUGINS_DIR / ".marketplace-autoupdate.lock"
# Skip-list of <plugin_key>:<catalog_version> entries that failed verification
# in a prior run (e.g. upstream repo lacks .claude-plugin/plugin.json). Bumping
# the catalog version invalidates the entry automatically.
REJECT_CACHE = PLUGINS_DIR / ".marketplace-autoupdate.reject-cache.json"
LOG_DIR = CLAUDE_HOME / "logs"
LOG_FILE = LOG_DIR / "marketplace-autoupdate.log"

# Anchored excludes for rsync. Leading "/" matches only the top-level
# path so we do NOT prune e.g. dist/src/ when excluding /src.
RSYNC_EXCLUDES = [
    "/.git",
    ".DS_Store",
    "/node_modules",
]

# Total wall-clock budget for one hook invocation. Plugins beyond this
# defer to the next session.
BUDGET_SECONDS = 30


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}\n"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Lock file
# ---------------------------------------------------------------------------

def acquire_lock() -> bool:
    """Best-effort exclusive lock. Returns True on acquire, False if
    another run is in progress (stale or live)."""
    try:
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        # O_EXCL fails atomically if file exists
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{os.getpid()}\n{datetime.now(timezone.utc).isoformat()}\n")
        return True
    except FileExistsError:
        return False
    except OSError as e:
        _log(f"WARN: lock acquire failed (treating as unlocked): {e}")
        return True


def release_lock() -> None:
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except OSError as e:
        _log(f"WARN: lock release failed: {e}")


# ---------------------------------------------------------------------------
# Reject cache (skip plugins that failed verification last run)
# ---------------------------------------------------------------------------

def load_reject_cache() -> dict:
    try:
        if REJECT_CACHE.exists():
            return json.loads(REJECT_CACHE.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_reject_cache(cache: dict) -> None:
    try:
        REJECT_CACHE.write_text(
            json.dumps(cache, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        _log(f"WARN: reject-cache save failed: {e}")


# ---------------------------------------------------------------------------
# Registry read
# ---------------------------------------------------------------------------

def load_registry() -> Optional[dict]:
    if not REGISTRY_PATH.exists():
        _log(f"ERROR: registry not found at {REGISTRY_PATH}")
        return None
    try:
        with REGISTRY_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        _log(f"ERROR: cannot read registry: {e}")
        return None


def write_registry_atomic(reg: dict) -> bool:
    """Atomic write with roundtrip-parse verify. Returns True on swap,
    False if anything fails (original file untouched)."""
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_name = f"installed_plugins.json.tmp-{os.getpid()}-{int(time.time())}"
    tmp_path = PLUGINS_DIR / tmp_name
    try:
        # 1. Write
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(reg, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        # 2. Roundtrip parse — verify the file we just wrote is valid JSON
        with tmp_path.open("r", encoding="utf-8") as fh:
            json.load(fh)
        # 3. Backup the current registry one time per run
        backup = REGISTRY_PATH.with_suffix(
            REGISTRY_PATH.suffix + f".bak-{int(time.time())}"
        )
        if REGISTRY_PATH.exists() and not backup.exists():
            shutil.copy2(REGISTRY_PATH, backup)
        # 4. Atomic rename (POSIX atomic on same filesystem)
        os.rename(tmp_path, REGISTRY_PATH)
        return True
    except (OSError, json.JSONDecodeError, TypeError) as e:
        _log(f"ERROR: registry write aborted: {e}")
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        return False


# ---------------------------------------------------------------------------
# Catalog reading
# ---------------------------------------------------------------------------

def _catalog_source_kind(catalog_entry: dict) -> Optional[str]:
    """Return the source kind ('github' | 'directory' | None) for a catalog entry.
    The official Anthropic marketplace uses `source: "./relative-path"` (a
    string) which we do not support here — those plugins are bundled with
    the marketplace checkout and updated as the marketplace updates."""
    source = catalog_entry.get("source")
    if isinstance(source, dict):
        kind = source.get("source")
        if kind in ("github", "directory"):
            return kind
        return None
    # String-form (relative path inside the marketplace) — out of scope.
    return None


def read_catalog_entry(marketplace_name: str, plugin_name: str) -> Optional[dict]:
    catalog = MARKETPLACES_DIR / marketplace_name / ".claude-plugin" / "marketplace.json"
    if not catalog.exists():
        _log(f"WARN: catalog not found for marketplace={marketplace_name}")
        return None
    try:
        with catalog.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        _log(f"ERROR: cannot parse catalog {catalog}: {e}")
        return None
    for entry in data.get("plugins", []) or []:
        if entry.get("name") == plugin_name:
            return entry
    return None


def read_install_path_version(install_path: str) -> tuple[Optional[str], str]:
    """Return (version, status) by reading installPath/.claude-plugin/plugin.json.
    status: ok | missing-dir | missing-manifest | invalid-json | no-version-field"""
    p = Path(install_path)
    if not p.exists():
        return None, "missing-dir"
    manifest = p / ".claude-plugin" / "plugin.json"
    if not manifest.exists():
        return None, "missing-manifest"
    try:
        with manifest.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None, "invalid-json"
    version = data.get("version")
    if not version:
        return None, "no-version-field"
    return str(version), "ok"


# ---------------------------------------------------------------------------
# Version comparison (PEP-440-ish, stdlib only)
# ---------------------------------------------------------------------------

_VERSION_PART = re.compile(r"^(\d+)([a-zA-Z].*)?$")


def _parse_version(v: str) -> tuple:
    if not v:
        return ()
    s = v.lstrip("vV")
    parts = re.split(r"[.\-+]", s)
    out: list = []
    for p in parts:
        if not p:
            continue
        m = _VERSION_PART.match(p)
        if m and m.group(1) and not m.group(2):
            out.append((0, int(m.group(1))))
        elif m and m.group(1) and m.group(2):
            out.append((0, int(m.group(1))))
            out.append((-1, m.group(2)))
        else:
            out.append((-1, p))
    return tuple(out)


def version_lt(a: str, b: str) -> bool:
    try:
        return _parse_version(a) < _parse_version(b)
    except Exception:
        return str(a) < str(b)


def version_eq(a: str, b: str) -> bool:
    try:
        return _parse_version(a) == _parse_version(b)
    except Exception:
        return str(a) == str(b)


# ---------------------------------------------------------------------------
# Symlink detection (looks at installPath, not legacy plugin dir)
# ---------------------------------------------------------------------------

def is_install_path_symlink(install_path: str) -> bool:
    return Path(install_path).is_symlink()


# ---------------------------------------------------------------------------
# Fetching (always into a fresh versioned cache dir)
# ---------------------------------------------------------------------------

def _rsync_cmd(src: str, dest: str) -> list[str]:
    cmd = ["rsync", "-a", "--delete"]
    for pat in RSYNC_EXCLUDES:
        cmd.extend(["--exclude", pat])
    cmd.append(src.rstrip("/") + "/")
    cmd.append(dest.rstrip("/") + "/")
    return cmd


def fetch_into_versioned_cache(
    marketplace_name: str,
    plugin_name: str,
    target_version: str,
    catalog_entry: dict,
    deadline: float,
    dry_run: bool,
) -> tuple[bool, Optional[str], Optional[str]]:
    """Clone the catalog source into a fresh cache/<mkt>/<plugin>/<target_version>/
    directory. Returns (ok, new_install_path, git_commit_sha).

    On any failure: leaves the new dir empty/absent and returns (False, None, None).
    Caller MUST NOT update the registry on failure.
    """
    if time.monotonic() > deadline:
        _log(f"DEFER: budget exhausted before fetching {plugin_name}")
        return False, None, None

    new_dir = PLUGINS_DIR / "cache" / marketplace_name / plugin_name / target_version

    # Refuse to write into a symlinked target — that would clobber a
    # local-dev checkout. Caller should flag this as a symlink skip.
    if new_dir.is_symlink():
        _log(
            f"SKIP {plugin_name}@{target_version}: target cache dir is a "
            f"symlink ({new_dir} -> {Path(new_dir).resolve()}); refusing "
            f"to fetch into a local-dev tree"
        )
        return False, None, None

    # If new_dir already exists (e.g. from a prior partial run), preserve it
    # only if the plugin.json version matches. Otherwise remove and re-clone.
    if new_dir.exists() and not new_dir.is_symlink():
        existing_v, _ = read_install_path_version(str(new_dir))
        if existing_v and version_eq(existing_v, target_version):
            _log(
                f"REUSE existing cache dir for {plugin_name}@{target_version} "
                f"(plugin.json already at target)"
            )
            git_sha = _read_existing_git_sha(new_dir)
            return True, str(new_dir), git_sha
        _log(
            f"PURGE stale cache dir {new_dir} "
            f"(plugin.json version={existing_v}, target={target_version})"
        )
        if not dry_run:
            try:
                shutil.rmtree(new_dir)
            except OSError as e:
                _log(f"ERROR: rmtree {new_dir} failed: {e}")
                return False, None, None

    if dry_run:
        _log(f"DRY-RUN would fetch {plugin_name}@{target_version} -> {new_dir}")
        return True, str(new_dir), None

    src_kind = _catalog_source_kind(catalog_entry)
    source = catalog_entry.get("source")

    if src_kind == "github" and isinstance(source, dict):
        repo = source.get("repo")
        if not repo:
            _log(f"ERROR: {plugin_name} catalog source.source=github but no source.repo")
            return False, None, None
        return _fetch_github(repo, new_dir, deadline)
    if src_kind == "directory" and isinstance(source, dict):
        path = source.get("path") or source.get("source_path")
        if not path:
            _log(f"ERROR: {plugin_name} catalog source.source=directory but no path")
            return False, None, None
        return _fetch_directory(Path(path).expanduser(), new_dir, deadline)

    _log(f"ERROR: unsupported source type {src_kind!r} for {plugin_name}")
    return False, None, None


def _read_existing_git_sha(install_dir: Path) -> Optional[str]:
    """If the install dir was cloned with .git intact (it isn't, given our
    rsync excludes), we'd read it here. We don't, so this returns None.
    Kept for symmetry with the registry's gitCommitSha field."""
    return None


def _fetch_github(
    repo: str, new_dir: Path, deadline: float
) -> tuple[bool, Optional[str], Optional[str]]:
    if time.monotonic() > deadline:
        return False, None, None

    with tempfile.TemporaryDirectory(prefix="mp-autoupdate-") as tmp:
        clone_dir = Path(tmp) / "clone"
        url = f"https://github.com/{repo}.git"
        try:
            res = subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", url, str(clone_dir)],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            _log(f"ERROR: git clone {url} failed: {e}")
            return False, None, None
        if res.returncode != 0:
            _log(f"ERROR: git clone {url} rc={res.returncode}: {res.stderr.strip()}")
            return False, None, None

        # Capture commit sha BEFORE the .git directory gets excluded by rsync
        git_sha: Optional[str] = None
        try:
            res = subprocess.run(
                ["git", "-C", str(clone_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode == 0:
                git_sha = res.stdout.strip() or None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        new_dir.mkdir(parents=True, exist_ok=True)
        try:
            res = subprocess.run(
                _rsync_cmd(str(clone_dir), str(new_dir)),
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            _log(f"ERROR: rsync into {new_dir} failed: {e}")
            return False, None, None
        if res.returncode != 0:
            _log(f"ERROR: rsync rc={res.returncode}: {res.stderr.strip()}")
            return False, None, None

    return True, str(new_dir), git_sha


def _fetch_directory(
    src: Path, new_dir: Path, deadline: float
) -> tuple[bool, Optional[str], Optional[str]]:
    if not src.is_dir():
        _log(f"ERROR: source directory not found: {src}")
        return False, None, None
    if time.monotonic() > deadline:
        return False, None, None

    new_dir.mkdir(parents=True, exist_ok=True)
    try:
        res = subprocess.run(
            _rsync_cmd(str(src), str(new_dir)),
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        _log(f"ERROR: rsync from {src} failed: {e}")
        return False, None, None
    if res.returncode != 0:
        _log(f"ERROR: rsync rc={res.returncode}: {res.stderr.strip()}")
        return False, None, None
    return True, str(new_dir), None


# ---------------------------------------------------------------------------
# Drift detection per entry
# ---------------------------------------------------------------------------

def detect_drift(
    entry: dict, plugin_key: str, catalog_version: str
) -> tuple[bool, str, str]:
    """Return (is_drift, current_effective_version, reason).

    Effective version is the on-disk plugin.json version when readable;
    otherwise the registry's `version` field. We treat drift as
    `effective_version < catalog_version`.
    """
    install_path = entry.get("installPath", "")
    registry_version = str(entry.get("version") or "")

    on_disk_version, status = read_install_path_version(install_path)

    if status == "ok":
        effective = on_disk_version or registry_version
        if version_lt(effective, catalog_version):
            return True, effective, f"on-disk={effective} < catalog={catalog_version}"
        if version_lt(registry_version, catalog_version):
            # On-disk is current but the registry's version field is stale.
            # Treat as registry-only drift — needs a registry rewrite, no clone.
            return True, registry_version, (
                f"registry-version-stale (on-disk={effective} >= catalog, "
                f"registry={registry_version} < catalog={catalog_version})"
            )
        return False, effective, f"current ({effective})"

    if status == "missing-dir":
        return True, registry_version, f"installPath missing: {install_path}"

    if status in ("missing-manifest", "invalid-json", "no-version-field"):
        return True, registry_version, f"installPath manifest issue: {status}"

    return False, registry_version, status  # unreachable


# ---------------------------------------------------------------------------
# Per-entry update transaction
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def update_entry(
    entry: dict,
    plugin_key: str,
    plugin_name: str,
    marketplace_name: str,
    catalog_entry: dict,
    catalog_version: str,
    deadline: float,
    dry_run: bool,
) -> tuple[str, dict]:
    """Mutate-and-return a NEW entry dict for the given registry entry.
    Returns (status, new_entry). status: fetched | registry-only-fix | failed | deferred | symlink | unsupported | reused.
    """
    install_path = entry.get("installPath", "")

    if install_path and is_install_path_symlink(install_path):
        _log(f"SKIP {plugin_key}: installPath is a local-dev symlink -> {install_path}")
        return "symlink", entry

    # Also short-circuit if the target cache dir for the catalog version
    # is itself a symlink (local-dev). Don't clobber the dev tree.
    target_dir = PLUGINS_DIR / "cache" / marketplace_name / plugin_name / catalog_version
    if target_dir.is_symlink():
        _log(
            f"SKIP {plugin_key}: target cache dir {target_dir} is a "
            f"local-dev symlink -> {Path(target_dir).resolve()}"
        )
        return "symlink", entry

    src_kind = _catalog_source_kind(catalog_entry)
    if src_kind not in ("github", "directory"):
        _log(f"SKIP {plugin_key}: unsupported catalog source {src_kind!r}")
        return "unsupported", entry

    on_disk_version, status = read_install_path_version(install_path)

    # Case A: on-disk already at catalog version. Just fix the registry's
    # version/installPath fields (no clone).
    if status == "ok" and on_disk_version and not version_lt(on_disk_version, catalog_version):
        if dry_run:
            _log(
                f"DRY-RUN registry-only-fix for {plugin_key}: "
                f"set registry version={on_disk_version}"
            )
            return "registry-only-fix", entry
        new_entry = dict(entry)
        new_entry["version"] = on_disk_version
        new_entry["lastUpdated"] = _now_iso()
        return "registry-only-fix", new_entry

    # Case B: clone needed.
    ok, new_install_path, git_sha = fetch_into_versioned_cache(
        marketplace_name=marketplace_name,
        plugin_name=plugin_name,
        target_version=catalog_version,
        catalog_entry=catalog_entry,
        deadline=deadline,
        dry_run=dry_run,
    )

    if not ok:
        return "failed", entry

    if dry_run:
        return "fetched", entry

    # Case C: verify the new dir's plugin.json version matches the catalog.
    new_v, new_status = read_install_path_version(new_install_path or "")
    if new_status != "ok":
        _log(
            f"REJECT {plugin_key}: cloned dir manifest unreadable "
            f"({new_status}) at {new_install_path}"
        )
        return "failed", entry
    if not version_eq(new_v or "", catalog_version):
        _log(
            f"REJECT {plugin_key}: plugin.json version={new_v!r} != "
            f"catalog={catalog_version!r} at {new_install_path}. "
            f"Likely upstream packaging error — leaving registry pointing at old dir."
        )
        return "failed", entry

    new_entry = dict(entry)
    new_entry["installPath"] = new_install_path
    new_entry["version"] = catalog_version
    new_entry["lastUpdated"] = _now_iso()
    if git_sha:
        new_entry["gitCommitSha"] = git_sha
    _log(
        f"FETCHED {plugin_key}: {entry.get('version')} -> {catalog_version} "
        f"({new_install_path})"
    )
    return "fetched", new_entry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Marketplace autoupdate compensator")
    parser.add_argument("--dry-run", action="store_true", help="Report drift without fetching or writing")
    parser.add_argument("--verbose", action="store_true", help="Also print summary to stdout")
    args = parser.parse_args(argv)

    if KILL_SWITCH.exists():
        if args.verbose:
            print(f"kill-switch present: {KILL_SWITCH}")
        return 0

    if not args.dry_run and not acquire_lock():
        _log(f"SKIP RUN: lock present at {LOCK_FILE} (previous run still in progress?)")
        if args.verbose:
            print(f"locked: {LOCK_FILE}")
        return 0

    try:
        return _run(args)
    finally:
        if not args.dry_run:
            release_lock()


def _run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    deadline = started + BUDGET_SECONDS

    registry = load_registry()
    if not registry:
        return 0

    plugins_section = registry.get("plugins") or {}
    if not plugins_section:
        _log("RUN: empty plugins section in registry; nothing to do")
        return 0

    _log(
        "RUN-START "
        f"dry_run={args.dry_run} "
        f"registry={REGISTRY_PATH} "
        f"budget_s={BUDGET_SECONDS}"
    )

    counts = {
        "fetched": 0,
        "registry-only-fix": 0,
        "current": 0,
        "symlink": 0,
        "unsupported": 0,
        "missing-catalog": 0,
        "missing-version-in-catalog": 0,
        "failed": 0,
        "skip-rejected": 0,
        "deferred": 0,
        "reused": 0,
    }
    drift_report: list[str] = []
    registry_dirty = False
    reject_cache = load_reject_cache()
    reject_cache_dirty = False

    for plugin_key, entries in plugins_section.items():
        if "@" not in plugin_key:
            continue
        plugin_name, marketplace_name = plugin_key.split("@", 1)

        if marketplace_name == "local":
            # @local entries never have a marketplace catalog. Skip.
            counts["unsupported"] += 1
            continue

        catalog_entry = read_catalog_entry(marketplace_name, plugin_name)
        if not catalog_entry:
            counts["missing-catalog"] += 1
            _log(f"SKIP {plugin_key}: no catalog entry")
            continue

        catalog_version = catalog_entry.get("version")
        if not catalog_version:
            counts["missing-version-in-catalog"] += 1
            _log(f"SKIP {plugin_key}: catalog has no version field")
            continue

        if not isinstance(entries, list):
            entries = [entries]

        for idx, entry in enumerate(entries):
            if time.monotonic() > deadline:
                counts["deferred"] += 1
                _log(f"DEFER {plugin_key}[{idx}]: budget exhausted")
                continue

            is_drift, effective, reason = detect_drift(entry, plugin_key, catalog_version)
            if not is_drift:
                counts["current"] += 1
                _log(f"OK {plugin_key}[{idx}]: {reason}")
                continue

            _log(f"DRIFT {plugin_key}[{idx}]: {reason}")

            reject_key = f"{plugin_key}:{catalog_version}"
            if reject_key in reject_cache and not args.dry_run:
                counts["skip-rejected"] += 1
                _log(
                    f"SKIP-REJECTED {plugin_key}[{idx}]: prior verification "
                    f"failed for catalog={catalog_version} "
                    f"({reject_cache[reject_key].get('reason', 'unknown')}). "
                    f"Bump catalog version to retry."
                )
                continue

            status, new_entry = update_entry(
                entry=entry,
                plugin_key=plugin_key,
                plugin_name=plugin_name,
                marketplace_name=marketplace_name,
                catalog_entry=catalog_entry,
                catalog_version=catalog_version,
                deadline=deadline,
                dry_run=args.dry_run,
            )
            counts[status] = counts.get(status, 0) + 1

            if status == "failed" and not args.dry_run:
                reject_cache[reject_key] = {
                    "rejected_at": _now_iso(),
                    "reason": "clone-or-verify-failed",
                }
                reject_cache_dirty = True

            if status in ("fetched", "registry-only-fix") and not args.dry_run:
                entries[idx] = new_entry
                registry_dirty = True

            if status in ("fetched", "registry-only-fix"):
                drift_report.append(
                    f"{plugin_key}: {entry.get('version')} -> {catalog_version} "
                    f"({status})"
                )

        # Re-attach (in case we replaced a single non-list entry above)
        plugins_section[plugin_key] = entries

    # Atomic registry write if anything changed
    write_ok = True
    if registry_dirty:
        registry["plugins"] = plugins_section
        write_ok = write_registry_atomic(registry)
        if not write_ok:
            _log("CRITICAL: registry write failed — installs untouched on disk but registry not updated")

    # Reject-cache persists so failed clones don't retry every session
    if reject_cache_dirty:
        save_reject_cache(reject_cache)

    elapsed = time.monotonic() - started

    summary_parts = [f"{k}={v}" for k, v in counts.items() if v]
    _log(
        "RUN-END "
        f"elapsed_s={elapsed:.2f} "
        f"registry_written={'yes' if registry_dirty and write_ok else 'no'} "
        + " ".join(summary_parts)
    )
    for line in drift_report:
        _log(f"DRIFT-SUMMARY {line}")

    if args.verbose:
        print(
            f"done: " + " ".join(summary_parts) +
            f" elapsed={elapsed:.2f}s registry_written={registry_dirty and write_ok}"
        )
        for line in drift_report:
            print(f"  {line}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:  # noqa: BLE001 — last-ditch swallow
        _log(f"FATAL: unhandled exception: {e!r}")
        try:
            release_lock()
        except Exception:
            pass
        sys.exit(0)
