#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Regenerate each agent's model: frontmatter from the model index's current recommended default, keeping the fallback in sync.
#   application: meta
#   status: active
"""Regenerate every agent's ``model:`` frontmatter from the model index.

The durable KEY is each agent's ``(segment, tier)`` ROLE. The ``model:`` line is
the index-DERIVED recommended default for the active host — generated, never
hand-edited. When the index (``references/model-taxonomy.json``) changes — a
reordered preferred list, a new default, a newly-classified model — the
recommended ``model:`` values must regenerate so the frontmatter fallback is
never a stale hardcode. This is that regenerator.

It REUSES the front-door resolver (``resolve_agent_model.resolve``) — the SAME
path dispatch uses — so the synced ``model:`` and the dispatch-time override can
never diverge. No vendor API calls.

Emit contract — only HARNESS-VALID ``model:`` tokens are written. The recommended
default is the top-ranked AVAILABLE id for the agent's role on the active host.
On a Claude host the host-provider filter already restricts that to an Anthropic
id (``fable``/``opus``/``sonnet``/``haiku``), the exact short tokens the Claude
Code harness accepts. As a hard guard this script writes a resolved id into
``model:`` ONLY when its provider is ``anthropic``; a cross-provider id is NOT a
valid Claude Code ``model:`` token, so the existing token is kept and the agent is
reported as ``skipped: non-harness-token`` rather than written.

Modes::

    python3 scripts/sync_agent_model_defaults.py --check   # CI: report drift, exit 1 if any
    python3 scripts/sync_agent_model_defaults.py --apply    # rewrite drifting model: lines (idempotent)

``inherit`` agents are skipped (their ``model:`` is intentionally ``inherit``).
Only the single ``model:`` line is rewritten (exact-line replace) — ``tier:``,
``segment:``, and the body are never touched.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:  # pragma: no cover - import shim
    import model_taxonomy
    import resolve_agent_model
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import model_taxonomy  # type: ignore[no-redefine]
    import resolve_agent_model  # type: ignore[no-redefine]

INHERIT = resolve_agent_model.INHERIT
_MODEL_LINE = re.compile(r"^(?P<indent>\s*)model:\s*(?P<value>\S.*?)\s*$")


def _is_harness_valid(model_id: str | None) -> bool:
    """A model id is harness-valid (writable to ``model:``) iff it is an Anthropic
    model. A Claude Code ``model:`` frontmatter token must name an Anthropic model;
    a cross-provider id would be rejected by the harness."""
    if not model_id:
        return False
    meta = model_taxonomy.model_meta(model_id) or {}
    return (meta.get("provider") or "").strip().lower() == "anthropic"


def _current_model_line(text: str) -> tuple[int, str] | None:
    """Return (line_index, current_value) for the top-level ``model:`` line in the
    frontmatter, or None. Only scans the frontmatter block (between the first two
    ``---`` fences) so a ``model:`` mention in the body is never matched."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return None
        m = _MODEL_LINE.match(lines[i])
        if m and not lines[i][0].isspace():  # top-level (column 0) only
            return i, m.group("value")
    return None


def compute_recommended(agent: str, workdir: Path, agents_dir: Path | None,
                        host_providers: set[str] | frozenset[str] | None) -> dict[str, Any]:
    """Resolve the agent's recommended default via the front-door resolver."""
    return resolve_agent_model.resolve(
        agent=agent, workdir=workdir, agents_dir=agents_dir, host_providers=host_providers,
    )


def evaluate_agent(agent_path: Path, workdir: Path, agents_dir: Path,
                   host_providers: set[str] | frozenset[str] | None) -> dict[str, Any]:
    """Compute the drift verdict for one agent file (no write)."""
    name = agent_path.stem
    text = agent_path.read_text(encoding="utf-8")
    found = _current_model_line(text)
    current = found[1] if found else None

    env = compute_recommended(name, workdir, agents_dir, host_providers)
    recommended = env.get("model")

    if env.get("source") == "inherit" or current == INHERIT:
        return {"agent": name, "status": "skipped", "reason": "inherit", "current": current}
    if not recommended:
        return {"agent": name, "status": "skipped", "reason": "unresolved", "current": current}
    if not _is_harness_valid(recommended):
        # Cross-provider recommended id — not a valid model: token. Keep existing.
        return {"agent": name, "status": "skipped", "reason": "non-harness-token",
                "current": current, "recommended": recommended}
    if current == recommended:
        return {"agent": name, "status": "in-sync", "current": current}
    return {"agent": name, "status": "drift", "current": current, "recommended": recommended,
            "line_index": found[0] if found else None}


def apply_agent(agent_path: Path, line_index: int | None, recommended: str) -> bool:
    """Rewrite the single ``model:`` line. Returns True if the file changed."""
    text = agent_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if line_index is None or line_index >= len(lines):
        return False
    m = _MODEL_LINE.match(lines[line_index].rstrip("\n"))
    if not m:
        return False
    newline = "\n" if lines[line_index].endswith("\n") else ""
    lines[line_index] = f"{m.group('indent')}model: {recommended}{newline}"
    agent_path.write_text("".join(lines), encoding="utf-8")
    return True


def run(*, workdir: Path, agents_dir: Path, apply: bool,
        host_providers: set[str] | frozenset[str] | None) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    changed: list[str] = []
    for agent_path in sorted(agents_dir.glob("*.md")):
        verdict = evaluate_agent(agent_path, workdir, agents_dir, host_providers)
        if verdict["status"] == "drift" and apply:
            if apply_agent(agent_path, verdict.get("line_index"), verdict["recommended"]):
                verdict = {**verdict, "status": "applied"}
                changed.append(verdict["agent"])
        results.append(verdict)

    drift = [r for r in results if r["status"] == "drift"]
    return {
        "mode": "apply" if apply else "check",
        "total": len(results),
        "drift_count": len(drift),
        "applied": changed,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".")
    p.add_argument("--agents-dir", default=None, help="Override agents/ (tests).")
    p.add_argument(
        "--host-providers",
        default=None,
        help="Comma-separated providers (default: detect host; 'any' disables filter).",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Report drift; exit 1 if any (CI).")
    mode.add_argument("--apply", action="store_true", help="Rewrite drifting model: lines.")
    p.add_argument("--json", action="store_true", help="Machine output.")
    args = p.parse_args(argv)

    import resolve_agent_model as ram
    agents_dir = Path(args.agents_dir) if args.agents_dir else ram.default_agents_dir()
    import model_resolver
    host_providers = model_resolver._parse_host_providers_arg(args.host_providers)

    report = run(workdir=Path(args.workdir), agents_dir=agents_dir,
                 apply=bool(args.apply), host_providers=host_providers)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for r in report["results"]:
            if r["status"] in ("drift", "applied"):
                arrow = "WOULD SET" if r["status"] == "drift" else "SET"
                print(f"  [{r['status']}] {r['agent']}: {r.get('current')} {arrow} {r.get('recommended')}")
            elif r["status"] == "skipped" and r.get("reason") == "non-harness-token":
                print(f"  [skipped] {r['agent']}: kept {r.get('current')} (recommended {r.get('recommended')} is cross-provider)")
        print(f"{report['mode']}: {report['total']} agents, {report['drift_count']} drift, {len(report['applied'])} applied")

    # --check exits 1 when any drift remains; --apply exits 0 (it resolved drift).
    if args.check and report["drift_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
