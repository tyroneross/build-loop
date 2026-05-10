#!/usr/bin/env python3
"""
check_checklist.py — deterministic spec-writing checklist verifier.

Reads a plan markdown file, locates the <!-- checklist --> HTML comment block,
and checks whether each required item is answered (not blank, not
the literal placeholder text, and not omitted entirely).

Exit codes:
    0 — all required items answered (Items 15 and 17 are conditional on UI files in scope)
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
    ("item_9_stable_id_traceability",  "Item 9 — Stable ID traceability"),
    ("item_10_json_spec_object",       "Item 10 — JSON spec object"),
    ("item_11_blocking_and_novel_question_gate", "Item 11 — Blocking-and-novel question gate"),
    ("item_12_low_reversibility_adrs", "Item 12 — Low-reversibility ADRs"),
    ("item_13_analytical_lens",        "Item 13 — Analytical lens"),
    ("item_14_handoff_document",       "Item 14 — Handoff document"),
    ("item_15_synthesis_dimensions",   "Item 15 — Synthesis dimensions"),
    ("item_16_risk_reason",            "Item 16 — Risk reason"),
    ("item_17_ui_io_contract",         "Item 17 — UI input/output contract"),
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
# Structural validators for items 9-14
# ---------------------------------------------------------------------------

# ID patterns: U-NN, F-NN, D-NN, S-NN, T-NN, A-NN
_ID_RE = re.compile(r"\b[UFDSTA]-\d+\b")
_P0_LINE_RE = re.compile(r"\[P0\]", re.IGNORECASE)
_T_ID_RE = re.compile(r"\bT-\d+\b")
_A_ID_RE = re.compile(r"\bA-\d+\b")
_JSON_SPEC_SECTION_RE = re.compile(r"^##\s+Spec Object.*\(JSON\)", re.IGNORECASE | re.MULTILINE)
_OPEN_Q_SECTION_RE = re.compile(r"^##\s+Open Questions", re.IGNORECASE | re.MULTILINE)
_BLOCKING_TEST_RE = re.compile(r"blocking-test:\s*T-\d+", re.IGNORECASE)
_ADR_HEADING_RE = re.compile(r"^##\s+ADR-\d+", re.IGNORECASE | re.MULTILINE)
_LOW_REV_RE = re.compile(
    r"low-reversib|db choice|auth provider|api contract|public schema",
    re.IGNORECASE,
)
_LENS_LINE_RE = re.compile(r"analytical lens\s*:", re.IGNORECASE)

# Items 15 and 17 — UI-only checks.
_UI_FILE_RE = re.compile(
    r"""
    (
      \b[\w/.\-]+\.(?:tsx|jsx|vue|svelte)\b
      |(?:^|[\s`(/])components/[\w/.\-]+
      |(?:^|[\s`(/])(?:app|pages)/(?!(?:api|_app|_document)\b)[\w/.\-]*(?:page|layout)\.(?:ts|tsx|js|jsx)\b
      |(?:^|[\s`(/])Views/[\w/.\-]+\.swift\b
      |\b[\w/.\-]*(?:View|Screen|Page)\.swift\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SYNTHESIS_BLOCK_RE = re.compile(
    r"synthesis_dimensions\s*:\s*\n((?:[ \t]+[\w_]+\s*:.*\n?)+)", re.IGNORECASE)
_REQUIRED_SYNTHESIS_KEYS = ("placement", "cta_tier", "copy_tone",
                            "visual_weight", "empty_state")
_UI_IO_SECTION_RE = re.compile(
    r"^##\s+UI\s+Input/Output\s+Contract\b", re.IGNORECASE | re.MULTILINE)
_UI_IO_REQUIRED_TERMS = (
    "surface", "input", "output", "taxonomy", "operation", "component",
    "state", "modality", "validation", "traceability",
)


def check_item_15_synthesis_dimensions(plan_text: str) -> tuple[str, str | None]:
    """OK if no UI in scope; else require synthesis_dimensions block w/ all keys."""
    if not _UI_FILE_RE.search(plan_text):
        return ("OK", None)
    m = _SYNTHESIS_BLOCK_RE.search(plan_text)
    if not m:
        return ("FAIL", "UI files detected but no `synthesis_dimensions:` block. "
                "Required keys: " + ", ".join(_REQUIRED_SYNTHESIS_KEYS) + ".")
    body = m.group(1)
    missing = [k for k in _REQUIRED_SYNTHESIS_KEYS
               if not re.search(rf"^\s+{re.escape(k)}\s*:", body, re.MULTILINE)]
    if missing:
        return ("FAIL", "synthesis_dimensions missing key(s): " + ", ".join(missing))
    return ("OK", None)


def check_item_17_ui_io_contract(plan_text: str) -> tuple[str, str | None]:
    """OK if no UI in scope; else require a UI Input/Output Contract section."""
    if not _UI_FILE_RE.search(plan_text):
        return ("OK", None)

    m = _UI_IO_SECTION_RE.search(plan_text)
    if not m:
        return ("FAIL", "UI files detected but no `## UI Input/Output Contract` section found.")

    section_start = m.end()
    next_heading = re.search(r"^##\s+", plan_text[section_start:], re.MULTILINE)
    section = (
        plan_text[section_start: section_start + next_heading.start()]
        if next_heading else plan_text[section_start:]
    )
    lower = section.lower()
    missing = [term for term in _UI_IO_REQUIRED_TERMS if term not in lower]
    if missing:
        return ("FAIL", "UI Input/Output Contract missing term(s): " + ", ".join(missing))
    return ("OK", None)


def _structural_findings(text: str, plan_path: Path) -> list[dict]:
    """
    Run structural checks for higher-order checklist items against the full plan text.
    Returns a list of finding dicts (same shape as checklist findings).
    Each finding has item_id, label, status ('ok'|'warn'|'fail'), and message.
    These supplement — they do not replace — the checklist block checks.
    """
    findings = []

    # Item 9: P0 lines must have at least one T- reference
    p0_lines = [ln for ln in text.splitlines() if _P0_LINE_RE.search(ln)]
    if p0_lines:
        p0_without_test = [ln for ln in p0_lines if not _T_ID_RE.search(ln)]
        if p0_without_test:
            findings.append({
                "item_id": "item_9_stable_id_traceability",
                "label": "Item 9 — Stable ID traceability",
                "status": "warn",
                "message": (
                    f"{len(p0_without_test)} [P0] line(s) found without a linked T-NN test ID. "
                    "Each P0 must trace to at least one acceptance test."
                ),
            })
        else:
            # Also verify at least one full trace chain appears (U-, F-, T-)
            has_chain = (
                bool(_ID_RE.search(text))
                and bool(re.search(r"\bU-\d+\b", text))
                and bool(re.search(r"\bF-\d+\b", text))
            )
            if not has_chain:
                findings.append({
                    "item_id": "item_9_stable_id_traceability",
                    "label": "Item 9 — Stable ID traceability",
                    "status": "warn",
                    "message": (
                        "P0 lines have T- links but no full U-NN → F-NN → T-NN chain found. "
                        "Add stable IDs threading from user need through feature to test."
                    ),
                })

    # Item 10: ## Spec Object (JSON) section must exist
    if not _JSON_SPEC_SECTION_RE.search(text):
        findings.append({
            "item_id": "item_10_json_spec_object",
            "label": "Item 10 — JSON spec object",
            "status": "warn",
            "message": (
                "No '## Spec Object (JSON)' section found. "
                "Emit a structured JSON block before rendering markdown."
            ),
        })

    # Item 11: Open Questions entries must carry blocking-test: annotation
    oq_match = _OPEN_Q_SECTION_RE.search(text)
    if oq_match:
        # Extract the Open Questions section (up to the next ## heading)
        oq_start = oq_match.end()
        next_heading = re.search(r"^##\s+", text[oq_start:], re.MULTILINE)
        oq_body = text[oq_start: oq_start + next_heading.start()] if next_heading else text[oq_start:]
        # Count question lines (non-empty, non-heading lines)
        q_lines = [ln.strip() for ln in oq_body.splitlines()
                   if ln.strip() and not ln.strip().startswith("#")]
        missing_annotation = [ln for ln in q_lines if not _BLOCKING_TEST_RE.search(ln)]
        if missing_annotation:
            findings.append({
                "item_id": "item_11_blocking_and_novel_question_gate",
                "label": "Item 11 — Blocking-and-novel question gate",
                "status": "warn",
                "message": (
                    f"{len(missing_annotation)} Open Question line(s) lack a 'blocking-test: T-NN' "
                    "annotation. Convert non-blocking questions to [ASSUMED: ...] entries."
                ),
            })

    # Item 12: Low-reversibility decision mentions must have ADR link
    low_rev_lines = [ln for ln in text.splitlines() if _LOW_REV_RE.search(ln)]
    adr_headings_exist = bool(_ADR_HEADING_RE.search(text))
    if low_rev_lines and not adr_headings_exist:
        findings.append({
            "item_id": "item_12_low_reversibility_adrs",
            "label": "Item 12 — Low-reversibility ADRs",
            "status": "warn",
            "message": (
                f"{len(low_rev_lines)} low-reversibility decision mention(s) found but no "
                "'## ADR-NN:' section detected. Each such decision needs an ADR record."
            ),
        })

    # Item 13: Analytical lens line must appear in the plan body (outside the checklist block)
    body_text = CHECKLIST_RE.sub("", text)  # strip the <!-- checklist --> block
    if not _LENS_LINE_RE.search(body_text):
        findings.append({
            "item_id": "item_13_analytical_lens",
            "label": "Item 13 — Analytical lens",
            "status": "warn",
            "message": (
                "No 'Analytical lens:' line found in the plan body. "
                "Name the lens (JTBD, QFD, TRIZ, Pugh/AHP, DSM, etc.) in Locked Decisions."
            ),
        })

    # Item 14: Sibling .handoff.md must exist
    slug = plan_path.stem  # e.g. "my-feature" from "my-feature.md"
    handoff_path = plan_path.parent / f"{slug}.handoff.md"
    if not handoff_path.exists():
        findings.append({
            "item_id": "item_14_handoff_document",
            "label": "Item 14 — Handoff document",
            "status": "warn",
            "message": (
                f"Sibling handoff file '{handoff_path.name}' not found. "
                "Generate docs/plans/<slug>.handoff.md alongside the plan."
            ),
        })

    # Item 15: Synthesis dimensions block required when UI is in scope
    status_15, msg_15 = check_item_15_synthesis_dimensions(text)
    if status_15 == "FAIL":
        findings.append({
            "item_id": "item_15_synthesis_dimensions",
            "label": "Item 15 — Synthesis dimensions",
            "status": "fail",
            "message": msg_15,
        })

    # Item 17: UI Input/Output Contract required when UI is in scope
    status_17, msg_17 = check_item_17_ui_io_contract(text)
    if status_17 == "FAIL":
        findings.append({
            "item_id": "item_17_ui_io_contract",
            "label": "Item 17 — UI input/output contract",
            "status": "fail",
            "message": msg_17,
        })

    return findings


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
        "structural_warnings": [{"item_id": str, "label": str, "status": "warn", "message": str}],
        "missing_count": int,
        "structural_warning_count": int,
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
            "structural_warnings": [],
            "missing_count": len(ITEMS),
            "structural_warning_count": 0,
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
            "structural_warnings": [],
            "missing_count": len(ITEMS),
            "structural_warning_count": 0,
            "exit_code": 1,
        }

    parsed = parse_checklist_block(block)
    # Items 15 and 17 are conditional: only required when UI files are in scope.
    ui_in_scope = bool(_UI_FILE_RE.search(text))

    findings = []
    missing = 0
    for item_id, label in ITEMS:
        normalized = _normalize_label(label)
        # Item 15/17 optional when no UI in scope; Item 16 always optional (only required
        # when author has identified a high-consequence boundary — absent is valid).
        is_optional = (
            (item_id == "item_15_synthesis_dimensions" and not ui_in_scope)
            or item_id == "item_16_risk_reason"
            or (item_id == "item_17_ui_io_contract" and not ui_in_scope)
        )
        if normalized not in parsed:
            if is_optional:
                findings.append({"item_id": item_id, "label": label, "answer": None,
                                 "status": "ok", "message": "N/A: no UI in scope."})
                continue
            findings.append({"item_id": item_id, "label": label, "answer": None,
                             "status": "missing",
                             "message": f"Item not found. Expected '{label}:'"})
            missing += 1
        elif not is_answered(parsed[normalized]):
            if is_optional:
                findings.append({"item_id": item_id, "label": label,
                                 "answer": parsed[normalized], "status": "ok",
                                 "message": "N/A: no UI in scope."})
                continue
            findings.append({"item_id": item_id, "label": label,
                             "answer": parsed[normalized], "status": "unanswered",
                             "message": "Placeholder or empty. Provide answer or 'N/A: <reason>'."})
            missing += 1
        else:
            findings.append({"item_id": item_id, "label": label,
                             "answer": parsed[normalized], "status": "ok"})

    structural_warnings = _structural_findings(text, plan_path)
    structural_fail_count = sum(1 for w in structural_warnings if w.get("status") == "fail")
    return {
        "plan": str(plan_path), "checklist_found": True, "findings": findings,
        "structural_warnings": structural_warnings, "missing_count": missing,
        "structural_warning_count": len(structural_warnings),
        "structural_fail_count": structural_fail_count,
        "exit_code": 0 if (missing == 0 and structural_fail_count == 0) else 1,
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
        status = "CLEAN" if result["exit_code"] == 0 else "INCOMPLETE"
        warn_count = result.get("structural_warning_count", 0)
        print(
            f"check_checklist — {status} "
            f"({result['missing_count']} items missing/unanswered, "
            f"{warn_count} structural warnings)"
        )
        for f in result["findings"]:
            if f["status"] != "ok":
                print(f"  [{f['status'].upper()}] {f['label']}: {f.get('message', '')}")
        for w in result.get("structural_warnings", []):
            tag = w.get("status", "warn").upper()
            print(f"  [{tag}] {w['label']}: {w.get('message', '')}")

    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
