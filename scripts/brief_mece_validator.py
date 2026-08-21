#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Validate peer handoff briefs for the seven MECE ownership fields."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

MECE_FIELDS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "owns",
        "owns",
        re.compile(r"(?im)^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?owns(?:\*\*)?\s*(?:\(|:|-|$)"),
    ),
    (
        "does_not_own",
        "does-not-own",
        re.compile(
            r"(?im)^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?"
            r"does\s*[-_ ]?not\s*[-_ ]?own(?:s)?(?:\*\*)?\s*(?:\(|:|-|$)"
        ),
    ),
    (
        "interface_contract",
        "interface-contract",
        re.compile(
            r"(?im)^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?"
            r"interface\s*[-_ ]?contract(?:\*\*)?\s*(?:\(|:|-|$)"
        ),
    ),
    (
        "integration_checkpoint",
        "integration-checkpoint",
        re.compile(
            r"(?im)^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?"
            r"integration\s*[-_ ]?checkpoint(?:\*\*)?\s*(?:\(|:|-|$)"
        ),
    ),
    (
        "allowed_tools",
        "allowed-tools",
        re.compile(
            r"(?im)^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?"
            r"allowed[-_ ]?tools(?:\*\*)?\s*(?:\(|:|-|$)"
        ),
    ),
    (
        "denied_tools",
        "denied-tools",
        re.compile(
            r"(?im)^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?"
            r"denied[-_ ]?tools(?:\*\*)?\s*(?:\(|:|-|$)"
        ),
    ),
    (
        "acceptance_criteria",
        "acceptance-criteria",
        re.compile(
            r"(?im)^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?"
            r"acceptance[-_ ]?criteria(?:\*\*)?\s*(?:\(|:|-|$)"
        ),
    ),
)



# ---------------------------------------------------------------------------
# EC-02 rca part 2 (2026-06-13, landed 2026-08-21): a brief that hands a peer an
# ENFORCEMENT mechanism must say how its activation is proven.
#
# plan_verify.py:rule_activation_map_required already enforces this for PLANS —
# build-loop's recurring failure class is "machinery built, activation path never
# verified", four live instances in one week. A handoff brief is the other door
# into the same failure: the plan can be clean while the brief that dispatches the
# work never mentions activation, so the peer builds a gate nothing fires.
#
# The vocabulary is deliberately COPIED from plan_verify rather than reinvented, so
# the two surfaces cannot drift into disagreeing about what "enforcement" means.
#
# WARN, never a hard fail: `valid` stays governed by the seven MECE fields. The
# original proposal specified WARNING, and a brief is authored under time pressure
# mid-dispatch — a blocking check here would be routed around.
_ENFORCEMENT_RE = re.compile(
    r"\b(?:stop\s+hook|sessionstart|pretooluse|posttooluse|cron|launchd|watcher|"
    r"git\s+hook|pre-commit|post-commit|webhook|hooks?\.json|"
    r"(?:repo-level|lifecycle|codex|claude(?:\s+code)?|session|host)\s+hooks?|"
    r"gate|guard|lint|linter|validator|enforce[sd]?|enforcement|blocker)\b",
    re.IGNORECASE,
)
# An activation claim: naming the trigger, or declaring verified-live either way.
_ACTIVATION_CLAIM_RE = re.compile(
    r"\b(?:verified-live\s*:|trigger\s*:|activation\s*(?:map|path)?\s*:|"
    r"fires\s+(?:on|at|when|for)|invoked\s+(?:by|from)|wired\s+(?:in|into|to))",
    re.IGNORECASE,
)
# Anchored so quoting the token in prose cannot silence the check.
_ACTIVATION_EXEMPT_RE = re.compile(
    r"^\s*(?:[-*]\s*)?override\s*:\s*activation-claim-exempt", re.IGNORECASE | re.MULTILINE
)


def _enforcement_without_activation(text: str) -> str | None:
    """WARN message when a brief describes enforcement but claims no activation."""
    if _ACTIVATION_EXEMPT_RE.search(text):
        return None
    m = _ENFORCEMENT_RE.search(text)
    if not m or _ACTIVATION_CLAIM_RE.search(text):
        return None
    return (
        f"brief describes an enforcement mechanism ({m.group(0)!r}) but names no "
        f"activation path — add `trigger:` and `verified-live: yes|pending`, or "
        f"`override: activation-claim-exempt`. Machinery whose trigger is never "
        f"stated is the failure class plan_verify's activation-map rule exists for."
    )


def validate_brief(brief: str) -> dict[str, Any]:
    """Return MECE validation for a handoff brief.

    A valid peer-handoff packet names all seven ownership elements: owns,
    does-not-own, interface-contract, integration-checkpoint, allowed-tools,
    denied-tools, and acceptance-criteria. The validator accepts markdown
    headings, bold bullet labels, or explicit colon fields.
    """
    text = brief or ""
    present: list[str] = []
    missing: list[str] = []
    for key, label, pattern in MECE_FIELDS:
        if pattern.search(text):
            present.append(key)
        else:
            missing.append(label)

    warnings: list[str] = []
    if not text.strip():
        warnings.append("brief is empty")
    activation_warning = _enforcement_without_activation(text)
    if activation_warning:
        warnings.append(activation_warning)

    return {
        "valid": not missing,
        "missing": missing,
        "warnings": warnings,
        "present": present,
    }


def capture_brief(
    brief: str,
    *,
    workdir: Path,
    run_id: str,
    chunk_id: str,
) -> dict[str, Any]:
    """Persist an assembled brief to ``.build-loop/briefs/<run_id>/<chunk_id>.md``.

    Brief text is otherwise retained nowhere: it is passed into an Agent-tool
    prompt and discarded, and ``state.json.runs[]`` carries no brief field.
    Without a copy on disk there is no way to check afterward whether brief
    shape actually varied by the receiving model's tier, which is the only
    evidence separating a live prompting profile from a decorative one.

    Fail-open by contract. Capture is observability, so a write failure
    records a warning and never blocks the dispatch it was meant to observe.
    """
    safe_run = re.sub(r"[^A-Za-z0-9._-]", "_", run_id) or "unknown-run"
    safe_chunk = re.sub(r"[^A-Za-z0-9._-]", "_", chunk_id) or "unknown-chunk"
    dest = Path(workdir).expanduser() / ".build-loop" / "briefs" / safe_run / f"{safe_chunk}.md"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(brief, encoding="utf-8")
    except OSError as exc:
        return {"captured": False, "path": str(dest), "warning": f"brief capture failed: {exc}"}
    return {"captured": True, "path": str(dest), "warning": None}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--brief-file", required=True, help="Path to the handoff brief to lint")
    p.add_argument("--json", action="store_true", help="Accepted for explicitness; output is always JSON")
    p.add_argument("--capture-run-id", default=None,
                   help="Persist the brief to .build-loop/briefs/<run-id>/<chunk-id>.md. "
                        "Requires --capture-chunk-id.")
    p.add_argument("--capture-chunk-id", default=None,
                   help="Chunk/commit id used as the captured brief's filename.")
    p.add_argument("--workdir", default=".", help="Repo root for the capture path.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = Path(args.brief_file).expanduser()
    try:
        brief = path.read_text(encoding="utf-8")
    except OSError as exc:
        result = {
            "valid": False,
            "missing": [label for _key, label, _pattern in MECE_FIELDS],
            "warnings": [f"could not read brief file: {exc}"],
            "present": [],
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1

    result = validate_brief(brief)

    # Capture rides the lint because the lint is already a mandatory call at
    # the moment the brief exists in full. Making capture a side effect here
    # beats adding a step an orchestrator has to remember.
    if args.capture_run_id and args.capture_chunk_id:
        cap = capture_brief(
            brief,
            workdir=Path(args.workdir),
            run_id=args.capture_run_id,
            chunk_id=args.capture_chunk_id,
        )
        result["capture"] = cap
        if cap["warning"]:
            result.setdefault("warnings", []).append(cap["warning"])

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
