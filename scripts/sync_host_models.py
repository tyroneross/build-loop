#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Read the model lists each coding CLI already caches (Codex, Claude Code, Cursor) and report models the taxonomy does not know or routes an older version of.
#   application: meta
#   status: active
"""Host model sync — compare what each CLI offers against the model taxonomy.

WHY. ``references/model-taxonomy.json`` routed Codex to gpt-5.6-sol for weeks
after gpt-6.1-sol shipped, and knew only grok-4.6 while Cursor offered
grok-4.7. Every CLI already caches its own model list; nothing read it.

Sources (all read-only):

  * Codex       ``~/.codex/models_cache.json`` — ``models[].slug``,
                ``display_name``, ``supported_reasoning_levels[].effort``.
                The ``identity`` and ``etag`` fields are never accessed.
  * Claude Code ``~/.claude/cache/model-catalog/*.json`` — only model
                ``id``/``name`` and ``fetchedAt``/``staleAt``. File names are
                reported as a hash, never verbatim.
  * Cursor      ``cursor-agent --list-models`` (network; 15 s timeout). A
                timeout or failure means "source unavailable", never "no
                models".
  * Gemini CLI, OpenCode: no cache found; reported as unsupported.

Subcommands::

    sync_host_models.py collect              # write the snapshot
    sync_host_models.py diff [--json] [--report-file PATH]
    sync_host_models.py apply --in-family [--taxonomy PATH] [--dry-run]
    sync_host_models.py refresh              # collect + diff --report-file (used by the detached trigger)

The snapshot and reports live in ``~/.build-loop/host-models/`` (override with
``BUILD_LOOP_HOST_MODELS_DIR``), never inside a repository.

``apply`` adds ``candidate-unverified`` entries but never routes them: the
resolver does not filter on status, so a routed candidate would be dispatched
immediately with no release date or verification. See ``apply_in_family``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import host_model_map  # noqa: E402
import model_taxonomy  # noqa: E402

SCHEMA_VERSION = 1
DIR_ENV = "BUILD_LOOP_HOST_MODELS_DIR"
CODEX_CACHE_ENV = "BUILD_LOOP_CODEX_MODELS_CACHE"
CLAUDE_CATALOG_ENV = "BUILD_LOOP_CLAUDE_MODEL_CATALOG_DIR"
CURSOR_BIN_ENV = "BUILD_LOOP_CURSOR_AGENT_BIN"
TRIGGER_ENV = "BUILD_LOOP_HOST_SYNC"  # "0"/"off" disables the automatic refresh

CURSOR_TIMEOUT_S = 15.0
STALE_AFTER_S = 7 * 24 * 3600
TRIGGER_INTERVAL_S = 3600

SNAPSHOT_NAME = "snapshot.json"
REPORT_MD_NAME = "report.md"
REPORT_JSON_NAME = "report.json"
STAMP_NAME = ".refresh-stamp"

UNSUPPORTED_HOSTS = ("gemini", "opencode")


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def models_dir() -> Path:
    raw = os.environ.get(DIR_ENV, "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".build-loop" / "host-models"


def codex_cache_path() -> Path:
    raw = os.environ.get(CODEX_CACHE_ENV, "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".codex" / "models_cache.json"


def claude_catalog_dir() -> Path:
    raw = os.environ.get(CLAUDE_CATALOG_ENV, "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".claude" / "cache" / "model-catalog"


def _display(path: Path) -> str:
    """Home-relative path for reports (no user name in output)."""
    try:
        return "~/" + str(path.resolve().relative_to(Path.home().resolve()))
    except ValueError:
        return str(path)


def _hashed_name(path: Path) -> str:
    """Claude catalog file names may carry token-like prefixes: never print them."""
    return hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:12] + path.suffix


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: Any) -> float | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _ms_to_iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)) or isinstance(ms, bool):
        return None
    return _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Taxonomy access
# --------------------------------------------------------------------------

def load_taxonomy(path: Path | None = None) -> dict[str, Any]:
    """The real taxonomy via model_taxonomy, or an explicit file (tests, apply)."""
    if path is None:
        return model_taxonomy.taxonomy()
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _models(tax: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        k: v for k, v in tax.get("models", {}).items()
        if not k.startswith("_") and isinstance(v, dict)
    }


def _preferred_ids(tax: dict[str, Any]) -> list[str]:
    """Every id in a preferred cell (same filtering as model_taxonomy.preferred)."""
    out: list[str] = []
    for segment, cells in tax.get("preferred", {}).items():
        if segment.startswith("_") or not isinstance(cells, dict):
            continue
        for rung, ids in cells.items():
            if rung.startswith("_") or not isinstance(ids, list):
                continue
            out.extend(str(i) for i in ids)
    return out


# --------------------------------------------------------------------------
# Families + versions
# --------------------------------------------------------------------------

_CLAUDE_ROLE_FIRST = re.compile(r"^claude-([a-z]+)-(\d+(?:-\d+)*)$")
_CLAUDE_VERSION_FIRST = re.compile(r"^claude-(\d+(?:\.\d+)*)-([a-z]+)$")
_GENERIC = re.compile(r"^([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*?)-(\d+(?:\.\d+)*)(?:-([a-z][a-z0-9.-]*))?$")
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def family_of(model_id: str | None) -> tuple[str, tuple[int, ...]] | None:
    """(family, numeric version) for a model id, or None when unparseable.

    Family = provider line + role word: ``gpt-6.1-sol`` -> ("gpt-*-sol", (6, 1)),
    ``grok-4.7`` -> ("grok-*", (4, 7)), ``claude-opus-5-5`` -> ("claude-opus",
    (5, 5)). Versions compare as integer tuples, so 6.1 > 6 > 5.6.
    """
    raw = (model_id or "").strip().lower().split("[", 1)[0]
    if not raw:
        return None
    if raw.startswith("claude-"):
        stem = _DATE_SUFFIX.sub("", raw)
        m = _CLAUDE_ROLE_FIRST.match(stem)
        if m:
            return f"claude-{m.group(1)}", tuple(int(x) for x in m.group(2).split("-"))
        m = _CLAUDE_VERSION_FIRST.match(stem)
        if m:
            return f"claude-{m.group(2)}", tuple(int(x) for x in m.group(1).split("."))
        return None
    m = _GENERIC.match(raw)
    if not m:
        return None
    prefix, version, role = m.group(1), m.group(2), m.group(3)
    family = f"{prefix}-*-{role}" if role else f"{prefix}-*"
    return family, tuple(int(x) for x in version.split("."))


def _ver_str(version: tuple[int, ...]) -> str:
    return ".".join(str(v) for v in version)


def routed_families(tax: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Families the taxonomy routes, with the newest routed member.

    A preferred id with a version (``gpt-6.1-sol``) routes its own family. A
    preferred id without one (``opus``) routes through its auto-tracking
    aliases; those families are marked ``auto_alias`` and are reported, never
    changed.
    """
    models = _models(tax)
    out: dict[str, dict[str, Any]] = {}

    def note(family: str, version: tuple[int, ...], routed_id: str, auto_alias: bool) -> None:
        row = out.get(family)
        if row is None or version > row["version"]:
            out[family] = {"version": version, "predecessor": routed_id, "auto_alias": auto_alias}

    for mid in dict.fromkeys(_preferred_ids(tax)):
        fam = family_of(mid)
        if fam:
            note(fam[0], fam[1], mid, False)
            continue
        meta = models.get(mid) or {}
        for alias in meta.get("aliases") or []:
            afam = family_of(str(alias))
            if afam:
                note(afam[0], afam[1], mid, True)
    return out


def known_family_versions(tax: dict[str, Any]) -> dict[str, tuple[tuple[int, ...], str]]:
    """{family: (newest known version, id)} over every taxonomy id and alias."""
    out: dict[str, tuple[tuple[int, ...], str]] = {}
    for mid, meta in _models(tax).items():
        for name in [mid, *(str(a) for a in (meta.get("aliases") or []))]:
            fam = family_of(name)
            if fam and (fam[0] not in out or fam[1] > out[fam[0]][0]):
                out[fam[0]] = (fam[1], mid)
    return out


# --------------------------------------------------------------------------
# Collect
# --------------------------------------------------------------------------

def _source(host: str, kind: str, ref: str, status: str = "ok", reason: str | None = None) -> dict[str, Any]:
    return {
        "host": host,
        "source_kind": kind,
        "source_ref": ref,
        "status": status,
        "reason": reason,
        "fetched_at": None,
        "models": [],
    }


def _model_row(slug: str, models: dict[str, dict[str, Any]], **extra: Any) -> dict[str, Any]:
    canonical = host_model_map.canonical_for_slug(slug, models)
    row = {
        "slug": slug,
        "canonical_family_id": canonical or slug,
        "known": canonical is not None,
        "efforts": [],
    }
    row.update(extra)
    return row


def collect_codex(path: Path | None = None, models: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    path = path or codex_cache_path()
    models = models if models is not None else _models(load_taxonomy())
    src = _source("codex", "cache-file", _display(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        src.update(status="unavailable", reason="cache file not found")
        return src
    except (OSError, json.JSONDecodeError) as exc:
        src.update(status="unavailable", reason=f"unreadable: {type(exc).__name__}")
        return src
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        src.update(status="unavailable", reason="unexpected shape")
        return src
    # Only these keys are read. identity/etag are never accessed.
    src["fetched_at"] = data.get("fetched_at") if isinstance(data.get("fetched_at"), str) else None
    if isinstance(data.get("client_version"), str):
        src["client_version"] = data["client_version"]
    for item in data["models"]:
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
            continue
        efforts = [
            str(lvl.get("effort"))
            for lvl in item.get("supported_reasoning_levels") or []
            if isinstance(lvl, dict) and lvl.get("effort")
        ]
        row = _model_row(
            item["slug"].strip().lower(), models,
            name=item.get("display_name") if isinstance(item.get("display_name"), str) else None,
            hidden=item.get("visibility") == "hide",
        )
        row["efforts"] = efforts
        src["models"].append(row)
    return src


def collect_claude(directory: Path | None = None, models: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    directory = directory or claude_catalog_dir()
    models = models if models is not None else _models(load_taxonomy())
    files = sorted(Path(p) for p in glob.glob(str(directory / "*.json")))
    src = _source(
        "claude", "cache-file",
        [f"{_display(directory)}/{_hashed_name(p)}" for p in files] or _display(directory),
    )
    if not files:
        src.update(status="unavailable", reason="no catalog files")
        return src
    fetched: list[float] = []
    stale: list[float] = []
    by_id: dict[str, dict[str, Any]] = {}
    readable = 0
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        readable += 1
        # Only fetchedAt / staleAt and catalog.config.models[].{id,name} are read.
        for key, bucket in (("fetchedAt", fetched), ("staleAt", stale)):
            val = data.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                bucket.append(float(val))
        catalog = data.get("catalog")
        config = catalog.get("config") if isinstance(catalog, dict) else None
        entries = config.get("models") if isinstance(config, dict) else None
        for item in entries if isinstance(entries, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            mid = item["id"].strip().lower()
            if mid not in by_id:
                by_id[mid] = _model_row(
                    mid, models,
                    name=item.get("name") if isinstance(item.get("name"), str) else None,
                )
    if not readable:
        src.update(status="unavailable", reason="catalog files unreadable")
        return src
    src["fetched_at"] = _ms_to_iso(max(fetched)) if fetched else None
    src["stale_at"] = _ms_to_iso(max(stale)) if stale else None
    src["models"] = list(by_id.values())
    return src


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_CURSOR_LINE = re.compile(r"^\s*([a-z0-9][a-z0-9._\[\]=-]*)\s+-\s+(\S.*?)\s*$", re.IGNORECASE)


def parse_cursor_list(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in _ANSI.sub("", text).splitlines():
        m = _CURSOR_LINE.match(line)
        if m:
            out.append((m.group(1).lower(), m.group(2)))
    return out


def _run_on_pty(argv: list[str], timeout: float) -> tuple[int, str]:
    """Run ``argv`` with stdout on a pseudo-terminal; return (exit code, text).

    Measured 2026-11-01: ``cursor-agent --list-models`` prints 15 lines to a
    pipe but 276 to a terminal, so a pipe silently drops most models. stdin is
    /dev/null. On timeout the whole process group is killed and
    ``subprocess.TimeoutExpired`` is raised.
    """
    import fcntl  # noqa: PLC0415
    import pty  # noqa: PLC0415
    import select  # noqa: PLC0415
    import struct  # noqa: PLC0415
    import termios  # noqa: PLC0415

    master, slave = pty.openpty()
    try:
        # Wide window so the CLI never truncates a "<slug> - <Name>" line.
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 500, 400, 0, 0))
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=slave, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
    except OSError:
        os.close(master)
        os.close(slave)
        raise
    os.close(slave)
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    proc.kill()
                proc.wait(timeout=2)
                raise subprocess.TimeoutExpired(argv, timeout)
            ready, _, _ = select.select([master], [], [], min(remaining, 0.5))
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            try:
                data = os.read(master, 65536)
            except OSError:  # EIO: the child closed the terminal
                break
            if not data:
                break
            chunks.append(data)
        rc = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        raise
    finally:
        os.close(master)
    return rc, b"".join(chunks).decode("utf-8", errors="replace")


def collect_cursor(
    binary: str | None = None,
    models: dict[str, dict[str, Any]] | None = None,
    timeout: float = CURSOR_TIMEOUT_S,
) -> dict[str, Any]:
    models = models if models is not None else _models(load_taxonomy())
    binary = binary or os.environ.get(CURSOR_BIN_ENV, "").strip() or shutil.which("cursor-agent")
    src = _source("cursor", "cli-command", "cursor-agent --list-models")
    if not binary:
        src.update(status="unavailable", reason="cursor-agent not found")
        return src
    try:
        rc, out = _run_on_pty([binary, "--list-models"], timeout)
    except OSError as exc:
        src.update(status="unavailable", reason=f"could not start: {type(exc).__name__}")
        return src
    except subprocess.TimeoutExpired:
        src.update(status="unavailable", reason=f"timed out after {timeout:g}s")
        return src
    if rc != 0:
        src.update(status="unavailable", reason=f"exit status {rc}")
        return src
    lines = parse_cursor_list(out or "")
    if not lines:
        src.update(status="unavailable", reason="no model lines parsed")
        return src
    src["fetched_at"] = _now_iso()
    by_base: dict[str, dict[str, Any]] = {}
    for slug, name in lines:
        parts = host_model_map.split_host_slug(slug)
        base = parts["stem"] or slug
        row = by_base.get(base)
        if row is None:
            row = by_base[base] = _model_row(base, models, name=name, variants=[], host_slugs=[])
        if parts["effort"] and parts["effort"] not in row["efforts"]:
            row["efforts"].append(parts["effort"])
        if parts["variant"] and parts["variant"] not in row["variants"]:
            row["variants"].append(parts["variant"])
        row["host_slugs"].append(slug)
    src["models"] = list(by_base.values())
    return src


def collect(
    *,
    taxonomy_path: Path | None = None,
    codex_path: Path | None = None,
    claude_dir: Path | None = None,
    cursor_bin: str | None = None,
    out_dir: Path | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Read every source and (by default) write the snapshot atomically."""
    models = _models(load_taxonomy(taxonomy_path))
    out_dir = out_dir or models_dir()
    previous = read_snapshot(out_dir) or {}
    first_seen: dict[tuple[str, str], str] = {}
    for src in previous.get("sources", []) if isinstance(previous, dict) else []:
        for row in src.get("models", []) if isinstance(src, dict) else []:
            if isinstance(row, dict) and row.get("first_seen"):
                first_seen[(src.get("host"), row.get("slug"))] = row["first_seen"]

    sources = [
        collect_codex(codex_path, models),
        collect_claude(claude_dir, models),
        collect_cursor(cursor_bin, models),
    ]
    sources.extend(
        _source(h, "none", "", status="unsupported", reason="unsupported source (not checked)")
        for h in UNSUPPORTED_HOSTS
    )
    today = _dt.date.today().isoformat()
    for src in sources:
        for row in src["models"]:
            row["first_seen"] = first_seen.get((src["host"], row["slug"]), today)
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "collected_at": _now_iso(),
        "sources": sources,
    }
    if write:
        _atomic_write(out_dir / SNAPSHOT_NAME, json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    return snapshot


def _atomic_write(path: Path, text: str) -> None:
    from atomic_io import atomic_write_bytes  # noqa: PLC0415 - keep resolve's import cheap

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_bytes(path, text.encode("utf-8"), mode=0o600)


def read_snapshot(directory: Path | None = None) -> dict[str, Any] | None:
    path = (directory or models_dir()) / SNAPSHOT_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------

def diff(snapshot: dict[str, Any], tax: dict[str, Any]) -> dict[str, Any]:
    """Classify every host model against the taxonomy.

    * ``unknown``           every base id no taxonomy id/alias matches.
    * ``newer_in_family``   unknown + newer than the routed member of a routed
                            (non-Claude) family — ``apply --in-family`` input.
    * ``informational``     unknown + newer than a Claude family routed through
                            an auto-tracking alias (opus/sonnet/haiku/fable).
    * ``new_family``        unknown + newer than anything known in a family the
                            taxonomy does not route — needs a tier decision.
    * ``older_unregistered``  unknown but not newer than a known member.
    * ``unparseable``       unknown ids with no provider-line + version shape.
    """
    models = _models(tax)
    routed = routed_families(tax)
    known_versions = known_family_versions(tax)

    merged: dict[str, dict[str, Any]] = {}
    sources_summary = []
    for src in snapshot.get("sources", []):
        if not isinstance(src, dict):
            continue
        sources_summary.append({
            "host": src.get("host"),
            "status": src.get("status"),
            "reason": src.get("reason"),
            "source_ref": src.get("source_ref"),
            "fetched_at": src.get("fetched_at"),
            "model_count": len(src.get("models") or []),
        })
        for row in src.get("models") or []:
            if not isinstance(row, dict) or row.get("hidden"):
                continue
            base = str(row.get("slug") or "").lower()
            if not base:
                continue
            entry = merged.setdefault(base, {
                "id": base, "hosts": [], "efforts": [], "host_slugs": [],
                "name": row.get("name"), "first_seen": row.get("first_seen"),
                "source_refs": [],
            })
            if src.get("host") not in entry["hosts"]:
                entry["hosts"].append(src.get("host"))
                entry["source_refs"].append(src.get("source_ref"))
            for e in row.get("efforts") or []:
                if e not in entry["efforts"]:
                    entry["efforts"].append(e)
            entry["host_slugs"].extend(s for s in row.get("host_slugs") or [] if s not in entry["host_slugs"])
            if row.get("first_seen") and (not entry["first_seen"] or row["first_seen"] < entry["first_seen"]):
                entry["first_seen"] = row["first_seen"]

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "snapshot_collected_at": snapshot.get("collected_at"),
        "sources": sources_summary,
        "known": [],
        "unknown": [],
        "newer_in_family": [],
        "informational": [],
        "new_family": [],
        "older_unregistered": [],
        "unparseable": [],
    }
    for base in sorted(merged):
        entry = merged[base]
        canonical = host_model_map.canonical_for_slug(base, models)
        if canonical is not None:
            report["known"].append({"id": base, "canonical": canonical, "hosts": entry["hosts"]})
            continue
        report["unknown"].append(base)
        fam = family_of(base)
        if fam is None:
            report["unparseable"].append({"id": base, "hosts": entry["hosts"]})
            continue
        family, version = fam
        item = dict(entry, family=family, version=_ver_str(version))
        r = routed.get(family)
        if r is not None and version > r["version"]:
            item["predecessor"] = r["predecessor"]
            item["predecessor_version"] = _ver_str(r["version"])
            if r["auto_alias"]:
                item["note"] = f"routes via auto-tracking alias '{r['predecessor']}'; no change proposed"
                report["informational"].append(item)
            else:
                report["newer_in_family"].append(item)
            continue
        known = known_versions.get(family)
        if r is None and (known is None or version > known[0]):
            item["nearest_known"] = known[1] if known else None
            item["note"] = "needs a tier decision"
            report["new_family"].append(item)
            continue
        report["older_unregistered"].append(item)
    return report


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Host model sync report",
        "",
        f"Generated {report.get('generated_at')} from snapshot {report.get('snapshot_collected_at')}.",
        "",
        "## Sources",
        "",
    ]
    for s in report.get("sources", []):
        detail = f"{s['model_count']} models" if s.get("status") == "ok" else (s.get("reason") or "")
        lines.append(f"- {s.get('host')}: {s.get('status')} — {detail}")
    lines.append("")
    lines.append(
        f"Known: {len(report['known'])}. Unknown: {len(report['unknown'])} "
        f"(newer in a routed family {len(report['newer_in_family'])}, new family "
        f"{len(report['new_family'])}, Claude informational {len(report['informational'])}, "
        f"older {len(report['older_unregistered'])}, unparseable {len(report['unparseable'])})."
    )
    sections = (
        ("newer_in_family", "Newer than the routed model (`apply --in-family` adds these, unrouted)",
         lambda i: f"- `{i['id']}` > routed `{i['predecessor']}` ({', '.join(i['hosts'])})"),
        ("new_family", "New family — needs a tier decision",
         lambda i: f"- `{i['id']}` ({', '.join(i['hosts'])})"
         + (f"; nearest known `{i['nearest_known']}`" if i.get("nearest_known") else "")),
        ("informational", "Claude — routed by auto-tracking alias, no change proposed",
         lambda i: f"- `{i['id']}` newer than `{i['predecessor']}` alias set ({', '.join(i['hosts'])})"),
        ("older_unregistered", "Older or same-version ids not in the taxonomy",
         lambda i: f"- `{i['id']}` ({', '.join(i['hosts'])})"),
        ("unparseable", "Unparseable ids",
         lambda i: f"- `{i['id']}` ({', '.join(i['hosts'])})"),
    )
    for key, title, fmt in sections:
        items = report.get(key) or []
        if not items:
            continue
        lines += ["", f"## {title}", ""]
        lines += [fmt(i) for i in items]
    lines += [
        "",
        "Re-run: `python3 scripts/sync_host_models.py collect && python3 scripts/sync_host_models.py diff`.",
        "",
    ]
    return "\n".join(lines)


def write_report(report: dict[str, Any], md_path: Path) -> None:
    _atomic_write(md_path, render_report(report))
    _atomic_write(md_path.with_name(REPORT_JSON_NAME), json.dumps(report, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------

def _derive_label(pred_meta: dict[str, Any], pred_id: str, new_id: str, host_name: str | None) -> str:
    if host_name:
        return host_name
    label = str(pred_meta.get("label") or pred_id)
    pf, nf = family_of(pred_id), family_of(new_id)
    if pf and nf:
        return label.replace(_ver_str(pf[1]), _ver_str(nf[1]), 1)
    return new_id


def apply_in_family(tax: dict[str, Any], report: dict[str, Any], today: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (new taxonomy, changes) adding one entry per newer_in_family item.

    ROUTING DECISION — added, never routed. ``model_overrides.resolve_role``
    and ``model_resolver`` walk preferred cells filtering only on availability
    and host reachability, never on ``status``; a ``candidate-unverified`` id in
    a preferred cell would be dispatched at once. The new entry also has
    ``released: null`` (no source carries a release date), which loses every
    recency comparison and cannot satisfy the routed-is-newest freshness test.
    Routing is a reviewed edit to ``preferred`` after a release date and a
    verification run exist.
    """
    today = today or _dt.date.today().isoformat()
    models_raw = tax.get("models", {})
    changes: list[dict[str, Any]] = []
    additions: dict[str, tuple[str, dict[str, Any]]] = {}
    for item in report.get("newer_in_family", []):
        new_id, pred = item["id"], item["predecessor"]
        if new_id in models_raw or new_id in additions:
            continue
        pred_meta = models_raw.get(pred)
        if not isinstance(pred_meta, dict):
            changes.append({"id": new_id, "action": "skipped", "reason": f"predecessor {pred} has no model entry"})
            continue
        aliases: list[str] = []
        for alias in pred_meta.get("aliases") or []:
            alias = str(alias)
            if pred in alias:
                aliases.append(alias.replace(pred, new_id))
        for slug in item.get("host_slugs") or []:
            if slug != new_id and slug not in aliases:
                aliases.append(slug)
        refs = ", ".join(str(r) for r in item.get("source_refs") or [])
        entry = {
            "provider": pred_meta.get("provider"),
            "label": _derive_label(pred_meta, pred, new_id, item.get("name")),
            "segment": pred_meta.get("segment"),
            "tier": pred_meta.get("tier"),
            "tags": list(pred_meta.get("tags") or []),
            "released": None,
            "status": "candidate-unverified",
            "aliases": aliases,
            "evidence": (
                f"Added {today} by scripts/sync_host_models.py apply --in-family. "
                f"First seen {item.get('first_seen') or today} in {refs} "
                f"(hosts: {', '.join(item.get('hosts') or [])}). Segment/tier/tags copied from "
                f"routed predecessor {pred}. Release date and capability UNVERIFIED; not routed."
            ),
        }
        additions[new_id] = (pred, entry)
        changes.append({
            "id": new_id, "action": "added, not routed: needs release date + verification",
            "predecessor": pred, "segment": entry["segment"], "tier": entry["tier"],
        })
    if not additions:
        return tax, changes
    # Insert each new entry just before its predecessor so families stay grouped.
    before: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for new_id, (pred, entry) in additions.items():
        before.setdefault(pred, []).append((new_id, entry))
    new_models: dict[str, Any] = {}
    for key, value in models_raw.items():
        for new_id, entry in before.get(key, []):
            new_models[new_id] = entry
        new_models[key] = value
    out = dict(tax)
    out["models"] = new_models
    return out, changes


def _taxonomy_text(tax: dict[str, Any]) -> str:
    # Matches the checked-in file byte-for-byte on a no-op round trip.
    return json.dumps(tax, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------
# Automatic, non-mutating staleness trigger (called from model_index resolve)
# --------------------------------------------------------------------------

def _source_mtimes() -> list[float]:
    out: list[float] = []
    paths = [codex_cache_path(), *(Path(p) for p in glob.glob(str(claude_catalog_dir() / "*.json")))]
    for p in paths:
        try:
            out.append(p.stat().st_mtime)
        except OSError:
            continue
    return out


def is_stale(now: float | None = None) -> bool:
    now = time.time() if now is None else now
    mtimes = _source_mtimes()
    snap = read_snapshot()
    if snap is None:
        return bool(mtimes)
    collected = _parse_iso(snap.get("collected_at"))
    if collected is None:
        return True
    if now - collected > STALE_AFTER_S:
        return True
    return any(m > collected for m in mtimes)


def _spawn_refresh() -> None:
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "refresh"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True, cwd=str(models_dir()),
    )


def maybe_trigger_refresh(now: float | None = None, spawn: Any = None) -> bool:
    """Spawn a detached ``refresh`` when the snapshot is stale. Never raises.

    At most once per hour (stamp file written before the spawn, so a crashing
    refresh cannot respawn in a loop). Returns True when a spawn was issued.
    """
    try:
        if os.environ.get(TRIGGER_ENV, "1").strip().lower() in {"0", "off", "false", "no"}:
            return False
        # Test runs call resolve constantly; never start a real refresh (reads the
        # owner's caches, calls Cursor's servers) unless a test injects a spawn.
        if spawn is None and os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        now = time.time() if now is None else now
        stamp = models_dir() / STAMP_NAME
        try:
            if now - stamp.stat().st_mtime < TRIGGER_INTERVAL_S:
                return False
        except OSError:
            pass
        if not is_stale(now):
            return False
        stamp.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp.write_text(str(int(now)) + "\n", encoding="utf-8")
        os.utime(stamp, (now, now))
        (spawn or _spawn_refresh)()
        return True
    except Exception:  # noqa: BLE001 - resolve must never fail because of sync
        return False


def pending_notice() -> str | None:
    """One line for resolve's human output when the last report has work. Never raises."""
    try:
        directory = models_dir()
        data = json.loads((directory / REPORT_JSON_NAME).read_text(encoding="utf-8"))
        n = len(data.get("newer_in_family") or []) + len(data.get("new_family") or [])
        if n <= 0:
            return None
        return f"{n} newer models found by host sync: see {_display(directory / REPORT_MD_NAME)}"
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _cmd_collect(args: argparse.Namespace) -> int:
    snap = collect(taxonomy_path=args.taxonomy)
    if args.json:
        print(json.dumps(snap, indent=2, sort_keys=True))
    else:
        for src in snap["sources"]:
            detail = f"{len(src['models'])} models" if src["status"] == "ok" else src["reason"]
            print(f"{src['host']:<9} {src['status']:<12} {detail}")
        print(f"snapshot: {_display(models_dir() / SNAPSHOT_NAME)}")
    return 0


def _load_snapshot_or_fail() -> dict[str, Any]:
    snap = read_snapshot()
    if snap is None:
        raise SystemExit("no snapshot; run `sync_host_models.py collect` first")
    return snap


def _cmd_diff(args: argparse.Namespace) -> int:
    report = diff(_load_snapshot_or_fail(), load_taxonomy(args.taxonomy))
    if args.report_file:
        write_report(report, Path(args.report_file).expanduser())
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_report(report))
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    if not args.in_family:
        print("apply requires --in-family (the only supported change)", file=sys.stderr)
        return 2
    path = Path(args.taxonomy) if args.taxonomy else model_taxonomy._TAXONOMY_PATH
    tax = json.loads(path.read_text(encoding="utf-8"))
    report = diff(_load_snapshot_or_fail(), tax)
    new_tax, changes = apply_in_family(tax, report)
    for change in changes:
        print(f"{change['id']}: {change['action']}"
              + (f" (from {change['predecessor']}: {change['segment']}/{change['tier']})" if change.get("predecessor") else "")
              + (f" — {change['reason']}" if change.get("reason") else ""))
    added = [c for c in changes if c.get("predecessor")]
    if not added:
        print("no newer-in-family models to add")
        return 0
    if args.dry_run:
        print(f"dry run: {len(added)} entr{'y' if len(added) == 1 else 'ies'} would be added to {path}")
        return 0
    _atomic_write_preserve(path, _taxonomy_text(new_tax))
    print(f"wrote {len(added)} entr{'y' if len(added) == 1 else 'ies'} to {path}; preferred cells unchanged")
    return 0


def _atomic_write_preserve(path: Path, text: str) -> None:
    from atomic_io import atomic_write_text  # noqa: PLC0415

    atomic_write_text(path, text)


def _cmd_refresh(args: argparse.Namespace) -> int:
    directory = models_dir()
    try:
        snap = collect()
        write_report(diff(snap, load_taxonomy()), directory / REPORT_MD_NAME)
    except Exception as exc:  # noqa: BLE001 - detached; record, never raise
        try:
            _atomic_write(directory / "refresh-error.txt", f"{_now_iso()} {type(exc).__name__}: {exc}\n")
        except Exception:  # noqa: BLE001
            pass
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sync_host_models.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect", help="Read every host source and write the snapshot.")
    c.add_argument("--json", action="store_true")
    c.add_argument("--taxonomy", default=None, help=argparse.SUPPRESS)
    c.set_defaults(func=_cmd_collect)
    d = sub.add_parser("diff", help="Compare the snapshot to the taxonomy.")
    d.add_argument("--json", action="store_true")
    d.add_argument("--report-file", default=None, help="Also write markdown (+ sibling report.json).")
    d.add_argument("--taxonomy", default=None, help="Taxonomy file (default: the checked-in one).")
    d.set_defaults(func=_cmd_diff)
    a = sub.add_parser("apply", help="Add newer-in-family models to the taxonomy (unrouted).")
    a.add_argument("--in-family", action="store_true", required=True)
    a.add_argument("--taxonomy", default=None, help="Taxonomy file to edit (default: the checked-in one).")
    a.add_argument("--dry-run", action="store_true")
    a.set_defaults(func=_cmd_apply)
    r = sub.add_parser("refresh", help="collect + diff --report-file (detached trigger target).")
    r.set_defaults(func=_cmd_refresh)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
