#!/usr/bin/env python3
"""Regression test for the SessionEnd retro-sweep noise gate.

Provenance: between 2026-08-19 and 2026-08-29 the sweep emitted 64 consecutive
`needs-attention` markers. Every one of them — 100%, with zero recurring-lesson
items — came from one shape family: a run of `Bash:command` steps terminated by
`Skill:skill` or `ToolSearch:query`. `sequence_is_generic` gated only when EVERY
step's tool was in GENERIC_TOOL_PREFIXES, so a single placeholder terminator
whose tool sat outside that set rescued an otherwise fully-generic run.

The fix gates on whether a STEP names specific work, not on whether its tool is
core. This test pins both directions: the 11 real noisy shapes must gate, and
sequences naming real work must survive (an over-broad filter would silence
genuine workflow candidates, which is the same failure in the other direction).
"""
import importlib.util
import pathlib
import sys

SWEEP = pathlib.Path(__file__).resolve().parent / "hooks" / "session_end_retro_sweep.py"


def load():
    spec = importlib.util.spec_from_file_location("sweep", SWEEP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The 11 distinct sequences observed across the 64 real markers.
MUST_GATE = [
    "Bash:command → Bash:command → Bash:command → Bash:command → Bash:command → Skill:skill",
    "Bash:command → Bash:command → ToolSearch:query",
    "Bash:command → Bash:command → Skill:skill",
    "Bash:command → Bash:command → Bash:command → Bash:command → Bash:command → ToolSearch:query",
    "Skill:skill → Bash:command → Bash:command → Bash:command → Bash:command → Bash:command",
    "Skill:skill → Bash:command → Bash:command → Bash:command → Bash:command",
    "Bash:command → Bash:command → Bash:command → Skill:skill",
    "Skill:skill → Bash:command → Bash:command",
    "Bash:command → Bash:command → Bash:command → ToolSearch:query",
    "Bash:command → Bash:command → Bash:command → Bash:command → ToolSearch:query",
    "Bash:command → Bash:command → Bash:command → Bash:command → Skill:skill",
]

# Sequences that name real work — gating these would lose genuine signal.
MUST_SURVIVE = [
    "Skill:build-loop:run → Bash:pytest",
    "Bash:command → Skill:ibr:scan",
    "mcp__operations-center__claim_task → Bash:command",
    "Skill:research:research → Skill:research:save",
    "Bash:command → Bash:command → Skill:prompt-builder:optimize",
]


def main() -> int:
    m = load()
    failures = []

    for s in MUST_GATE:
        c = {"shape": "repeated_tool_sequence", "sequence": s.split(" → ")}
        if not m.sequence_is_generic(c):
            failures.append(f"should gate but did not: {s}")

    for s in MUST_SURVIVE:
        c = {"shape": "repeated_tool_sequence", "sequence": s.split(" → ")}
        if m.sequence_is_generic(c):
            failures.append(f"should survive but was gated: {s}")

    # manual_command_ritual carries its content elsewhere; never gated here.
    ritual = {"shape": "manual_command_ritual",
              "sequence": ["Bash:command", "Skill:skill"]}
    if m.sequence_is_generic(ritual):
        failures.append("manual_command_ritual was gated")

    for f in failures:
        print(f"FAIL: {f}")
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print(f"PASS: {len(MUST_GATE)} noisy shapes gated, "
          f"{len(MUST_SURVIVE)} real shapes preserved, ritual exempt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
