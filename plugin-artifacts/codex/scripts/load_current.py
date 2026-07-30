#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Load pointer-dense short-term working context before Phase 1 memory bootstrap.
#   application: memory
#   status: active
"""Pre-Assess working-context loader (Pillar 0 short-term layer).

Phase 1 Assess reads this BEFORE running ``context_bootstrap.py`` so the
agent resumes from the prior session's working state immediately. The cost
is one local FS read of ``.build-loop/context/current.md`` and one parse —
no DB, no network, no embedding model — so this is safe on every entrypoint.

Contract:

* Absence-tolerant. Missing ``current.md`` returns ``{exists: False, ...}``
  with a graceful reason and ``warm_read_latency_ms = None``. Never raises.
* Pointer-only. The loader does NOT re-derive memory; it surfaces the
  pointers already embedded in ``current.md`` (Memory Backlinks, snapshot
  ID, project memory path) for Phase 1 to expand on demand.
* Fast. Measured warm-read latency is part of the returned envelope so
  Phase 1 can record it without re-timing.
* Crash-safe. Bad UTF-8 / partial writes / nonexistent dirs all yield a
  structured envelope with ``reasons[]`` describing the degradation.

The envelope is intentionally typed (``WorkingContextEnvelope``) because
future tiers (cross-host context sync, dispatch-time briefs) will reuse
the same shape — pay-it-forward over a 1-shot dict.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_REL_PATH = ".build-loop/context/current.md"
DEFAULT_INDEX_REL = ".build-loop/context/index.json"
_MAX_FIELD_LEN = 200
_MAX_LINKS = 16
_MAX_POINTERS = 16

# ``# Build Loop Working Context`` is the v2 marker (P0 rewrite).
# ``# Build Loop Context Snapshot`` is the v1 marker (kept for back-compat).
_VALID_TITLE_RE = re.compile(r"^#\s+Build Loop\s+(Working Context|Context Snapshot)\s*$")
_FIELD_LINE_RE = re.compile(r"^-\s+(?P<key>[A-Z][A-Za-z ]+):\s+(?P<val>.+)$")
_LINK_LINE_RE = re.compile(
    r"^-\s+(?P<kind>[a-z][a-z_-]*):\s+(?P<title>.+?)(?:\s+\[(?P<project>[^\]]+)\])?(?:\s+—\s+`(?P<path>[^`]+)`)?\s*$"
)
_POINTER_LINE_RE = re.compile(r"^-\s+(?P<label>[^:]+?):\s+`?(?P<value>[^`]+?)`?(?:\s+\([^)]+\))?\s*$")


@dataclass
class WorkingContextEnvelope:
    """Typed return value — future tiers extend without breaking callers."""
    exists: bool
    path: str
    warm_read_latency_ms: float | None
    parsed: dict[str, Any] = field(default_factory=dict)
    text_preview: str = ""
    reasons: list[str] = field(default_factory=list)


def expand_path(raw: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(raw)))).resolve()


def _strip_field(value: str) -> str:
    return value.strip().strip("`").strip()


def _truncate(value: str) -> str:
    if len(value) <= _MAX_FIELD_LEN:
        return value
    return value[: _MAX_FIELD_LEN - 1] + "…"


def _parse_sections(text: str) -> dict[str, Any]:
    """Lightweight markdown section parser tuned to the current.md shape.

    Avoids a full markdown lib (we don't need it; the schema is fixed).
    """
    sections: dict[str, list[str]] = {}
    current = "_header"
    sections[current] = []
    for raw_line in text.splitlines():
        if raw_line.startswith("## "):
            current = raw_line[3:].strip()
            sections[current] = []
            continue
        sections[current].append(raw_line)
    return sections


def _parse_header(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        match = _FIELD_LINE_RE.match(line)
        if not match:
            continue
        key = match.group("key").strip().lower().replace(" ", "_")
        out[key] = _truncate(_strip_field(match.group("val")))
    return out


def _parse_current_work(lines: list[str]) -> dict[str, str]:
    # Same shape as the header (key/value list).
    return _parse_header(lines)


def _parse_backlinks(lines: list[str]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for line in lines:
        match = _LINK_LINE_RE.match(line)
        if not match:
            continue
        links.append(
            {
                "kind": match.group("kind"),
                "title": _truncate(_strip_field(match.group("title"))),
                "project": match.group("project") or "",
                "path": match.group("path") or "",
            }
        )
        if len(links) >= _MAX_LINKS:
            break
    return links


def _parse_pointers(lines: list[str]) -> list[dict[str, str]]:
    pointers: list[dict[str, str]] = []
    for line in lines:
        match = _POINTER_LINE_RE.match(line)
        if not match:
            continue
        pointers.append(
            {
                "label": _strip_field(match.group("label")),
                "value": _truncate(_strip_field(match.group("value"))),
            }
        )
        if len(pointers) >= _MAX_POINTERS:
            break
    return pointers


def parse_current_text(text: str) -> dict[str, Any]:
    """Return a structured view of current.md. Tolerant of v1+v2 markers."""
    if not text.strip():
        return {"valid": False, "reason": "empty_file"}

    first_line = text.splitlines()[0] if text else ""
    if not _VALID_TITLE_RE.match(first_line.strip()):
        return {"valid": False, "reason": f"unrecognized_title: {first_line[:80]}"}

    sections = _parse_sections(text)
    parsed: dict[str, Any] = {
        "valid": True,
        "header": _parse_header(sections.get("_header", [])),
        "current_work": _parse_current_work(sections.get("Current Work", [])),
        "links_down": _parse_backlinks(sections.get("Memory Backlinks", [])),
        "pointers": _parse_pointers(sections.get("Pointers", [])),
        "sections_present": [s for s in sections if s != "_header"],
    }
    return parsed


def load_current(
    workdir: Path | str,
    *,
    rel_path: str = DEFAULT_REL_PATH,
    preview_chars: int = 2000,
) -> WorkingContextEnvelope:
    """Read + parse current.md without triggering any heavy bootstrap.

    Returns a typed envelope; never raises. ``warm_read_latency_ms`` is
    measured at the same time the load happens, so callers (Phase 1 Assess)
    can record it without re-timing.
    """
    workdir_path = expand_path(workdir)
    current_path = workdir_path / rel_path
    reasons: list[str] = []

    if not current_path.is_file():
        return WorkingContextEnvelope(
            exists=False,
            path=str(current_path),
            warm_read_latency_ms=None,
            reasons=[f"missing: {current_path}"],
        )

    try:
        t0 = time.perf_counter()
        text = current_path.read_text(encoding="utf-8")
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    except UnicodeDecodeError as exc:
        return WorkingContextEnvelope(
            exists=True,
            path=str(current_path),
            warm_read_latency_ms=None,
            reasons=[f"decode_error: {exc}"],
        )
    except OSError as exc:
        return WorkingContextEnvelope(
            exists=False,
            path=str(current_path),
            warm_read_latency_ms=None,
            reasons=[f"read_error: {exc}"],
        )

    parsed = parse_current_text(text)
    if not parsed.get("valid"):
        reasons.append(f"parse_error: {parsed.get('reason')}")

    # Pull the snapshot index alongside so Phase 1 sees the embedded latency
    # from the LAST write (which is the latency the next agent will pay).
    index_path = workdir_path / DEFAULT_INDEX_REL
    index_info: dict[str, Any] = {}
    if index_path.is_file():
        try:
            index_info = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            reasons.append(f"index_error: {exc}")

    if index_info:
        parsed["last_write_warm_read_ms"] = index_info.get("warm_read_latency_ms")
        parsed["last_write_density_findings"] = index_info.get("pointer_density_findings") or []
        parsed["last_snapshot_id"] = index_info.get("last_snapshot_id")

    return WorkingContextEnvelope(
        exists=True,
        path=str(current_path),
        warm_read_latency_ms=latency_ms,
        parsed=parsed,
        text_preview=text[:preview_chars],
        reasons=reasons,
    )


def envelope_to_dict(envelope: WorkingContextEnvelope) -> dict[str, Any]:
    """Stable JSON-serializable view of the envelope."""
    return asdict(envelope)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default=os.getcwd())
    parser.add_argument("--rel-path", default=DEFAULT_REL_PATH)
    parser.add_argument("--preview-chars", type=int, default=2000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    envelope = load_current(
        args.workdir,
        rel_path=args.rel_path,
        preview_chars=args.preview_chars,
    )
    if args.json:
        print(json.dumps(envelope_to_dict(envelope), indent=2, sort_keys=True, default=str))
    else:
        if not envelope.exists:
            print(f"load_current: no working context at {envelope.path}")
            for reason in envelope.reasons:
                print(f"  reason: {reason}")
        else:
            parsed = envelope.parsed
            hdr = parsed.get("header", {})
            cw = parsed.get("current_work", {})
            print(f"load_current: {envelope.path}")
            print(f"  warm_read_latency_ms: {envelope.warm_read_latency_ms}")
            print(f"  phase={hdr.get('phase')} run={hdr.get('run')} task={cw.get('task')}")
            print(f"  backlinks: {len(parsed.get('links_down') or [])}")
            print(f"  pointers:  {len(parsed.get('pointers') or [])}")
            for reason in envelope.reasons:
                print(f"  reason: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
