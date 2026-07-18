# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""CLI: python3 -m post_push_retro {run|drain} --workdir <repo> [...]

- ``run``   — the detached background job spawned by the pre-push arm. Computes
              coverage, runs the deterministic retro, classifies, routes. Consumes
              (deletes) its armed baton. Default no-LLM (a git-hook child) => the
              Fable upgrade is armed for the next LLM context.
- ``drain`` — the durable fallback, called by ``session-start-closeout.sh``.
              Re-runs any STALE armed baton (machine slept / crashed before the
              detached job finished) and ESCALATES a stale queued Fable upgrade to
              the fallback so "medium -> run the judge" can never become "never".

Every path is FAIL-OPEN (exit 0). On an unexpected crash ``run`` still writes a
fallback witness — the whole design forbids a silent skip.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from post_push_retro import config as _config  # noqa: E402
from post_push_retro import coverage as _coverage  # noqa: E402
from post_push_retro import fallback as _fallback  # noqa: E402
from post_push_retro import router as _router  # noqa: E402

STALE_BATON_SECONDS = 60  # a baton older than this => the detached job never finished


def _short_head(repo: Path) -> str:
    return (_coverage._git(repo, "rev-parse", "--short", "HEAD") or "nohead")


def _run_once(repo: Path, cfg: dict, *, pushed_range: str | None,
              llm_available: bool, dry_run: bool) -> dict:
    """Coverage -> deterministic retro -> classify -> route. Returns the decision."""
    checkpoint = _coverage.read_checkpoint(repo)
    th = _config.thresholds(cfg)
    cov = _coverage.compute_coverage(
        repo, checkpoint, pushed_range=pushed_range,
        cap=int(th.get("first_run_cap", 50)))
    if cov["commit_count"] == 0:
        return {"skipped": "no_new_work", "range": cov.get("range_label")}

    if dry_run:
        signals = _router.classifier.extract_signals(cov, None, th)
        tier = _router.classifier.classify(signals, th)
        return {"dry_run": True, "tier": tier, "commit_count": cov["commit_count"],
                "repos_touched": cov["repos_touched"], "range": cov["range_label"]}

    run_id = f"post-push-{_short_head(repo)}"
    retro_result = _router.run_deterministic_retro(repo, run_id)
    budget_exceeded = _config.budget_guard_exceeded(repo, cfg)
    return _router.route(repo, cov, cfg, llm_available=llm_available,
                         retro_result=retro_result, budget_exceeded=budget_exceeded)


def cmd_run(args) -> dict:
    repo = Path(args.workdir).resolve()
    cfg = _config.load(repo)
    if not _config.is_enabled(cfg):
        return {"skipped": "disabled_or_opted_out"}

    pushed_range = None
    baton = Path(args.armed) if args.armed else None
    if baton and baton.exists():
        try:
            pushed_range = json.loads(baton.read_text(encoding="utf-8")).get("pushed_range") or None
        except (json.JSONDecodeError, OSError):
            pushed_range = None

    try:
        decision = _run_once(repo, cfg, pushed_range=pushed_range,
                             llm_available=args.llm_available, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 — never silently skip, even on a crash
        receipt = _fallback.write(repo, pushed_range or "", "unknown",
                                  f"post_push_retro run crashed: {type(exc).__name__}: {exc}")
        decision = {"action": "fallback_crash", "filed": receipt.get("filed"),
                    "witness": receipt.get("witness"), "error": str(exc)}

    # Consume the baton (the work is now recorded via retro / upgrade / fallback).
    if baton and baton.exists() and not args.dry_run:
        try:
            baton.unlink()
        except OSError:
            pass
    return decision


def cmd_drain(args) -> dict:
    """Session-start durable fallback (zero-LLM shell hook calls this).

    Three responsibilities: re-run a stale baton, escalate a stale queued Fable
    upgrade, and DELIVER any last-resort ``failed/`` witness (re-file it now that
    the CLI may be back up) — without which the witness would sit in ``.git/``
    read by no one (auditor f2). Emits ``did_work`` + a one-line ``summary`` the
    shell surfaces to the session (auditor f3)."""
    repo = Path(args.workdir).resolve()
    cfg = _config.load(repo)
    result: dict = {"reran_batons": [], "upgrade": None, "failed_witnesses": []}
    if not _config.is_enabled(cfg):
        return {"skipped": "disabled_or_opted_out", "did_work": False, "summary": ""}

    state_dir = _coverage.retro_state_dir(repo)

    # 1. Re-run any STALE armed baton (the detached job never finished).
    now = time.time()
    for baton in sorted(state_dir.glob("armed-*.json")):
        try:
            if now - baton.stat().st_mtime < STALE_BATON_SECONDS:
                continue  # a fresh baton belongs to an in-flight detached job
        except OSError:
            continue
        ns = argparse.Namespace(workdir=str(repo), armed=str(baton),
                                llm_available=False, dry_run=False)
        result["reran_batons"].append({"baton": baton.name, "decision": cmd_run(ns)})

    # 2. Escalate a STALE queued Fable upgrade so "medium/substantial -> judge"
    #    can never become "never" on a repo no LLM context ever opens.
    upgrade = state_dir / "upgrade.json"
    if upgrade.exists():
        try:
            data = json.loads(upgrade.read_text(encoding="utf-8"))
            armed_at = data.get("armed_at", "")
            stale_h = float(_config.thresholds(cfg).get("upgrade_stale_hours", 24))
            age_h = _age_hours(armed_at)
            if age_h is not None and age_h >= stale_h:
                receipt = _fallback.write(
                    repo, data.get("range_label") or "", data.get("tier", "medium"),
                    f"queued Fable upgrade unclaimed for {age_h:.0f}h "
                    f"(>= {stale_h:.0f}h) — no LLM context drained it")
                result["upgrade"] = {"escalated": True, "filed": receipt.get("filed"),
                                     "witness": receipt.get("witness")}
                upgrade.unlink()
            else:
                # still fresh: surface it so the in-context agent sees it this turn.
                result["upgrade"] = {"pending": True, "tier": data.get("tier"),
                                     "range": data.get("range_label"), "age_hours": age_h}
        except (json.JSONDecodeError, OSError) as exc:
            result["upgrade"] = {"error": str(exc)}

    # 3. DELIVER last-resort failure witnesses: the CLI that failed at write time
    #    may be back up now. Re-file each; delete the marker only on success (no
    #    new witness written on a repeat failure — write_witness_on_fail=False).
    for marker in sorted((_fallback.failed_dir(repo)).glob("retro-failed-*.json")):
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        receipt = _fallback.write(
            repo, data.get("ref_range") or "", data.get("tier", "unknown"),
            f"re-filed from local witness: {data.get('reason', '')}",
            write_witness_on_fail=False)
        entry = {"marker": marker.name, "refiled": bool(receipt.get("filed"))}
        if receipt.get("filed"):
            try:
                marker.unlink()
            except OSError:
                pass
        result["failed_witnesses"].append(entry)

    return _with_summary(result)


def _with_summary(result: dict) -> dict:
    """Attach ``did_work`` + a one-line ``summary`` the shell surfaces to the
    session (SessionStart stdout is the injection surface)."""
    parts = []
    if result["reran_batons"]:
        parts.append(f"re-ran {len(result['reran_batons'])} stale retro baton(s)")
    up = result.get("upgrade")
    if isinstance(up, dict) and up.get("escalated"):
        parts.append("escalated a stale Fable upgrade to the backlog/Ops fallback")
    elif isinstance(up, dict) and up.get("pending"):
        parts.append(f"a {up.get('tier')} Fable retro upgrade is PENDING (range {up.get('range')}) "
                     "— run it via `/build-loop:run` retrospective when ready")
    if result["failed_witnesses"]:
        refiled = sum(1 for w in result["failed_witnesses"] if w["refiled"])
        parts.append(f"re-filed {refiled}/{len(result['failed_witnesses'])} deferred-retro witness(es)")
    result["did_work"] = bool(parts)
    result["summary"] = "post-push retro drain: " + "; ".join(parts) if parts else ""
    return result


def _age_hours(iso_ts: str) -> float | None:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y%m%dT%H%M%S%fZ", "%Y%m%dT%H%M%SZ"):
        try:
            dt = datetime.strptime(iso_ts, fmt).replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        except (ValueError, TypeError):
            continue
    return None


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="post_push_retro",
                                 description="Scope-gated post-push retrospective auto-trigger.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the retro for a pushed delta (detached job)")
    r.add_argument("--workdir", required=True)
    r.add_argument("--armed", default=None, help="path to the armed baton to consume")
    r.add_argument("--llm-available", dest="llm_available", action="store_true",
                   help="an LLM context is present (dispatch Fable agents rather than arm)")
    r.add_argument("--dry-run", dest="dry_run", action="store_true")
    r.add_argument("--json", action="store_true")

    d = sub.add_parser("drain", help="session-start durable fallback drain")
    d.add_argument("--workdir", required=True)
    d.add_argument("--json", action="store_true")
    return ap


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "run":
            out = cmd_run(args)
        else:
            out = cmd_drain(args)
    except Exception as exc:  # noqa: BLE001 — fail-open sentinel
        out = {"error": f"{type(exc).__name__}: {exc}"}
    if getattr(args, "json", False):
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(json.dumps(out, sort_keys=True))
    return 0  # always fail-open


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
