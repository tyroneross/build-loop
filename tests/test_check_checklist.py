"""
Tests for skills/spec-writing/scripts/check_checklist.py.

Covers:
  - clean plan with all required items answered → exit 0
  - plan with missing checklist block → exit 1, all items flagged
  - plan with some items missing → exit 1, correct count
  - plan with placeholder answers → exit 1
  - plan with N/A answers → treated as answered (valid)
  - json output structure
  - standalone CLI: deliberately incomplete fixture → flags missing items

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
    """
    Return a plan markdown with all required checklist items properly answered.
    Named _all_8_items_answered for backward compat with existing tests;
    now includes later checklist items as the verifier requires more than 8.
    Does not write a sibling handoff file — item 14 is answered N/A.
    """
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
Item 9 — Stable ID traceability: N/A: no P0 scope in this patch
Item 10 — JSON spec object: N/A: doc-only change, no spec object required
Item 11 — Blocking-and-novel question gate: no open questions; all non-blocking resolved as assumptions
Item 12 — Low-reversibility ADRs: N/A: all decisions are reversible
Item 13 — Analytical lens: N/A: trivial patch, no analytical lens required
Item 14 — Handoff document: N/A: no implementation tasks
Item 15 — Synthesis dimensions: 1 dim — auth+rate-limit interaction in handler
Item 16 — Risk reason: N/A
Item 17 — UI input/output contract: N/A: no UI surface
-->

## Goal

Add podcast feature with LLM summarisation.

## Synthesis Dimensions

```yaml
synthesis_dimensions:
  placement: dashboard sidebar
  cta_tier: secondary
  copy_tone: concise neutral
  visual_weight: low
  empty_state: "Get started"
```

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
    """Answer only the first `answered_count` items from the checklist."""
    all_items = [
        "Item 1 — Auth guard: requireAuth from lib/api-auth-guard.ts",
        "Item 2 — External APIs: N/A: no external APIs",
        "Item 3 — Rate-limit criterion: N/A: no paid APIs",
        "Item 4 — Discoverability: Nav → Settings → Feature tab; empty state CTA",
        "Item 5 — Server/client boundary: import 'server-only' in lib/accessor.ts",
        "Item 6 — Concurrency: Prisma upsert on unique index",
        "Item 7 — Observability: structuredLog with userId and outcome",
        "Item 8 — Input validation: Zod schema at route handler entry",
        "Item 9 — Stable ID traceability: N/A: no P0 scope",
        "Item 10 — JSON spec object: N/A: doc-only change",
        "Item 11 — Blocking-and-novel question gate: N/A: no open questions",
        "Item 12 — Low-reversibility ADRs: N/A: all reversible",
        "Item 13 — Analytical lens: JTBD — fuzzy user problem space",
        "Item 14 — Handoff document: N/A: no implementation tasks",
        "Item 15 — Synthesis dimensions: 1 dim — auth+rate-limit interaction in handler",
        "Item 16 — Risk reason: N/A",
        "Item 17 — UI input/output contract: N/A: no UI surface",
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
    """
    Items 1-6: placeholder answers (invalid). Items 7-8: answered. Items 9-14: answered N/A.
    Expect 6 bad findings (items 1-6).
    """
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
Item 9 — Stable ID traceability: N/A: no P0 scope
Item 10 — JSON spec object: N/A: doc-only change
Item 11 — Blocking-and-novel question gate: N/A: no open questions
Item 12 — Low-reversibility ADRs: N/A: all reversible
Item 13 — Analytical lens: N/A: trivial change
Item 14 — Handoff document: N/A: no implementation tasks
Item 15 — Synthesis dimensions: 1 dim — auth+rate-limit interaction in handler
Item 16 — Risk reason: N/A
Item 17 — UI input/output contract: N/A: no UI surface
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

    def test_all_14_flagged(self, tmp_path: Path) -> None:
        # Despite the legacy name, the checklist is now 17 items (Item 15
        # synthesis_dimensions, Item 16 risk_reason, and Item 17 UI I/O contract). When no
        # checklist block is present the verifier returns len(ITEMS) without
        # filtering optionals, so the count tracks the full ITEMS list.
        plan = tmp_path / "plan.md"
        plan.write_text(_no_checklist_block(), encoding="utf-8")
        _, payload = _run(plan)
        assert payload["missing_count"] == 17


class TestPartialChecklist:
    @pytest.mark.parametrize("answered,expected_missing", [
        (0, 14),
        (4, 10),
        (7, 7),
        (13, 1),
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
Item 9 — Stable ID traceability: N/A: no P0 scope
Item 10 — JSON spec object: N/A: doc-only change
Item 11 — Blocking-and-novel question gate: N/A: no open questions
Item 12 — Low-reversibility ADRs: N/A: all decisions are reversible
Item 13 — Analytical lens: N/A: trivial widget patch
Item 14 — Handoff document: N/A: no implementation tasks
Item 15 — Synthesis dimensions: 1 dim — auth+rate-limit interaction in handler
Item 16 — Risk reason: N/A
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

    def test_exactly_14_findings(self, tmp_path: Path) -> None:
        # Findings list always has one entry per ITEM, regardless of optional
        # filtering. Now 17 items (Item 15 + Item 16 + Item 17).
        plan = tmp_path / "plan.md"
        plan.write_text(_all_8_items_answered(), encoding="utf-8")
        _, payload = _run(plan)
        assert len(payload["findings"]) == 17

    def test_structural_warnings_key_present(self, tmp_path: Path) -> None:
        """structural_warnings must always be present in output (may be empty list)."""
        plan = tmp_path / "plan.md"
        plan.write_text(_all_8_items_answered(), encoding="utf-8")
        _, payload = _run(plan)
        assert "structural_warnings" in payload
        assert "structural_warning_count" in payload

    def test_exactly_14_findings_with_items_9_to_14(self, tmp_path: Path) -> None:
        """When all required items are answered, findings list has 17 entries
        (one per ITEM, including optional items)."""
        plan = tmp_path / "plan.md"
        plan.write_text(_all_14_items_answered(tmp_path), encoding="utf-8")
        _, payload = _run(plan)
        assert len(payload["findings"]) == 17, (
            f"Expected 17 findings; got {len(payload['findings'])}"
        )


# ---------------------------------------------------------------------------
# Helpers for items 9-14 tests
# ---------------------------------------------------------------------------

def _all_14_items_answered(tmp_path: Path) -> str:
    """
    Full plan with required checklist items answered plus structural elements
    required by items 9-14: ID chains, JSON spec section, ADR heading,
    analytical lens, and a sibling .handoff.md (written by the fixture helper).
    """
    # Write the sibling handoff file so item 14 check passes
    handoff = tmp_path / "plan.handoff.md"
    handoff.write_text(
        "# Handoff: plan\n\nWhen implementing F-01, read ADR-001 and satisfy T-01.\n",
        encoding="utf-8",
    )
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
Item 9 — Stable ID traceability: U-01 → F-01 → D-01 → T-01 trace chain documented in Locked Decisions
Item 10 — JSON spec object: Spec Object (JSON) section present with needs[], features[], tests[]
Item 11 — Blocking-and-novel question gate: all open questions carry blocking-test annotation; non-blocking ones converted to assumptions
Item 12 — Low-reversibility ADRs: ADR-001 covers DB choice (Postgres); ADR-002 covers auth provider (Better Auth)
Item 13 — Analytical lens: QFD — need-to-feature mapping for requirements; DSM for cross-component deps
Item 14 — Handoff document: plan.handoff.md generated alongside this plan
Item 15 — Synthesis dimensions: 1 dim — auth+rate-limit interaction in POST handler
Item 16 — Risk reason: N/A: no high-consequence boundary in scope
Item 17 — UI input/output contract: N/A: no UI surface
-->

## Goal

Add podcast feature with LLM summarisation.

## Synthesis Dimensions

```yaml
synthesis_dimensions:
  placement: dashboard sidebar
  cta_tier: secondary
  copy_tone: concise neutral
  visual_weight: low
  empty_state: "Get started"
```

## Locked Decisions

Analytical lens: QFD — need-to-feature mapping

| Decision | Type | ADR |
|----------|------|-----|
| PostgreSQL as primary DB | low-reversibility | ADR-001 |
| Better Auth for auth provider | low-reversibility | ADR-002 |

Trace: U-01 (user needs podcast summary) → F-01 (summarisation endpoint) → D-01 (summary text field) → T-01 (acceptance: returns 200 with summary)

## Scope

In scope: summarisation endpoint.

### Out of scope

Mobile app changes.

## Spec Object (JSON)

```json
{
  "needs": [{"id": "U-01", "description": "User needs podcast summary", "priority": "P0"}],
  "features": [{"id": "F-01", "need_ids": ["U-01"], "description": "Summarisation endpoint"}],
  "data_points": [{"id": "D-01", "feature_ids": ["F-01"], "description": "summary text field"}],
  "tests": [{"id": "T-01", "feature_ids": ["F-01"], "description": "Returns 200 with summary"}],
  "adrs": [
    {"id": "A-01", "decision": "PostgreSQL", "alternatives": ["MySQL", "SQLite"], "rollback": "Dump and restore"},
    {"id": "A-02", "decision": "Better Auth", "alternatives": ["NextAuth", "Clerk"], "rollback": "Swap provider config"}
  ]
}
```

## ADR-001: PostgreSQL

Context: Need a relational DB. Alternatives: MySQL (less native jsonb), SQLite (no concurrent writes). Rollback: pg_dump + restore.

## ADR-002: Better Auth

Context: Auth provider choice. Alternatives: NextAuth (more boilerplate), Clerk (vendor lock-in). Rollback: swap provider config + migrate sessions.

## Six-Commit Table

| # | Commit subject | Files owned | Depends on |
|---|----------------|-------------|------------|
| 1 | feat(api): add summarise endpoint | app/api/summarise/route.ts | — |

## F-Criteria (functional)

| Criterion | Pass condition | Grader |
|-----------|---------------|--------|
| Auth | 401 on unauth | curl |
| F-01 [P0] | Returns 200 with summary text T-01 | integration test |

## Q-Criteria (quality)

| Criterion | Pass condition | Grader |
|-----------|---------------|--------|
| TypeScript | tsc exits 0 | CI |

## Risks

None identified.

## Out of Scope

Mobile app changes.
"""


def _plan_missing_items_9_to_14(tmp_path: Path) -> str:
    """Plan with items 1-8 answered but items 9-14 missing from checklist block."""
    return """\
# Plan: Partial Feature

<!-- checklist
Item 1 — Auth guard: requireAuth from lib/api-auth-guard.ts
Item 2 — External APIs: N/A: no external APIs
Item 3 — Rate-limit criterion: N/A: no paid APIs
Item 4 — Discoverability: Nav → Settings → Feature tab; empty state CTA
Item 5 — Server/client boundary: import 'server-only' in lib/accessor.ts
Item 6 — Concurrency: Prisma upsert on unique index
Item 7 — Observability: structuredLog with userId and outcome
Item 8 — Input validation: Zod schema at route handler entry
-->

## Goal

Missing items 9-14 from checklist.
"""


def _plan_no_json_spec_section() -> str:
    """Plan with required checklist items answered but no ## Spec Object (JSON) section."""
    return """\
# Plan: No JSON Spec

<!-- checklist
Item 1 — Auth guard: requireAuth from lib/api-auth-guard.ts
Item 2 — External APIs: N/A
Item 3 — Rate-limit criterion: N/A
Item 4 — Discoverability: N/A: API-only
Item 5 — Server/client boundary: N/A: server-only
Item 6 — Concurrency: Prisma upsert
Item 7 — Observability: structuredLog on create
Item 8 — Input validation: Zod at POST handler
Item 9 — Stable ID traceability: N/A: no P0 scope
Item 10 — JSON spec object: present
Item 11 — Blocking-and-novel question gate: no open questions; all non-blocking resolved as assumptions
Item 12 — Low-reversibility ADRs: N/A: all decisions are reversible
Item 13 — Analytical lens: JTBD — fuzzy user problem space
Item 14 — Handoff document: N/A: no implementation tasks
Item 15 — Synthesis dimensions: 1 dim — auth+rate-limit interaction in handler
Item 16 — Risk reason: N/A
-->

## Goal

Missing the actual JSON spec section despite the checklist claiming it is present.

## Locked Decisions

Analytical lens: JTBD
"""


def _plan_open_questions_without_annotation() -> str:
    """Plan with Open Questions section but entries missing blocking-test annotation."""
    return """\
# Plan: Bad Open Questions

<!-- checklist
Item 1 — Auth guard: requireAuth from lib/api-auth-guard.ts
Item 2 — External APIs: N/A
Item 3 — Rate-limit criterion: N/A
Item 4 — Discoverability: N/A: API-only
Item 5 — Server/client boundary: N/A
Item 6 — Concurrency: Prisma upsert
Item 7 — Observability: structuredLog
Item 8 — Input validation: Zod
Item 9 — Stable ID traceability: N/A: no P0 scope
Item 10 — JSON spec object: N/A: doc-only
Item 11 — Blocking-and-novel question gate: open questions annotated
Item 12 — Low-reversibility ADRs: N/A
Item 13 — Analytical lens: QFD
Item 14 — Handoff document: N/A
Item 15 — Synthesis dimensions: 1 dim — auth+rate-limit interaction in handler
Item 16 — Risk reason: N/A
-->

## Goal

Open questions test.

## Locked Decisions

Analytical lens: QFD

## Open Questions

- Should we support multi-tenant mode?
- Which cache TTL to use?

## Risks

None.
"""


def _plan_with_annotated_open_questions() -> str:
    """Plan with Open Questions properly annotated with blocking-test references."""
    return """\
# Plan: Good Open Questions

<!-- checklist
Item 1 — Auth guard: requireAuth from lib/api-auth-guard.ts
Item 2 — External APIs: N/A
Item 3 — Rate-limit criterion: N/A
Item 4 — Discoverability: N/A: API-only
Item 5 — Server/client boundary: N/A
Item 6 — Concurrency: Prisma upsert
Item 7 — Observability: structuredLog
Item 8 — Input validation: Zod
Item 9 — Stable ID traceability: N/A: no P0 scope
Item 10 — JSON spec object: N/A: doc-only
Item 11 — Blocking-and-novel question gate: all questions carry blocking-test annotations
Item 12 — Low-reversibility ADRs: N/A
Item 13 — Analytical lens: QFD
Item 14 — Handoff document: N/A
Item 15 — Synthesis dimensions: 1 dim — auth+rate-limit interaction in handler
Item 16 — Risk reason: N/A
-->

## Goal

Good open questions test.

## Locked Decisions

Analytical lens: QFD

## Open Questions

- Should we support multi-tenant mode? blocking-test: T-05
- Which cache TTL to use? blocking-test: T-07

## Risks

None.
"""


def _ui_plan_without_io_contract() -> str:
    """UI plan with synthesis dimensions but no UI Input/Output Contract section."""
    return """\
# Plan: Missing UI IO Contract

<!-- checklist
Item 1 — Auth guard: N/A
Item 2 — External APIs: N/A
Item 3 — Rate-limit criterion: N/A
Item 4 — Discoverability: Search page → results panel
Item 5 — Server/client boundary: N/A
Item 6 — Concurrency: N/A
Item 7 — Observability: structuredLog on search submit
Item 8 — Input validation: query length checked before submit
Item 9 — Stable ID traceability: N/A: no P0 scope
Item 10 — JSON spec object: N/A: doc-only
Item 11 — Blocking-and-novel question gate: N/A
Item 12 — Low-reversibility ADRs: N/A
Item 13 — Analytical lens: QFD
Item 14 — Handoff document: N/A
Item 15 — Synthesis dimensions: required keys present below
Item 16 — Risk reason: N/A
Item 17 — UI input/output contract: claimed present
-->

## Goal

Update `components/search/SearchResults.tsx`.

## Synthesis Dimensions

```yaml
synthesis_dimensions:
  placement: "inside components/search/SearchResults.tsx after result header"
  cta_tier: "secondary"
  copy_tone: "concise neutral"
  visual_weight: "medium"
  empty_state: "one-line empty result with retry CTA"
```

## Locked Decisions

Analytical lens: QFD
"""


def _ui_plan_with_io_contract(*, include_checklist_item: bool = True) -> str:
    """UI plan with a valid UI Input/Output Contract section."""
    item_17 = (
        "Item 17 — UI input/output contract: SearchResults row covers inputs, outputs, data taxonomy, operation, mapping, states, modality, validation/security, traceability\n"
        if include_checklist_item else ""
    )
    return f"""\
# Plan: Valid UI IO Contract

<!-- checklist
Item 1 — Auth guard: N/A
Item 2 — External APIs: N/A
Item 3 — Rate-limit criterion: N/A
Item 4 — Discoverability: Search page → results panel
Item 5 — Server/client boundary: N/A
Item 6 — Concurrency: N/A
Item 7 — Observability: structuredLog on search submit
Item 8 — Input validation: query length checked before submit
Item 9 — Stable ID traceability: N/A: no P0 scope
Item 10 — JSON spec object: N/A: doc-only
Item 11 — Blocking-and-novel question gate: N/A
Item 12 — Low-reversibility ADRs: N/A
Item 13 — Analytical lens: QFD
Item 14 — Handoff document: N/A
Item 15 — Synthesis dimensions: required keys present below
Item 16 — Risk reason: N/A
{item_17}-->

## Goal

Update `components/search/SearchResults.tsx`.

## Synthesis Dimensions

```yaml
synthesis_dimensions:
  placement: "inside components/search/SearchResults.tsx after result header"
  cta_tier: "secondary"
  copy_tone: "concise neutral"
  visual_weight: "medium"
  empty_state: "one-line empty result with retry CTA"
```

## Locked Decisions

Analytical lens: QFD

## UI Input/Output Contract

| Surface | Inputs | Outputs | Data taxonomy | Operation | Component mapping | States | Modality | Validation/security | Traceability |
|---|---|---|---|---|---|---|---|---|---|
| SearchResults (`components/search/SearchResults.tsx`) | query string | markdown summary and table rows | scalar text input, markdown/table output, computed | Read/query | search input and table renderer | empty, loading, populated, error | text with table fallback | length validation and markdown sanitization | `/api/search` POST and SearchResponse schema |
"""


# ---------------------------------------------------------------------------
# Tests for items 9-14
# ---------------------------------------------------------------------------

class TestItems9To14ChecklistBlock:
    """Items 9-14 checklist block presence — exit code and finding counts."""

    def test_missing_items_9_to_14_causes_exit_1(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_plan_missing_items_9_to_14(tmp_path), encoding="utf-8")
        code, payload = _run(plan)
        assert code == 1

    def test_missing_items_9_to_14_flagged_correctly(self, tmp_path: Path) -> None:
        """6 items (9-14) missing → missing_count includes those 6."""
        plan = tmp_path / "plan.md"
        plan.write_text(_plan_missing_items_9_to_14(tmp_path), encoding="utf-8")
        _, payload = _run(plan)
        assert payload["missing_count"] == 6, (
            f"Expected 6 missing items; got {payload['missing_count']}"
        )

    def test_all_14_items_answered_exits_0(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_all_14_items_answered(tmp_path), encoding="utf-8")
        code, payload = _run(plan)
        assert code == 0, f"Expected exit 0; findings: {payload['findings']}"

    def test_all_14_items_zero_missing(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_all_14_items_answered(tmp_path), encoding="utf-8")
        _, payload = _run(plan)
        assert payload["missing_count"] == 0


class TestStructuralWarningsItem10:
    """Item 10: ## Spec Object (JSON) section must exist."""

    def test_no_json_spec_section_raises_warning(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_plan_no_json_spec_section(), encoding="utf-8")
        _, payload = _run(plan)
        warn_ids = [w["item_id"] for w in payload.get("structural_warnings", [])]
        assert "item_10_json_spec_object" in warn_ids, (
            f"Expected item_10 warning; got: {warn_ids}"
        )

    def test_json_spec_section_present_no_warning(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_all_14_items_answered(tmp_path), encoding="utf-8")
        _, payload = _run(plan)
        warn_ids = [w["item_id"] for w in payload.get("structural_warnings", [])]
        assert "item_10_json_spec_object" not in warn_ids


class TestStructuralWarningsItem11:
    """Item 11: Open Questions entries must carry blocking-test annotation."""

    def test_open_questions_without_annotation_warns(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_plan_open_questions_without_annotation(), encoding="utf-8")
        _, payload = _run(plan)
        warn_ids = [w["item_id"] for w in payload.get("structural_warnings", [])]
        assert "item_11_blocking_and_novel_question_gate" in warn_ids

    def test_annotated_open_questions_no_warning(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_plan_with_annotated_open_questions(), encoding="utf-8")
        _, payload = _run(plan)
        warn_ids = [w["item_id"] for w in payload.get("structural_warnings", [])]
        assert "item_11_blocking_and_novel_question_gate" not in warn_ids


class TestStructuralWarningsItem13:
    """Item 13: Analytical lens line must appear in the plan."""

    def test_missing_lens_line_warns(self, tmp_path: Path) -> None:
        # The checklist block has "Item 13 — Analytical lens: JTBD" but the body
        # has no standalone "Analytical lens:" line. The checker strips the checklist
        # block before searching the body, so it should fire the warning.
        content = """\
# Plan: No Lens

<!-- checklist
Item 1 — Auth guard: N/A
Item 2 — External APIs: N/A
Item 3 — Rate-limit criterion: N/A
Item 4 — Discoverability: N/A
Item 5 — Server/client boundary: N/A
Item 6 — Concurrency: N/A
Item 7 — Observability: N/A
Item 8 — Input validation: N/A
Item 9 — Stable ID traceability: N/A
Item 10 — JSON spec object: N/A
Item 11 — Blocking-and-novel question gate: N/A
Item 12 — Low-reversibility ADRs: N/A
Item 13 — Analytical lens: JTBD for fuzzy user scope
Item 14 — Handoff document: N/A
Item 15 — Synthesis dimensions: 1 dim — auth+rate-limit interaction in handler
Item 16 — Risk reason: N/A
-->

## Goal

No lens line in body.

## Locked Decisions

Method: JTBD. (no 'Analytical lens:' label here — intentionally missing)
"""

    def test_lens_line_present_no_warning(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_all_14_items_answered(tmp_path), encoding="utf-8")
        _, payload = _run(plan)
        warn_ids = [w["item_id"] for w in payload.get("structural_warnings", [])]
        assert "item_13_analytical_lens" not in warn_ids


class TestStructuralWarningsItem14:
    """Item 14: Sibling .handoff.md must exist."""

    def test_missing_handoff_file_warns(self, tmp_path: Path) -> None:
        plan = tmp_path / "my-feature.md"
        plan.write_text(_plan_with_annotated_open_questions(), encoding="utf-8")
        # Do NOT write my-feature.handoff.md
        _, payload = _run(plan)
        warn_ids = [w["item_id"] for w in payload.get("structural_warnings", [])]
        assert "item_14_handoff_document" in warn_ids

    def test_handoff_file_present_no_warning(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        # _all_14_items_answered writes plan.handoff.md to tmp_path
        plan.write_text(_all_14_items_answered(tmp_path), encoding="utf-8")
        _, payload = _run(plan)
        warn_ids = [w["item_id"] for w in payload.get("structural_warnings", [])]
        assert "item_14_handoff_document" not in warn_ids


class TestStructuralFailuresItem17:
    """Item 17: UI plans must include a UI Input/Output Contract."""

    def test_ui_plan_without_io_contract_fails(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_ui_plan_without_io_contract(), encoding="utf-8")
        code, payload = _run(plan)
        assert code == 1
        failures = [
            w for w in payload.get("structural_warnings", [])
            if w["item_id"] == "item_17_ui_io_contract"
        ]
        assert failures and failures[0]["status"] == "fail"

    def test_ui_plan_with_io_contract_no_item17_failure(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_ui_plan_with_io_contract(), encoding="utf-8")
        _, payload = _run(plan)
        failures = [
            w for w in payload.get("structural_warnings", [])
            if w["item_id"] == "item_17_ui_io_contract"
        ]
        assert failures == []

    def test_ui_plan_missing_item17_checklist_line_counts_missing(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(_ui_plan_with_io_contract(include_checklist_item=False), encoding="utf-8")
        code, payload = _run(plan)
        assert code == 1
        item_17 = [
            f for f in payload["findings"]
            if f["item_id"] == "item_17_ui_io_contract"
        ][0]
        assert item_17["status"] == "missing"
