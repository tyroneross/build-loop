#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Index installed plugins (Claude Code, Codex) and declared command-line tools into a small, self-refreshing machine-wide catalog Build Loop can query.
#   application: meta
#   status: active
"""Installed plugin + command-line tool index (ring 3).

build_capability_registry.py and build_capability_index.py only see the repo
they run in. This index covers what is installed on the MACHINE: every plugin
each agent host has installed, plus the command-line tools declared in
references/external-tools.json. Build Loop queries it to reach for a plugin or
tool when a task needs one.

Per plugin it records ONLY: name, marketplace, version, host, install_path, a
<=160-char summary from the manifest description, <=6 ``use_when`` trigger
phrases (quoted phrases in skill/agent/command frontmatter descriptions, then
short clauses of skill descriptions; manifest keywords are not copied), and the NAMES of its skills, agents, commands and MCP servers.
Bodies, MCP args/env and every other manifest field are never copied.

Hosts (missing files mean "not found", never an error; one failing host never
stops the others):
  claude  $CLAUDE_CONFIG_DIR or ~/.claude -> plugins/installed_plugins.json, minus
          plugins settings.json ``enabledPlugins`` sets to false; project-scope
          installs are offered only inside their project
  codex   $CODEX_HOME or ~/.codex -> config.toml (tomllib; only [plugins."name@marketplace"]
          ids and ``enabled`` are kept); files under plugins/cache/<marketplace>/<name>/<version>/

Surfaces are found in the conventional skills/, agents/, commands/ dirs plus
any location the host's manifest declares; every file used must resolve inside
the install root (symlinks pointing elsewhere are not followed), metadata reads
are size-bounded, and one plugin that fails to scan is recorded under its host's
``skipped`` instead of aborting the build.

Freshness without maintenance: the index stores a fingerprint of each host's
installed-plugin file and Claude settings.json (size+mtime), each plugin's
version and install path, every file extraction reads per plugin (manifest,
SKILL.md, agent/command files, MCP files), the tool declaration file, and each
declared tool's ``shutil.which`` result. A malformed cached index is rebuilt.
``check`` recomputes it; ``query`` rebuilds first when stale. Every result is
verify-on-use: ``query`` drops an entry whose install path or binary is gone
and says so on stderr.

CLI::

    python3 scripts/plugin_index.py build [--out PATH]
    python3 scripts/plugin_index.py check [--out PATH]       # 0 fresh, 3 stale/missing
    python3 scripts/plugin_index.py query <words...> [--out PATH] [--host claude|codex]
    python3 scripts/plugin_index.py show [--out PATH]

Default index path: $BUILD_LOOP_PLUGIN_INDEX or ~/.build-loop/plugin-index.json
(machine-wide, because installed plugins are machine-wide). Stdlib only.
Atomic write. A serialized index over MAX_BYTES is trimmed (component lists
shortened) and flagged ``trimmed``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"
MAX_BYTES = 200 * 1024
SUMMARY_MAX = 160
USE_WHEN_MAX = 6
QUERY_CAP = 8
EXIT_STALE = 3

TOOLS_FILE = Path(__file__).resolve().parents[1] / "references" / "external-tools.json"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_QUOTED_RE = re.compile(r"[\"“]([^\"”\n]{3,60})[\"”]")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "into", "use", "when",
    "you", "your", "are", "any", "all", "can", "how", "what", "via", "not",
})


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def default_index_path() -> Path:
    env = os.environ.get("BUILD_LOOP_PLUGIN_INDEX")
    return Path(env).expanduser() if env else Path.home() / ".build-loop" / "plugin-index.json"


def claude_root() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude"


def codex_root() -> Path:
    env = os.environ.get("CODEX_HOME")
    return Path(env).expanduser() if env else Path.home() / ".codex"


# Size limits. Metadata on this machine tops out near 12 KB (largest plugin.json,
# 2026-11-09) and host lists near 64 KB (~/.codex/config.toml), so 256 KiB and
# 4 MiB leave ~20x / ~60x headroom while keeping a hostile multi-GB file from
# being allocated on the shortlist path. An oversized file is skipped alone.
MAX_META_BYTES = 256 * 1024
MAX_HOST_BYTES = 4 * 1024 * 1024
FRONTMATTER_READ = 8192  # SKILL.md / agent / command files: frontmatter only

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
# Stored surface names (skill dirs, agent/command stems, MCP keys) are what an
# agent will type to invoke something, so they must be short identifiers:
# no spaces, no control characters, at most 64 chars. Others are not stored.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
SKIPPED_KEEP = 20  # per-host diagnostics kept; the total is still counted


def _clean_text(s: Any, limit: int) -> str:
    return _CONTROL_RE.sub("", str(s))[:limit]


def _err(exc: BaseException) -> str:
    return _clean_text(f"{type(exc).__name__}: {exc}", 200)


def _skip(status: dict[str, Any], name: str, reason: str) -> None:
    status["skipped_total"] = status.get("skipped_total", 0) + 1
    lst = status.setdefault("skipped", [])
    if len(lst) < SKIPPED_KEEP:
        lst.append({"name": _clean_text(name, 80), "reason": _clean_text(reason, 200)})
SURFACE_KINDS = ("skills", "agents", "commands")


def _stat_sig(p: Path) -> str:
    try:
        st = p.stat()
        return f"{st.st_size}:{int(st.st_mtime_ns)}"
    except OSError:
        return "missing"


def _read_bounded(p: Path, limit: int) -> bytes | None:
    """Read at most ``limit`` bytes of a REGULAR file; None otherwise.

    Opened non-blocking and checked with fstat, so a FIFO or device at a
    metadata path is rejected instead of blocking the shortlist forever.
    """
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
    except (OSError, ValueError):
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            return None
        with os.fdopen(fd, "rb", closefd=False) as fh:
            data = fh.read(limit + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    return data if len(data) <= limit else None


def _read_json(p: Path, limit: int | None = None) -> Any:
    data = _read_bounded(p, MAX_META_BYTES if limit is None else limit)
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _inside(p: Path, root: Path) -> bool:
    """True when ``p`` exists and its real path lies inside ``root`` (already resolved)."""
    try:
        return p.resolve(strict=True).is_relative_to(root)
    except (OSError, RuntimeError):  # missing, unreadable, or a symlink loop
        return False


# ---------------------------------------------------------------------------
# Host discovery -> list of (host, name, marketplace, version, install_path)
# ---------------------------------------------------------------------------

def discover_claude() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """installed_plugins.json, minus plugins the user settings disable.

    ``enabledPlugins`` in ``settings.json`` (user scope) maps "name@market" to
    a bool; ``false`` means installed but not callable, so it is skipped. A
    plugin absent from that map counts as enabled (TAG:ASSUMED: installs add it
    as true). Project-scope installs keep ``scope``/``project_path`` so query
    offers them only inside that project. Project-level settings files are not
    consulted (the index is machine-wide).
    """
    root = claude_root()
    src = root / "plugins" / "installed_plugins.json"
    settings_path = root / "settings.json"
    data = _read_json(src, MAX_HOST_BYTES)
    if not isinstance(data, dict):
        return {"status": "not found", "source": str(src), "settings": str(settings_path)}, []
    settings = _read_json(settings_path, MAX_HOST_BYTES)
    enabled = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    enabled = enabled if isinstance(enabled, dict) else {}
    del settings  # only the enabled flags leave this function
    status: dict[str, Any] = {"status": "found", "source": str(src), "settings": str(settings_path), "disabled": 0}
    found: list[dict[str, Any]] = []
    plugins = data.get("plugins") if isinstance(data.get("plugins"), dict) else data
    for key, installs in sorted(plugins.items()):
        if not isinstance(installs, list) or not installs or "@" not in key:
            continue
        name, _, market = key.partition("@")
        if not (_SAFE_ID_RE.fullmatch(name) and _SAFE_ID_RE.fullmatch(market)):
            _skip(status, key, "invalid plugin id")
            continue
        if enabled.get(key) is False:
            status["disabled"] += 1
            continue
        # Keep EVERY installation record: a user-scope install stays usable
        # even when a later record is a project install for another project.
        # query() picks the first record applicable to the caller's workdir.
        for rec in installs:
            if not isinstance(rec, dict):
                continue
            path = rec.get("installPath")
            f = {
                "host": "claude", "name": name, "marketplace": market,
                "version": _clean_text(rec.get("version") or "", 40),
                "install_path": str(path) if isinstance(path, str) and path else None,
            }
            if rec.get("scope") == "project":
                pp = rec.get("projectPath")
                f["scope"] = "project"
                f["project_path"] = str(pp) if isinstance(pp, str) else ""
            found.append(f)
    return status, found


def discover_codex() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Plugin ids + ``enabled`` from config.toml; nothing else is kept."""
    root = codex_root()
    src = root / "config.toml"
    raw = _read_bounded(src, MAX_HOST_BYTES)
    if raw is None:
        return {"status": "not found", "source": str(src)}, []
    try:
        config = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return {"status": "unreadable", "source": str(src), "reason": f"{type(exc).__name__}"}, []
    plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    del config  # only plugin ids and flags leave this function
    cache = root / "plugins" / "cache"
    status: dict[str, Any] = {"status": "found", "source": str(src), "enabled_without_local_files": 0}
    try:
        cache_real = cache.resolve()
    except (OSError, RuntimeError) as exc:  # symlink loop raises RuntimeError on 3.11
        status.update(status="unreadable", reason=_err(exc))
        return status, []
    found: list[dict[str, Any]] = []
    for key, val in sorted(plugins.items()):
        enabled = val.get("enabled", True) if isinstance(val, dict) else True
        if enabled is False:
            continue
        name, _, market = str(key).partition("@")
        if not (_SAFE_ID_RE.fullmatch(name) and _SAFE_ID_RE.fullmatch(market)):
            _skip(status, str(key), "invalid plugin id (must be name@marketplace, single path components)")
            continue
        base = cache / market / name
        try:
            if not base.is_dir():
                status["enabled_without_local_files"] += 1  # remote/app connector
                continue
            # The marketplace/name dirs must stay in the cache. A version dir may be
            # a symlink (local installs point at a checkout); the host follows it too.
            if not base.resolve().is_relative_to(cache_real):
                _skip(status, str(key), "plugin directory resolves outside the Codex cache")
                continue
            versions = [d for d in base.iterdir() if d.is_dir()]
            newest = max(versions, key=lambda d: d.stat().st_mtime) if versions else None
        except (OSError, RuntimeError) as exc:
            _skip(status, str(key), _err(exc))
            continue
        if newest is None:
            status["enabled_without_local_files"] += 1
            continue
        found.append({
            "host": "codex", "name": name, "marketplace": market,
            "version": _clean_text(newest.name, 40), "install_path": str(newest),
        })
    return status, found


HOSTS = (discover_claude, discover_codex)


# ---------------------------------------------------------------------------
# Per-plugin extraction (names + short metadata only)
# ---------------------------------------------------------------------------

MANIFEST_CANDIDATES = {
    "claude": (".claude-plugin/plugin.json", "plugin.json", ".codex-plugin/plugin.json"),
    "codex": (".codex-plugin/plugin.json", "plugin.json", ".claude-plugin/plugin.json"),
}


def manifest_path(install: Path, host: str = "claude") -> Path | None:
    """The host's own manifest first; only files inside the install root count."""
    root = install.resolve()
    for rel in MANIFEST_CANDIDATES.get(host, MANIFEST_CANDIDATES["claude"]):
        p = install / rel
        if p.is_file() and _inside(p, root):
            return p
    return None


def _declared(manifest: dict[str, Any], key: str) -> list[str]:
    v = manifest.get(key)
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def _locations(install: Path, root: Path, manifest: dict[str, Any], kind: str) -> list[Path]:
    """Conventional ``<kind>/`` plus manifest-declared paths, each inside the root."""
    out: list[Path] = []
    for rel in [kind] + _declared(manifest, kind):
        p = install / rel  # an absolute ``rel`` replaces install and fails _inside
        if _inside(p, root) and p.resolve() not in [q.resolve() for q in out]:
            out.append(p)
    return out


def _describe_surfaces(install: Path, manifest: dict[str, Any], host: str) -> dict[str, Any]:
    """Names and source files of skills/agents/commands/MCP servers.

    Only files whose real path is inside the install root are used, so a
    symlink pointing elsewhere is never followed. Raises OSError when a
    directory cannot be listed; build() records that plugin as skipped.
    """
    root = install.resolve()
    surfaces: dict[str, Any] = {k: {} for k in (*SURFACE_KINDS, "mcp_servers")}
    files: list[Path] = []
    for loc in _locations(install, root, manifest, "skills"):
        if loc.is_dir():
            for d in sorted(loc.iterdir()):
                md = d / "SKILL.md"
                if _SAFE_NAME_RE.fullmatch(d.name) and md.is_file() and _inside(md, root):
                    surfaces["skills"].setdefault(d.name, md)
    for kind in ("agents", "commands"):
        for loc in _locations(install, root, manifest, kind):
            mds = [loc] if loc.is_file() and loc.suffix == ".md" else sorted(loc.glob("*.md")) if loc.is_dir() else []
            for md in mds:
                if _SAFE_NAME_RE.fullmatch(md.stem) and md.is_file() and _inside(md, root):
                    surfaces[kind].setdefault(md.stem, md)
    inline = manifest.get("mcpServers")
    if isinstance(inline, dict):
        for k, v in inline.items():
            if isinstance(v, dict) and _SAFE_NAME_RE.fullmatch(str(k)):
                surfaces["mcp_servers"][str(k)] = None
    mcp_files = [".mcp.json", "mcp.json"] + ([inline] if isinstance(inline, str) else [])
    for rel in mcp_files:
        p = install / rel
        if not (p.is_file() and _inside(p, root)):
            continue
        files.append(p)
        data = _read_json(p)
        servers = data.get("mcpServers", data) if isinstance(data, dict) else None
        if isinstance(servers, dict):
            for k, v in servers.items():
                if isinstance(v, dict) and _SAFE_NAME_RE.fullmatch(str(k)):
                    surfaces["mcp_servers"].setdefault(str(k), p)
    for kind in SURFACE_KINDS:
        files.extend(surfaces[kind].values())
    surfaces["files"] = files
    return surfaces


def scan_plugin(found: dict[str, Any]) -> dict[str, Any]:
    """Manifest + surfaces + fingerprint for one plugin. Raises on unreadable dirs."""
    if not found.get("install_path"):
        raise FileNotFoundError("no install path")
    install = Path(found["install_path"])
    if not install.is_dir():
        raise FileNotFoundError(f"install path missing: {install}")
    mpath = manifest_path(install, found["host"])
    manifest = _read_json(mpath) if mpath else None
    manifest = manifest if isinstance(manifest, dict) else {}
    surfaces = _describe_surfaces(install, manifest, found["host"])
    inputs = ([mpath] if mpath else []) + surfaces["files"]
    sig = hashlib.sha256("\n".join(
        sorted(f"{p.relative_to(install) if p.is_relative_to(install) else p}={_stat_sig(p)}" for p in inputs)
    ).encode()).hexdigest()[:16]
    return {"manifest": manifest, "surfaces": surfaces, "sig": f"{len(inputs)}:{sig}"}


def _clean(p: str) -> str | None:
    p = " ".join(p.lower().split()).strip(" .,;:!")
    if _CLEAN_PHRASE_RE.fullmatch(p) and 1 <= len(p.split()) <= 6:
        return p
    return None


_CLEAN_PHRASE_RE = re.compile(r"^[a-z0-9][a-z0-9 '\-/?]*$")


def _description(md: Path) -> str:
    data = None
    try:
        with md.open("rb") as fh:
            data = fh.read(FRONTMATTER_READ)  # frontmatter only; bodies are never read
    except OSError:
        return ""
    m = _FRONTMATTER_RE.match(data.decode("utf-8", errors="ignore"))
    if not m:
        return ""
    out: list[str] = []
    grab = False
    for line in m.group(1).splitlines():
        if line.startswith("description:"):
            grab = True
            out.append(line.partition(":")[2].strip().strip(">|-"))
        elif grab and line[:1] in (" ", "\t"):
            out.append(line.strip())
        elif grab:
            break
    return " ".join(out).strip()


def _phrases(text: str) -> list[str]:
    """Quoted trigger phrases ("trace this data flow") from a description."""
    return [c for c in (_clean(q) for q in _QUOTED_RE.findall(text or "")) if c]


def _clauses(text: str) -> list[str]:
    """Fallback phrases: 2-6 word clauses of an unquoted description."""
    out = []
    for part in re.split(r"[,.;:()]", text or ""):
        c = _clean(re.sub(r"^\s*(and|or|also)\s+", "", part))
        if c and len(c.split()) >= 2:
            out.append(c)
    return out


def _str(v: Any) -> str:
    return v if isinstance(v, str) else ""


def describe_plugin(found: dict[str, Any], scanned: dict[str, Any]) -> dict[str, Any]:
    manifest, surfaces = scanned["manifest"], scanned["surfaces"]
    interface = manifest.get("interface") if isinstance(manifest.get("interface"), dict) else {}
    desc = _str(manifest.get("description")) or _str(interface.get("shortDescription"))
    entry = {
        "kind": "plugin", **found, "summary": _clean_text(" ".join(desc.split()), SUMMARY_MAX),
        "use_when": [], **{k: sorted(surfaces[k]) for k in (*SURFACE_KINDS, "mcp_servers")},
    }
    if not entry["version"] and isinstance(manifest.get("version"), (str, int, float)):
        entry["version"] = _clean_text(manifest["version"], 40)
    # Triggers come only from frontmatter descriptions (allowlisted): quoted
    # phrases first, then short clauses of skill descriptions. Manifest
    # keywords are deliberately NOT copied (outside the field allowlist).
    skill_descs = [_description(p) for p in surfaces["skills"].values()]
    other_descs = [_description(p) for k in ("agents", "commands") for p in surfaces[k].values()]
    phrases: list[str] = []
    for d in skill_descs + other_descs:
        phrases.extend(_phrases(d))
    for d in skill_descs:
        phrases.extend(_clauses(d)[:2])
    seen: list[str] = []
    for p in phrases:
        if p not in seen:
            seen.append(p)
    entry["use_when"] = seen[:USE_WHEN_MAX]
    return entry


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def load_tool_decls(path: Path = TOOLS_FILE) -> list[dict[str, Any]]:
    data = _read_json(path, MAX_HOST_BYTES)
    tools = data.get("tools") if isinstance(data, dict) else None
    return [t for t in tools or [] if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]]


def describe_tools(decls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in decls:
        resolved = shutil.which(t["name"])
        uw = t.get("use_when") if isinstance(t.get("use_when"), list) else []
        out.append({
            "kind": "tool", "name": t["name"], "host": "path",
            "summary": _clean_text(_str(t.get("purpose")), SUMMARY_MAX),
            "use_when": [_clean_text(x, 80) for x in uw][:USE_WHEN_MAX],
            "present": resolved is not None, "path": resolved,
        })
    return out


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def compute_fingerprint(tools_file: Path | None = None) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    """Return (parts, scanned plugins, host status).

    Each plugin part covers EVERY file extraction reads (manifest, SKILL.md,
    agent/command files, MCP files) by size+mtime, so adding, editing or
    deleting any of them marks the index stale. A plugin that fails to scan
    gets an ``error:`` part and is listed under its host's ``skipped``.
    """
    tools_file = tools_file or TOOLS_FILE
    # The indexer itself is an input: a changed extractor must rebuild the cache.
    parts: dict[str, str] = {"tools-file": _stat_sig(tools_file), "indexer": _stat_sig(Path(__file__))}
    scanned: list[dict[str, Any]] = []
    hosts: dict[str, Any] = {}
    for fn in HOSTS:
        host = fn.__name__.replace("discover_", "")
        try:
            status, found = fn()
        except Exception as exc:  # noqa: BLE001 — one host never aborts the others
            hosts[host] = {"status": "unreadable", "reason": _err(exc)}
            parts[f"host:{host}"] = f"error:{type(exc).__name__}"
            continue
        hosts[host] = status
        parts[f"host:{host}"] = _stat_sig(Path(status["source"]))
        if status.get("settings"):
            parts[f"settings:{host}"] = _stat_sig(Path(status["settings"]))
        for f in found:
            # Scope identity is part of the key, so each installation record
            # of one plugin is tracked separately.
            key = f"plugin:{host}:{f['name']}@{f['marketplace']}#{f.get('scope', 'user')}:{f.get('project_path', '')}"
            # Version and install path are inputs too: a version swap that keeps
            # every file's size and mtime must still rebuild the cache.
            ident = f"{f.get('version')}|{f.get('install_path')}|{f.get('project_path', '')}"
            try:
                s = scan_plugin(f)
                parts[key] = f"{ident}|{s['sig']}"
                scanned.append({"found": f, **s})
            except Exception as exc:  # noqa: BLE001 — isolate one bad plugin
                parts[key] = f"{ident}|error:{type(exc).__name__}"
                _skip(status, f["name"], _err(exc))
    for t in load_tool_decls(tools_file):
        parts[f"tool:{t['name']}"] = shutil.which(t["name"]) or "absent"
    return parts, scanned, hosts


def _digest(parts: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Build / write / check
# ---------------------------------------------------------------------------

def serialize(index: dict[str, Any]) -> bytes:
    """The exact bytes write_atomic writes; the size cap is measured on these."""
    return json.dumps(index, indent=1).encode()


def _trim(index: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    """Shrink until the serialized index fits ``max_bytes``; always terminates under it.

    Order: component lists, triggers, summaries, the per-input fingerprint
    detail (``check`` then reports a bare mismatch), then whole entries from the
    end (tools before plugins), counted in ``dropped_entries``.
    """
    def fits() -> bool:
        return len(serialize(index)) <= max_bytes

    steps = []
    for limit in (40, 20, 8, 0):
        steps.append(lambda n=limit: [e.__setitem__(k, e[k][:n]) for e in index["entries"]
                                      for k in (*SURFACE_KINDS, "mcp_servers") if k in e])
    for n in (2, 0):
        steps.append(lambda n=n: [e.__setitem__("use_when", e["use_when"][:n]) for e in index["entries"]])
    for n in (60, 0):
        steps.append(lambda n=n: [e.__setitem__("summary", e["summary"][:n]) for e in index["entries"]])
    steps.append(lambda: index.__setitem__("fingerprint_parts", {}))
    steps.append(lambda: [h.pop("skipped", None) for h in index["hosts"].values()])
    for step in steps:
        if fits():
            return index
        index["trimmed"] = True
        step()
    while not fits() and index["entries"]:
        index["trimmed"] = True
        index["entries"].pop()
        index["dropped_entries"] = index.get("dropped_entries", 0) + 1
    if not fits():  # last resort: host status reduced to its counters
        index["hosts"] = {h: {k: v for k, v in s.items() if isinstance(v, (int, float))} | {"status": _clean_text(s.get("status", ""), 20)}
                          for h, s in index["hosts"].items()}
    return index


def build(tools_file: Path | None = None, max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    tools_file = tools_file or TOOLS_FILE
    t0 = time.monotonic()
    parts, scanned, hosts = compute_fingerprint(tools_file)
    entries = []
    for s in scanned:
        try:
            entries.append(describe_plugin(s["found"], s))
        except Exception as exc:  # noqa: BLE001 — isolate one bad plugin
            _skip(hosts[s["found"]["host"]], s["found"]["name"], _err(exc))
    for host, status in hosts.items():
        status["plugins"] = len({e["name"] for e in entries if e["host"] == host})
    entries.extend(describe_tools(load_tool_decls(tools_file)))
    index = {
        "schema_version": SCHEMA_VERSION,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "build_seconds": round(time.monotonic() - t0, 3),
        "hosts": hosts,
        "fingerprint": _digest(parts),
        "fingerprint_parts": parts,
        "trimmed": False,
        "entries": entries,
    }
    return _trim(index, max_bytes)


def write_atomic(index: dict[str, Any], out: Path, max_bytes: int = MAX_BYTES) -> int:
    """Write atomically; the final size check is on the exact bytes written."""
    out.parent.mkdir(parents=True, exist_ok=True)
    data = serialize(index)
    if len(data) > max_bytes:
        data = serialize(_trim(index, max_bytes))
    if len(data) > max_bytes:
        raise ValueError(f"plugin index is {len(data)} bytes after trimming; cap {max_bytes}")
    fd, tmp = tempfile.mkstemp(dir=str(out.parent), prefix=".plugin-index.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, out)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(data)


def load_index(out: Path) -> dict[str, Any] | None:
    """The cached index, or None when missing OR malformed (so callers rebuild)."""
    data = _read_json(out, 2 * MAX_BYTES)
    if not (isinstance(data, dict) and isinstance(data.get("entries"), list)
            and isinstance(data.get("fingerprint"), str)
            and isinstance(data.get("fingerprint_parts", {}), dict)
            and isinstance(data.get("hosts", {}), dict)):
        return None
    if not all(_valid_entry(e) for e in data["entries"]):
        return None
    return data


def _strs(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _valid_entry(e: Any) -> bool:
    """Every field query/shortlist touch has the type build() writes.

    A cache whose fingerprint still matches but whose entries were damaged
    (e.g. ``skills: 7``) is treated as malformed, so it is rebuilt instead of
    raising TypeError on every shortlist call.
    """
    if not (isinstance(e, dict) and isinstance(e.get("name"), str) and isinstance(e.get("host"), str)
            and isinstance(e.get("summary", ""), str) and _strs(e.get("use_when", []))):
        return False
    if e.get("kind") == "tool":
        return isinstance(e.get("present"), bool) and isinstance(e.get("path"), (str, type(None)))
    if e.get("kind") == "plugin":
        return (isinstance(e.get("install_path"), (str, type(None)))
                and isinstance(e.get("scope", "user"), str) and isinstance(e.get("project_path", ""), str)
                and all(_strs(e.get(k)) for k in (*SURFACE_KINDS, "mcp_servers")))
    return False


def staleness(out: Path, tools_file: Path | None = None) -> list[str]:
    """Return reasons the cached index is stale; empty list means fresh."""
    tools_file = tools_file or TOOLS_FILE
    index = load_index(out)
    if index is None:
        return [f"index missing, unreadable or malformed: {out}"]
    if index.get("schema_version") != SCHEMA_VERSION:
        return [f"schema {index.get('schema_version')} != {SCHEMA_VERSION}"]
    parts, _, _ = compute_fingerprint(tools_file)
    if index.get("fingerprint") == _digest(parts):
        return []
    old = index.get("fingerprint_parts") or {}
    reasons = [f"changed: {k}" for k in sorted(set(old) | set(parts)) if old and old.get(k) != parts.get(k)]
    return reasons or ["fingerprint mismatch"]


def ensure_fresh(out: Path, tools_file: Path | None = None) -> dict[str, Any]:
    tools_file = tools_file or TOOLS_FILE
    if staleness(out, tools_file):
        index = build(tools_file)
        write_atomic(index, out)
        return index
    return load_index(out) or {}


def live_surfaces(entry: dict[str, Any]) -> dict[str, list[str]] | None:
    """Re-scan a plugin on disk; None when it can no longer be scanned."""
    try:
        s = scan_plugin({k: entry.get(k) for k in ("host", "name", "marketplace", "version", "install_path")})
    except Exception:  # noqa: BLE001
        return None
    return {k: sorted(s["surfaces"][k]) for k in (*SURFACE_KINDS, "mcp_servers")}


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def _tokens(text: str) -> set[str]:
    return {t.rstrip("s") if len(t) > 4 else t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 3 and t not in _STOP}


def _match(q: str, field: set[str]) -> bool:
    """Exact token match, or a shared stem of 6+ chars (architect ~ architecture)."""
    if q in field:
        return True
    return len(q) >= 6 and any(len(f) >= 6 and (f.startswith(q) or q.startswith(f)) for f in field)


def _hits(field: set[str], qtok: set[str]) -> int:
    return sum(1 for q in qtok if _match(q, field))


def _invoke(entry: dict[str, Any], qtok: set[str]) -> dict[str, Any]:
    if entry["kind"] == "tool":
        return {"binary": entry["name"], "path": entry.get("path")}
    best: tuple[int, int, str, str] | None = None
    for rank, kind in enumerate(("skills", "agents", "commands", "mcp_servers")):
        for n in entry.get(kind) or []:
            hit = _hits(_tokens(n.replace("-", " ")), qtok)
            # Most query hits, then skill > agent > command > mcp, then the
            # plugin's namesake surface, then the shortest name.
            cand = (-hit, rank, n != entry["name"], len(n), kind, n)
            if best is None or cand < best:
                best = cand
    if best is None:
        return {"plugin": entry["name"], "host": entry["host"]}
    kind = best[4][:-1] if best[4] != "mcp_servers" else "mcp_server"
    name = best[5]
    if entry["host"] == "claude" and kind in ("skill", "agent", "command"):
        name = f"{entry['name']}:{name}"
    return {kind: name, "plugin": entry["name"], "host": entry["host"]}


def score(entry: dict[str, Any], qtok: set[str]) -> int:
    fields = (
        (4, entry.get("name", "").replace("-", " ")),
        (3, " ".join(n.replace("-", " ") for k in ("skills", "agents", "commands", "mcp_servers") for n in entry.get(k) or [])),
        (2, " ".join(entry.get("use_when") or [])),
        (1, entry.get("summary", "")),
    )
    toks = [(w, _tokens(text)) for w, text in fields]
    return sum(max((w for w, ft in toks if _match(t, ft)), default=0) for t in qtok)


def _in_scope(e: dict[str, Any], workdir: Path) -> bool:
    """A project-scope install is callable only inside its own project."""
    if e.get("scope") != "project":
        return True
    # Directory identity, not strings: case aliases on a case-insensitive
    # filesystem, symlinks and trailing slashes all name the same directory.
    pp = e.get("project_path")
    if not isinstance(pp, str) or not pp:
        return False
    try:
        return os.path.samefile(workdir, pp)
    except (OSError, ValueError):
        return False


def query(index: dict[str, Any], words: str, host: str | None = None, cap: int = QUERY_CAP,
          include_surfaces: bool = False, workdir: Path | None = None) -> list[dict[str, Any]]:
    """Best-first matches; drops entries whose files are gone (stderr note).

    ``host`` keeps only that host's plugins (tools are host-neutral) and is
    applied BEFORE cross-host de-duplication, so the consuming host's own
    install wins. ``workdir`` (default cwd) decides project-scope installs.
    ``include_surfaces`` adds the plugin's skill/agent/command/MCP names so a
    caller can rescore with its own rule (capability_shortlist does).
    """
    qtok = _tokens(words)
    wd = workdir or Path.cwd()
    scored = []
    for e in index.get("entries") or []:
        if host and e["kind"] == "plugin" and e.get("host") != host:
            continue
        if e["kind"] == "plugin" and not _in_scope(e, wd):
            continue
        if e["kind"] == "tool" and not e.get("present"):
            continue
        s = score(e, qtok)
        if s > 0:
            scored.append((s, e))
    scored.sort(key=lambda t: (-t[0], t[1]["kind"] != "plugin", t[1].get("host", ""), t[1]["name"]))
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for s, e in scored:
        key = (e["kind"], e["name"])
        if key in seen:  # same plugin on two hosts: keep the better-ranked one
            continue
        target = e.get("path") if e["kind"] == "tool" else e.get("install_path")
        if not target or not Path(target).exists():
            print(f"plugin_index: dropped {e['kind']} {e['name']} ({e.get('host')}): {target} no longer exists",
                  file=sys.stderr)
            continue
        if e["kind"] == "plugin":
            # Verify the specific surfaces on disk: never hand back a skill,
            # agent, command or MCP server whose file has gone.
            live = live_surfaces(e)
            if live is None:
                print(f"plugin_index: dropped plugin {e['name']} ({e.get('host')}): can no longer be scanned",
                      file=sys.stderr)
                continue
            gone = sorted(n for k in live for n in set(e.get(k) or []) - set(live[k]))
            if gone:
                print(f"plugin_index: {e['name']} ({e.get('host')}): ignoring removed {', '.join(gone)}",
                      file=sys.stderr)
            e = {**e, **live}
        seen.add(key)
        out.append({
            "kind": e["kind"], "name": e["name"], "host": e.get("host"), "score": s,
            "summary": e.get("summary", ""), "use_when": e.get("use_when", []),
            "invoke": _invoke(e, qtok),
        })
        if include_surfaces:
            out[-1]["surfaces"] = [n for k in ("skills", "agents", "commands", "mcp_servers") for n in e.get(k) or []]
        if len(out) >= cap:
            break
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "check", "query", "show"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=Path, default=None, help="index path (default ~/.build-loop/plugin-index.json)")
        p.add_argument("--tools-file", type=Path, default=TOOLS_FILE)
        if name == "query":
            p.add_argument("words", nargs="+")
            p.add_argument("--host", choices=["claude", "codex"], default=None)
    args = ap.parse_args(argv)
    out = args.out or default_index_path()

    if args.cmd == "build":
        index = build(args.tools_file)
        size = write_atomic(index, out)
        per_host = {h: s.get("plugins", 0) if s["status"] == "found" else "not found" for h, s in index["hosts"].items()}
        tools = [e for e in index["entries"] if e["kind"] == "tool"]
        print(json.dumps({
            "out": str(out), "bytes": size, "build_seconds": index["build_seconds"],
            "plugins_by_host": per_host, "trimmed": index["trimmed"],
            "tools_present": [t["name"] for t in tools if t["present"]],
            "tools_absent": [t["name"] for t in tools if not t["present"]],
        }, indent=2))
        return 0
    if args.cmd == "check":
        reasons = staleness(out, args.tools_file)
        if reasons:
            print("stale:\n  " + "\n  ".join(reasons[:20]))
            return EXIT_STALE
        print(f"fresh: {out}")
        return 0
    if args.cmd == "query":
        index = ensure_fresh(out, args.tools_file)
        print(json.dumps(query(index, " ".join(args.words), args.host), indent=2))
        return 0
    index = load_index(out)
    if index is None:
        print(f"no index at {out}; run build", file=sys.stderr)
        return EXIT_STALE
    for e in index["entries"]:
        if e["kind"] == "tool":
            state = e["path"] if e["present"] else "absent"
            print(f"tool    {'path':6} {e['name']:28} {state}")
        else:
            n = f"s{len(e['skills'])} a{len(e['agents'])} c{len(e['commands'])} m{len(e['mcp_servers'])}"
            print(f"plugin  {e['host']:6} {e['name']:28} {e.get('version', '')[:12]:12} {n:16} {e['summary'][:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
