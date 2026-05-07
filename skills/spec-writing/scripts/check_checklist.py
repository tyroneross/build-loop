#!/usr/bin/env python3
"""
check_checklist.py — deterministic spec-writing checklist verifier.

Reads a plan markdown file, locates the <!-- checklist --> HTML comment block,
and checks whether each of the 8 required items is answered (not blank, not
the literal placeholder text, and not omitted entirely).

Exit codes:
    0 — all 8 items answered
    1 — one or more items missing or unanswered
    2 — verifier error (file not found, parse failure)

Usage:
    python3 check_checklist.py --plan docs/plans/my-feature.md [--json] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ITEMS: list[tuple[str, str]] = [
    ("item_1_auth_guard",              "Item 1 — Auth guard"),
    ("item_2_external_apis",           "Item 2 — External APIs"),
    ("item_3_rate_limit",              "Item 3 — Rate-limit criterion"),
    ("item_4_discoverability",         "Item 4 — Discoverability"),
    ("item_5_server_client_boundary",  "Item 5 — Server/client boundary"),
    ("item_6_concurrency",             "Item 6 — Concurrency"),
    ("item_7_observability",           "Item 7 — Observability"),
    ("item_8_input_validation",        "Item 8 — Input validation"),
]

# Values that count as "not answered" — case-insensitive, stripped
PLACEHOLDER_PATTERNS: list[re.Pattern] = [
    re.compile(r"^<answer>$"),
    re.compile(r"^\.\.\.$"),
    re.compile(r"^$"),
    re.compile(r"^tbd$", re.IGNORECASE),
    re.compile(r"^todo$", re.IGNORECASE),
    re.compile(r"^none$", re.IGNORECASE),
]

CHECKLIST_RE = re.compile(
    r"<!--\s*checklist\s*(.*?)\s*-->",
    re.DOTALL | re.IGNORECASE,
)

ITEM_LINE_RE = re.compile(
    r"^(Item\s*\d+\s*[—\-–]\s*[^:]+)\s*:\s*(.*)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def extract_checklist_block(text: str) -> str | None:
    """Return the inner text of the first <!-- checklist ... --> block, or None."""
    m = CHECKLIST_RE.search(text)
    return m.group(1).strip() if m else None


def parse_checklist_block(block: str) -> dict[str, str]:
    """
    Parse `Item N — Label: answer` lines from the checklist block.
    Returns a dict mapping the item label prefix (lowercased, spaces→underscores)
    to the answer string.
    """
    parsed: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        m = ITEM_LINE_RE.match(line)
        if m:
            key = _normalize_label(m.group(1))
            parsed[key] = m.group(2).strip()
    return parsed


def _normalize_label(label: str) -> str:
    """'Item 1 — Auth guard' → 'item_1_auth_guard'"""
    s = label.lower()
    s = re.sub(r"[—\-–]+", " ", s)
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", "_", s.strip())
    return s


def is_answered(value: str) -> bool:
    v = value.strip()
    return not any(p.match(v) for p in PLACEHOLDER_PATTERNS)


# ---------------------------------------------------------------------------
# Main verification
# ---------------------------------------------------------------------------

def verify(plan_path: Path) -> dict:
    """
    Returns a result dict:
    {
        "plan": str,
        "checklist_found": bool,
        "findings": [{"item_id": str, "label": str, "answer": str|None, "status": "ok"|"missing"|"unanswered"}],
        "missing_count": int,
        "exit_code": int  # 0=clean, 1=failures, 2=error
    }
    """
    try:
        text = plan_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "plan": str(plan_path),
            "checklist_found": False,
            "findings": [],
            "missing_count": len(ITEMS),
            "exit_code": 2,
            "error": str(exc),
        }

    block = extract_checklist_block(text)
    if block is None:
        findings = [
            {
                "item_id": item_id,
                "label": label,
                "answer": None,
                "status": "missing",
                "message": "No <!-- checklist --> block found in plan file.",
            }
            for item_id, label in ITEMS
        ]
        return {
            "plan": str(plan_path),
            "checklist_found": False,
            "findings": findings,
            "missing_count": len(ITEMS),
            "exit_code": 1,
        }

    parsed = parse_checklist_block(block)

    findings = []
    missing = 0
    for item_id, label in ITEMS:
        normalized = _normalize_label(label)
        if normalized not in parsed:
            findings.append({
                "item_id": item_id,
                "label": label,
                "answer": None,
                "status": "missing",
                "message": f"Item not found in checklist block. Expected line starting with '{label}:'",
            })
            missing += 1
        elif not is_answered(parsed[normalized]):
            findings.append({
                "item_id": item_id,
                "label": label,
                "answer": parsed[normalized],
                "status": "unanswered",
                "message": "Answer is a placeholder or empty. Provide a real answer or 'N/A: <reason>'.",
            })
            missing += 1
        else:
            findings.append({
                "item_id": item_id,
                "label": label,
                "answer": parsed[normalized],
                "status": "ok",
            })

    return {
        "plan": str(plan_path),
        "checklist_found": True,
        "findings": findings,
        "missing_count": missing,
        "exit_code": 0 if missing == 0 else 1,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify spec-writing checklist in a plan markdown file."
    )
    parser.add_argument("--plan", required=True, help="Path to the plan markdown file.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit JSON output.")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress human-readable summary (use with --json).")
    args = parser.parse_args()

    plan_path = Path(args.plan)
    result = verify(plan_path)

    if args.as_json:
        print(json.dumps(result, indent=2))

    if not args.quiet:
        status = "CLEAN" if result["missing_count"] == 0 else "INCOMPLETE"
        print(f"check_checklist — {status} ({result['missing_count']} items missing/unanswered)")
        for f in result["findings"]:
            if f["status"] != "ok":
                print(f"  [{f['status'].upper()}] {f['label']}: {f.get('message', '')}")

    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
