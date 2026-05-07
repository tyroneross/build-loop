"""
Tests for skills/spec-writing/scripts/check_checklist.py.

Covers:
  - clean plan with all 8 items answered → exit 0
  - plan with missing checklist block → exit 1, all 8 flagged
  - plan with some items missing → exit 1, correct count
  - plan with placeholder answers → exit 1
  - plan with N/A answers → treated as answered (valid)
  - json output structure
  - standalone CLI: deliberately incomplete fixture → flags 8 items

Stdlib only + pytest.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills" / "spec-writing" / "scripts" / "check_checklist.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(plan_path: Path, *, quiet: bool = True) -> tuple[int, dict]:
    """Run check_checklist.py --json against plan_path. Returns (exit_code, parsed_json)."""
    cmd = [sys.executable, str(SCRIPT), "--plan", str(plan_path), "--json"]
    if quiet:
        cmd.append("--quiet")
    result = subprocess.run(cmd, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    return result.returncode, payload


def _all_8_items_answered() -> str:
    """Return a plan markdown with all 8 checklist items properly answered."""
    return """\
# Plan: Test Feature

<!-- checklist
Item 1 — Auth guard: requireAuth from lib/api-auth-guard.ts (grep confirmed, 12 uses)
Item 2 — External APIs: OpenAI Chat API — 10,000 TPM, 500 RPM per org; max 4096 tokens
Item 3 — Rate-limit criterion: 10 calls/hour per user for OpenAI Chat; 429 triggers retry
Item 4 — Discoverability: Nav → Dashboard → New Feature tab; empty state shows "Get started" CTA
Item 5 — Server/client boundary: lib/podcast-accessor.ts has import 'server-only'; types in lib/podcast-shared.ts
Item 6 — Concurrency: Prisma upsert on unique(userId, episodeId) index; no transaction needed
Item 7 — Observability: structuredLog on each LLM call with userId, charCount, latencyMs, cost
Item 8 — Input validation: Zod schema z.object({episodeId: z.string().uuid()}) at top of POST handler
-->

## Goal

Add podcast feature with LLM summarisation.

## Locked Decisions

- Use OpenAI Chat API

## Scope

In scope: summarisation endpoint.

### Out of scope

Mobile app changes.

## Six-Commit Table

| # | Commit subject | Files owned | Depends on |
|---|----------------|-------------|------------|
| 1 | feat(api): add summarise endpoint | app/api/summarise/route.ts | — |

## F-Criteria (functional)

| Criterion | Pass condition | Grader |
|-----------|---------------|--------|
| Auth | 401 on unauth | curl |

## Q-Criteria (quality)

| Criterion | Pass condition | Grader |
|-----------|---------------|--------|
| TypeScript | tsc exits 0 | CI |

## Risks

None identified.

## Out of Scope

Mobile app changes.
"""


def _no_checklist_block() -> str:
    return """\
# Plan: No Checklist

## Goal

This plan has no checklist block at all.

## Scope

Some scope.
"""


def _partial_checklist(answered_count: int) -> str:
    """Answer only the first `answered_count` items."""
    all_items = [
        "Item 1 — Auth guard: requireAuth from lib/api-auth-guard.ts",
        "Item 2 — External APIs: N/A: no external APIs",
        "Item 3 — Rate-limit criterion: N/A: no paid APIs",
        "Item 4 — Discoverability: Nav → Settings → Feature tab; empty state CTA",
        "Item 5 — Server/client boundary: import 'server-only' in lib/accessor.ts",
        "Item 6 — Concurrency: Prisma upsert on unique index",
        "Item 7 — Observability: structuredLog with userId and outcome",
        "Item 8 — Input validation: Zod schema at route handler entry",
    ]
    lines = "\n".join(all_items[:answered_count])
    return f"""\
# Plan: Partial Checklist

<!-- checklist
{lines}
-->

## Goal

Partial spec for testing.
"""


def _placeholder_answers() -> str:
    return """\
# Plan: Placeholder Answers

<!-- checklist
Item 1 — Auth guard: <answer>
Item 2 — External APIs: ...
Item 3 — Rate-limit criterion: TODO
Item 4 — Discoverability: tbd
Item 5 — Server/client boundary: none
Item 6 — Concurrency:
Item 7 — Observability: N/A: no side effects here, logging not needed
Item 8 — Input validation: Zod schema at route handler entry with z.object({id: z.string()})
-->

## Goal

Plan with placeholders.
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAllItemsAnswered:
    def test_exit_0(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_all_8_items_answered(), encoding="utf-8")
        code, payload = _run(plan)
        assert code == 0, f"Expected exit 0; got {code}. Findings: {payload['findings']}"

    def test_missing_count_zero(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_all_8_items_answered(), encoding="utf-8")
        _, payload = _run(plan)
        assert payload["missing_count"] == 0

    def test_all_findings_ok(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_all_8_items_answered(), encoding="utf-8")
        _, payload = _run(plan)
        bad = [f for f in payload["findings"] if f["status"] != "ok"]
        assert bad == [], f"Unexpected non-ok findings: {bad}"

    def test_checklist_found_true(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_all_8_items_answered(), encoding="utf-8")
        _, payload = _run(plan)
        assert payload["checklist_found"] is True


class TestNoChecklistBlock:
    def test_exit_1(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_no_checklist_block(), encoding="utf-8")
        code, _ = _run(plan)
        assert code == 1

    def test_checklist_found_false(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_no_checklist_block(), encoding="utf-8")
        _, payload = _run(plan)
        assert payload["checklist_found"] is False

    def test_all_8_flagged(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_no_checklist_block(), encoding="utf-8")
        _, payload = _run(plan)
        assert payload["missing_count"] == 8


class TestPartialChecklist:
    @pytest.mark.parametrize("answered,expected_missing", [
        (0, 8),
        (4, 4),
        (7, 1),
    ])
    def test_partial_flags_correct_count(
        self, tmp_path: Path, answered: int, expected_missing: int
    ) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_partial_checklist(answered), encoding="utf-8")
        code, payload = _run(plan)
        assert code == 1
        assert payload["missing_count"] == expected_missing, (
            f"answered={answered}: expected {expected_missing} missing, "
            f"got {payload['missing_count']}. Findings: {payload['findings']}"
        )


class TestPlaceholderAnswers:
    def test_exit_1_on_placeholders(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_placeholder_answers(), encoding="utf-8")
        code, payload = _run(plan)
        assert code == 1

    def test_placeholder_items_flagged(self, tmp_path: Path) -> None:
        """Items 1-6 are placeholders; items 7-8 are answered. Expect 6 missing."""
        plan = tmp_path / "plan.md"
        plan.write_text(_placeholder_answers(), encoding="utf-8")
        _, payload = _run(plan)
        # Items 7 and 8 are answered with real text
        bad = [f for f in payload["findings"] if f["status"] != "ok"]
        # Items 1-6: <answer>, ..., TODO, tbd, none, ""  → all placeholders
        assert len(bad) == 6, f"Expected 6 bad findings, got {len(bad)}: {bad}"

    def test_na_with_reason_counts_as_answered(self, tmp_path: Path) -> None:
        """'N/A: <reason>' must pass — it's a legitimate answer."""
        plan = tmp_path / "plan.md"
        # Use partial checklist — first 3 items use N/A style
        content = """\
# Plan: NA Test

<!-- checklist
Item 1 — Auth guard: N/A: client-only feature, no server routes
Item 2 — External APIs: N/A: no new external API calls
Item 3 — Rate-limit criterion: N/A: no paid APIs in scope
Item 4 — Discoverability: Nav → Dashboard → Widget panel; empty state "Add your first widget"
Item 5 — Server/client boundary: lib/widget-accessor.ts has import 'server-only'
Item 6 — Concurrency: Prisma upsert on unique(userId, widgetId)
Item 7 — Observability: structuredLog on widget create with userId and widgetType
Item 8 — Input validation: Zod at POST /api/widgets with z.object({type: z.string()})
-->

## Goal

Test N/A handling.
"""
        plan.write_text(content, encoding="utf-8")
        code, payload = _run(plan)
        assert code == 0, f"N/A answers should be accepted; findings: {payload['findings']}"


class TestMissingFile:
    def test_exit_2_on_missing_file(self, tmp_path: Path) -> None:
        plan = tmp_path / "nonexistent.md"
        code, payload = _run(plan)
        assert code == 2
        assert "error" in payload


class TestJsonOutputShape:
    def test_has_required_keys(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_all_8_items_answered(), encoding="utf-8")
        _, payload = _run(plan)
        for key in ("plan", "checklist_found", "findings", "missing_count", "exit_code"):
            assert key in payload, f"Missing key '{key}' in output"

    def test_findings_have_required_keys(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_all_8_items_answered(), encoding="utf-8")
        _, payload = _run(plan)
        for f in payload["findings"]:
            for key in ("item_id", "label", "status"):
                assert key in f, f"Finding missing key '{key}': {f}"

    def test_exactly_8_findings(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_all_8_items_answered(), encoding="utf-8")
        _, payload = _run(plan)
        assert len(payload["findings"]) == 8
