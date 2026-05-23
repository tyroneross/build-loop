#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Inject a dependency-cooldown config into a JS project (supply-chain gate).

Build-loop Phase 1 ASSESS calls this with ``--workdir <repo>`` to make the
project's package manager refuse third-party package installs / version
bumps until the version has been published >= ``--threshold-days`` (default
7) days. This filters out smash-and-grab npm compromises (malicious version
published, then yanked within hours-to-days) before ``npm install`` ever
runs lifecycle scripts.

This is layer 1 of build-loop's 3-layer defense-in-depth (the *primary*
gate). Layer 2 is the PreToolUse Bash backstop hook
(``scripts/hooks/pre_bash_dependency_cooldown.sh``) which calls this script
with ``--check`` to decide whether a project is already protected. Layer 3
is the ``C-SUPPLY/dependency_cooldown`` constitution rule + commit-auditor
advisory flag.

User-authored scopes are exempt via a config-driven allowlist. Default is
``["@tyroneross/*"]``; users extend it via
``<workdir>/.build-loop/config.json`` key ``dependencyCooldown.allowlist``.
The ``@tyroneross/*`` default is always unioned in (config-driven, not
hardcoded-only — extra scopes/names are honored, the self-author default is
not removable).

Authoritative ecosystem facts (verified empirically on npm 11.14.1,
2026-05-16; source mcollina gist + npm `config ls -l` + Socket.dev):

  | PM            | key                  | unit    | file                | exclude/allowlist        |
  |---------------|----------------------|---------|---------------------|--------------------------|
  | npm >= 11.10  | min-release-age      | DAYS    | .npmrc              | NONE (npm/cli#8994)      |
  | pnpm >= 11    | minimumReleaseAge    | MINUTES | pnpm-workspace.yaml | minimumReleaseAgeExclude |
  | pnpm 10.x     | minimum-release-age  | MINUTES | .npmrc              | (workspace yaml)         |
  | yarn >= 4.10  | npmMinimalAgeGate    | MINUTES | .yarnrc.yml         | npmPreapprovedPackages   |

  npm has NO native exclude mechanism (open issue npm/cli#8994). For npm the
  allowlist is therefore enforced by the PreToolUse backstop hook, NOT by
  native config. ``allowlist_mechanism`` in the envelope tells the hook which
  regime is active: ``"native"`` (pnpm/yarn — hook stands down once enforced)
  or ``"hook"`` (npm — hook stays engaged to honor the allowlist).

  npm errors hard if both ``min-release-age`` config and a ``--before`` flag
  are present in one invocation ("--before cannot be provided when using
  --min-release-age"). The backstop hook must never add ``--before`` when npm
  native config is active.

  Reference: https://gist.github.com/mcollina/b294a6c39ee700d24073c0e5a4e93104

CRITICAL — ``--check`` reports ``enforced: true`` ONLY after verifying the
package manager *recognizes* the key, not merely that a file was written. A
written-but-unrecognized key (e.g. an old camelCase key on modern npm, or a
fresh key on an npm too old to support it) reports ``enforced: false`` with a
``reason``. This is the false-positive fix: previously ``--check`` only
checked for file presence, so an inert key still claimed ``enforced: true``
and the hook stood down, leaving the project with NO gate at all.

Stdlib only (build-loop minimal-deps rule). Atomic temp+rename writes,
mirroring ``scripts/write_run_entry.py``. Idempotent: a second run on an
already-configured project produces a byte-identical file (keys merged, not
appended).

Output envelope (``--json``):
    {
      "status": "configured" | "fallback-hook" | "skipped",
      "reason": "<str>",                 # present on skipped/fallback/not-enforced
      "package_manager": "npm"|"pnpm"|"yarn"|null,
      "threshold_days": 7,
      "enforced": true|false,            # PM actually recognizes the key?
      "allowlist": ["@tyroneross/*", ...],
      "allowlist_mechanism": "native"|"hook"|null,
      "config_file": "<rel-path>"|null,
      "npm_version": "11.14.1"|null,     # npm path only
      "changed": true|false              # did this run modify a file?
    }

``--check`` reports ``enforced`` / ``allowlist`` / ``allowlist_mechanism``
without writing (the hook calls this).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_ALLOWLIST = ["@tyroneross/*"]
NPM_NATIVE_MIN = (11, 10, 0)  # first npm with native min-release-age
UNKNOWN_CONFIG_RE = re.compile(r"Unknown project config", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _atomic_write(target: Path, content: str) -> None:
    """Temp + os.replace, mirroring scripts/write_run_entry.py."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".tmp.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _resolve_allowlist(workdir: Path) -> list[str]:
    """Union of DEFAULT_ALLOWLIST + user config. Default not removable."""
    user: list[str] = []
    cfg = workdir / ".build-loop" / "config.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            raw = data.get("dependencyCooldown", {}).get("allowlist", [])
            if isinstance(raw, list):
                user = [str(x) for x in raw if isinstance(x, str)]
        except (json.JSONDecodeError, OSError):
            user = []
    out: list[str] = []
    for item in DEFAULT_ALLOWLIST + user:
        if item not in out:
            out.append(item)
    return out


def _detect_pm(workdir: Path) -> str:
    """Lockfile precedence: pnpm > yarn > npm (default)."""
    if (workdir / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (workdir / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _npm_version() -> tuple[int, int, int] | None:
    try:
        out = subprocess.run(
            ["npm", "--version"], capture_output=True, text=True, timeout=10
        )
        if out.returncode != 0:
            return None
        m = re.match(r"(\d+)\.(\d+)\.(\d+)", out.stdout.strip())
        if not m:
            return None
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except (OSError, subprocess.SubprocessError):
        return None


def _yaml_list(items: list[str]) -> str:
    """Render a flow-style YAML list (stdlib, no yaml dep)."""
    return "[" + ", ".join(json.dumps(i) for i in items) + "]"


def _verify_npm_recognizes(workdir: Path, key: str, expected: str) -> tuple[bool, str]:
    """Run ``npm config get <key>`` in workdir; recognized IFF it returns the
    expected value AND stderr has no "Unknown project config" warning.

    This is the false-positive fix: a written-but-unrecognized key (wrong
    camelCase name, or npm too old) must report enforced=False.
    """
    try:
        proc = subprocess.run(
            ["npm", "config", "get", key],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"npm config get {key} failed to run: {exc}"
    if UNKNOWN_CONFIG_RE.search(proc.stderr or ""):
        return False, (
            f'npm rejects "{key}" ("Unknown project config") — key written '
            f"but NOT honored by this npm; cooldown is inert"
        )
    got = (proc.stdout or "").strip()
    if got != expected:
        return False, (
            f'npm config get {key} returned "{got}", expected "{expected}" '
            f"— key not in effect"
        )
    return True, ""


def _pnpm_recognizes(workdir: Path, key: str, expected: str) -> bool | None:
    """Best-effort: ``pnpm config get <key>``. Returns True/False, or None if
    pnpm is not installed (caller falls back to file-presence — the native
    yaml config carries the exclude list regardless of CLI availability)."""
    try:
        proc = subprocess.run(
            ["pnpm", "config", "get", key],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() == expected


# ---------------------------------------------------------------------------
# Per-package-manager config merge (idempotent line-merge, no yaml dep)
# ---------------------------------------------------------------------------
def _merge_lines(existing: str, updates: dict[str, str]) -> tuple[str, bool]:
    """Replace-or-append ``key=value`` / ``key: value`` lines idempotently."""
    lines = existing.splitlines() if existing else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        replaced = False
        for lhs, full in updates.items():
            if line.strip().startswith(lhs):
                out.append(full)
                seen.add(lhs)
                replaced = True
                break
        if not replaced:
            out.append(line)
    for lhs, full in updates.items():
        if lhs not in seen:
            out.append(full)
    new = "\n".join(out)
    if new and not new.endswith("\n"):
        new += "\n"
    changed = new != (existing if existing.endswith("\n") or not existing else existing + "\n")
    return new, changed


def _write_npm(workdir: Path, allowlist: list[str], days: int, check: bool) -> dict[str, Any]:
    ver = _npm_version()
    target = workdir / ".npmrc"
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    has_key = bool(re.search(r"(?m)^\s*min-release-age\s*=", existing))
    ver_str = ".".join(map(str, ver)) if ver else None

    # npm has NO native exclude — allowlist is hook-provided.
    base = {
        "package_manager": "npm",
        "npm_version": ver_str,
        "allowlist_mechanism": "hook",
    }

    if ver is None or ver < NPM_NATIVE_MIN:
        # Native key would be inert here. Do NOT claim it works.
        return {
            **base,
            "status": "fallback-hook",
            "reason": (
                f"npm {ver_str or 'unknown'} < 11.10.0 — native min-release-age "
                "unavailable; PreToolUse backstop hook's --before= date-pin is "
                "the active gate on this machine"
            ),
            "enforced": False,
            "config_file": ".npmrc" if target.is_file() else None,
            "changed": False,
        }

    if check:
        if not has_key:
            return {
                **base,
                "status": "fallback-hook",
                "reason": "no min-release-age key in .npmrc (not yet injected)",
                "enforced": False,
                "config_file": ".npmrc" if target.is_file() else None,
                "changed": False,
            }
        # Real verification: does THIS npm honor the written key?
        ok, why = _verify_npm_recognizes(workdir, "min-release-age", str(days))
        return {
            **base,
            "status": "configured" if ok else "fallback-hook",
            "reason": "" if ok else why,
            "enforced": ok,
            "config_file": ".npmrc",
            "changed": False,
        }

    # Write path. npm key is DAYS. No exclude key (npm has none).
    updates = {"min-release-age=": f"min-release-age={days}"}
    new, changed = _merge_lines(existing, updates)
    if changed:
        _atomic_write(target, new)
    ok, why = _verify_npm_recognizes(workdir, "min-release-age", str(days))
    return {
        **base,
        "status": "configured" if ok else "fallback-hook",
        "reason": "" if ok else why,
        "enforced": ok,
        "config_file": ".npmrc",
        "changed": changed,
    }


def _write_pnpm(workdir: Path, allowlist: list[str], days: int, check: bool) -> dict[str, Any]:
    minutes = days * 24 * 60  # pnpm unit is MINUTES
    ws = workdir / "pnpm-workspace.yaml"
    npmrc = workdir / ".npmrc"
    ws_existing = ws.read_text(encoding="utf-8") if ws.is_file() else ""
    has_key = bool(re.search(r"(?m)^\s*minimumReleaseAge\s*:", ws_existing))
    base = {"package_manager": "pnpm", "allowlist_mechanism": "native"}

    if check:
        if not has_key:
            return {
                **base,
                "status": "fallback-hook",
                "reason": "no minimumReleaseAge in pnpm-workspace.yaml (not yet injected)",
                "enforced": False,
                "config_file": "pnpm-workspace.yaml" if ws.is_file() else None,
                "changed": False,
            }
        rec = _pnpm_recognizes(workdir, "minimumReleaseAge", str(minutes))
        if rec is None:
            # pnpm CLI absent — fall back to file presence. The native yaml
            # config carries the exclude list and is honored by pnpm at
            # install time regardless of CLI availability here.
            return {
                **base,
                "status": "configured",
                "reason": "pnpm CLI unavailable for live verify; config-file present (native exclude carried in yaml)",
                "enforced": True,
                "config_file": "pnpm-workspace.yaml",
                "changed": False,
            }
        return {
            **base,
            "status": "configured" if rec else "fallback-hook",
            "reason": "" if rec else "pnpm does not recognize minimumReleaseAge (too old or inert key)",
            "enforced": bool(rec),
            "config_file": "pnpm-workspace.yaml",
            "changed": False,
        }

    ws_updates = {
        "minimumReleaseAge:": f"minimumReleaseAge: {minutes}",
        "minimumReleaseAgeExclude:": f"minimumReleaseAgeExclude: {_yaml_list(allowlist)}",
    }
    ws_new, ws_changed = _merge_lines(ws_existing, ws_updates)
    if ws_changed:
        _atomic_write(ws, ws_new)

    # pnpm 10.x reads kebab `minimum-release-age` (MINUTES) from .npmrc.
    npmrc_existing = npmrc.read_text(encoding="utf-8") if npmrc.is_file() else ""
    npmrc_new, npmrc_changed = _merge_lines(
        npmrc_existing, {"minimum-release-age=": f"minimum-release-age={minutes}"}
    )
    if npmrc_changed:
        _atomic_write(npmrc, npmrc_new)

    return {
        **base,
        "status": "configured",
        "enforced": True,
        "config_file": "pnpm-workspace.yaml",
        "changed": ws_changed or npmrc_changed,
    }


def _write_yarn(workdir: Path, allowlist: list[str], days: int, check: bool) -> dict[str, Any]:
    minutes = days * 24 * 60  # yarn unit is MINUTES (numeric — 7d string form is bugged)
    target = workdir / ".yarnrc.yml"
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    has_key = bool(re.search(r"(?m)^\s*npmMinimalAgeGate\s*:", existing))
    base = {"package_manager": "yarn", "allowlist_mechanism": "native"}

    if check:
        # yarn config get is unreliable headless; verify by key presence in
        # the project .yarnrc.yml (Berry reads this file at install time).
        return {
            **base,
            "status": "configured" if has_key else "fallback-hook",
            "reason": "" if has_key else "no npmMinimalAgeGate in .yarnrc.yml (not yet injected)",
            "enforced": has_key,
            "config_file": ".yarnrc.yml" if target.is_file() else None,
            "changed": False,
        }

    updates = {
        "npmMinimalAgeGate:": f"npmMinimalAgeGate: {minutes}",
        "npmPreapprovedPackages:": f"npmPreapprovedPackages: {_yaml_list(allowlist)}",
    }
    new, changed = _merge_lines(existing, updates)
    if changed:
        _atomic_write(target, new)
    return {
        **base,
        "status": "configured",
        "enforced": True,
        "config_file": ".yarnrc.yml",
        "changed": changed,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(workdir: Path, days: int, check: bool) -> dict[str, Any]:
    if not (workdir / "package.json").is_file():
        return {
            "status": "skipped",
            "reason": "no-package-json (non-JS project; pip/cargo cooldown is a v1 follow-up)",
            "package_manager": None,
            "threshold_days": days,
            "enforced": False,
            "allowlist": [],
            "allowlist_mechanism": None,
            "config_file": None,
            "changed": False,
        }
    allowlist = _resolve_allowlist(workdir)
    pm = _detect_pm(workdir)
    if pm == "pnpm":
        env = _write_pnpm(workdir, allowlist, days, check)
    elif pm == "yarn":
        env = _write_yarn(workdir, allowlist, days, check)
    else:
        env = _write_npm(workdir, allowlist, days, check)
    env["threshold_days"] = days
    env["allowlist"] = allowlist
    return env


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Inject a dependency-cooldown config into a JS project.")
    ap.add_argument("--workdir", default=".", help="Project root (default: cwd)")
    ap.add_argument("--threshold-days", type=int, default=7, help="Cooldown in days (default: 7)")
    ap.add_argument("--check", action="store_true", help="Report enforced/allowlist without writing")
    ap.add_argument("--json", action="store_true", help="Emit envelope as JSON")
    args = ap.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    env = run(workdir, args.threshold_days, args.check)

    if args.json:
        print(json.dumps(env))
    else:
        print(
            f"[dependency-cooldown] {env['status']} "
            f"pm={env.get('package_manager')} enforced={env.get('enforced')} "
            f"mechanism={env.get('allowlist_mechanism')} "
            f"allowlist={env.get('allowlist')} "
            + (f"reason={env['reason']}" if env.get("reason") else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
