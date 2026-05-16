#!/usr/bin/env python3
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

Verified ecosystem facts (T2, 2026-05-16):
  - npm >= 11.10.0 : native ``minimumReleaseAge`` (unit = DAYS) +
    ``minimumReleaseAgeExclude``.
  - pnpm >= 10.16  : ``minimumReleaseAge`` (unit = MINUTES; 7d = 10080) +
    ``minimumReleaseAgeExclude``.
  - yarn >= 4.10   : ``npmMinimalAgeGate`` (MINUTES) +
    ``npmPreapprovedPackages``.
  - Fallback for older npm : ``npm install --before=<YYYY-MM-DD>`` resolves
    to the latest version published on/before that date. Emitted by the
    backstop hook, not by this script — when npm < 11.10.0 this script sets
    ``status: fallback-hook`` so the report states the hook is the active
    gate on this machine.
  Reference: https://gist.github.com/mcollina/b294a6c39ee700d24073c0e5a4e93104

Stdlib only (build-loop minimal-deps rule). Atomic temp+rename writes,
mirroring ``scripts/write_run_entry.py``. Idempotent: a second run on an
already-configured project produces a byte-identical file (keys merged, not
appended).

Output envelope (``--json``):
    {
      "status": "configured" | "fallback-hook" | "skipped",
      "reason": "<str>",                 # present on skipped/fallback
      "package_manager": "npm"|"pnpm"|"yarn"|null,
      "threshold_days": 7,
      "enforced": true|false,            # is cooldown active for this PM?
      "allowlist": ["@tyroneross/*", ...],
      "config_file": "<rel-path>"|null,
      "npm_version": "10.9.4"|null,      # npm path only
      "changed": true|false              # did this run modify a file?
    }

``--check`` reports ``enforced`` / ``allowlist`` without writing (the hook
calls this).
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
NPM_NATIVE_MIN = (11, 10, 0)  # first npm with native minimumReleaseAge


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
    # Union, default first, de-dup preserving order.
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


# ---------------------------------------------------------------------------
# Per-package-manager config merge (idempotent line-merge, no yaml dep)
# ---------------------------------------------------------------------------
def _merge_lines(existing: str, updates: dict[str, str]) -> tuple[str, bool]:
    """Replace-or-append ``key=value`` / ``key: value`` lines idempotently.

    ``updates`` keys are full left-hand sides incl. separator, e.g.
    ``"minimumReleaseAge="`` or ``"minimumReleaseAge: "``. Returns
    (new_content, changed).
    """
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
    has_key = bool(re.search(r"(?m)^\s*minimumReleaseAge\s*=", existing))

    if ver is None or ver < NPM_NATIVE_MIN:
        # Native key would be inert here. Do NOT claim it works.
        return {
            "status": "fallback-hook",
            "reason": (
                f"npm {'.'.join(map(str, ver)) if ver else 'unknown'} < 11.10.0 — "
                "native minimumReleaseAge unavailable; PreToolUse backstop hook's "
                "--before= date-pin is the active gate on this machine"
            ),
            "package_manager": "npm",
            "enforced": has_key,  # only enforced if a newer npm wrote it before
            "npm_version": ".".join(map(str, ver)) if ver else None,
            "config_file": ".npmrc" if target.is_file() else None,
            "changed": False,
        }

    enforced_before = has_key
    if check:
        return {
            "status": "configured" if enforced_before else "fallback-hook",
            "package_manager": "npm",
            "enforced": enforced_before,
            "npm_version": ".".join(map(str, ver)),
            "config_file": ".npmrc" if target.is_file() else None,
            "changed": False,
        }

    updates = {
        "minimumReleaseAge=": f"minimumReleaseAge={days}",
        "minimumReleaseAgeExclude=": "minimumReleaseAgeExclude=" + ",".join(allowlist),
    }
    new, changed = _merge_lines(existing, updates)
    if changed:
        _atomic_write(target, new)
    return {
        "status": "configured",
        "package_manager": "npm",
        "enforced": True,
        "npm_version": ".".join(map(str, ver)),
        "config_file": ".npmrc",
        "changed": changed,
    }


def _write_pnpm(workdir: Path, allowlist: list[str], days: int, check: bool) -> dict[str, Any]:
    minutes = days * 24 * 60
    target = workdir / "pnpm-workspace.yaml"
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    has_key = bool(re.search(r"(?m)^\s*minimumReleaseAge\s*:", existing))
    if check:
        return {
            "status": "configured" if has_key else "fallback-hook",
            "package_manager": "pnpm",
            "enforced": has_key,
            "config_file": "pnpm-workspace.yaml" if target.is_file() else None,
            "changed": False,
        }
    updates = {
        "minimumReleaseAge:": f"minimumReleaseAge: {minutes}",
        "minimumReleaseAgeExclude:": f"minimumReleaseAgeExclude: {_yaml_list(allowlist)}",
    }
    new, changed = _merge_lines(existing, updates)
    if changed:
        _atomic_write(target, new)
    return {
        "status": "configured",
        "package_manager": "pnpm",
        "enforced": True,
        "config_file": "pnpm-workspace.yaml",
        "changed": changed,
    }


def _write_yarn(workdir: Path, allowlist: list[str], days: int, check: bool) -> dict[str, Any]:
    minutes = days * 24 * 60
    target = workdir / ".yarnrc.yml"
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    has_key = bool(re.search(r"(?m)^\s*npmMinimalAgeGate\s*:", existing))
    if check:
        return {
            "status": "configured" if has_key else "fallback-hook",
            "package_manager": "yarn",
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
        "status": "configured",
        "package_manager": "yarn",
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
            f"allowlist={env.get('allowlist')} "
            + (f"reason={env['reason']}" if env.get("reason") else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
