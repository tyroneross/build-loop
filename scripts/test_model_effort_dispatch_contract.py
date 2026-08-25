#!/usr/bin/env python3
"""Regression tests for the resolver-to-dispatch effort contract."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR = ROOT / "agents" / "build-orchestrator.md"
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


if __name__ == "__main__":
    unittest.main()
