#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Background plugin-cache drift detector. Compares cached plugin install
SHA against the source repo's main HEAD; writes markers under
~/.claude/plugins/.drift-markers/ for Phase 1 Assess to surface.
Fire-and-forget; fcntl single-flight; per-plugin 24h freshness gate."""
from __future__ import annotations
import argparse, fcntl, json, os, re, subprocess, time
from pathlib import Path

# Vault-only credential policy: an anonymous read must never reach a credential
# helper. `-c credential.helper=` does not reset URL-scoped helpers
# (credential.https://github.com.helper), so reset the URL's scope explicitly
# (the inspectable form credential_policy accepts) and drop user + system config.
_ANON_GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
                 "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
_HEX = re.compile(r"[0-9a-f]{7,40}")


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
        origin = "/".join(url.split("/", 3)[:3])  # https://github.com
        r = subprocess.run(["git","-c","credential.helper=","-c",f"credential.{origin}.helper=",
                            "ls-remote",url,"refs/heads/main"],
                           capture_output=True, text=True, timeout=t, check=False,
                           env=_ANON_GIT_ENV, stdin=subprocess.DEVNULL)
        if r.returncode == 0 and r.stdout.strip(): return r.stdout.split()[0]
    except (FileNotFoundError, subprocess.TimeoutExpired): pass
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=str(Path.home() / ".claude" / "plugins"))
    ap.add_argument("--force", action="store_true")
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
        # Markers for plugins no longer installed are never revisited below.
        for old in md.glob("*__*.json"):
            n, _, m = old.stem.partition("__")
            if not plugins.get(f"{n}@{m}"):
                try: old.unlink()
                except OSError: pass
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
            # A semver install key ("0.31.0") is not a SHA, so it cannot be
            # compared to main; it used to "drift" forever.
            if not _HEX.fullmatch(str(csha or "")):
                mp.unlink(missing_ok=True); continue
            if not a.force and mp.exists():
                try:
                    if now - mp.stat().st_mtime < 86400: continue
                except OSError: pass
            msha = _sha(src)
            if not msha: continue
            if not msha.startswith(str(csha)):
                mp.write_text(json.dumps({"plugin": name, "marketplace": mkt, "source": src,
                    "cached_version": cv, "cached_sha": csha, "main_sha": msha,
                    "commits_behind": None, "drifted": True, "last_checked": int(now)}, indent=2))
            elif mp.exists():
                try: mp.unlink()
                except OSError: pass
    finally:
        try: fcntl.flock(lf.fileno(), fcntl.LOCK_UN); lf.close()
        except Exception: pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
