#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Collect and evaluate a run's standing pre-authorization for unattended execution.

Phase 1 (Assess) can ask, ONCE, up front: what repos are in scope, what to do on
an irreversible action, whether a conditional deploy/publish gate is
pre-authorized against a MEASURED threshold, and when to stop. That answer is
recorded here as `.build-loop/preauthorization.json` and consulted for the rest
of the run instead of stopping again on the same class of question.

This is the PROACTIVE counterpart to `question_timeout.py` (which is REACTIVE —
it resolves an already-surfaced question that went unanswered). The two must
never conflict: `question_timeout.py`'s never-auto-resolve carve-out for
PRODUCTION/CONFIRM/BLOCK questions still holds even when a run is
`unattended: true` here — see the block guard in `check_action_guarded()`.

Vocabulary is borrowed directly from `autonomy_gate.py` (`auto`/`confirm`/
`block`) plus one preauthorization-specific verdict, `skip_and_record`, used
when a conditional gate's measured value fails its threshold (skip the action,
record why, keep going — never silently promote to auto).

THE LOAD-BEARING SAFETY PROPERTY: a standing preauthorization can only ever
RELAX a `confirm` into an `auto`, and only for an action it explicitly covers
with a satisfied, evidence-backed condition. It can NEVER produce `auto` for:
  (a) an action it does not cover
  (b) a conditional gate with no measurement supplied
  (c) a conditional gate whose measurement fails its threshold
  (d) anything `autonomy_gate.classify()` calls `block`
  (e) a path outside the recorded `repo_scope`, when a path is being checked
Each of these is an explicit branch in the code below — (a) in `check_action()`,
(b) in `check_action()`, (c) in `evaluate_gate()`, and (d)+(e) in
`check_action_guarded()` / `evaluate_gate_guarded()` — not just documentation.
`evaluate_gate()` on its own enforces ONLY (c); it takes no action string and
no workdir, so it cannot see (d) or (e). Any caller that is about to ACT on an
`evaluate_gate()` result — not just inspect it — MUST go through
`evaluate_gate_guarded()` instead, the same way `check_action_guarded()` is
required over bare `check_action()`. The CLI's `evaluate` subcommand calls
`evaluate_gate_guarded()`, never `evaluate_gate()` directly.

Schema (`.build-loop/preauthorization.json`):
  {
    "run_id": "...", "recorded_at": "<ISO8601>", "unattended": true,
    "repo_scope": ["<abs path or repo slug>", ...],
    "irreversible_policy": "skip_and_record" | "surface_and_wait" | "never_attempt",
    "stop_rule": {"consecutive_failures": 5, "wall_clock_hours": 8, "scope": "same_problem"},
    "conditional_gates": [
      {"id": "...", "action": "<command or action label>",
       "metric": "...", "op": ">=", "threshold": 4.5,
       "measurement_source": "<how the value is obtained — required, non-empty>",
       "on_fail": "skip_and_record"}
    ]
  }

Subcommands:
  record       write the standing preauthorization (validates gates before writing)
  show         print the recorded preauthorization
  check        is an action covered by a standing authorization (no measurement)?
  evaluate     compare a measured value against a named conditional gate, guarded
               by the block check (and scope check when --path is given) — the
               load-bearing surface; exit 0 when authorized, exit 1 when refused
  scope-check  is a path inside the recorded repo_scope?
"""
from __future__ import annotations

import argparse
import json
import operator
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_io import atomic_write_bytes  # noqa: E402

PREAUTH_REL_PATH = Path(".build-loop") / "preauthorization.json"

_VALID_IRREVERSIBLE_POLICY = {"skip_and_record", "surface_and_wait", "never_attempt"}
_REQUIRED_GATE_KEYS = ("id", "action", "metric", "op", "threshold", "measurement_source", "on_fail")

# No epsilon slack anywhere in this table — a boundary measurement (measured ==
# threshold) authorizes exactly, and a fractional shortfall refuses exactly.
_OPS = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
    "==": operator.eq,
    "!=": operator.ne,
}


class PreauthorizationError(ValueError):
    """Raised when a --gate payload or record() call fails validation."""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _preauth_path(workdir: Path) -> Path:
    return Path(workdir) / PREAUTH_REL_PATH


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


def load(workdir: Path) -> dict[str, Any] | None:
    """Return the recorded preauthorization config, or None if absent.

    Absent means "attended run, use the normal (autonomy_gate/question_timeout)
    gates" — NEVER "everything is authorized". Callers must treat None as the
    most conservative state, not the most permissive one.
    """
    path = _preauth_path(workdir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


def _validate_gate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PreauthorizationError("gate payload must be a JSON object")

    missing = [k for k in _REQUIRED_GATE_KEYS if k not in raw]
    if missing:
        raise PreauthorizationError(f"gate missing required key(s): {', '.join(missing)}")

    measurement_source = raw.get("measurement_source")
    if not isinstance(measurement_source, str) or not measurement_source.strip():
        raise PreauthorizationError(
            "gate.measurement_source must be a non-empty string — an authorization "
            "whose evidence source is unnamed cannot be checked later"
        )

    op = raw.get("op")
    if op not in _OPS:
        raise PreauthorizationError(f"gate.op must be one of {sorted(_OPS)}, got {op!r}")

    try:
        float(raw["threshold"])
    except (TypeError, ValueError):
        raise PreauthorizationError(f"gate.threshold must be numeric, got {raw['threshold']!r}") from None

    on_fail = raw.get("on_fail")
    if not isinstance(on_fail, str) or not on_fail.strip():
        raise PreauthorizationError("gate.on_fail must be a non-empty string")

    for key in ("id", "action", "metric"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PreauthorizationError(f"gate.{key} must be a non-empty string")

    return dict(raw)


def record(
    *,
    workdir: Path,
    run_id: str,
    unattended: bool,
    repo_scope: list[str],
    irreversible_policy: str,
    stop_rule_failures: int,
    stop_rule_hours: float,
    stop_rule_scope: str = "same_problem",
    gates_raw: list[str] | None = None,
) -> dict[str, Any]:
    """Validate and atomically write `.build-loop/preauthorization.json`.

    Raises PreauthorizationError on any validation failure. Validation runs to
    completion BEFORE the single atomic_write_bytes() call, so a rejected gate
    (e.g. missing measurement_source) never produces a partial or invalid file —
    the file is simply not written at all.
    """
    if irreversible_policy not in _VALID_IRREVERSIBLE_POLICY:
        raise PreauthorizationError(
            f"irreversible_policy must be one of {sorted(_VALID_IRREVERSIBLE_POLICY)}, "
            f"got {irreversible_policy!r}"
        )

    gates: list[dict[str, Any]] = []
    for raw_gate in gates_raw or []:
        if isinstance(raw_gate, str):
            try:
                parsed = json.loads(raw_gate)
            except json.JSONDecodeError as exc:
                raise PreauthorizationError(f"--gate is not valid JSON: {exc}") from exc
        else:
            parsed = raw_gate
        gates.append(_validate_gate(parsed))

    config: dict[str, Any] = {
        "run_id": run_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "unattended": bool(unattended),
        "repo_scope": list(repo_scope or []),
        "irreversible_policy": irreversible_policy,
        "stop_rule": {
            "consecutive_failures": int(stop_rule_failures),
            "wall_clock_hours": float(stop_rule_hours),
            "scope": stop_rule_scope,
        },
        "conditional_gates": gates,
    }

    path = _preauth_path(workdir)
    atomic_write_bytes(path, json.dumps(config, indent=2).encode("utf-8"))
    return config


# ---------------------------------------------------------------------------
# evaluate_gate — the measurement comparison (see evaluate_gate_guarded below
# for the load-bearing surface any caller acting on the result must use)
# ---------------------------------------------------------------------------


def _find_gate(config: dict[str, Any] | None, gate_id: str) -> dict[str, Any] | None:
    if not config:
        return None
    for gate in config.get("conditional_gates", []) or []:
        if isinstance(gate, dict) and gate.get("id") == gate_id:
            return gate
    return None


def evaluate_gate(config: dict[str, Any] | None, gate_id: str, measured: float) -> dict[str, Any]:
    """Compare `measured` against gate.threshold using gate.op — MEASUREMENT ONLY.

    This function performs the numeric comparison and nothing else: it takes no
    `workdir` and no action string, so it structurally CANNOT consult
    `autonomy_gate.classify()` (safety branch (d)) or `scope_check()` (repo
    scope). Any caller that is about to ACT on this result — not just inspect
    it — MUST call `evaluate_gate_guarded()` instead, which runs the block
    guard and the optional scope check BEFORE delegating here. Calling this
    function directly and then authorizing on its output bypasses those two
    safety branches.

    No epsilon slack — `4.5 >= 4.5` authorizes on the boundary exactly, and a
    measurement that falls even fractionally short (e.g. 4.2045 < 4.5) refuses,
    because the authorization is bound to evidence, not to blanket approval.
    """
    gate = _find_gate(config, gate_id)
    if gate is None:
        # Safety branch (a): nothing to authorize against — never auto.
        return {
            "gate": gate_id,
            "metric": None,
            "op": None,
            "threshold": None,
            "measured": measured,
            "authorized": False,
            "verdict": "confirm",
            "reason": f"no conditional_gate named {gate_id!r} in the standing preauthorization",
        }

    op = gate.get("op")
    threshold = float(gate.get("threshold"))
    compare = _OPS.get(op)
    if compare is None:
        return {
            "gate": gate_id,
            "metric": gate.get("metric"),
            "op": op,
            "threshold": threshold,
            "measured": measured,
            "authorized": False,
            "verdict": "confirm",
            "reason": f"unsupported comparison operator {op!r}",
        }

    measured_val = float(measured)
    authorized = bool(compare(measured_val, threshold))

    if authorized:
        verdict = "auto"
        reason = (
            f"measured {measured_val} {op} threshold {threshold}: "
            "condition satisfied, standing authorization applies"
        )
    else:
        # Safety branch (c): condition failed — NEVER auto. Route to the gate's
        # declared on_fail policy instead (default skip_and_record).
        verdict = gate.get("on_fail") or "skip_and_record"
        reason = (
            f"measured {measured_val} does not satisfy {op} {threshold}: "
            "authorization was bound to evidence, not blanket approval — refusing"
        )

    return {
        "gate": gate_id,
        "metric": gate.get("metric"),
        "op": op,
        "threshold": threshold,
        "measured": measured_val,
        "authorized": authorized,
        "verdict": verdict,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# check_action
# ---------------------------------------------------------------------------


def check_action(config: dict[str, Any] | None, action: str) -> dict[str, Any]:
    """Is `action` covered by a standing authorization, absent any measurement?

    A conditional gate existing for this action is NOT itself an authorization —
    it is a promise to authorize IF a future measurement satisfies it. Without a
    measurement in hand, coverage is always false and the verdict is `confirm`.
    This is safety branch (b): a conditional gate with no measurement supplied
    can never resolve to `auto` through this function.
    """
    if not config:
        return {
            "covered": False,
            "verdict": "confirm",
            "matched": None,
            "reason": "no standing preauthorization recorded",
        }

    for gate in config.get("conditional_gates", []) or []:
        if isinstance(gate, dict) and gate.get("action") == action:
            return {
                "covered": False,
                "verdict": "confirm",
                "matched": gate.get("id"),
                "reason": "conditional_gate_requires_measurement",
            }

    return {
        "covered": False,
        "verdict": "confirm",
        "matched": None,
        "reason": "action not named in any standing conditional_gate",
    }


def _autonomy_baseline_action(workdir: Path, action: str) -> str | None:
    """Best-effort consult of autonomy_gate.classify(). Returns None on failure.

    Import is local + guarded: preauthorization must degrade gracefully (never
    crash) if autonomy_gate.py is ever unavailable, but the guard it provides
    (never relax a `block`) is load-bearing when it IS present.
    """
    try:
        import autonomy_gate
    except Exception:
        return None
    try:
        return autonomy_gate.classify(Path(workdir), "preauthorization-check", action).get("action")
    except Exception:
        return None


def check_action_guarded(workdir: Path, config: dict[str, Any] | None, action: str) -> dict[str, Any]:
    """`check_action()` plus the non-negotiable block guard (needs workdir).

    `autonomy_gate.classify()` is the single source of truth for `block`.
    Whatever `check_action()` or any recorded policy says, a `block` here is
    NEVER relaxed — not to `auto`, not even to `skip_and_record`. This is
    safety branch (d), enforced BEFORE any recorded policy is consulted.
    """
    baseline = _autonomy_baseline_action(workdir, action)
    if baseline == "block":
        return {
            "covered": False,
            "verdict": "block",
            "matched": None,
            "reason": "autonomy_gate classifies this action as block; preauthorization never relaxes a block",
        }
    return check_action(config, action)


def evaluate_gate_guarded(
    workdir: Path,
    config: dict[str, Any] | None,
    gate_id: str,
    measured: float,
    path: str | None = None,
) -> dict[str, Any]:
    """`evaluate_gate()` plus the non-negotiable block guard AND scope check.

    This is THE surface any caller must use before ACTING on an evaluate
    result — `evaluate_gate()` alone only performs the measurement comparison
    and cannot see `action` or `workdir`, so it cannot enforce safety branches
    (a)/(d) from the module docstring. Order matters and is fixed:

      1. `autonomy_gate.classify()` — a `block` here is NEVER relaxed, no
         matter how satisfied `measured` is. Checked first, before the
         measurement is even looked at.
      2. `scope_check()` — when `path` is supplied, a path outside every
         recorded `repo_scope` entry is refused, again regardless of the
         measurement.
      3. Only once both guards clear does this delegate to `evaluate_gate()`
         for the actual threshold comparison.
    """
    gate = _find_gate(config, gate_id)
    action = gate.get("action") if isinstance(gate, dict) else None
    if action is not None:
        baseline = _autonomy_baseline_action(workdir, action)
        if baseline == "block":
            return {
                "gate": gate_id,
                "metric": gate.get("metric") if isinstance(gate, dict) else None,
                "op": gate.get("op") if isinstance(gate, dict) else None,
                "threshold": gate.get("threshold") if isinstance(gate, dict) else None,
                "measured": measured,
                "authorized": False,
                "verdict": "block",
                "reason": "autonomy_gate classifies this gate's action as block; preauthorization never relaxes a block",
            }

    if path is not None:
        scope_result = scope_check(config, path)
        if not scope_result["in_scope"]:
            return {
                "gate": gate_id,
                "metric": gate.get("metric") if isinstance(gate, dict) else None,
                "op": gate.get("op") if isinstance(gate, dict) else None,
                "threshold": gate.get("threshold") if isinstance(gate, dict) else None,
                "measured": measured,
                "authorized": False,
                "verdict": "block",
                "reason": "path_outside_repo_scope",
            }

    return evaluate_gate(config, gate_id, measured)


# ---------------------------------------------------------------------------
# scope-check
# ---------------------------------------------------------------------------


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def scope_check(config: dict[str, Any] | None, path: str) -> dict[str, Any]:
    """Is `path` inside one of the recorded repo_scope entries?

    Resolves symlinks and collapses `..` traversal on BOTH sides before
    comparing, so a `../` escape out of an authorized repo_scope entry is
    rejected rather than silently allowed.
    """
    target = Path(path).resolve()
    scope_entries = (config or {}).get("repo_scope", []) or []
    for entry in scope_entries:
        try:
            scope_path = Path(entry).resolve()
        except (OSError, RuntimeError):
            continue
        if target == scope_path or _is_within(target, scope_path):
            return {"in_scope": True, "matched": str(scope_path), "path": str(target)}
    return {
        "in_scope": False,
        "matched": None,
        "path": str(target),
        "reason": "path resolves outside every recorded repo_scope entry",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print(data: dict[str, Any], emit_json: bool) -> None:
    if emit_json:
        print(json.dumps(data))
    else:
        print(json.dumps(data, indent=2))


def _cmd_record(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    try:
        config = record(
            workdir=workdir,
            run_id=args.run_id,
            unattended=args.unattended,
            repo_scope=args.repo_scope,
            irreversible_policy=args.irreversible_policy,
            stop_rule_failures=args.stop_rule_failures,
            stop_rule_hours=args.stop_rule_hours,
            stop_rule_scope=args.stop_rule_scope,
            gates_raw=args.gate,
        )
    except PreauthorizationError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    _print(config, args.emit_json)
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    config = load(workdir)
    if config is None:
        _print({"recorded": False, "reason": "no .build-loop/preauthorization.json"}, args.emit_json)
        return 0
    _print({"recorded": True, **config}, args.emit_json)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    config = load(workdir)
    result = check_action_guarded(workdir, config, args.action)
    _print(result, args.emit_json)
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    config = load(workdir)
    result = evaluate_gate_guarded(workdir, config, args.gate, args.measured, path=args.path)
    _print(result, args.emit_json)
    if result["verdict"] == "block":
        return 1
    return 0 if result["authorized"] else 1


def _cmd_scope_check(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    config = load(workdir)
    result = scope_check(config, args.path)
    _print(result, args.emit_json)
    return 0 if result["in_scope"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect and evaluate a run's standing preauthorization for unattended execution."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_record = sub.add_parser("record", help="Record a run's standing preauthorization.")
    p_record.add_argument("--workdir", default=".")
    p_record.add_argument("--run-id", required=True)
    p_record.add_argument("--unattended", action="store_true")
    p_record.add_argument("--repo-scope", action="append")
    p_record.add_argument(
        "--irreversible-policy", required=True, choices=sorted(_VALID_IRREVERSIBLE_POLICY)
    )
    p_record.add_argument("--stop-rule-failures", type=int, default=5)
    p_record.add_argument("--stop-rule-hours", type=float, default=8.0)
    p_record.add_argument("--stop-rule-scope", default="same_problem")
    p_record.add_argument("--gate", action="append", help="JSON gate payload; repeatable")
    p_record.add_argument("--json", action="store_true", dest="emit_json")
    p_record.set_defaults(func=_cmd_record)

    p_show = sub.add_parser("show", help="Show the recorded preauthorization.")
    p_show.add_argument("--workdir", default=".")
    p_show.add_argument("--json", action="store_true", dest="emit_json")
    p_show.set_defaults(func=_cmd_show)

    p_check = sub.add_parser("check", help="Is an action covered by a standing authorization?")
    p_check.add_argument("--workdir", default=".")
    p_check.add_argument("--action", required=True)
    p_check.add_argument("--json", action="store_true", dest="emit_json")
    p_check.set_defaults(func=_cmd_check)

    p_eval = sub.add_parser("evaluate", help="Evaluate a conditional gate against a measured value.")
    p_eval.add_argument("--workdir", default=".")
    p_eval.add_argument("--gate", required=True, help="conditional_gate id")
    p_eval.add_argument("--measured", required=True, type=float)
    p_eval.add_argument(
        "--path", default=None, help="optional path to also verify against repo_scope before authorizing"
    )
    p_eval.add_argument("--json", action="store_true", dest="emit_json")
    p_eval.set_defaults(func=_cmd_evaluate)

    p_scope = sub.add_parser("scope-check", help="Is a path inside the recorded repo_scope?")
    p_scope.add_argument("--workdir", default=".")
    p_scope.add_argument("--path", required=True)
    p_scope.add_argument("--json", action="store_true", dest="emit_json")
    p_scope.set_defaults(func=_cmd_scope_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
