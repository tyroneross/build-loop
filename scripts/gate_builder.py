#!/usr/bin/env python3
"""gate_builder.py — scaffold a DRAFT enforced check from a Prevention-Pattern spec.

Move C of the recursive-learning pipeline (see
docs/design/recursive-learning-pipeline.md). Consumes the `enforcement_specs`
emitted by `scripts/learning_to_draft.py` (encoding_target eval/gate/preflight/
approval) and scaffolds, per spec, a pending gate directory:

    .build-loop/gates/experimental/<slug>/
        gate.md         — the spec + status:draft + requires_approval:true
        check.py        — INERT stub (raises NotImplementedError); body to be filled
        test_check.py   — regression-test stub (skipped) documenting old-fails/new-passes

CAREFUL BY DESIGN — this does NOT auto-generate check logic, does NOT wire the gate
into the verify step, and does NOT activate it. A wrong gate (false positive) blocks
all work, so the assertion body is left to a human or a follow-up drafting agent, and
the gate is inert until its body is written, its regression test fails-on-old, and a
human approves it. Stdlib only; imports nothing from learning_to_draft (consumes its
output shape only).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_OUT = ".build-loop/gates/experimental"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "unnamed-gate")[:60]


def _gate_md(spec: dict, slug: str) -> str:
    ev = spec.get("evidence") or []
    ev_lines = "\n".join(f"- {e}" for e in ev) or "- (none recorded)"
    return f"""---
slug: {slug}
encoding_target: {spec.get("encoding_target", "gate")}
status: draft
requires_approval: true
activated: false
---

# Draft gate: {slug}

**Prevention Pattern**
> {spec.get("prevention_pattern", "(missing)")}

| Field | Value |
|---|---|
| Condition | {spec.get("condition", "")} |
| Required behavior | {spec.get("required_behavior", "")} |
| Lever | {spec.get("lever", "")} |
| Actuator | {spec.get("actuator", "")} |
| Verification | {spec.get("verification", "")} |

**Evidence**
{ev_lines}

## Before this gate may activate (human-gated)
1. Fill the assertion body in `check.py` (replace the `NotImplementedError`).
2. Make `test_check.py` demonstrably FAIL on the prior behavior and PASS on the fixed
   behavior (the old-fails/new-passes rule). Remove the skip.
3. A human approves (pending → active) and wires it into the verify step.
This file is INERT until all three are done.
"""


def _check_py(spec: dict, slug: str) -> str:
    return f'''"""DRAFT gate body for: {slug} — INERT until implemented + approved.

Prevention Pattern:
    {spec.get("prevention_pattern", "(missing)")}

Condition : {spec.get("condition", "")}
Behavior  : {spec.get("required_behavior", "")}
Actuator  : {spec.get("actuator", "")}
"""


def check(context: dict) -> dict:
    """Return {{"passed": bool, "detail": str}}. NOT YET IMPLEMENTED.

    A human or a follow-up drafting agent fills this with the actual assertion that
    enforces the Required behavior above. Until then this raises, so the gate cannot
    silently pass (or block) — it is inert by construction.
    """
    raise NotImplementedError(
        "Draft gate {slug!r}: assertion body not written. See gate.md for the spec."
    )
'''.replace("{slug!r}", repr(slug))


def _test_py(spec: dict, slug: str) -> str:
    return f'''"""Regression-test stub for draft gate: {slug}.

Verification basis: {spec.get("verification", "")}

The gate may NOT activate until this test demonstrably FAILS on the prior behavior
and PASSES on the fixed behavior, then the skip is removed.
"""
import pytest


@pytest.mark.skip(reason="draft gate {slug!r}: write the old-fails/new-passes assertion first")
def test_gate_catches_prior_behavior():
    # Arrange: reproduce the prior (bad) behavior the Prevention Pattern targets.
    # Act:     run the gate's check().
    # Assert:  it FAILS on the prior behavior and PASSES on the fixed behavior.
    raise NotImplementedError("write the regression assertion; see gate.md verification")
'''.replace("{slug!r}", repr(slug))


def scaffold(specs: list[dict], out_dir: Path) -> dict:
    created, skipped = [], []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        slug = slugify(spec.get("required_behavior") or spec.get("condition") or "")
        gate_dir = out_dir / slug
        if gate_dir.exists():
            skipped.append({"slug": slug, "reason": "already exists (idempotent)"})
            continue
        gate_dir.mkdir(parents=True, exist_ok=True)
        (gate_dir / "gate.md").write_text(_gate_md(spec, slug), encoding="utf-8")
        (gate_dir / "check.py").write_text(_check_py(spec, slug), encoding="utf-8")
        (gate_dir / "test_check.py").write_text(_test_py(spec, slug), encoding="utf-8")
        created.append({"slug": slug, "path": str(gate_dir)})
    return {"created": created, "skipped": skipped,
            "summary": {"created": len(created), "skipped": len(skipped),
                        "note": "all gates are DRAFT/inert — require body + fail-on-old + human approval to activate"}}


def _load_specs(path: str | None) -> list[dict]:
    raw = sys.stdin.read() if path in (None, "-") else open(path, encoding="utf-8").read()
    data = json.loads(raw)
    # Accept the full learning_to_draft result, or a bare enforcement_specs list.
    if isinstance(data, dict):
        data = data.get("enforcement_specs", data.get("specs", []))
    if not isinstance(data, list):
        raise SystemExit("input must be enforcement_specs (a list), or learning_to_draft output")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scaffold draft gates from Prevention-Pattern specs.")
    ap.add_argument("--in", dest="inp", default="-", help="enforcement_specs JSON (or learning_to_draft output); default stdin.")
    ap.add_argument("--out-dir", dest="out", default=DEFAULT_OUT, help=f"gate dir (default {DEFAULT_OUT}).")
    ap.add_argument("--json", action="store_true", help="emit only the summary.")
    args = ap.parse_args(argv)
    result = scaffold(_load_specs(args.inp), Path(args.out))
    print(json.dumps(result["summary"] if args.json else result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
