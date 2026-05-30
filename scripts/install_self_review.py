#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""install_self_review.py — install/uninstall/status the build-loop self-review launchd jobs.

Subcommands
-----------
  install    Write plist files and load the launchd jobs.
  uninstall  Unload the launchd jobs and remove plists.
  status     Report whether each job is loaded.

Options
-------
  --json               Emit result as JSON to stdout.
  --plist-dir PATH     Override ~/Library/LaunchAgents/ (for tests only).
  --repo PATH          Override repo root detection (for tests only).

Defaults (from .build-loop/config.json selfReview block)
---------
  enabled   : true
  autonomy  : apply_push
  light     : daily
  deep      : weekly
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LABEL_LIGHT = "com.tyroneross.buildloop.selfreview-light"
LABEL_DEEP = "com.tyroneross.buildloop.selfreview-deep"

_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "autonomy": "apply_push",
    "light": "daily",
    "deep": "weekly",
}

# CalendarInterval presets
_CADENCE_MAP = {
    "daily": {"Hour": 9, "Minute": 0},
    "weekly": {"Weekday": 0, "Hour": 3, "Minute": 0},
    # "disabled" handled by skipping the job entirely
}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _find_repo(override: str | None) -> Path:
    if override:
        return Path(override).resolve()
    here = Path(__file__).resolve().parent
    return here.parent  # scripts/ is one level below repo root


def _load_config(repo: Path) -> dict[str, Any]:
    config_path = repo / ".build-loop" / "config.json"
    base: dict[str, Any] = dict(_DEFAULT_CONFIG)
    if config_path.exists():
        try:
            raw = json.loads(config_path.read_text())
            sr_block = raw.get("selfReview", {})
            base.update({k: sr_block[k] for k in _DEFAULT_CONFIG if k in sr_block})
        except Exception:
            pass  # fail-soft; use defaults
    return base


# ---------------------------------------------------------------------------
# Plist generation
# ---------------------------------------------------------------------------


def _plist_xml(
    label: str,
    mode: str,
    repo: Path,
    cadence: str,
    plist_dir: Path,
    log_suffix: str,
) -> str:
    """Return plist XML string. Never writes to disk — caller decides where."""
    if cadence not in _CADENCE_MAP:
        raise ValueError(f"unsupported cadence: {cadence!r}")

    cal = _CADENCE_MAP[cadence]
    cal_xml_lines = "\n".join(
        f"\t\t\t<key>{k}</key>\n\t\t\t<integer>{v}</integer>" for k, v in cal.items()
    )

    script = str(repo / "scripts" / "self_review_run.sh")
    log_path = str(repo / ".build-loop" / "self-review" / f"launchd-{log_suffix}.log")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>Label</key>
\t<string>{label}</string>
\t<key>ProgramArguments</key>
\t<array>
\t\t<string>/bin/bash</string>
\t\t<string>{script}</string>
\t\t<string>{mode}</string>
\t</array>
\t<key>EnvironmentVariables</key>
\t<dict>
\t\t<key>BUILDLOOP_SELF_REVIEW_REPO</key>
\t\t<string>{repo}</string>
\t</dict>
\t<key>StartCalendarInterval</key>
\t<dict>
{cal_xml_lines}
\t</dict>
\t<key>StandardOutPath</key>
\t<string>{log_path}</string>
\t<key>StandardErrorPath</key>
\t<string>{log_path}</string>
\t<key>RunAtLoad</key>
\t<false/>
</dict>
</plist>
"""


# ---------------------------------------------------------------------------
# launchctl helpers (fail-soft — errors returned, not raised)
# ---------------------------------------------------------------------------


def _uid() -> str:
    return str(os.getuid())


def _bootout(label: str) -> tuple[bool, str]:
    """Idempotently unload a job. Returns (ok, detail)."""
    target = f"gui/{_uid()}/{label}"
    r = subprocess.run(
        ["launchctl", "bootout", target],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        return True, f"bootout {label}: ok"
    # exit 3 / 36 = "not loaded" — that's fine
    if r.returncode in (3, 36) or "No such process" in (r.stderr or ""):
        return True, f"bootout {label}: was not loaded"
    return False, f"bootout {label}: exit {r.returncode} {r.stderr.strip()}"


def _load(label: str, plist_path: Path) -> tuple[bool, str]:
    """Load a job. Tries bootstrap first, falls back to load -w."""
    target = f"gui/{_uid()}"
    r = subprocess.run(
        ["launchctl", "bootstrap", target, str(plist_path)],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        return True, f"bootstrap {label}: ok"
    # fallback for older macOS
    r2 = subprocess.run(
        ["launchctl", "load", "-w", str(plist_path)],
        capture_output=True,
        text=True,
    )
    if r2.returncode == 0:
        return True, f"load -w {label}: ok"
    return False, (
        f"bootstrap exit {r.returncode} {r.stderr.strip()}; "
        f"load -w exit {r2.returncode} {r2.stderr.strip()}"
    )


def _is_loaded(label: str) -> bool:
    r = subprocess.run(
        ["launchctl", "list", label],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_install(args: argparse.Namespace) -> dict[str, Any]:
    repo = _find_repo(args.repo)
    config = _load_config(repo)
    plist_dir = Path(args.plist_dir) if args.plist_dir else Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {"status": "ok", "enabled": config["enabled"], "jobs": []}

    if not config["enabled"]:
        result["status"] = "noop"
        result["reason"] = "selfReview.enabled is false in .build-loop/config.json"
        return result

    jobs = [
        (LABEL_LIGHT, "light", config["light"], "light"),
        (LABEL_DEEP, "deep", config["deep"], "deep"),
    ]

    for label, mode, cadence, log_suffix in jobs:
        job_result: dict[str, Any] = {"label": label, "cadence": cadence}

        if cadence == "disabled":
            job_result["status"] = "skipped"
            job_result["reason"] = "cadence is disabled"
            result["jobs"].append(job_result)
            continue

        if cadence not in _CADENCE_MAP:
            job_result["status"] = "error"
            job_result["reason"] = f"unknown cadence {cadence!r}"
            result["jobs"].append(job_result)
            continue

        plist_path = plist_dir / f"{label}.plist"
        try:
            xml = _plist_xml(label, mode, repo, cadence, plist_dir, log_suffix)
            plist_path.write_text(xml)
            job_result["plist_path"] = str(plist_path)
        except Exception as exc:
            job_result["status"] = "error"
            job_result["reason"] = f"plist write failed: {exc}"
            result["jobs"].append(job_result)
            continue

        ok_out, detail_out = _bootout(label)
        ok_in, detail_in = _load(label, plist_path)

        job_result["status"] = "loaded" if ok_in else "error"
        job_result["details"] = [detail_out, detail_in]
        result["jobs"].append(job_result)

    if any(j.get("status") == "error" for j in result["jobs"]):
        result["status"] = "partial"

    return result


def cmd_uninstall(args: argparse.Namespace) -> dict[str, Any]:
    plist_dir = Path(args.plist_dir) if args.plist_dir else Path.home() / "Library" / "LaunchAgents"
    result: dict[str, Any] = {"status": "ok", "jobs": []}

    for label in (LABEL_LIGHT, LABEL_DEEP):
        job_result: dict[str, Any] = {"label": label}
        ok, detail = _bootout(label)
        job_result["bootout"] = detail

        plist_path = plist_dir / f"{label}.plist"
        if plist_path.exists():
            plist_path.unlink()
            job_result["plist_removed"] = True
        else:
            job_result["plist_removed"] = False

        job_result["status"] = "ok" if ok else "error"
        result["jobs"].append(job_result)

    return result


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    plist_dir = Path(args.plist_dir) if args.plist_dir else Path.home() / "Library" / "LaunchAgents"
    result: dict[str, Any] = {"status": "ok", "jobs": []}

    for label in (LABEL_LIGHT, LABEL_DEEP):
        loaded = _is_loaded(label)
        plist_path = plist_dir / f"{label}.plist"
        result["jobs"].append({
            "label": label,
            "loaded": loaded,
            "plist_exists": plist_path.exists(),
            "plist_path": str(plist_path),
        })

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Manage build-loop self-review launchd jobs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--json", action="store_true", help="Emit result as JSON to stdout.")
    p.add_argument("--plist-dir", dest="plist_dir", default="", help="Override LaunchAgents dir (tests).")
    p.add_argument("--repo", default="", help="Override repo root detection (tests).")

    sub = p.add_subparsers(dest="subcommand")

    for name, help_text in [
        ("install", "Write plists and load launchd jobs."),
        ("uninstall", "Unload jobs and remove plists."),
        ("status", "Report loaded/not-loaded state."),
    ]:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--json", action="store_true", help="Emit result as JSON to stdout.")
    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        sys.exit(0)

    dispatch = {"install": cmd_install, "uninstall": cmd_uninstall, "status": cmd_status}
    result = dispatch[args.subcommand](args)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        # Human-readable summary
        print(f"status: {result.get('status', '?')}")
        for job in result.get("jobs", []):
            label = job.get("label", "?")
            status = job.get("status") or ("loaded" if job.get("loaded") else "not-loaded")
            print(f"  {label}: {status}")
        if "reason" in result:
            print(f"  reason: {result['reason']}")


if __name__ == "__main__":
    main()
