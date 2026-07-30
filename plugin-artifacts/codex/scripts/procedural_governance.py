#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Three-phase procedural memory governance.

Per design ref §14 — three-phase learning curve:
  Phase 1: manual authoring (no script; convention only)
  Phase 2: pattern detection — scan state.json.runs[] for recurring
           root_causes (>= 3 incidents -> candidate)
  Phase 3: auto-draft — gated until >= 5 hand-authored procedures exist

Modes (--mode):
  detect-patterns   Read .build-loop/state.json.runs[]; cluster by
                    root_cause; if any cluster has count >= 3, write
                    a candidate row to .procedural/_candidates.jsonl.
  auto-draft        For each candidate in _candidates.jsonl, draft a
                    procedure to .procedural/_drafts/<name>/procedure.md.
                    GATED: requires >= 5 hand-authored procedures
                    in .procedural/ (excluding _drafts/, _candidates.jsonl,
                    _index.yaml). When PROCEDURAL_GOVERNANCE_MOCK_DRAFT=1,
                    skips the LLM and writes a deterministic stub
                    (used by tests).
  validate-symbols  For each procedure, grep --paths for each symbol in
                    `depends_on`. Mark `stale: true` if any are missing.
                    Emits JSON to stdout. Frontmatter is rewritten only
                    when --rewrite is passed.

Exit codes: 0 success, 1 validation, 2 filesystem.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from write_decision import parse_frontmatter  # type: ignore  # noqa: E402

PATTERN_THRESHOLD = 3
HAND_AUTHORED_GATE = 5


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------- detect-patterns ----------


def load_runs(workdir: Path) -> list[dict]:
    state = workdir / ".build-loop" / "state.json"
    if not state.exists():
        return []
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"state.json malformed: {e}")
    return data.get("runs", []) or []


def cluster_root_causes(runs: list[dict]) -> dict[str, list[str]]:
    """Group run_ids by root_cause, harvesting BOTH levels of the schema.

    Two sources per run: the TOP-LEVEL ``root_cause`` and every
    ``phases[*].root_cause`` (the canonical Review-G run schema —
    ``skills/self-improve/SKILL.md``: ``phases: {"N": {status, duration_s,
    root_cause}}``). No production writer emits a top-level ``root_cause``, so
    clustering on it alone left the detector dormant on its own target path; a
    failing phase records its cause in the nested field, and that is where the
    clusterable signal actually lives. A run contributes its id at most once per
    distinct cause (a cause repeated across top-level + phases is one incident).
    Runs with no non-empty ``root_cause`` at any level are skipped.
    """
    out: dict[str, list[str]] = {}
    for r in runs:
        rid = str(r.get("run_id") or r.get("id") or "")
        causes: list[str] = []
        top = r.get("root_cause")
        if isinstance(top, str) and top.strip():
            causes.append(top.strip())
        phases = r.get("phases")
        if isinstance(phases, dict):
            for ph in phases.values():
                if isinstance(ph, dict):
                    pc = ph.get("root_cause")
                    if isinstance(pc, str) and pc.strip():
                        causes.append(pc.strip())
        for rc in dict.fromkeys(causes):  # dedupe per run, preserve order
            out.setdefault(rc, []).append(rid)
    return out


def existing_candidate_keys(cand_path: Path) -> set[str]:
    if not cand_path.exists():
        return set()
    keys: set[str] = set()
    for line in cand_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            keys.add(obj.get("root_cause", ""))
        except json.JSONDecodeError:
            continue
    return keys


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "candidate"


def detect_patterns(workdir: Path) -> int:
    runs = load_runs(workdir)
    clusters = cluster_root_causes(runs)
    cand_path = workdir / ".procedural" / "_candidates.jsonl"
    cand_path.parent.mkdir(parents=True, exist_ok=True)
    have = existing_candidate_keys(cand_path)
    new_lines: list[str] = []
    for rc, ids in clusters.items():
        if len(ids) < PATTERN_THRESHOLD:
            continue
        if rc in have:
            continue
        new_lines.append(json.dumps({
            "name": slug(rc),
            "root_cause": rc,
            "incident_count": len(ids),
            "run_ids": ids,
        }))
    if new_lines:
        existing = cand_path.read_text(encoding="utf-8") if cand_path.exists() else ""
        cand_path.write_text(existing + "\n".join(new_lines) + "\n", encoding="utf-8")
        _log(f"detect-patterns: wrote {len(new_lines)} candidate(s) to {cand_path}")
    else:
        _log("detect-patterns: no new candidates above threshold")
    return 0


# ---------- auto-draft ----------


def list_hand_authored(workdir: Path) -> list[Path]:
    """Procedures not under _drafts/ and not the candidate file."""
    pdir = workdir / ".procedural"
    if not pdir.exists():
        return []
    out: list[Path] = []
    for child in pdir.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith("_"):
            continue
        proc = child / "procedure.md"
        if proc.exists():
            out.append(proc)
    return out


def auto_draft(workdir: Path) -> int:
    hand = list_hand_authored(workdir)
    if len(hand) < HAND_AUTHORED_GATE:
        _log(
            f"auto-draft: gated — {len(hand)}/{HAND_AUTHORED_GATE} hand-authored procedures; "
            "skipping draft generation. Author more procedures manually first."
        )
        return 0

    cand_path = workdir / ".procedural" / "_candidates.jsonl"
    if not cand_path.exists():
        _log("auto-draft: no _candidates.jsonl; nothing to draft")
        return 0

    drafts_dir = workdir / ".procedural" / "_drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[dict] = []
    for line in cand_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            candidates.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    mock = os.environ.get("PROCEDURAL_GOVERNANCE_MOCK_DRAFT") == "1"

    written = 0
    for c in candidates:
        name = c.get("name") or slug(c.get("root_cause", "candidate"))
        target_dir = drafts_dir / name
        if target_dir.exists():
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        body = _render_draft(c, mock=mock)
        (target_dir / "procedure.md").write_text(body, encoding="utf-8")
        written += 1
        _log(f"auto-draft: wrote {target_dir / 'procedure.md'}")

    _log(f"auto-draft: drafted {written} procedure(s)")
    return 0


def _render_draft(c: dict, mock: bool) -> str:
    """Render a draft procedure.

    When mock=True (tests + offline runs), produce a deterministic
    skeleton without calling cheap_complete. When mock=False, attempt
    to call ollama qwen3:8b for a richer body; on failure, fall through
    to the deterministic skeleton.
    """
    name = c.get("name", "draft")
    rc = c.get("root_cause", "")
    incidents = c.get("incident_count", 0)
    run_ids = c.get("run_ids", [])

    skeleton_body = _render_skeleton(name, rc, incidents, run_ids)

    if mock:
        return skeleton_body

    # Attempt LLM draft
    llm_body = _render_via_llm(name, rc, incidents, run_ids)
    if llm_body:
        return llm_body
    return skeleton_body


def _render_skeleton(name: str, rc: str, incidents: int, run_ids: list[str]) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"trigger: \"{rc}\"\n"
        "domains: [auto-draft]\n"
        "confidence: low\n"
        "created: \"auto-draft\"\n"
        f"incident_count: {incidents}\n"
        "depends_on: []\n"
        "stale: false\n"
        f"source: candidates_jsonl\n"
        f"run_ids: {run_ids}\n"
        "---\n\n"
        f"# {name}\n\n"
        f"Auto-drafted procedure for recurring root cause: **{rc}**\n\n"
        f"Observed in {incidents} run(s): {run_ids}\n\n"
        "## Symptom\n\n"
        f"_(populate from incident reports)_\n\n"
        "## Diagnostic moves\n\n"
        f"_(populate after manual review — root cause: {rc})_\n\n"
        "## Fix template\n\n"
        "_(populate)_\n\n"
        "## Provenance\n\n"
        f"Auto-drafted by `procedural_governance.py --mode auto-draft`. Review before promoting to active.\n"
    )


def _render_via_llm(name: str, rc: str, incidents: int, run_ids: list[str]) -> str | None:
    """Best-effort LLM draft via local Ollama qwen3. Returns None on any failure."""
    try:
        import urllib.request
        prompt = (
            "Draft a debugging-procedure markdown file for a recurring failure.\n"
            f"Root cause: {rc}\n"
            f"Incident count: {incidents}\n"
            "Output ONLY a markdown body (no frontmatter) with sections: Symptom, "
            "Diagnostic moves, Fix template, Provenance. Concise, no filler.\n"
        )
        body = json.dumps({
            "model": "qwen3:8b",
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2},
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data.get("response", "").strip()
        if not text:
            return None
        # Wrap with frontmatter
        return (
            "---\n"
            f"name: {name}\n"
            f"trigger: \"{rc}\"\n"
            "domains: [auto-draft]\n"
            "confidence: low\n"
            "created: \"auto-draft\"\n"
            f"incident_count: {incidents}\n"
            "depends_on: []\n"
            "stale: false\n"
            "source: candidates_jsonl\n"
            f"run_ids: {run_ids}\n"
            "---\n\n"
            f"# {name}\n\n"
            + text
            + "\n"
        )
    except Exception as e:  # noqa: BLE001
        _log(f"auto-draft: LLM call failed ({e}); using skeleton")
        return None


# ---------- validate-symbols ----------


def list_procedures(workdir: Path, include_drafts: bool = False) -> list[Path]:
    pdir = workdir / ".procedural"
    if not pdir.exists():
        return []
    out: list[Path] = []
    for child in pdir.iterdir():
        if not child.is_dir():
            continue
        if not include_drafts and child.name == "_drafts":
            continue
        if child.name.startswith("_"):
            continue
        proc = child / "procedure.md"
        if proc.exists():
            out.append(proc)
    return out


_DEPENDS_ON_RE = re.compile(r"depends_on:\s*\n((?:\s+- .*?\n(?:\s{4,}.*?\n)*)+)", re.MULTILINE)
_SYMBOL_LINE_RE = re.compile(r"^\s*- symbol:\s*(?:\"([^\"]+)\"|'([^']+)'|(\S+))", re.MULTILINE)


def parse_depends_on_symbols(text: str) -> list[str]:
    """Extract symbol names from a YAML `depends_on: [{symbol, ...}]` block.

    The tiny YAML parser in write_decision.py doesn't handle nested
    sequence-of-mappings, so we use a targeted regex.
    """
    m = _DEPENDS_ON_RE.search(text)
    if not m:
        return []
    block = m.group(1)
    syms: list[str] = []
    for sm in _SYMBOL_LINE_RE.finditer(block):
        sym = sm.group(1) or sm.group(2) or sm.group(3)
        if sym:
            syms.append(sym)
    return syms


def grep_symbol(symbol: str, paths: list[str], workdir: Path) -> bool:
    """Return True if symbol appears in any file under paths/.

    Uses ripgrep when available (fast); falls back to grep -r.
    Symbols are matched as substrings (no word boundary), since they
    may include parens or dots.
    """
    if not symbol:
        return False
    rg = shutil.which("rg")
    abs_paths = []
    for p in paths:
        ap = (workdir / p)
        if ap.exists():
            abs_paths.append(str(ap))
    if not abs_paths:
        return False
    if rg:
        cmd = [rg, "--fixed-strings", "--quiet", symbol] + abs_paths
    else:
        cmd = ["grep", "-r", "-q", "--fixed-strings", symbol] + abs_paths
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=10)
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def validate_symbols(workdir: Path, paths: list[str], rewrite: bool) -> int:
    procs = list_procedures(workdir)
    out: list[dict] = []
    for proc_path in procs:
        text = proc_path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text) or {}
        symbols = parse_depends_on_symbols(text)
        missing: list[str] = []
        present: list[str] = []
        for sym in symbols:
            if grep_symbol(sym, paths, workdir):
                present.append(sym)
            else:
                missing.append(sym)
        stale = bool(missing)
        out.append({
            "name": fm.get("name") or proc_path.parent.name,
            "path": str(proc_path.relative_to(workdir)),
            "symbols": symbols,
            "present_symbols": present,
            "missing_symbols": missing,
            "stale": stale,
        })
        if rewrite and stale:
            _rewrite_stale_flag(proc_path, text, True)
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _rewrite_stale_flag(proc_path: Path, text: str, value: bool) -> None:
    """Add or update `stale: true|false` in frontmatter. Best-effort."""
    new_line = f"stale: {'true' if value else 'false'}"
    if re.search(r"^stale:\s*\S+", text, re.MULTILINE):
        text = re.sub(r"^stale:\s*\S+", new_line, text, count=1, flags=re.MULTILINE)
    else:
        # Insert before closing --- of frontmatter
        text = re.sub(r"^---\n", f"---\n{new_line}\n", text, count=2)
    proc_path.write_text(text, encoding="utf-8")


# ---------- main ----------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Three-phase procedural memory governance")
    p.add_argument("--workdir", default=".")
    p.add_argument(
        "--mode",
        required=True,
        choices=["detect-patterns", "auto-draft", "validate-symbols"],
    )
    p.add_argument(
        "--paths",
        default="",
        help="Comma-separated codebase paths to grep for symbols (validate-symbols only). Default: scripts,src,app",
    )
    p.add_argument(
        "--rewrite",
        action="store_true",
        help="validate-symbols: rewrite frontmatter to set/clear stale: true",
    )
    args = p.parse_args(argv)

    workdir = Path(args.workdir).resolve()

    try:
        if args.mode == "detect-patterns":
            return detect_patterns(workdir)
        if args.mode == "auto-draft":
            return auto_draft(workdir)
        if args.mode == "validate-symbols":
            paths = [s.strip() for s in args.paths.split(",") if s.strip()]
            if not paths:
                paths = ["scripts", "src", "app"]
            return validate_symbols(workdir, paths, args.rewrite)
    except ValueError as e:
        _log(f"validation error: {e}")
        return 1
    except OSError as e:
        _log(f"filesystem error: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
