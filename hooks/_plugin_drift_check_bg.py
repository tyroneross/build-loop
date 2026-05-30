#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Background plugin-cache maintenance. Two passes, both fire-and-forget under
one fcntl single-flight lock:

  1. DRIFT  — compare each cached install's SHA against the source repo's main
              HEAD; write markers under ~/.claude/plugins/.drift-markers/ for
              Phase 1 Assess to surface. Per-plugin 24h freshness gate.
  2. CACHE-GC — prune orphaned cache *version* directories that accumulate
              across plugin updates and trip `/doctor` (it scans every cached
              version, including stale ones). SAFE RULE: a version dir is pruned
              only when it is BOTH unreferenced by installed_plugins.json AND
              semver-older than the newest referenced version for that plugin.
              The active version and any newer-than-active (freshly fetched,
              pending activation) dir are never touched. Claude Code re-fetches
              on demand, so prunes are reversible. Opt out with
              BUILDLOOP_NO_CACHE_GC=1; preview with --dry-run."""
from __future__ import annotations
import argparse, fcntl, json, os, shutil, subprocess, time
from pathlib import Path


def _j(p):
    try: return json.loads(Path(p).read_text())
    except Exception: return None

def _url(s):
    if not isinstance(s, dict): return None
    if s.get("source") == "github" and s.get("repo"): return f"https://github.com/{s['repo']}"
    if s.get("source") == "git" and s.get("url"): return s["url"]
    return None

def _sha(url, t=8):
    try:
        r = subprocess.run(["git","ls-remote",url,"refs/heads/main"],
                           capture_output=True, text=True, timeout=t, check=False)
        if r.returncode == 0 and r.stdout.strip(): return r.stdout.split()[0]
    except (FileNotFoundError, subprocess.TimeoutExpired): pass
    return None

def _is_semver(s):
    """True only for pure dotted-numeric versions (e.g. 0.13.4). Hash-named
    cache dirs (1c7d8765472a, f2cbfbefebbf) return False so they are never
    ranked or pruned — set-membership still protects active hash installs."""
    parts = str(s).split(".")
    return bool(parts) and all(p.isdigit() for p in parts)

def _ver_tuple(s):
    return tuple(int(p) for p in str(s).split("."))


def _prune_orphan_cache(plugins, cache_root, dry_run=False):
    """Delete cache version dirs that are unreferenced AND older than the newest
    referenced version for that plugin. Returns list of pruned (or would-prune)
    absolute paths. Never deletes outside cache_root; never deletes an active or
    newer-than-active dir."""
    cache_root = cache_root.resolve()
    # container dir (parent of each installPath) -> {referenced version basenames}
    containers: dict[Path, set] = {}
    for entries in plugins.values():
        for e in entries or []:
            ip = e.get("installPath")
            if not ip: continue
            ipp = Path(ip)
            try: parent = ipp.parent.resolve()
            except Exception: continue
            # safety: only manage dirs strictly under the plugin cache root
            if cache_root not in parent.parents and parent != cache_root: continue
            containers.setdefault(parent, set()).add(ipp.name)

    pruned = []
    for parent, referenced in containers.items():
        if not parent.is_dir(): continue
        # only rank against pure-semver active versions; skip hash-versioned plugins
        sem_ref = [_ver_tuple(v) for v in referenced if _is_semver(v)]
        if not sem_ref: continue
        max_ref = max(sem_ref)
        for child in parent.iterdir():
            if not child.is_dir(): continue
            name = child.name
            if name in referenced: continue                 # active — keep
            if not _is_semver(name): continue                # hash/unknown — keep
            if _ver_tuple(name) >= max_ref: continue         # newest/pending — keep
            # final guard: stay inside cache_root
            cr = child.resolve()
            if cache_root not in cr.parents: continue
            pruned.append(str(cr))
            if not dry_run:
                try: shutil.rmtree(cr)
                except OSError: pruned.pop()
    return pruned


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=str(Path.home() / ".claude" / "plugins"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="report cache-GC prune candidates without deleting")
    a = ap.parse_args(argv)
    root = Path(a.workdir).resolve()
    plugins = (_j(root/"installed_plugins.json") or {}).get("plugins", {}) or {}
    markets = _j(root/"known_marketplaces.json") or {}
    if not plugins: return 0

    md = root/".drift-markers"; md.mkdir(parents=True, exist_ok=True)
    try:
        lf = open(md/".check.lock", "a+"); fcntl.flock(lf.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)
    except (BlockingIOError, OSError): return 0
    try:
        now = time.time()
        # Pass 1: drift detection (per-plugin 24h freshness gate)
        for key, entries in plugins.items():
            if not entries or "@" not in key: continue
            name, mkt = key.split("@", 1)
            loc = (markets.get(mkt) or {}).get("installLocation")
            src = None
            if loc:
                for pl in (_j(Path(loc)/".claude-plugin"/"marketplace.json") or {}).get("plugins", []) or []:
                    if pl.get("name") == name: src = _url(pl.get("source") or {}); break
            if not src: continue
            ip = Path(entries[0].get("installPath") or "")
            if not ip.exists(): continue
            pj = ip/".claude-plugin"/"plugin.json"
            if not pj.exists(): pj = ip/"plugin.json"
            cv = (_j(pj) or {}).get("version")
            csha = entries[0].get("version")
            mp = md/f"{name}__{mkt}.json"
            if not a.force and mp.exists():
                try:
                    if now - mp.stat().st_mtime < 86400: continue
                except OSError: pass
            msha = _sha(src)
            if not msha: continue
            drifted = bool(csha) and not msha.startswith(str(csha))
            if drifted:
                mp.write_text(json.dumps({"plugin": name, "marketplace": mkt, "source": src,
                    "cached_version": cv, "cached_sha": csha, "main_sha": msha,
                    "commits_behind": None, "drifted": True, "last_checked": int(now)}, indent=2))
            elif mp.exists():
                try: mp.unlink()
                except OSError: pass

        # Pass 2: orphaned cache-version GC (every session; cheap + idempotent)
        if os.environ.get("BUILDLOOP_NO_CACHE_GC") != "1":
            pruned = _prune_orphan_cache(plugins, root/"cache", dry_run=a.dry_run)
            if a.dry_run:
                print(json.dumps({"would_prune": pruned}, indent=2))
            elif pruned:
                (md/".cache-gc.json").write_text(json.dumps(
                    {"pruned": pruned, "pruned_at": int(now)}, indent=2))
    finally:
        try: fcntl.flock(lf.fileno(), fcntl.LOCK_UN); lf.close()
        except Exception: pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
