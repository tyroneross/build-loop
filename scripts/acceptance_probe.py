#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""acceptance_probe.py — the acceptance-probe contract (gate #1).

Binds each Phase-1 Assess criterion to a deterministic Phase-4 Review re-run so a
criterion's own repro cannot silently fall out of scope.

CONTRACT
========
A defect/behavioral criterion in ``goal.md`` carries three fields:

  - ``acceptance_probe`` : a paste-ready command that reproduces the failure
  - ``baseline``         : the captured failing value the probe returned at Assess
  - ``boundary``         : the boundary the probe observes — one of
                           ``data | api | render | console | visual``

Optional:

  - ``defect_class``     : true when the criterion fixes an observed defect (vs a
                           net-new behavioral criterion). A defect-class criterion
                           with NO probe is a HARD failure (it had a reproducible
                           bug at Assess and must carry its repro); a non-defect
                           criterion with no probe degrades to ``unverifiable`` and
                           is flagged, never silently passed.

PHASE 1 ASSESS classification (``classify``)
  verifiable    — probe + baseline + boundary all present
  unverifiable  — missing one or more; flagged, NOT silently passed
  invalid       — defect_class with no probe (hard: a defect must carry its repro)

PHASE 4 REVIEW re-run (``rerun_probe`` + ``gate_verdict``)
  The harness re-executes the probe. A criterion whose probe STILL returns its
  baseline-failure state:
    - CANNOT be marked ``passed``
    - CANNOT be deferred inline — deferral routes through ``autonomy_gate.py`` as a
      DECISION-class surface (``decision_command`` builds the pseudo-command the
      gate classifies to ``confirm``, landing it in the report's ``## Held``).

The re-run is "still at baseline" when the probe's current output matches the
captured baseline (the bug is unchanged). Match is substring/normalized-equality
on the baseline text — the baseline is the *failing signal* to look for, not an
exact full-output snapshot, so a probe that emits extra lines around the same
error still counts as still-failing.

CLI
===
  # Phase 1: validate that every criterion in goal.md carries a probe contract
  python3 scripts/acceptance_probe.py classify --goal .build-loop/goal.md --json

  # Phase 4: re-run every probe and emit per-criterion gate verdicts
  python3 scripts/acceptance_probe.py rerun --goal .build-loop/goal.md \
      --workdir "$PWD" --json

Exit codes (classify):
  0 — all criteria verifiable, OR only unverifiable (flagged) criteria
  1 — at least one invalid criterion (defect_class without a probe)
  2 — error (goal file unreadable / unparseable)

Exit codes (rerun):
  0 — no criterion is blocked (all passed/resolved/unverifiable)
  1 — at least one criterion is blocked (probe still at baseline → DECISION)
  2 — error

PARSING
=======
Criteria are read from a fenced ``acceptance_probe`` block in goal.md OR from a
sidecar ``.build-loop/acceptance-probes.json``. The fenced form keeps goal.md the
single source of truth; the sidecar is for tooling that prefers JSON. Both parse
to the same schema. goal.md fenced block shape:

```acceptance_probe
[
  {
    "id": "C1",
    "criterion": "search for 'AI startup funding' returns vector-routed results",
    "acceptance_probe": "curl -s localhost:3000/api/search?q=AI+startup+funding | jq .route",
    "baseline": "\"keyword\"",
    "boundary": "api",
    "defect_class": true
  }
]
```

A goal.md with NO fenced block and no sidecar parses to zero criteria — the gate
is additive/opt-in for existing runs that predate the contract (``classify``
exits 0 with an empty list and a ``no_probes`` note).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

VALID_BOUNDARIES = ("data", "api", "render", "console", "visual")

# Fenced block in goal.md:  ```acceptance_probe  ...  ```
_FENCE_RE = re.compile(
    r"```acceptance_probe\s*\n(.*?)\n```",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class ProbeParseError(ValueError):
    """Raised when the acceptance_probe source cannot be parsed."""


def parse_goal_probes(goal_path: Path) -> list[dict[str, Any]]:
    """Extract the criteria list from a goal.md fenced block or sidecar JSON.

    Precedence: a fenced ```acceptance_probe block in goal.md wins; if absent,
    fall back to ``<goal_dir>/acceptance-probes.json``. Missing both → [].
    """
    criteria: list[dict[str, Any]] | None = None

    if goal_path.exists():
        text = goal_path.read_text()
        m = _FENCE_RE.search(text)
        if m:
            body = m.group(1).strip()
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError as exc:
                raise ProbeParseError(
                    f"acceptance_probe block in {goal_path} is not valid JSON: {exc}"
                ) from exc
            if not isinstance(parsed, list):
                raise ProbeParseError(
                    f"acceptance_probe block in {goal_path} must be a JSON array"
                )
            criteria = parsed

    if criteria is None:
        sidecar = goal_path.parent / "acceptance-probes.json"
        if sidecar.exists():
            try:
                parsed = json.loads(sidecar.read_text())
            except json.JSONDecodeError as exc:
                raise ProbeParseError(
                    f"{sidecar} is not valid JSON: {exc}"
                ) from exc
            if not isinstance(parsed, list):
                raise ProbeParseError(f"{sidecar} must be a JSON array")
            criteria = parsed

    if criteria is None:
        return []

    out: list[dict[str, Any]] = []
    for i, c in enumerate(criteria):
        if not isinstance(c, dict):
            raise ProbeParseError(f"criterion #{i} is not a JSON object")
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# Classification (Phase 1 Assess)
# ---------------------------------------------------------------------------


def classify_criterion(crit: dict[str, Any]) -> dict[str, Any]:
    """Classify a single criterion's probe contract.

    Returns {id, status, missing[], boundary, defect_class, reason}.
    status ∈ {verifiable, unverifiable, invalid}.
    """
    cid = str(crit.get("id") or crit.get("criterion") or "unnamed")
    defect_class = bool(crit.get("defect_class", False))

    probe = crit.get("acceptance_probe")
    baseline = crit.get("baseline")
    boundary = crit.get("boundary")

    missing: list[str] = []
    if not (isinstance(probe, str) and probe.strip()):
        missing.append("acceptance_probe")
    # baseline may legitimately be an empty string ("" = empty output is the
    # failing value), so check presence by key, not truthiness.
    if "baseline" not in crit or baseline is None:
        missing.append("baseline")
    if not (isinstance(boundary, str) and boundary.strip()):
        missing.append("boundary")
    elif boundary not in VALID_BOUNDARIES:
        missing.append(f"boundary(invalid:{boundary})")

    if not missing:
        return {
            "id": cid,
            "status": "verifiable",
            "missing": [],
            "boundary": boundary,
            "defect_class": defect_class,
            "reason": "probe + baseline + boundary present",
        }

    # Missing fields. A defect-class criterion with no probe is invalid (hard):
    # it had a reproducible bug at Assess and must carry its repro.
    if defect_class and "acceptance_probe" in missing:
        return {
            "id": cid,
            "status": "invalid",
            "missing": missing,
            "boundary": boundary if boundary in VALID_BOUNDARIES else None,
            "defect_class": True,
            "reason": "defect-class criterion has no acceptance_probe — a defect "
            "must carry its reproducible repro",
        }

    return {
        "id": cid,
        "status": "unverifiable",
        "missing": missing,
        "boundary": boundary if boundary in VALID_BOUNDARIES else None,
        "defect_class": defect_class,
        "reason": "missing " + ", ".join(missing) + " — flagged unverifiable, "
        "not silently passed",
    }


def classify_all(criteria: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify every criterion. Returns the summary envelope + per-criterion list."""
    results = [classify_criterion(c) for c in criteria]
    verifiable = [r for r in results if r["status"] == "verifiable"]
    unverifiable = [r for r in results if r["status"] == "unverifiable"]
    invalid = [r for r in results if r["status"] == "invalid"]

    if not criteria:
        verdict = "no_probes"
    elif invalid:
        verdict = "invalid"
    elif unverifiable:
        verdict = "flagged"
    else:
        verdict = "ok"

    return {
        "verdict": verdict,
        "counts": {
            "total": len(criteria),
            "verifiable": len(verifiable),
            "unverifiable": len(unverifiable),
            "invalid": len(invalid),
        },
        "criteria": results,
    }


# ---------------------------------------------------------------------------
# Re-run harness (Phase 4 Review)
# ---------------------------------------------------------------------------


def _normalize(s: str) -> str:
    """Collapse whitespace for tolerant baseline comparison."""
    return re.sub(r"\s+", " ", s.strip())


def still_at_baseline(baseline: str, current_output: str) -> bool:
    """True when the probe's current output still carries the baseline-failure.

    The baseline is the *failing signal* to look for, not an exact snapshot. We
    match when the normalized baseline appears as a substring of the normalized
    current output (a probe emitting extra context around the same error still
    counts as still-failing). An empty baseline ("" — empty output is the bug)
    matches only when current output is also empty.
    """
    nb = _normalize(baseline)
    nc = _normalize(current_output)
    if nb == "":
        return nc == ""
    return nb in nc


def rerun_probe(
    crit: dict[str, Any],
    workdir: Path,
    timeout: int = 60,
) -> dict[str, Any]:
    """Execute a single criterion's probe and compare to baseline.

    Returns {id, boundary, ran, exit_code, output, rerun_state, error}.
    rerun_state ∈ {still_failing, resolved, error, skipped}.
      skipped     — criterion is not verifiable (no probe); nothing to re-run
      still_failing — probe output still matches the captured baseline
      resolved    — probe ran and no longer matches the baseline
      error       — probe could not be executed (timeout, OS error)
    """
    cls = classify_criterion(crit)
    cid = cls["id"]
    boundary = crit.get("boundary")

    if cls["status"] != "verifiable":
        return {
            "id": cid,
            "boundary": boundary,
            "ran": False,
            "exit_code": None,
            "output": "",
            "rerun_state": "skipped",
            "error": f"not verifiable ({cls['status']}: missing {cls['missing']})",
        }

    probe = crit["acceptance_probe"]
    baseline = str(crit["baseline"])

    try:
        r = subprocess.run(
            probe,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(workdir),
        )
    except subprocess.TimeoutExpired:
        return {
            "id": cid,
            "boundary": boundary,
            "ran": True,
            "exit_code": None,
            "output": "",
            "rerun_state": "error",
            "error": f"probe timed out after {timeout}s",
        }
    except OSError as exc:
        return {
            "id": cid,
            "boundary": boundary,
            "ran": False,
            "exit_code": None,
            "output": "",
            "rerun_state": "error",
            "error": f"probe could not run: {exc}",
        }

    combined = (r.stdout or "") + (r.stderr or "")
    state = "still_failing" if still_at_baseline(baseline, combined) else "resolved"
    return {
        "id": cid,
        "boundary": boundary,
        "ran": True,
        "exit_code": r.returncode,
        "output": combined[:4000],
        "rerun_state": state,
        "error": None,
    }


def gate_verdict(rerun_result: dict[str, Any]) -> str:
    """Map a re-run result to the Review gate verdict.

    Returns one of:
      passed        — probe resolved; criterion may be marked passed
      blocked       — probe still at baseline; CANNOT pass, CANNOT inline-defer.
                      Deferral must route through autonomy_gate as DECISION.
      unverifiable  — no probe to re-run; flagged (falls through to LLM judging)
      error         — probe could not be executed; surfaced, not silently passed
    """
    state = rerun_result["rerun_state"]
    if state == "resolved":
        return "passed"
    if state == "still_failing":
        return "blocked"
    if state == "skipped":
        return "unverifiable"
    return "error"


def decision_command(crit: dict[str, Any]) -> str:
    """Build the pseudo-command handed to autonomy_gate.py for a blocked criterion.

    The deferral of a still-failing criterion is a DECISION-class surface: it must
    require explicit operator confirmation, not an inline prose defer. autonomy_gate
    classifies an arbitrary label/command; we route through its repo override
    (``confirmFor`` pattern ``defer acceptance criterion *``) so the verdict is
    ``confirm`` → the item lands in the report's ``## Held`` section.

    Returns the command string; the caller passes it as
    ``--action "defer acceptance criterion <id>" --command "<this>"``.
    """
    cid = str(crit.get("id") or crit.get("criterion") or "unnamed")
    return f"defer acceptance criterion {cid}"


def rerun_all(
    criteria: list[dict[str, Any]],
    workdir: Path,
    timeout: int = 60,
) -> dict[str, Any]:
    """Re-run every probe and emit per-criterion gate verdicts + summary."""
    results: list[dict[str, Any]] = []
    for c in criteria:
        rr = rerun_probe(c, workdir, timeout=timeout)
        rr["gate_verdict"] = gate_verdict(rr)
        if rr["gate_verdict"] == "blocked":
            rr["decision_command"] = decision_command(c)
        results.append(rr)

    blocked = [r for r in results if r["gate_verdict"] == "blocked"]
    passed = [r for r in results if r["gate_verdict"] == "passed"]
    unverifiable = [r for r in results if r["gate_verdict"] == "unverifiable"]
    errored = [r for r in results if r["gate_verdict"] == "error"]

    return {
        "verdict": "blocked" if blocked else "clear",
        "counts": {
            "total": len(criteria),
            "passed": len(passed),
            "blocked": len(blocked),
            "unverifiable": len(unverifiable),
            "error": len(errored),
        },
        "criteria": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_classify(args: argparse.Namespace) -> int:
    goal_path = Path(args.goal).resolve()
    try:
        criteria = parse_goal_probes(goal_path)
    except ProbeParseError as exc:
        env = {"verdict": "error", "error": str(exc)}
        print(json.dumps(env, indent=2) if args.emit_json else f"error: {exc}")
        return 2

    summary = classify_all(criteria)

    if args.emit_json:
        print(json.dumps(summary, indent=2))
    else:
        c = summary["counts"]
        print(
            f"classify: verdict={summary['verdict']} total={c['total']} "
            f"verifiable={c['verifiable']} unverifiable={c['unverifiable']} "
            f"invalid={c['invalid']}"
        )
        for r in summary["criteria"]:
            print(f"  [{r['status']}] {r['id']} — {r['reason']}")

    # Exit 1 only on invalid (defect-class without probe). Unverifiable is a flag,
    # not a hard failure — additive/opt-in for net-new criteria.
    return 1 if summary["verdict"] == "invalid" else 0


def _cmd_rerun(args: argparse.Namespace) -> int:
    goal_path = Path(args.goal).resolve()
    workdir = Path(args.workdir).resolve()
    try:
        criteria = parse_goal_probes(goal_path)
    except ProbeParseError as exc:
        env = {"verdict": "error", "error": str(exc)}
        print(json.dumps(env, indent=2) if args.emit_json else f"error: {exc}")
        return 2

    summary = rerun_all(criteria, workdir, timeout=args.timeout)

    if args.emit_json:
        print(json.dumps(summary, indent=2))
    else:
        c = summary["counts"]
        print(
            f"rerun: verdict={summary['verdict']} total={c['total']} "
            f"passed={c['passed']} blocked={c['blocked']} "
            f"unverifiable={c['unverifiable']} error={c['error']}"
        )
        for r in summary["criteria"]:
            extra = ""
            if r["gate_verdict"] == "blocked":
                extra = f"  → DECISION: autonomy_gate --command '{r['decision_command']}'"
            print(f"  [{r['gate_verdict']}] {r['id']} ({r['rerun_state']}){extra}")

    return 1 if summary["verdict"] == "blocked" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_classify = sub.add_parser(
        "classify", help="Phase 1: validate each criterion carries a probe contract"
    )
    p_classify.add_argument("--goal", required=True, help="Path to goal.md")
    p_classify.add_argument("--json", action="store_true", dest="emit_json")
    p_classify.set_defaults(func=_cmd_classify)

    p_rerun = sub.add_parser(
        "rerun", help="Phase 4: re-run each probe, emit gate verdicts"
    )
    p_rerun.add_argument("--goal", required=True, help="Path to goal.md")
    p_rerun.add_argument("--workdir", default=".", help="Repo root for probe execution")
    p_rerun.add_argument("--timeout", type=int, default=60, help="Per-probe timeout (s)")
    p_rerun.add_argument("--json", action="store_true", dest="emit_json")
    p_rerun.set_defaults(func=_cmd_rerun)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
