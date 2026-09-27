#!/usr/bin/env python3
"""Regression tests for the resolver-to-dispatch effort contract."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR = ROOT / "agents" / "build-orchestrator.md"
MAPPING = ROOT / "references" / "model-tier-mapping.md"
EXECUTE = ROOT / "skills" / "build-loop" / "references" / "phase-3-execute.md"


class ModelEffortDispatchContractTests(unittest.TestCase):
    def test_orchestrator_consumes_the_full_resolution_envelope(self) -> None:
        text = ORCHESTRATOR.read_text(encoding="utf-8")
        self.assertIn("resolve_agent_model.py <agent-name> --workdir \"$PWD\" --json", text)
        for field in ("preferred_effort", "effort_guidance.supported", "requested model/effort", "actual model/effort"):
            with self.subTest(field=field):
                self.assertIn(field, text)

    def test_effort_escalation_requires_evidence_and_haiku_does_not_fake_support(self) -> None:
        text = EXECUTE.read_text(encoding="utf-8")
        self.assertIn("xhigh` only after a verifier establishes a quality miss", text)
        self.assertIn("max` only after a controlled comparison", text)
        self.assertIn("Haiku has no effort control", text)

    def test_orchestrator_owns_each_subagent_effort_and_reports_delivery(self) -> None:
        orchestrator = ORCHESTRATOR.read_text(encoding="utf-8")
        mapping = MAPPING.read_text(encoding="utf-8")
        self.assertIn("orchestrator chooses requested effort for every subagent dispatch", orchestrator)
        self.assertIn("unsupported/not-applied", orchestrator)
        self.assertIn("reasoning_effort", mapping)
        self.assertIn("Claude Code subagent definitions accept `effort`", mapping)
        self.assertIn("Record actual model and effort separately", mapping)


if __name__ == "__main__":
    unittest.main()
