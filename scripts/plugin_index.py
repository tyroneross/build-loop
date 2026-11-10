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
manifest keywords, then short clauses of skill descriptions), and the NAMES of its skills, agents, commands and MCP servers.
Bodies, MCP args/env and every other manifest field are never copied.

Hosts (missing files mean "not found", never an error):
  claude  $CLAUDE_CONFIG_DIR or ~/.claude -> plugins/installed_plugins.json
  codex   $CODEX_HOME or ~/.codex -> config.toml [plugins."name@marketplace"]
          headers + ``enabled``; files under plugins/cache/<marketplace>/<name>/<version>/

Freshness without maintenance: the index stores a fingerprint of each host's
installed-plugin file (size+mtime), each plugin manifest (size+mtime), the
tool declaration file, and each declared tool's ``shutil.which`` result.
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
import sys
import tempfile
import time
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
_CODEX_HEADER_RE = re.compile(r'^\[plugins\."([^"@]+)@([^"]+)"\]\s*$')
_ENABLED_RE = re.compile(r"^enabled\s*=\s*(true|false)\s*$")


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


def _stat_sig(p: Path) -> str:
    try:
        st = p.stat()
        return f"{st.st_size}:{int(st.st_mtime_ns)}"
    except OSError:
        return "missing"


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Host discovery -> list of (host, name, marketplace, version, install_path)
# ---------------------------------------------------------------------------

def discover_claude() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    src = claude_root() / "plugins" / "installed_plugins.json"
    data = _read_json(src)
    if not isinstance(data, dict):
        return {"status": "not found", "source": str(src)}, []
    found: list[dict[str, Any]] = []
    plugins = data.get("plugins") if isinstance(data.get("plugins"), dict) else data
    for key, installs in sorted(plugins.items()):
        if not isinstance(installs, list) or not installs or "@" not in key:
            continue
        rec = installs[-1] if isinstance(installs[-1], dict) else {}
        name, _, market = key.partition("@")
        path = rec.get("installPath")
        found.append({
            "host": "claude", "name": name, "marketplace": market,
            "version": str(rec.get("version") or ""),
            "install_path": str(path) if path else None,
        })
    return {"status": "found", "source": str(src)}, found


def discover_codex() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read only plugin section headers and their ``enabled`` line — no other config."""
    root = codex_root()
    src = root / "config.toml"
    try:
        lines = src.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {"status": "not found", "source": str(src)}, []
    declared: list[tuple[str, str, bool]] = []
    current: list[Any] | None = None
    for line in lines:
        s = line.strip()
        m = _CODEX_HEADER_RE.match(s)
        if m:
            current = [m.group(1), m.group(2), True]
            declared.append(current)  # type: ignore[arg-type]
            continue
        if s.startswith("["):
            current = None
            continue
        if current is not None:
            e = _ENABLED_RE.match(s)
            if e:
                current[2] = e.group(1) == "true"
    found: list[dict[str, Any]] = []
    no_local = 0
    for name, market, enabled in declared:
        if not enabled:
            continue
        base = root / "plugins" / "cache" / market / name
        versions = [d for d in base.iterdir() if d.is_dir()] if base.is_dir() else []
        if not versions:
            no_local += 1  # remote/app connector: nothing on disk to index
            continue
        newest = max(versions, key=lambda d: d.stat().st_mtime)
        found.append({
            "host": "codex", "name": name, "marketplace": market,
            "version": newest.name, "install_path": str(newest),
        })
    return {"status": "found", "source": str(src), "enabled_without_local_files": no_local}, found


HOSTS = (discover_claude, discover_codex)


# ---------------------------------------------------------------------------
# Per-plugin extraction (names + short metadata only)
# ---------------------------------------------------------------------------

MANIFEST_CANDIDATES = (".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "plugin.json")


def manifest_path(install: Path) -> Path | None:
    for rel in MANIFEST_CANDIDATES:
        p = install / rel
        if p.is_file():
            return p
    return None


def _description(md: Path) -> str:
    try:
        with md.open(encoding="utf-8") as fh:
            head = fh.read(8192)  # frontmatter only; bodies are never read in full
    except (OSError, UnicodeDecodeError):
        return ""
    m = _FRONTMATTER_RE.match(head)
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


def _mcp_names(install: Path, manifest: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    inline = manifest.get("mcpServers")
    if isinstance(inline, dict):
        names.update(inline)
    for rel in (".mcp.json", "mcp.json"):
        data = _read_json(install / rel)
        if isinstance(data, dict):
            servers = data.get("mcpServers", data)
            if isinstance(servers, dict):
                names.update(k for k, v in servers.items() if isinstance(v, dict))
    return sorted(names)


_CLEAN_PHRASE_RE = re.compile(r"^[a-z0-9][a-z0-9 '\-/?]*$")


def _clean(p: str) -> str | None:
    p = " ".join(p.lower().split()).strip(" .,;:!")
    if _CLEAN_PHRASE_RE.match(p) and 1 <= len(p.split()) <= 6:
        return p
    return None


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


def describe_plugin(found: dict[str, Any]) -> dict[str, Any]:
    install = Path(found["install_path"]) if found.get("install_path") else None
    entry = {
        "kind": "plugin", **found, "summary": "", "use_when": [],
        "skills": [], "agents": [], "commands": [], "mcp_servers": [],
    }
    if install is None or not install.is_dir():
        entry["error"] = "install path missing"
        return entry
    mpath = manifest_path(install)
    manifest = _read_json(mpath) if mpath else None
    manifest = manifest if isinstance(manifest, dict) else {}
    desc = manifest.get("description") or (manifest.get("interface") or {}).get("shortDescription") or ""
    entry["summary"] = " ".join(str(desc).split())[:SUMMARY_MAX]
    if not entry["version"] and manifest.get("version"):
        entry["version"] = str(manifest["version"])

    descs: list[str] = []
    skills_dir = install / "skills"
    if skills_dir.is_dir():
        for d in sorted(skills_dir.iterdir()):
            if (d / "SKILL.md").is_file():
                entry["skills"].append(d.name)
                descs.append(_description(d / "SKILL.md"))
    for kind in ("agents", "commands"):
        sub = install / kind
        if sub.is_dir():
            for md in sorted(sub.glob("*.md")):
                entry[kind].append(md.stem)
                descs.append(_description(md))
    entry["mcp_servers"] = _mcp_names(install, manifest)

    # Priority: quoted triggers > manifest keywords > clauses of skill descriptions.
    phrases: list[str] = []
    for d in descs:
        phrases.extend(_phrases(d))
    keywords = [c for c in (_clean(str(k)) for k in manifest.get("keywords") or []) if c]
    clauses: list[str] = []
    for d in descs[: len(entry["skills"])]:
        clauses.extend(_clauses(d)[:2])
    seen: list[str] = []
    for p in phrases + keywords + clauses:
        if p not in seen:
            seen.append(p)
    entry["use_when"] = seen[:USE_WHEN_MAX]
    return entry


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def load_tool_decls(path: Path = TOOLS_FILE) -> list[dict[str, Any]]:
    data = _read_json(path)
    tools = data.get("tools") if isinstance(data, dict) else None
    return [t for t in tools or [] if isinstance(t, dict) and t.get("name")]


def describe_tools(decls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in decls:
        resolved = shutil.which(t["name"])
        out.append({
            "kind": "tool", "name": t["name"], "host": "path",
            "summary": str(t.get("purpose", ""))[:SUMMARY_MAX],
            "use_when": list(t.get("use_when") or [])[:USE_WHEN_MAX],
            "present": resolved is not None, "path": resolved,
        })
    return out


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def compute_fingerprint(tools_file: Path = TOOLS_FILE) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    """Return (parts, discovered plugins, host status). Stats only; no bodies read."""
    # The indexer itself is an input: a changed extractor must rebuild the cache.
    parts: dict[str, str] = {"tools-file": _stat_sig(tools_file), "indexer": _stat_sig(Path(__file__))}
    discovered: list[dict[str, Any]] = []
    hosts: dict[str, Any] = {}
    for fn in HOSTS:
        status, found = fn()
        host = fn.__name__.replace("discover_", "")
        hosts[host] = status
        parts[f"host:{host}"] = _stat_sig(Path(status["source"]))
        for f in found:
            mp = manifest_path(Path(f["install_path"])) if f.get("install_path") else None
            parts[f"manifest:{host}:{f['name']}@{f['marketplace']}"] = (
                _stat_sig(mp) if mp else f"no-manifest:{f.get('install_path')}"
            )
        discovered.extend(found)
    for t in load_tool_decls(tools_file):
        parts[f"tool:{t['name']}"] = shutil.which(t["name"]) or "absent"
    return parts, discovered, hosts


def _digest(parts: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Build / write / check
# ---------------------------------------------------------------------------

def _trim(index: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    for limit in (40, 20, 8, 0):
        if len(json.dumps(index).encode()) <= max_bytes:
            return index
        index["trimmed"] = True
        for e in index["entries"]:
            for k in ("skills", "agents", "commands", "mcp_servers"):
                if k in e and len(e[k]) > limit:
                    e[k] = e[k][:limit]
    return index


def build(tools_file: Path = TOOLS_FILE, max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    t0 = time.monotonic()
    parts, discovered, hosts = compute_fingerprint(tools_file)
    entries = [describe_plugin(f) for f in discovered]
    for host, status in hosts.items():
        status["plugins"] = sum(1 for e in entries if e["host"] == host)
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


def write_atomic(index: dict[str, Any], out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(index, indent=1, sort_keys=False).encode()
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
    data = _read_json(out)
    return data if isinstance(data, dict) and isinstance(data.get("entries"), list) else None


def staleness(out: Path, tools_file: Path = TOOLS_FILE) -> list[str]:
    """Return reasons the cached index is stale; empty list means fresh."""
    index = load_index(out)
    if index is None:
        return [f"index missing or unreadable: {out}"]
    if index.get("schema_version") != SCHEMA_VERSION:
        return [f"schema {index.get('schema_version')} != {SCHEMA_VERSION}"]
    parts, _, _ = compute_fingerprint(tools_file)
    if index.get("fingerprint") == _digest(parts):
        return []
    old = index.get("fingerprint_parts") or {}
    reasons = [f"changed: {k}" for k in sorted(set(old) | set(parts)) if old.get(k) != parts.get(k)]
    return reasons or ["fingerprint mismatch"]


def ensure_fresh(out: Path, tools_file: Path = TOOLS_FILE) -> dict[str, Any]:
    if staleness(out, tools_file):
        index = build(tools_file)
        write_atomic(index, out)
        return index
    return load_index(out) or {}


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


def query(index: dict[str, Any], words: str, host: str | None = None, cap: int = QUERY_CAP,
          include_surfaces: bool = False) -> list[dict[str, Any]]:
    """Best-first matches; drops entries whose files are gone (stderr note).

    ``include_surfaces`` adds the plugin's skill/agent/command/MCP names so a
    caller can rescore with its own rule (capability_shortlist does).
    """
    qtok = _tokens(words)
    scored = []
    for e in index.get("entries") or []:
        if host and e["kind"] == "plugin" and e.get("host") != host:
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
