# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tier router — reuse the three existing retro tiers; never rebuild an engine.

Flow (retro-FIRST): the deterministic zero-LLM retro runs BEFORE routing, so its
output feeds the classifier and — crucially — the baseline signal is ALWAYS
captured even when the Fable upgrade is deferred (deterministic-first / AI
narrates). The router then decides the LLM upgrade:

  * trivial     — deterministic capture is the whole job. Done.
  * medium      — deterministic capture + run the independent Stage-3 judge
                  (~1 Fable agent). When no LLM context is present (a git-hook
                  background job), the upgrade is ARMED for the next LLM context.
  * substantial — deterministic capture + the full 3-stage pipeline.

Any retro failure / budget-exceeded routes to the fallback (never a silent skip).
All side-effect callables are injectable so this is unit-tested with no LLM and
no real push.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from post_push_retro import classifier, config as _config, coverage as _coverage, fallback as _fallback


def run_deterministic_retro(
    repo: Path,
    run_id: str,
    *,
    retro_fn: Callable[[Path, str], dict] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run the zero-LLM deterministic retro (``python3 -m retrospective``) — the
    same synthesizer the Phase-4 dispatch and the SessionEnd sweep reuse. Returns
    ``{ok, output, error}``. Never raises."""
    if retro_fn is not None:
        try:
            return {"ok": True, "output": retro_fn(repo, run_id), "error": None}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "output": None, "error": f"{type(exc).__name__}: {exc}"}
    scripts_dir = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(scripts_dir) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(
            ["python3", "-m", "retrospective", "--workdir", str(repo),
             "--run-id", run_id, "--json"],
            check=False, capture_output=True, text=True, timeout=timeout,
            env=env, cwd=str(scripts_dir),
        )
        if proc.returncode != 0:
            return {"ok": False, "output": None,
                    "error": (proc.stderr or "retrospective exited non-zero")[:300]}
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return {"ok": True, "output": out, "error": None}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": None, "error": "retrospective timed out"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "output": None, "error": f"{type(exc).__name__}: {exc}"}


def parse_retro_signals(retro_result: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort semantic signals from the deterministic retro output. Unknown
    schema -> None (classifier degrades to delta-driven tiers). Maps to the real
    retrospective ``run()`` shape: enforce_candidates[] + meta counts."""
    if not retro_result.get("ok"):
        return None
    out = retro_result.get("output") or {}
    if not isinstance(out, dict):
        return None
    meta = out.get("meta") or {}
    enforce = out.get("enforce_candidates") or []
    rec = len(enforce) + int(meta.get("automation_candidate_count", 0) or 0)
    # >=2 issue signals ~ ">=2 failure instances sharing a possible root" (spec).
    failure_cluster = int(meta.get("issue_signal_count", 0) or 0) >= 2
    return {
        "recommendation_count": rec,
        "p0_recommendation": False,  # no deterministic P0 signal; risk-surface covers severity
        "failure_cluster": failure_cluster,
    }


def arm_upgrade(repo: Path, tier: str, coverage: dict[str, Any]) -> Path | None:
    """Queue a Fable tier upgrade for the next LLM context (build-loop Phase 4G
    or a session-start drain). Written to the per-repo shared state dir. Carries
    its own ref-range so it is self-contained if the checkpoint has advanced."""
    try:
        d = _coverage.retro_state_dir(repo)
        payload = {
            "tier": tier,
            "range_label": coverage.get("range_label"),
            "commit_count": coverage.get("commit_count"),
            "commits": coverage.get("commits", [])[:50],
            "armed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "agents": ["stage1", "stage2", "judge"] if tier == "substantial" else ["judge"],
        }
        target = d / "upgrade.json"
        tmp = d / ".upgrade.tmp.json"
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        return target
    except Exception:
        return None


def route(
    repo: Path,
    coverage: dict[str, Any],
    cfg: dict[str, Any],
    *,
    llm_available: bool,
    retro_result: dict[str, Any],
    budget_exceeded: bool = False,
    arm_fn: Callable[[Path, str, dict], Any] | None = None,
    fallback_fn: Callable[..., dict] | None = None,
    checkpoint_fn: Callable[[Path, dict], Any] | None = None,
) -> dict[str, Any]:
    """Decide + execute the tier action. Returns a decision dict for the caller
    (the orchestrator dispatches the named Fable agents when ``llm_available``)."""
    repo = Path(repo)
    th = _config.thresholds(cfg)
    signals = classifier.extract_signals(coverage, parse_retro_signals(retro_result), th)
    tier = classifier.classify(signals, th)
    rng = coverage.get("range_label") or ""
    _fb = fallback_fn or _fallback.write
    _ckpt = checkpoint_fn or _coverage.update_checkpoint_from_coverage

    # The deterministic retro itself failed — nothing was captured => fallback.
    if not retro_result.get("ok"):
        receipt = _fb(repo, rng, tier, f"deterministic retro failed: {retro_result.get('error')}")
        return {"tier": tier, "action": "fallback", "ran_deterministic": False,
                "filed": receipt.get("filed"), "witness": receipt.get("witness"),
                "signals": signals}

    # Deterministic capture succeeded — it is the baseline for every tier.
    if tier == classifier.TIER_TRIVIAL:
        _ckpt(repo, coverage)
        return {"tier": tier, "action": "deterministic_only", "ran_deterministic": True,
                "signals": signals}

    # medium / substantial need the Fable upgrade.
    if budget_exceeded:
        receipt = _fb(repo, rng, tier, "budget guard exceeded; Fable upgrade deferred "
                                       "(deterministic capture retained)")
        _ckpt(repo, coverage)  # deterministic capture is valid; advance checkpoint
        return {"tier": tier, "action": "fallback_budget", "ran_deterministic": True,
                "filed": receipt.get("filed"), "witness": receipt.get("witness"),
                "signals": signals}

    if llm_available:
        _ckpt(repo, coverage)
        agents = ["stage1", "stage2", "judge"] if tier == classifier.TIER_SUBSTANTIAL else ["judge"]
        return {"tier": tier, "action": "dispatch", "agents": agents,
                "ran_deterministic": True, "signals": signals}

    # No LLM context (git-hook background job): arm the upgrade for later.
    baton = (arm_fn or arm_upgrade)(repo, tier, coverage)
    _ckpt(repo, coverage)
    return {"tier": tier, "action": "armed_upgrade",
            "upgrade_baton": str(baton) if baton else None,
            "ran_deterministic": True, "signals": signals}
