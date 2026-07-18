# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Never-silently-skip fallback.

When the retro cannot run (Fable unavailable, budget exhausted, crash, timeout,
concurrent-lock) the work is NOT lost. Per the standing flagged-issue route:

  * build-loop's OWN repo  → a local backlog item (``scripts/backlog.py new``).
  * any OTHER repo         → an Operations-Center task (``file_to_operations_center.py``).

The falsifier this guards against: a push that produces neither a retro nor a
fallback entry. The primary trigger caller is a DETACHED process with
stdout/stderr = DEVNULL, so a ``filed: false`` receipt would otherwise vanish
(plan-critic WARN, adopted). Therefore whenever the CLI route fails we ALSO drop
a durable LOCAL failure marker under ``<git-common-dir>/build-loop-retro/failed/``
— a witness that survives even when both the retro and the Ops-Center CLI are
down. The session-start drain surfaces it.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from post_push_retro import coverage as _coverage


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL, text=True, timeout=15
        ).strip()
    except Exception:
        return ""


def repo_name(repo: Path) -> str:
    top = _git(repo, "rev-parse", "--show-toplevel")
    if top:
        return Path(top).name
    return Path(repo).resolve().name


def is_build_loop_repo(repo: Path) -> bool:
    """True when this IS build-loop's own repo (route findings to the local
    backlog, never to Operations Center)."""
    if repo_name(repo) == "build-loop":
        return True
    manifest = Path(repo) / ".claude-plugin" / "plugin.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if "build-loop" in str(data.get("name", "")).lower():
                return True
        except (json.JSONDecodeError, OSError):
            pass
    return False


def failed_dir(repo: Path) -> Path:
    d = _coverage.retro_state_dir(repo) / "failed"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def write_local_failure_marker(repo: Path, payload: dict[str, Any]) -> Path | None:
    """Durable local witness — the last-resort record that closes the DEVNULL
    silent-skip hole. Never raises."""
    try:
        d = failed_dir(repo)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = d / f"retro-failed-{ts}.json"
        fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".fail-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({**payload, "recorded_at": ts}, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, target)
        return target
    except Exception:
        return None


def _focus_actions(tier: str) -> list[str]:
    base = [
        "Run the independent Stage-3 retro judge on this delta (the load-bearing "
        "value: it catches false 'build X' recommendations before you act).",
        "Verify each recommendation against current repo state before acting on it.",
    ]
    if tier == "substantial":
        base.insert(0, "Run the full 3-stage recursive-retrospective (Stage-1 retro "
                       "-> Stage-2 packager -> Stage-3 independent judge).")
    return base


def _compose_body(repo: Path, ref_range: str, tier: str, reason: str,
                  focus_actions: list[str]) -> str:
    fa = "\n".join(f"  - {a}" for a in focus_actions)
    return (
        f"Post-push retrospective could not run and was deferred.\n\n"
        f"repo:       {repo_name(repo)}\n"
        f"ref-range:  {ref_range or '(unknown)'}\n"
        f"tier:       {tier}\n"
        f"why:        {reason}\n\n"
        f"Focus actions for a human / the next run:\n{fa}\n"
    )


def _default_backlog(name: str, title: str, body: str, tier: str, reason: str,
                     scripts_dir: Path) -> dict:
    cmd = [
        "python3", str(scripts_dir / "backlog.py"), "new",
        "--repo", name, "--type", "debt", "--priority", "P2",
        "--title", title, "--context", body,
        "--notes", f"deferred-retro tier={tier}; {reason}", "--json",
    ]
    out = subprocess.check_output(cmd, text=True, timeout=30, stderr=subprocess.STDOUT)
    try:
        rec = json.loads(out)
    except json.JSONDecodeError:
        rec = {"filed": True, "raw": out.strip()[:200]}
    rec.setdefault("filed", True)
    return rec


def _default_ops(name: str, title: str, body: str, scripts_dir: Path) -> dict:
    cmd = [
        "python3", str(scripts_dir / "file_to_operations_center.py"),
        "--repo", name, "--title", title, "--spec", body, "--json",
    ]
    proc = subprocess.run(cmd, text=True, timeout=30, capture_output=True)
    try:
        rec = json.loads(proc.stdout)
    except json.JSONDecodeError:
        rec = {"filed": proc.returncode == 0, "raw": (proc.stdout or proc.stderr)[:200]}
    return rec


def write(
    repo: Path,
    ref_range: str,
    tier: str,
    reason: str,
    *,
    focus_actions: list[str] | None = None,
    dry_run: bool = False,
    backlog_fn: Callable[..., dict] | None = None,
    ops_fn: Callable[..., dict] | None = None,
) -> dict[str, Any]:
    """File the deferred retro so it is NEVER silently lost. Returns a receipt
    ``{filed, route, detail, witness}``. On CLI failure, ``filed=false`` and a
    durable local failure marker is written (``witness`` = its path)."""
    repo = Path(repo)
    focus = focus_actions or _focus_actions(tier)
    name = repo_name(repo)
    is_bl = is_build_loop_repo(repo)
    route = "backlog" if is_bl else "ops"
    title = f"Post-push retro deferred ({tier}) — {name} {ref_range}".strip()
    body = _compose_body(repo, ref_range, tier, reason, focus)
    scripts_dir = Path(__file__).resolve().parent.parent

    if dry_run:
        return {"filed": False, "route": f"dry-run:{route}", "detail": {"title": title},
                "witness": None}

    receipt: dict[str, Any]
    try:
        if is_bl:
            receipt = (backlog_fn or (lambda **k: _default_backlog(scripts_dir=scripts_dir, **k)))(
                name=name, title=title, body=body, tier=tier, reason=reason)
        else:
            receipt = (ops_fn or (lambda **k: _default_ops(scripts_dir=scripts_dir, **k)))(
                name=name, title=title, body=body)
        filed = bool(receipt.get("filed", receipt.get("ok", False)))
    except Exception as exc:  # noqa: BLE001 — the fallback must itself never raise
        receipt = {"error": f"{type(exc).__name__}: {exc}"}
        filed = False

    witness = None
    if not filed:
        witness = write_local_failure_marker(repo, {
            "repo": name, "ref_range": ref_range, "tier": tier, "reason": reason,
            "focus_actions": focus, "route_attempted": route, "cli_receipt": receipt,
        })

    return {
        "filed": filed,
        "route": route,
        "detail": receipt,
        "witness": str(witness) if witness else None,
    }
