#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""
route_decision.py — deterministic Phase 1 synthesis-density routing helper.

The build-loop orchestrator (Mode A and Mode B) decides per plan whether to
fan out to Sonnet code-tier implementers (default) or escalate to a single
Thinking-tier inline execution. The decision follows a 4-priority rule
documented in `agents/build-orchestrator.md` Phase 1 §"Synthesis-density
routing" and mirrored in `skills/build-loop/SKILL.md` Phase 1 step 18. This
script is the deterministic helper the orchestrator can shell out to
instead of inlining the multi-step priority logic each invocation. It reads
a plan markdown file and an optional `state.json`, applies the priority
rule, and prints a JSON verdict to stdout. It is side-effect-free — the
orchestrator is responsible for persisting the verdict to
`state.json.synthesisDensity`.

Stdlib only. Reuses `count_synthesis_dimensions()` from `plan_verify.py`.

Resolution order (must match the orchestrator):
  1. `state.json.config.modelOverrides.thinking` set    → tier=thinking,
                                                          reason=explicit-override
  2. plan frontmatter `tier: thinking`                   → tier=thinking,
                                                          reason=explicit-override
  3. `count_synthesis_dimensions(plan) > 5`              → tier=thinking,
                                                          reason=density-escalate
  4. otherwise                                           → tier=code,
                                                          reason=default-fanout

When the plan path doesn't exist (orchestrator is still pre-Plan in Phase
1) the script exits 0 with reason=no-plan, tier=code — the deterministic
default that lets Phase 1 proceed without blocking on a plan that doesn't
exist yet.

Output JSON shape:
    {
      "tier": "thinking" | "code",
      "reason": "explicit-override" | "density-escalate" | "default-fanout"
                | "no-plan",
      "synthesis_dimensions_count": <int>,
      "details": "<one-line explanation>"
    }
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Reuse plan_verify's parser. Importing as a sibling module via sys.path
# manipulation keeps this script invokable both as `python3 scripts/route_decision.py`
# and `python3 -m scripts.route_decision`.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from plan_verify import count_synthesis_dimensions  # noqa: E402  (path-injected import)


# ---------------------------------------------------------------------------
# state.json reader
# ---------------------------------------------------------------------------

def read_state_thinking_override(state_path: Path) -> bool:
    """Return True iff `state.json.config.modelOverrides.thinking` is set
    to a truthy value. Missing file / malformed JSON / missing keys all
    return False — the orchestrator treats those as "no override"."""
    if not state_path.exists():
        return False
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    config = data.get("config")
    if not isinstance(config, dict):
        return False
    overrides = config.get("modelOverrides")
    if not isinstance(overrides, dict):
        return False
    thinking = overrides.get("thinking")
    # Truthy non-empty string / non-null = override is set.
    return bool(thinking)


# ---------------------------------------------------------------------------
# Plan frontmatter reader
# ---------------------------------------------------------------------------

# Minimal YAML-frontmatter parser for the single `tier:` key. Plan_verify.py
# exposes no frontmatter parser to reuse — the spec's "reuse plan_verify's
# parser" language refers to the synthesis-dimension block parser, which we
# DO reuse via count_synthesis_dimensions(). This hand-rolled reader handles
# only the top-of-file `---` fenced YAML block and only the `tier:` key,
# which is the only frontmatter datum the routing rule consults.

_FRONTMATTER_FENCE = "---"
_TIER_LINE_RE = re.compile(r"^\s*tier\s*:\s*['\"]?(\w+)['\"]?\s*$", re.IGNORECASE)


def read_plan_frontmatter_tier(plan_path: Path) -> str | None:
    """Return the value of the `tier:` key in the plan's YAML frontmatter,
    lowercased, or None if no frontmatter / no `tier:` key / malformed.

    Frontmatter is the YAML block between `---` fences at the top of the
    file (line 1 must be `---`). Reads only what's needed."""
    try:
        with plan_path.open("r", encoding="utf-8") as fh:
            first = fh.readline()
            if first.strip() != _FRONTMATTER_FENCE:
                return None
            for line in fh:
                if line.strip() == _FRONTMATTER_FENCE:
                    return None  # closed the block without finding tier
                m = _TIER_LINE_RE.match(line)
                if m:
                    return m.group(1).lower()
            return None  # EOF before closing fence
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

def decide(plan_path: Path, state_path: Path) -> dict[str, Any]:
    """Apply the 4-priority synthesis-density routing rule.

    Returns the verdict dict (see module docstring for shape)."""
    # Priority 0 (graceful pre-plan): plan file doesn't exist yet.
    if not plan_path.exists():
        return {
            "tier": "code",
            "reason": "no-plan",
            "synthesis_dimensions_count": 0,
            "details": (
                f"plan file not found at {plan_path}; defaulting to code-tier "
                "fan-out per Phase 1 graceful-pre-plan default"
            ),
        }

    # Priority 1: explicit user override via state.json.
    if read_state_thinking_override(state_path):
        # Count dims anyway so the verdict carries full diagnostic data.
        try:
            count = count_synthesis_dimensions(plan_path)
        except Exception:  # noqa: BLE001
            count = 0
        return {
            "tier": "thinking",
            "reason": "explicit-override",
            "synthesis_dimensions_count": count,
            "details": (
                f"state.json.config.modelOverrides.thinking is set "
                f"({state_path}); routing to thinking regardless of density"
            ),
        }

    # Priority 2: explicit override via plan frontmatter.
    fm_tier = read_plan_frontmatter_tier(plan_path)
    if fm_tier == "thinking":
        try:
            count = count_synthesis_dimensions(plan_path)
        except Exception:  # noqa: BLE001
            count = 0
        return {
            "tier": "thinking",
            "reason": "explicit-override",
            "synthesis_dimensions_count": count,
            "details": (
                f"plan frontmatter declares tier: thinking; routing to "
                f"thinking regardless of density"
            ),
        }

    # Priority 3: density-escalate when count > 5 (i.e. 6+ dims).
    count = count_synthesis_dimensions(plan_path)
    if count > 5:
        return {
            "tier": "thinking",
            "reason": "density-escalate",
            "synthesis_dimensions_count": count,
            "details": (
                f"{count} synthesis dimensions (>5 threshold); auto-escalating "
                "to thinking-tier inline per Phase 1 density rule"
            ),
        }

    # Priority 4: default fan-out.
    return {
        "tier": "code",
        "reason": "default-fanout",
        "synthesis_dimensions_count": count,
        "details": (
            f"{count} synthesis dimensions (≤5 threshold); default code-tier "
            "fan-out with C3/C4/C5 backstops"
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_DESCRIPTION = (
    "Deterministic Phase 1 synthesis-density routing helper for build-loop. "
    "Reads a plan markdown file plus an optional state.json, applies the "
    "4-priority routing rule documented in agents/build-orchestrator.md "
    "Phase 1 (explicit-override → density-escalate → default-fanout, with a "
    "no-plan graceful default), and prints a JSON verdict naming the tier "
    "(thinking|code), the reason, the synthesis-dimension count, and a "
    "one-line details string. Side-effect-free: the orchestrator persists "
    "the verdict to state.json.synthesisDensity itself. Reuses "
    "count_synthesis_dimensions() from plan_verify.py."
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "plan",
        nargs="?",
        help="Path to plan markdown file (positional). Use --plan to override.",
    )
    p.add_argument(
        "--plan",
        dest="plan_flag",
        help="Path to plan markdown file (alias for the positional).",
    )
    p.add_argument(
        "--state",
        default=".build-loop/state.json",
        help="Path to build-loop state.json (default: .build-loop/state.json in cwd).",
    )
    p.add_argument(
        "--self-test",
        action="store_true",
        help="Run inline self-tests and exit. Demonstrates each of the 4 reason values.",
    )
    args = p.parse_args(argv)

    if args.self_test:
        return _self_test()

    plan_arg = args.plan_flag or args.plan
    if not plan_arg:
        p.error("plan path is required (positional or --plan)")
    plan_path = Path(plan_arg).expanduser().resolve()
    state_path = Path(args.state).expanduser().resolve()

    verdict = decide(plan_path, state_path)
    print(json.dumps(verdict, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Inline self-test (demonstrates each of the 4 reason values)
# ---------------------------------------------------------------------------

def _self_test() -> int:
    """Demonstrate each of the 4 reason values via an in-tmpdir run.
    Prints PASS/FAIL per case, returns 0 if all pass else 1."""
    import tempfile
    import textwrap

    failures: list[str] = []

    def _check(label: str, got: dict[str, Any], want_tier: str, want_reason: str,
               want_count: int | None = None) -> None:
        ok = (got["tier"] == want_tier and got["reason"] == want_reason)
        if want_count is not None:
            ok = ok and got["synthesis_dimensions_count"] == want_count
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {label}: tier={got['tier']!r} reason={got['reason']!r} "
              f"count={got['synthesis_dimensions_count']}")
        if not ok:
            failures.append(label)

    print("route_decision self-test:")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)

        # Case A: no-plan (file doesn't exist).
        missing_plan = tmpdir / "nope.md"
        missing_state = tmpdir / "no-state.json"
        v = decide(missing_plan, missing_state)
        _check("no-plan", v, "code", "no-plan", 0)

        # Case B: default-fanout (low density, no overrides).
        plan_low = tmpdir / "plan-low.md"
        plan_low.write_text(textwrap.dedent("""\
            # Plan with 3 dims

            synthesis_dimensions:
              cli_form: positional
              caching: print-only
              error_mode: exit-0

            ## Body
            etc.
        """))
        v = decide(plan_low, missing_state)
        _check("default-fanout", v, "code", "default-fanout", 3)

        # Case C: density-escalate (>5 dims, no overrides).
        plan_dense = tmpdir / "plan-dense.md"
        plan_dense.write_text(textwrap.dedent("""\
            # Plan with 6 dims

            synthesis_dimensions:
              dim_a: x
              dim_b: y
              dim_c: z
              dim_d: w
              dim_e: v
              dim_f: u

            ## Body
            etc.
        """))
        v = decide(plan_dense, missing_state)
        _check("density-escalate", v, "thinking", "density-escalate", 6)

        # Case D1: explicit-override via state.json.
        state_override = tmpdir / "state.json"
        state_override.write_text(json.dumps({
            "config": {"modelOverrides": {"thinking": "claude-opus-4-7"}}
        }))
        v = decide(plan_low, state_override)  # low-density plan + override
        _check("explicit-override (state.json)", v, "thinking", "explicit-override", 3)

        # Case D2: explicit-override via plan frontmatter.
        plan_fm = tmpdir / "plan-fm.md"
        plan_fm.write_text(textwrap.dedent("""\
            ---
            tier: thinking
            ---
            # Frontmatter forces thinking

            synthesis_dimensions:
              only: one

            ## Body
        """))
        v = decide(plan_fm, missing_state)
        _check("explicit-override (frontmatter)", v, "thinking", "explicit-override", 1)

        # Case D3: priority — state.json beats frontmatter beats density.
        # state.json override on a dense plan still attributes to state-override.
        v = decide(plan_dense, state_override)
        _check("priority: state.json over density", v, "thinking", "explicit-override", 6)

        # Edge: count == 5 stays default-fanout (>5 means 6+).
        plan_5 = tmpdir / "plan-5.md"
        plan_5.write_text(textwrap.dedent("""\
            # Plan at threshold

            synthesis_dimensions:
              a: 1
              b: 2
              c: 3
              d: 4
              e: 5

            ## Body
        """))
        v = decide(plan_5, missing_state)
        _check("threshold: count==5 stays code", v, "code", "default-fanout", 5)

    if failures:
        print(f"\n{len(failures)} failure(s): {failures}")
        return 1
    print("\nall self-tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
