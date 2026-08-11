#!/usr/bin/env python3
"""Contract and adversarial tests for the Groundwork exchange adapter."""
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import math
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "groundwork_exchange.py"
REPO_ROOT = HERE.parent


def load_adapter():
    spec = importlib.util.spec_from_file_location("groundwork_exchange", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gx = load_adapter()


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True)
    return result.stdout.strip()


class GroundworkExchangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "config", "user.name", "Test User")
        git(self.root, "config", "core.hooksPath", "/dev/null")
        git(self.root, "config", "commit.gpgsign", "false")
        (self.root / "src").mkdir()
        (self.root / "src" / "guidance.ts").write_text("export const guidance = true;\n", encoding="utf-8")
        (self.root / ".build-loop" / "evidence").mkdir(parents=True)
        (self.root / ".build-loop" / "evidence" / "tests.log").write_text("1 test passed\n", encoding="utf-8")
        git(self.root, "add", "src/guidance.ts")
        git(self.root, "commit", "-q", "-m", "implement guidance")
        self.commit = git(self.root, "rev-parse", "HEAD")
        self.spec = {
            "id": "spec-demo", "schemaVersion": 3, "name": "Demo",
            "platformSurfaces": [{
                "id": "surface-web", "platform": "web", "role": "primary", "name": "Web",
                "interactionModes": ["pointer"], "featureIds": [],
            }],
            "architecture": {"components": [], "contracts": [], "relationships": [], "flows": [], "specDependencies": []},
        }
        self.request = self.make_request()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_request(self) -> dict:
        tasks = [{
            "id": "task-guidance",
            "title": "Implement guidance",
            "componentRefs": [],
            "contractRefs": [],
            "requirementIds": ["requirement-guidance"],
            "dependsOn": [],
            "acceptanceCriterionIds": ["acceptance-guidance"],
        }]
        request = {
            "contract": gx.BUILD_REQUEST_CONTRACT,
            "runId": "run-demo",
            "specId": "spec-demo",
            "specDigest": gx.digest_normalized(self.spec),
            "taskDigest": gx.digest_normalized(tasks),
            "platformSurfaces": self.spec["platformSurfaces"],
            "architecture": self.spec["architecture"],
            "tasks": tasks,
            "acceptanceCriteria": [{"id": "acceptance-guidance", "statement": "WHEN opened, THE SYSTEM SHALL show guidance."}],
            "manualActions": [],
            "returnVersions": {
                "implementationMap": [gx.IMPLEMENTATION_MAP_CONTRACT],
                "convergence": [gx.CONVERGENCE_CONTRACT],
            },
            "requestDigest": "sha256:" + "0" * 64,
            "createdAt": "2026-08-05T01:00:00Z",
        }
        request["requestDigest"] = gx.digest_normalized({key: value for key, value in request.items() if key != "requestDigest"})
        return request

    def evidence_draft(self) -> dict:
        return {
            "mappings": [{
                "id": "mapping-task-guidance",
                "kind": "task",
                "targetId": "task-guidance",
                "status": "verified",
                "fileRefs": ["src/guidance.ts"],
                "symbolRefs": ["guidance"],
                "commitRefs": [self.commit],
                "testEvidenceIds": ["evidence-guidance-test"],
                "runtimeEvidenceIds": [],
            }],
            "evidence": [{
                "id": "evidence-guidance-test",
                "kind": "test",
                "command": "python3 -m unittest tests.test_guidance",
                "outcome": "passed",
                "summary": "Guidance acceptance passed.",
                "artifactPath": ".build-loop/evidence/tests.log",
                "recordedAt": "2026-08-05T01:05:00Z",
            }],
            "deviations": [],
        }

    def test_normalization_matches_javascript_contract_vectors(self) -> None:
        value = {"z": -0.0, "a": [1.0, 1e-7, 1e20, "é"], "nested": {"b": True, "a": None}}
        self.assertEqual(
            gx.normalize_json(value),
            '{"a":[1,1e-7,100000000000000000000,"é"],"nested":{"a":null,"b":true},"z":0}',
        )
        with self.assertRaisesRegex(gx.ExchangeError, "finite"):
            gx.normalize_json({"bad": math.inf})
        with self.assertRaisesRegex(gx.ExchangeError, "finite"):
            gx.normalize_json({"tooLarge": 10 ** 400})
        self.assertEqual(
            gx.normalize_json({"big": 9007199254740993, "keys": {"\ue000": 1, "😀": 2}, "lone": "\ud800"}),
            '{"big":9007199254740992,"keys":{"😀":2,"":1},"lone":"\\ud800"}',
        )
        node = subprocess.run([
            "node", "-e",
            'const v={big:9007199254740993,keys:{"\\uE000":1,"😀":2},lone:"\\ud800"};'
            'const n=x=>x===null||typeof x!=="object"?(Object.is(x,-0)?0:x):'
            '(Array.isArray(x)?x.map(n):Object.fromEntries(Object.keys(x).sort().map(k=>[k,n(x[k])])));'
            'process.stdout.write(JSON.stringify(n(v)));',
        ], text=True, capture_output=True, check=False)
        self.assertEqual(node.returncode, 0, msg=node.stderr)
        self.assertEqual(
            gx.normalize_json({"big": 9007199254740993, "keys": {"\ue000": 1, "😀": 2}, "lone": "\ud800"}),
            node.stdout,
        )

    def test_phase_and_distribution_surfaces_activate_exchange(self) -> None:
        required = {
            "agents/build-orchestrator.md": ("validate-request", "emit-map"),
            "skills/build-loop/SKILL.md": ("validate-request", "emit-map"),
            "references/phase-gate-checklist.md": ("validate-request", "emit-map"),
            "skills/build-loop/references/phase-1-assess.md": ("validate-request",),
            "skills/build-loop/references/phase-4-review.md": ("emit-map", "--verified-evidence-id"),
            "README.md": ("Groundwork exchange", "implementation-map.json"),
        }
        for relative, needles in required.items():
            text = (REPO_ROOT / relative).read_text(encoding="utf-8")
            for needle in needles:
                self.assertIn(needle, text, msg=f"{relative} must activate {needle}")

    def test_request_bindings_validate_and_tampering_fails(self) -> None:
        self.assertEqual(gx.validate_request(self.request, self.spec)["runId"], "run-demo")
        tampered = json.loads(json.dumps(self.request))
        tampered["tasks"][0]["title"] = "Tampered"
        with self.assertRaisesRegex(gx.ExchangeError, "taskDigest"):
            gx.validate_request(tampered, self.spec)
        with self.assertRaisesRegex(gx.ExchangeError, "specDigest"):
            gx.validate_request(self.request, {**self.spec, "name": "different"})

        changed_architecture = json.loads(json.dumps(self.request))
        changed_architecture["architecture"]["components"] = [{"id": "forged"}]
        changed_architecture["requestDigest"] = gx.digest_normalized({key: value for key, value in changed_architecture.items() if key != "requestDigest"})
        with self.assertRaisesRegex(gx.ExchangeError, "architecture"):
            gx.validate_request(changed_architecture, self.spec)

        future_dependency = json.loads(json.dumps(self.request))
        future_dependency["tasks"].append({
            "id": "task-future", "title": "Future", "componentRefs": [], "contractRefs": [],
            "requirementIds": [], "dependsOn": [], "acceptanceCriterionIds": [],
        })
        future_dependency["tasks"][0]["dependsOn"] = ["task-future"]
        future_dependency["taskDigest"] = gx.digest_normalized(future_dependency["tasks"])
        future_dependency["requestDigest"] = gx.digest_normalized({key: value for key, value in future_dependency.items() if key != "requestDigest"})
        with self.assertRaisesRegex(gx.ExchangeError, "earlier task"):
            gx.validate_request(future_dependency, self.spec)

        unknown_version = json.loads(json.dumps(self.request))
        unknown_version["returnVersions"]["implementationMap"].append("build-loop.implementation-map/v2")
        unknown_version["requestDigest"] = gx.digest_normalized({key: value for key, value in unknown_version.items() if key != "requestDigest"})
        with self.assertRaisesRegex(gx.ExchangeError, "unsupported implementation-map"):
            gx.validate_request(unknown_version, self.spec)

        unsafe_manual = json.loads(json.dumps(self.request))
        unsafe_manual["manualActions"] = [{
            "id": "manual-secret", "location": "Provider", "action": "Set credential: sk-live-abcdefghijklmnop",
            "requiredValueName": "API_KEY", "destination": "Settings", "verification": "Confirm saved",
        }]
        unsafe_manual["requestDigest"] = gx.digest_normalized({key: value for key, value in unsafe_manual.items() if key != "requestDigest"})
        with self.assertRaisesRegex(gx.ExchangeError, "secret|credential"):
            gx.validate_request(unsafe_manual, self.spec)

        malformed_enum = json.loads(json.dumps(self.request))
        malformed_enum["platformSurfaces"][0]["platform"] = []
        malformed_enum["requestDigest"] = gx.digest_normalized({key: value for key, value in malformed_enum.items() if key != "requestDigest"})
        with self.assertRaisesRegex(gx.ExchangeError, "platform"):
            gx.validate_request(malformed_enum, {**self.spec, "platformSurfaces": malformed_enum["platformSurfaces"]})

        invalid_topology_id = json.loads(json.dumps(self.request))
        invalid_topology_id["platformSurfaces"][0]["id"] = "bad id"
        invalid_topology_spec = json.loads(json.dumps(self.spec))
        invalid_topology_spec["platformSurfaces"] = invalid_topology_id["platformSurfaces"]
        invalid_topology_id["specDigest"] = gx.digest_normalized(invalid_topology_spec)
        invalid_topology_id["requestDigest"] = gx.digest_normalized({key: value for key, value in invalid_topology_id.items() if key != "requestDigest"})
        with self.assertRaisesRegex(gx.ExchangeError, "architecture ID syntax"):
            gx.validate_request(invalid_topology_id, invalid_topology_spec)

        bad_timestamp = json.loads(json.dumps(self.request))
        bad_timestamp["createdAt"] = "2026-08-05 01:00:00+00:00"
        bad_timestamp["requestDigest"] = gx.digest_normalized({key: value for key, value in bad_timestamp.items() if key != "requestDigest"})
        with self.assertRaisesRegex(gx.ExchangeError, "RFC 3339"):
            gx.validate_request(bad_timestamp, self.spec)

        compact_offset = json.loads(json.dumps(self.request))
        compact_offset["createdAt"] = "2026-08-05T01:00:00+0000"
        compact_offset["requestDigest"] = gx.digest_normalized({key: value for key, value in compact_offset.items() if key != "requestDigest"})
        self.assertEqual(gx.validate_request(compact_offset, self.spec)["createdAt"], compact_offset["createdAt"])

        ambiguous = json.loads(json.dumps(self.request))
        ambiguous_component = {
            "id": "task-guidance", "name": "Ambiguous", "kind": "service",
            "featureIds": [], "owner": "app",
        }
        ambiguous["architecture"]["components"] = [ambiguous_component]
        ambiguous_spec = json.loads(json.dumps(self.spec))
        ambiguous_spec["architecture"]["components"] = [ambiguous_component]
        ambiguous["specDigest"] = gx.digest_normalized(ambiguous_spec)
        ambiguous["requestDigest"] = gx.digest_normalized({key: value for key, value in ambiguous.items() if key != "requestDigest"})
        with self.assertRaisesRegex(gx.ExchangeError, "globally unique"):
            gx.validate_request(ambiguous, ambiguous_spec)

        secret_manual_id = json.loads(json.dumps(self.request))
        secret_manual_id["manualActions"] = [{
            "id": "ghp_abcdefghijklmnopqrstuvwxyz", "location": "Provider", "action": "Set named value",
            "requiredValueName": "API_KEY", "destination": "Settings", "verification": "Confirm saved",
        }]
        secret_manual_id["requestDigest"] = gx.digest_normalized({key: value for key, value in secret_manual_id.items() if key != "requestDigest"})
        with self.assertRaisesRegex(gx.ExchangeError, "secret|credential"):
            gx.validate_request(secret_manual_id, self.spec)

        duplicate_consumers = json.loads(json.dumps(self.request))
        component = {"id": "component-a", "name": "A", "kind": "service", "featureIds": [], "owner": "app"}
        ref = {"specId": "spec-demo", "kind": "component", "id": "component-a"}
        contract = {
            "id": "contract-a", "name": "A contract", "provider": ref,
            "consumers": [ref, ref],
            "ports": [{"id": "port-a", "name": "Input", "type": "json", "direction": "input", "required": True}],
            "transport": "in-process", "failureModes": ["invalid"], "securityNotes": ["local"],
        }
        duplicate_consumers["architecture"]["components"] = [component]
        duplicate_consumers["architecture"]["contracts"] = [contract]
        duplicate_spec = json.loads(json.dumps(self.spec))
        duplicate_spec["architecture"] = duplicate_consumers["architecture"]
        duplicate_consumers["specDigest"] = gx.digest_normalized(duplicate_spec)
        duplicate_consumers["requestDigest"] = gx.digest_normalized({key: value for key, value in duplicate_consumers.items() if key != "requestDigest"})
        with self.assertRaisesRegex(gx.ExchangeError, "consumers contains duplicate"):
            gx.validate_request(duplicate_consumers, duplicate_spec)

        dangling = json.loads(json.dumps(self.request))
        component = {"id": "component-a", "name": "A", "kind": "service", "featureIds": [], "owner": "app"}
        dangling["architecture"]["components"] = [component]
        dangling["architecture"]["relationships"] = [{
            "id": "relationship-a-missing",
            "from": {"specId": "spec-demo", "kind": "component", "id": "component-a"},
            "to": {"specId": "spec-demo", "kind": "component", "id": "component-missing"},
            "direction": "unidirectional", "criticality": "hard", "optional": False,
            "rationale": "Connect the components.",
        }]
        dangling_spec = json.loads(json.dumps(self.spec))
        dangling_spec["architecture"] = dangling["architecture"]
        dangling["specDigest"] = gx.digest_normalized(dangling_spec)
        dangling["requestDigest"] = gx.digest_normalized({key: value for key, value in dangling.items() if key != "requestDigest"})
        with self.assertRaisesRegex(gx.ExchangeError, "unknown local component component-missing"):
            gx.validate_request(dangling, dangling_spec)

    def test_verified_map_is_bound_to_real_repository_evidence(self) -> None:
        request = gx.validate_request(self.request, self.spec)
        result = gx.build_implementation_map(
            request,
            self.evidence_draft(),
            workdir=self.root,
            producer_version="0.37.0",
            producer_commit=None,
            created_at="2026-08-05T01:06:00Z",
            verified_evidence_ids={"evidence-guidance-test"},
        )
        self.assertEqual(result["buildRequestDigest"], request["requestDigest"])
        self.assertNotIn("commit", result["producer"])
        self.assertRegex(result["evidence"][0]["artifactDigest"], r"^sha256:[a-f0-9]{64}$")
        projection = {key: value for key, value in result.items() if key != "implementationMapDigest"}
        self.assertEqual(result["implementationMapDigest"], gx.digest_normalized(projection))
        self.assertNotIn("artifactPath", result["evidence"][0])

    def test_overstated_or_fabricated_evidence_fails_closed(self) -> None:
        request = gx.validate_request(self.request, self.spec)
        with self.assertRaisesRegex(gx.ExchangeError, "verified-evidence-id"):
            gx.build_implementation_map(request, self.evidence_draft(), workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z")

        no_artifact = self.evidence_draft()
        del no_artifact["evidence"][0]["artifactPath"]
        with self.assertRaisesRegex(gx.ExchangeError, "requires artifactPath"):
            gx.build_implementation_map(request, no_artifact, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        unsafe = self.evidence_draft()
        unsafe["mappings"][0]["fileRefs"] = ["../outside.ts"]
        with self.assertRaisesRegex(gx.ExchangeError, "unsafe|repository-relative"):
            gx.build_implementation_map(request, unsafe, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        (self.root / "linked.ts").symlink_to(self.root / "src" / "guidance.ts")
        symlinked = self.evidence_draft()
        symlinked["mappings"][0]["fileRefs"] = ["linked.ts"]
        with self.assertRaisesRegex(gx.ExchangeError, "symlink"):
            gx.build_implementation_map(request, symlinked, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        private_artifact = self.evidence_draft()
        private_artifact["evidence"][0]["artifactPath"] = ".git/config"
        with self.assertRaisesRegex(gx.ExchangeError, "private|evidence"):
            gx.build_implementation_map(request, private_artifact, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        encoded_private = self.evidence_draft()
        encoded_private["mappings"][0]["fileRefs"] = ["%2egit/config"]
        with self.assertRaisesRegex(gx.ExchangeError, "private"):
            gx.build_implementation_map(request, encoded_private, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        invalid_percent = self.evidence_draft()
        invalid_percent["mappings"][0]["fileRefs"] = ["src/%ZZ.ts"]
        with self.assertRaisesRegex(gx.ExchangeError, "percent encoding"):
            gx.build_implementation_map(request, invalid_percent, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        contradictory = self.evidence_draft()
        contradictory["mappings"][0]["status"] = "implemented"
        with self.assertRaisesRegex(gx.ExchangeError, "contradicts"):
            gx.build_implementation_map(request, contradictory, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        commit_only = self.evidence_draft()
        commit_only["mappings"][0]["fileRefs"] = []
        with self.assertRaisesRegex(gx.ExchangeError, "both repository"):
            gx.build_implementation_map(request, commit_only, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        missing_commit = self.evidence_draft()
        missing_commit["mappings"][0]["commitRefs"] = ["deadbee"]
        with self.assertRaisesRegex(gx.ExchangeError, "does not resolve"):
            gx.build_implementation_map(request, missing_commit, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        stale = self.evidence_draft()
        stale["evidence"][0]["recordedAt"] = "2026-08-05T00:59:59Z"
        with self.assertRaisesRegex(gx.ExchangeError, "outside"):
            gx.build_implementation_map(request, stale, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        secret_command = self.evidence_draft()
        secret_command["evidence"][0]["command"] = "API_KEY=sk-abcdefghijklmnop npm test"
        with self.assertRaisesRegex(gx.ExchangeError, "secret|credential"):
            gx.build_implementation_map(request, secret_command, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        secret_summary = self.evidence_draft()
        secret_summary["evidence"][0]["summary"] = "Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz"
        with self.assertRaisesRegex(gx.ExchangeError, "secret|credential"):
            gx.build_implementation_map(request, secret_summary, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        secret_symbol = self.evidence_draft()
        secret_symbol["mappings"][0]["symbolRefs"] = ["ghp_abcdefghijklmnopqrstuvwxyz"]
        with self.assertRaisesRegex(gx.ExchangeError, "secret|credential"):
            gx.build_implementation_map(request, secret_symbol, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        secret_deviation = self.evidence_draft()
        secret_deviation["deviations"] = [{
            "id": "deviation-secret", "targetId": "task-guidance",
            "summary": "password=super-secret-value", "impact": "high",
        }]
        with self.assertRaisesRegex(gx.ExchangeError, "secret|credential"):
            gx.build_implementation_map(request, secret_deviation, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

        mixed_outcomes = self.evidence_draft()
        mixed_outcomes["evidence"].append({
            "id": "evidence-guidance-failure", "kind": "test", "command": "npm test -- failing",
            "outcome": "failed", "summary": "A verification command failed.",
            "recordedAt": "2026-08-05T01:05:30Z",
        })
        mixed_outcomes["mappings"][0]["testEvidenceIds"].append("evidence-guidance-failure")
        with self.assertRaisesRegex(gx.ExchangeError, "all referenced.*pass"):
            gx.build_implementation_map(request, mixed_outcomes, workdir=self.root, producer_version="0.37.0", producer_commit=None, created_at="2026-08-05T01:06:00Z", verified_evidence_ids={"evidence-guidance-test"})

    def test_cli_writes_map_atomically_and_reports_json(self) -> None:
        request_path = self.root / "build-request.json"
        spec_path = self.root / "spec.json"
        evidence_path = self.root / "evidence.json"
        output_path = self.root / ".build-loop" / "implementation-map.json"
        request_path.write_text(json.dumps(self.request), encoding="utf-8")
        spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        evidence_path.write_text(json.dumps(self.evidence_draft()), encoding="utf-8")
        result = subprocess.run([
            "python3", str(SCRIPT), "emit-map",
            "--request", str(request_path), "--spec", str(spec_path),
            "--evidence", str(evidence_path), "--workdir", str(self.root),
            "--output", str(output_path), "--producer-version", "0.37.0",
            "--created-at", "2026-08-05T01:06:00Z",
            "--verified-evidence-id", "evidence-guidance-test",
        ], text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["contract"], gx.IMPLEMENTATION_MAP_CONTRACT)
        self.assertTrue(output_path.is_file())
        self.assertFalse(list(output_path.parent.glob(f".{output_path.name}.*")))

    def test_cli_normalizes_operational_failures_to_exit_two(self) -> None:
        request_path = self.root / "build-request.json"
        spec_path = self.root / "spec.json"
        request_path.write_text(json.dumps(self.request), encoding="utf-8")
        spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        stderr = io.StringIO()
        with mock.patch.object(gx, "_atomic_write_json", side_effect=OSError("disk full")):
            with contextlib.redirect_stderr(stderr):
                result = gx.main([
                    "validate-request",
                    "--request", str(request_path),
                    "--spec", str(spec_path),
                    "--output", str(self.root / "validated.json"),
                ])
        self.assertEqual(result, 2)
        failure = json.loads(stderr.getvalue())
        self.assertFalse(failure["ok"])
        self.assertIn("operational failure", failure["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
