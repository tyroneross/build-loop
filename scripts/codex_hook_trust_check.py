#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Report recorded Codex hook grants without claiming native trust verification.

Codex owns the definition and plugin-content fingerprint algorithms. This
advisory reads its TOML state, reports missing/disabled grants and observed
command changes, and leaves recorded hashes unverified. It never creates a
trust grant, disables a hook, or substitutes a command digest for a native hash.
Stdlib only, Python 3.11+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tomllib
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

_HASH_RE = re.compile(r"sha256:[0-9a-fA-F]{64}\Z")

def default_config() -> Path:
    return Path.home() / ".codex" / "config.toml"


def fingerprint_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "build-loop" / "codex-hook-fingerprints.json"


def read_trust(config: Path) -> dict[str, dict[str, Any]]:
    """Read both legacy definition and modern content grants, including enabled."""
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    hooks = data.get("hooks", {})
    state = hooks.get("state", {}) if isinstance(hooks, dict) else None
    if not isinstance(state, dict):
        raise ValueError("hooks.state must be a TOML table")
    return {key: value for key, value in state.items() if isinstance(value, dict)}


def plugin_identity(hooks_json: Path) -> str | None:
    """Recognize the installed Codex cache layout; never guess from a subject."""
    parts = hooks_json.parts
    for i in range(len(parts) - 7):
        if parts[i:i + 3] == (".codex", "plugins", "cache"):
            tail = parts[i + 3:]
            if len(tail) == 5 and tail[-2:] == ("hooks", "hooks.json"):
                return f"{tail[1]}@{tail[0]}:hooks/hooks.json"
    return None


def read_hooks(hooks_json: Path) -> list[dict[str, Any]]:
    """Flatten hooks.json into positional entries mirroring Codex's key scheme."""
    data = json.loads(hooks_json.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError("hooks.json must contain a hooks object")
    out: list[dict[str, Any]] = []
    for event, groups in data.get("hooks", {}).items():
        token = EVENT_KEYS.get(event, event.lower())
        if not isinstance(groups, list):
            raise ValueError(f"{event}: expected hook groups")
        for gi, group in enumerate(groups):
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError(f"{event}: invalid hook group")
            for hi, hook in enumerate(group["hooks"]):
                if not isinstance(hook, dict) or not isinstance(hook.get("command"), str):
                    raise ValueError(f"{event}: expected command hook")
                command = hook["command"]
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
    errors: list[str] = []
    try:
        trust = read_trust(config or default_config())
    except (OSError, ValueError) as exc:
        trust = {}
        errors.append(f"Cannot read hook trust state: {type(exc).__name__}")
    try:
        entries = read_hooks(hooks_json)
    except (OSError, ValueError) as exc:
        entries = []
        errors.append(f"Cannot read hook definitions: {type(exc).__name__}")

    fp_file = fingerprints or fingerprint_path()
    seen = _load_fingerprints(fp_file)
    updated = dict(seen)
    untrusted: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    disabled: list[dict[str, Any]] = []
    drifted: list[dict[str, Any]] = []
    identity = plugin_identity(hooks_json) or str(hooks_json)

    for entry in entries:
        key = f"{identity}:{entry['key_suffix']}"
        item = {"key": entry["key_suffix"], "event": entry["event"],
                "command": entry["command"][:120]}
        grant = trust.get(key)
        if grant and grant.get("enabled") is False:
            disabled.append(item)
            continue
        if grant is None:
            untrusted.append({**item, "reason": "no recorded grant"})
            continue
        hashes = [grant[name] for name in ("trusted_hash", "trusted_content_hash") if name in grant]
        if (grant.get("enabled", True) is not True or not hashes
                or any(not isinstance(h, str) or not _HASH_RE.fullmatch(h) for h in hashes)):
            untrusted.append({**item, "reason": "invalid recorded grant"})
            continue
        prior = seen.get(key)
        if prior and prior != entry["digest"]:
            drifted.append({**item, "was": prior, "now": entry["digest"]})
        unknown.append({**item, "reason": "recorded hashes require Codex native verification"})
        updated[key] = entry["digest"]

    if record:
        _save_fingerprints(fp_file, updated)

    return {
        "hooks_json": str(hooks_json),
        "hooks_registered": len(entries),
        "trusted": 0,
        "recorded_grants": len(unknown),
        "verification": "unknown" if unknown or errors else "not_required",
        "untrusted": untrusted,
        "unknown": unknown,
        "disabled": disabled,
        "position_drift": drifted,
        "errors": errors,
        "ok": not untrusted and not drifted and not unknown and not errors,
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
            r["repo"] for r in unique if r["hooks_registered"] and len(r["untrusted"]) == r["hooks_registered"]
        ],
    }


def render_sweep(result: dict[str, Any]) -> str:
    lines = [
        f"Codex hook trust — {result['repos_checked']} repo(s), "
        f"{result['hooks_registered']} hook(s) registered, "
        f"{result['hooks_untrusted']} missing or invalid grants",
        "",
    ]
    for r in sorted(result["repos"], key=lambda x: -len(x["untrusted"])):
        dead = len(r["untrusted"])
        mark = "UNK" if r["unknown"] else ("OK " if r["ok"] else "GAP")
        lines.append(f"  {mark} {r['repo']:<40} {r['recorded_grants']:>2}/{r['hooks_registered']:<2} recorded grants")
    if result["repos_fully_dead"]:
        lines += ["", "  No hook runs at all in: " + ", ".join(result["repos_fully_dead"])]
    return "\n".join(lines) + "\n"


def render(result: dict[str, Any], quiet_unknown: bool = False) -> str:
    if result["ok"] or (quiet_unknown and not any(
            result[key] for key in ("errors", "untrusted", "position_drift"))):
        return ""
    lines = ["Codex hook trust: review recorded state in /hooks."]
    lines.extend(f"   {error}" for error in result["errors"])
    for item in result["untrusted"]:
        lines.append(f"   {item['event']} {item['key']}: {item['reason']}")
    if result["unknown"]:
        lines.append(f"   {len(result['unknown'])} recorded grant(s); current native hashes unverified.")
    if result["position_drift"]:
        lines.append("   Observed command changes at recorded positions (possible ordinal shift):")
        for item in result["position_drift"]:
            lines.append(f"   {item['event']} {item['key']}  {item['command'][:70]}")
    lines.append("   Only Codex can confirm current hook trust; this check changes no grants.")
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
    parser.add_argument("--quiet-unknown", action="store_true",
                        help="suppress text when only native verification is unknown; JSON is unchanged")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.sweep:
        result = sweep(args.sweep, args.config, args.fingerprints)
        quiet = args.quiet_unknown and not any(
            row[key] for row in result["repos"] for key in ("errors", "untrusted", "position_drift"))
        print(json.dumps(result, indent=2) if args.json else ("" if quiet else render_sweep(result)), end="")
        return 0

    hooks_json = args.hooks_json or (args.workdir / ".codex" / "hooks.json")
    if not Path(hooks_json).is_file():
        if args.json:
            print(json.dumps({"hooks_registered": 0, "ok": True, "reason": "no .codex/hooks.json"}))
        return 0

    result = check(hooks_json, args.config, args.fingerprints, record=not args.no_record)
    print(json.dumps(result, indent=2) if args.json else render(result, quiet_unknown=args.quiet_unknown), end="")
    # Advisory: a gap is reported, never blocking.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
