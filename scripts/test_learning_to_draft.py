"""Tests for learning_to_draft.convert — retrospective objects -> drafter proposals."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import learning_to_draft as l2d  # noqa: E402


def _obj(**kw):
    base = {"title": "Run full suite before merge", "evidence": ["e1", "e2"],
            "encoding_target": "skill", "scope": "cross-project",
            "confidence": "high", "encode": "yes"}
    base.update(kw)
    return base


class ConvertTests(unittest.TestCase):
    def test_skill_target_becomes_proposal_in_drafter_shape(self):
        out = l2d.convert([_obj()])
        self.assertEqual(len(out["proposals"]), 1)
        p = out["proposals"][0]
        # Matches the self-improvement-architect input contract.
        self.assertEqual(p["type"], "retrospective_pattern")
        self.assertEqual(p["signature"], "Run full suite before merge")
        self.assertEqual(p["target_type"], "skill")
        self.assertEqual(p["evidence"], ["e1", "e2"])
        self.assertIn("skillSkeleton", p["proposal"])
        for k in ("name", "trigger", "purpose"):
            self.assertIn(k, p["proposal"]["skillSkeleton"])

    def test_name_is_kebab_and_experimental_scoped(self):
        p = l2d.convert([_obj(title="Run ALL the Tests!!")])["proposals"][0]
        self.assertEqual(p["proposal"]["skillSkeleton"]["name"], "experimental-run-all-the-tests")

    def test_agent_target_routes_with_agent_type(self):
        p = l2d.convert([_obj(encoding_target="agent")])["proposals"][0]
        self.assertEqual(p["target_type"], "agent")

    def test_encode_no_is_skipped(self):
        out = l2d.convert([_obj(encode="no")])
        self.assertEqual(out["proposals"], [])
        self.assertEqual(out["summary"]["skipped"], 1)

    def test_needs_approval_is_not_auto_drafted(self):
        out = l2d.convert([_obj(encode="needs_approval")])
        self.assertEqual(out["proposals"], [])

    def test_memory_target_routed_elsewhere_not_drafted(self):
        out = l2d.convert([_obj(encoding_target="memory")])
        self.assertEqual(out["proposals"], [])
        self.assertEqual(out["summary"]["skipped"], 1)
        self.assertEqual(out["summary"]["enforcement_specs"], 0)

    def test_gate_and_eval_targets_become_prevention_pattern_specs(self):
        # Gap #3: real targets with no producer route to a routable spec, not a
        # dead end and not silently dropped.
        out = l2d.convert([_obj(encoding_target="gate"), _obj(encoding_target="eval")])
        self.assertEqual(out["proposals"], [])
        self.assertEqual(out["summary"]["enforcement_specs"], 2)
        self.assertEqual(out["summary"]["enforcement_targets"], ["eval", "gate"])
        for s in out["enforcement_specs"]:
            pp = s["prevention_pattern"]
            self.assertIn("enforced by", pp)
            self.assertIn("verified by", pp)
            self.assertTrue(s["lever"] and s["actuator"] and s["verification"])
            self.assertEqual(s["status"], "spec_ready_no_producer")

    def test_gate_lever_differs_from_eval_lever(self):
        specs = {s["encoding_target"]: s for s in
                 l2d.convert([_obj(encoding_target="gate"), _obj(encoding_target="eval")])["enforcement_specs"]}
        self.assertNotEqual(specs["gate"]["lever"], specs["eval"]["lever"])
        self.assertIn("verified by a regression check", specs["gate"]["prevention_pattern"])

    def test_defaults_when_trigger_and_purpose_missing(self):
        p = l2d.convert([_obj(title="X", trigger=None, purpose=None)])["proposals"][0]
        sk = p["proposal"]["skillSkeleton"]
        self.assertTrue(sk["trigger"])  # derived, non-empty
        self.assertEqual(sk["purpose"], "X")

    def test_summary_counts_total(self):
        out = l2d.convert([_obj(), _obj(encode="no"), _obj(encoding_target="gate")])
        self.assertEqual(out["summary"]["total"], 3)
        self.assertEqual(out["summary"]["drafted"], 1)

    def test_non_dict_items_are_ignored(self):
        out = l2d.convert([_obj(), "garbage", 42, None])
        self.assertEqual(out["summary"]["drafted"], 1)


if __name__ == "__main__":
    unittest.main()
