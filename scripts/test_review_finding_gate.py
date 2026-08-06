#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "review_finding_gate.py"
sys.path.insert(0, str(HERE))
from review_finding_gate import evaluate_payloads, normalize_severity  # noqa: E402


class ReviewFindingGateTests(unittest.TestCase):
    def test_legacy_major_maps_to_high_and_blocks(self) -> None:
        result = evaluate_payloads([{"findings": [{"id": "f1", "severity": "major"}]}])
        self.assertFalse(result["pass"])
        self.assertEqual(result["blocking_findings"][0]["normalized_severity"], "high")

    def test_minor_and_info_do_not_block(self) -> None:
        result = evaluate_payloads([{"findings": [
            {"id": "f1", "severity": "minor"},
            {"id": "f2", "severity": "info"},
        ]}])
        self.assertTrue(result["pass"])

    def test_high_closed_without_proof_still_blocks(self) -> None:
        result = evaluate_payloads([{"findings": [{"id": "sec1", "severity": "HIGH", "status": "resolved"}]}])
        self.assertFalse(result["pass"])

    def test_high_closed_with_proof_passes(self) -> None:
        result = evaluate_payloads([{"findings": [{
            "id": "sec1",
            "severity": "HIGH",
            "status": "resolved",
            "closure_proof": "pytest scripts/test_security.py",
        }]}])
        self.assertTrue(result["pass"])

    def test_unknown_severity_fails_conservative(self) -> None:
        self.assertEqual(normalize_severity("surprising"), "high")

    def test_wiki_under_capture_returns_orchestrator_to_iterate(self) -> None:
        result = evaluate_payloads([{"findings": [{
            "id": "education-source-coverage",
            "severity": "medium",
            "intent_relation": "same_intent",
            "disposition": "under-captured",
            "spec_ref": "intent:RC-1 comprehensive Booth, ERAU, and SJSU coverage",
            "observed": "13 sources spot-checked; diagnostic records coverage: under-captured",
            "evidence": "outputs/health/source-coverage-diagnostic.md",
            "recommended_phase": "iterate",
        }]}])

        self.assertFalse(result["pass"])
        self.assertEqual(result["blocking_count"], 1)
        finding = result["blocking_findings"][0]
        self.assertEqual(finding["intent_relation"], "same_intent")
        self.assertIn(
            "same_intent_without_evidenced_terminal_disposition",
            finding["blocking_reasons"],
        )
        self.assertEqual(result["orchestrator_route"], {
            "action": "return_to_orchestrator",
            "next_phase": "iterate",
            "open_item_ids": ["education-source-coverage"],
            "reason": "blocking findings require closure before Review-G",
        })

    def test_plan_narrowing_returns_orchestrator_to_replan(self) -> None:
        result = evaluate_payloads([{"findings": [{
            "id": "rc-1-narrowed-to-sample",
            "severity": "high",
            "intent_relation": "same_intent",
            "disposition": "open",
            "plan_narrowed": True,
            "closure_proof": None,
        }]}])

        self.assertFalse(result["pass"])
        self.assertEqual(result["orchestrator_route"]["next_phase"], "replan")

    def test_same_intent_fixed_with_real_input_proof_passes(self) -> None:
        result = evaluate_payloads([{"findings": [{
            "id": "education-source-coverage",
            "severity": "high",
            "intent_relation": "same_intent",
            "disposition": "fixed",
            "closure_proof": "source-relative audit: 67/67 sources covered",
        }]}])

        self.assertTrue(result["pass"])
        self.assertEqual(result["orchestrator_route"]["action"], "proceed")

    def test_user_deferred_requires_decision_record(self) -> None:
        without_record = evaluate_payloads([{"findings": [{
            "id": "semantic-expansion",
            "severity": "medium",
            "intent_relation": "same_intent",
            "disposition": "user_deferred",
        }]}])
        with_record = evaluate_payloads([{"findings": [{
            "id": "semantic-expansion",
            "severity": "medium",
            "intent_relation": "same_intent",
            "disposition": "user_deferred",
            "decision_record": "intent.md:RC-1 user narrowed to integration files",
            "decision_authority": "user",
        }]}])

        self.assertFalse(without_record["pass"])
        self.assertTrue(with_record["pass"])

    def test_user_deferred_does_not_accept_unrelated_closure_proof(self) -> None:
        result = evaluate_payloads([{"findings": [{
            "id": "semantic-expansion",
            "severity": "medium",
            "intent_relation": "same_intent",
            "disposition": "user_deferred",
            "closure_proof": "a test passed, but the user did not defer this item",
        }]}])

        self.assertFalse(result["pass"])
        self.assertFalse(result["findings"][0]["terminal_proof_present"])

    def test_user_deferred_rejects_agent_authored_decision_record(self) -> None:
        result = evaluate_payloads([{"findings": [{
            "id": "semantic-expansion",
            "severity": "medium",
            "intent_relation": "same_intent",
            "disposition": "user_deferred",
            "decision_record": "agent decided to defer",
            "decision_authority": "agent",
        }]}])

        self.assertFalse(result["pass"])

    def test_external_blocker_with_evidence_is_terminal(self) -> None:
        result = evaluate_payloads([{"findings": [{
            "id": "encrypted-source",
            "severity": "medium",
            "intent_relation": "same_intent",
            "disposition": "external_blocked",
            "blocker_evidence": "archive extraction failed: password required",
            "remaining_action": "user supplies password; rerun archive extraction",
        }]}])

        self.assertTrue(result["pass"])

    def test_external_blocker_requires_remaining_action(self) -> None:
        result = evaluate_payloads([{"findings": [{
            "id": "encrypted-source",
            "severity": "medium",
            "intent_relation": "same_intent",
            "disposition": "external_blocked",
            "blocker_evidence": "archive extraction failed: password required",
        }]}])

        self.assertFalse(result["pass"])

    def test_waiver_requires_waiver_record(self) -> None:
        wrong_proof = evaluate_payloads([{"findings": [{
            "id": "waived-check",
            "severity": "medium",
            "intent_relation": "same_intent",
            "disposition": "waived",
            "decision_record": "not a scoped waiver",
        }]}])
        right_proof = evaluate_payloads([{"findings": [{
            "id": "waived-check",
            "severity": "medium",
            "intent_relation": "same_intent",
            "disposition": "waived",
            "waiver_record": "waivers/RC-1.md expires 2026-09-01",
            "waiver_scope": "RC-1 encrypted archive only",
            "waiver_expiry": "2026-09-01",
            "waiver_approved_by": "user",
        }]}])

        self.assertFalse(wrong_proof["pass"])
        self.assertTrue(right_proof["pass"])

    def test_top_level_return_route_blocks_even_without_findings(self) -> None:
        result = evaluate_payloads([{
            "completion_routing": {
                "action": "return_to_orchestrator",
                "next_phase": "replan",
                "open_item_ids": ["RC-1"],
                "reason": "the plan narrowed comprehensive coverage to a sample",
            },
            "findings": [],
        }])

        self.assertFalse(result["pass"])
        self.assertEqual(result["blocking_count"], 1)
        self.assertEqual(result["orchestrator_route"]["next_phase"], "replan")
        self.assertEqual(result["orchestrator_route"]["open_item_ids"], ["RC-1"])

    def test_top_level_replan_route_overrides_duplicate_finding_phase(self) -> None:
        result = evaluate_payloads([{
            "completion_routing": {
                "action": "return_to_orchestrator",
                "next_phase": "replan",
                "open_item_ids": ["f1"],
            },
            "findings": [{
                "id": "f1",
                "severity": "high",
                "intent_relation": "same_intent",
                "disposition": "open",
            }],
        }])

        self.assertEqual(result["blocking_count"], 1)
        self.assertEqual(result["orchestrator_route"]["next_phase"], "replan")

    def test_generic_critical_cannot_close_with_external_blocker_record(self) -> None:
        result = evaluate_payloads([{"findings": [{
            "id": "critical-security",
            "severity": "critical",
            "intent_relation": "out_of_scope",
            "disposition": "external_blocked",
            "blocker_evidence": "scanner unavailable",
            "remaining_action": "rerun scanner",
        }]}])

        self.assertFalse(result["pass"])

    def test_generic_high_cannot_close_with_waiver_record(self) -> None:
        result = evaluate_payloads([{"findings": [{
            "id": "high-unknown",
            "severity": "high",
            "intent_relation": "unknown",
            "disposition": "waived",
            "waiver_record": "waivers/W-1.md",
            "waiver_scope": "finding",
            "waiver_expiry": "2026-09-01",
            "waiver_approved_by": "user",
        }]}])

        self.assertFalse(result["pass"])

    def test_out_of_scope_escalation_remains_nonblocking_at_medium(self) -> None:
        result = evaluate_payloads([{"findings": [{
            "id": "adjacent-ui-redesign",
            "severity": "medium",
            "intent_relation": "out_of_scope",
            "disposition": "escalated",
        }]}])

        self.assertTrue(result["pass"])

    def test_cli_exit_code_blocks_open_high(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"findings": [{"id": "f1", "severity": "critical"}]}, f)
            tmp = f.name
        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--findings-json", tmp, "--json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            Path(tmp).unlink()
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["blocking_count"], 1)
        self.assertEqual(payload["orchestrator_route"]["action"], "return_to_orchestrator")


if __name__ == "__main__":
    unittest.main(verbosity=2)
