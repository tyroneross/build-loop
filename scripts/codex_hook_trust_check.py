#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""codex_hook_trust_check.py — is every registered Codex hook actually trusted?

WHY THIS EXISTS (the defect it fixes)
-------------------------------------
Codex will not run a hook it has not been told to trust, and it records that
trust in ``~/.codex/config.toml`` under ``[hooks.state]`` keyed by ORDINAL
POSITION -- ``"<abs path to hooks.json>:<event>:<group>:<index>"``. Nothing
compares the two files, so a hook can ship in git, read as fully wired, and
never once execute.

Two observed instances in build-loop (2026-08-23 audit):

  * The rally ``session_probe`` Stop hook landed 2026-08-14 in b384b34f and had
    still never been trusted 9 days later.
  * Five transcript sweeps registered in a964f79 were inert on arrival.

build-loop's own trust set was granted once, between 2026-07-11 and 2026-08-13,
and has been frozen at 9 entries since. Every hook added after that grant is
dead until someone accepts a prompt -- and nothing tells you.

THE ORDINAL HAZARD, which is worse than either instance
-------------------------------------------------------
Because trust is keyed by POSITION rather than by content, inserting a hook
group ahead of an existing one shifts every later index and silently moves an
accepted grant onto a DIFFERENT command. Two merges on 2026-08-23 moved one
group from index 4 to 5; indices 0-3 happened to be untouched, so nothing
broke -- by insertion order, not by design.

Position alone cannot detect that, so this records a fingerprint of the command
text seen at each trusted key and reports when that key later holds different
content. The fingerprint file is local state, never a repo artifact.

Advisory by contract: reports, never blocks. Stdlib only. Python 3.11+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

# Codex event name -> the token used in a [hooks.state] key.
EVENT_KEYS = {
    "SessionStart": "session_start",
    "Stop": "stop",
    "PreCompact": "pre_compact",
    "SessionEnd": "session_end",
    "UserPromptSubmit": "user_prompt_submit",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
}

def _is_worktree(path: Path) -> bool:
    """Transient checkouts of a repo already counted in the sweep.

    Paths look like ``<repo>.worktrees/<slug>`` or ``<repo>/.build-loop/worktrees/<id>``.
    Codex keys trust by absolute path, so each worktree reads as a fresh
    untrusted repo and would inflate the gap with noise nobody acts on.
    """
    return any(
        part == ".build-loop" or part == "worktrees" or part.endswith(".worktrees")
        for part in path.parts
    )

_STATE_RE = re.compile(r'\[hooks\.state\."([^"]+)"\]\s*\n\s*trusted_hash\s*=\s*"([^"]+)"')


def default_config() -> Path:
    return Path.home() / ".codex" / "config.toml"


def fingerprint_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "build-loop" / "codex-hook-fingerprints.json"


def read_trust(config: Path) -> dict[str, str]:
    """Map "<hooks.json path>:<event>:<group>:<index>" -> trusted hash."""
    try:
        text = config.read_text(errors="replace")
    except OSError:
        return {}
    return {m.group(1): m.group(2) for m in _STATE_RE.finditer(text)}


def read_hooks(hooks_json: Path) -> list[dict[str, Any]]:
    """Flatten hooks.json into positional entries mirroring Codex's key scheme."""
    try:
        data = json.loads(hooks_json.read_text(errors="replace"))
    except (OSError, ValueError):
        return []
    out: list[dict[str, Any]] = []
    for event, groups in (data.get("hooks") or {}).items():
        token = EVENT_KEYS.get(event, event.lower())
        if not isinstance(groups, list):
            continue
        for gi, group in enumerate(groups):
            for hi, hook in enumerate((group or {}).get("hooks") or []):
                command = str(hook.get("command") or "")
                out.append({
                    "event": event,
                    "key_suffix": f"{token}:{gi}:{hi}",
                    "command": command,
                    "digest": hashlib.sha256(command.encode()).hexdigest()[:16],
                })
    return out


def _load_fingerprints(path: Path) -> dict[str, str]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_fingerprints(path: Path, data: dict[str, str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.part")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return


def check(
    hooks_json: Path,
    config: Path | None = None,
    fingerprints: Path | None = None,
    record: bool = True,
) -> dict[str, Any]:
    hooks_json = Path(hooks_json).resolve()
    trust = read_trust(config or default_config())
    entries = read_hooks(hooks_json)

    fp_file = fingerprints or fingerprint_path()
    seen = _load_fingerprints(fp_file)
    updated = dict(seen)

    untrusted: list[dict[str, Any]] = []
    drifted: list[dict[str, Any]] = []

    for entry in entries:
        key = f"{hooks_json}:{entry['key_suffix']}"
        if key not in trust:
            untrusted.append({
                "key": entry["key_suffix"],
                "event": entry["event"],
                "command": entry["command"][:120],
            })
            continue
        prior = seen.get(key)
        if prior and prior != entry["digest"]:
            drifted.append({
                "key": entry["key_suffix"],
                "event": entry["event"],
                "command": entry["command"][:120],
                "was": prior,
                "now": entry["digest"],
            })
        updated[key] = entry["digest"]

    if record:
        _save_fingerprints(fp_file, updated)

    return {
        "hooks_json": str(hooks_json),
        "hooks_registered": len(entries),
        "trusted": len(entries) - len(untrusted),
        "untrusted": untrusted,
        "position_drift": drifted,
        "ok": not untrusted and not drifted,
    }


def sweep(roots: list[Path], config: Path | None = None,
          fingerprints: Path | None = None) -> dict[str, Any]:
    """Check every ``.codex/hooks.json`` under ``roots``.

    A per-repo check answers "is THIS repo wired". The 2026-08-23 sweep showed
    the question that actually matters is fleet-wide: 28 of 46 registered hooks
    across 10 repos had never been trusted, and five repos had none trusted at
    all. One repo at a time hides that.
    """
    repos: list[dict[str, Any]] = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for hooks_json in sorted(root.glob("*/.codex/hooks.json")) + sorted(
            root.glob("*/*/.codex/hooks.json")
        ):
            # Worktrees are transient checkouts of a repo already counted here.
            # Codex keys trust by absolute path, so each one looks like a fresh
            # untrusted repo and would inflate the gap with noise nobody acts on.
            if _is_worktree(hooks_json):
                continue
            result = check(hooks_json, config, fingerprints, record=False)
            result["repo"] = hooks_json.parent.parent.name
            repos.append(result)
    seen: set[str] = set()
    unique = []
    for r in repos:
        if r["hooks_json"] in seen:
            continue
        seen.add(r["hooks_json"])
        unique.append(r)
    return {
        "repos": unique,
        "repos_checked": len(unique),
        "hooks_registered": sum(r["hooks_registered"] for r in unique),
        "hooks_untrusted": sum(len(r["untrusted"]) for r in unique),
        "repos_fully_dead": [
            r["repo"] for r in unique if r["hooks_registered"] and not r["trusted"]
        ],
    }


def render_sweep(result: dict[str, Any]) -> str:
    lines = [
        f"Codex hook trust — {result['repos_checked']} repo(s), "
        f"{result['hooks_registered']} hook(s) registered, "
        f"**{result['hooks_untrusted']} will not run**",
        "",
    ]
    for r in sorted(result["repos"], key=lambda x: -len(x["untrusted"])):
        dead = len(r["untrusted"])
        mark = "OK " if not dead else "GAP"
        lines.append(f"  {mark} {r['repo']:<40} {r['trusted']:>2}/{r['hooks_registered']:<2} trusted")
    if result["repos_fully_dead"]:
        lines += ["", "  No hook runs at all in: " + ", ".join(result["repos_fully_dead"])]
    return "\n".join(lines) + "\n"


def render(result: dict[str, Any]) -> str:
    if not result["hooks_registered"]:
        return ""
    if result["ok"]:
        return ""
    lines = ["⚠️  Codex hooks registered but not trusted — they will not run:"]
    for item in result["untrusted"]:
        lines.append(f"   {item['event']} {item['key']}  {item['command'][:70]}")
    if result["position_drift"]:
        lines.append("")
        lines.append("⚠️  Trusted positions now hold DIFFERENT commands (ordinal shift):")
        for item in result["position_drift"]:
            lines.append(f"   {item['event']} {item['key']}  {item['command'][:70]}")
        lines.append("   A merge likely inserted a hook group ahead of these.")
    lines.append("")
    lines.append("   Start a Codex session in this repo and accept the hook-trust prompt.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, default=Path.cwd())
    parser.add_argument("--hooks-json", type=Path, help="override the hooks.json path")
    parser.add_argument("--config", type=Path, help="override ~/.codex/config.toml")
    parser.add_argument("--fingerprints", type=Path, help="override the fingerprint store")
    parser.add_argument("--no-record", action="store_true",
                        help="do not update the fingerprint store")
    parser.add_argument("--sweep", type=Path, action="append", metavar="ROOT",
                        help="check every .codex/hooks.json under ROOT (repeatable)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.sweep:
        result = sweep(args.sweep, args.config, args.fingerprints)
        print(json.dumps(result, indent=2) if args.json else render_sweep(result), end="")
        return 0

    hooks_json = args.hooks_json or (args.workdir / ".codex" / "hooks.json")
    if not Path(hooks_json).is_file():
        if args.json:
            print(json.dumps({"hooks_registered": 0, "ok": True, "reason": "no .codex/hooks.json"}))
        return 0

    result = check(hooks_json, args.config, args.fingerprints, record=not args.no_record)
    print(json.dumps(result, indent=2) if args.json else render(result), end="")
    # Advisory: a gap is reported, never blocking.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
