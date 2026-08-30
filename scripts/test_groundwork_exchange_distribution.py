#!/usr/bin/env python3
"""Staged Claude/Codex checksum and execution parity for Groundwork exchange."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
ADAPTER = HERE / "groundwork_exchange.py"
SYNC = HERE / "sync_plugin_cache.py"
ARTIFACT = REPO_ROOT / "plugin-artifacts" / "codex"


def load_adapter():
    spec = importlib.util.spec_from_file_location("groundwork_exchange_distribution", ADAPTER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gx = load_adapter()


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def git(repo: Path, *args: str) -> str:
    result = run(["git", *args], cwd=repo)
    if result.returncode != 0:
        raise AssertionError(result.stderr + result.stdout)
    return result.stdout.strip()


class GroundworkExchangeDistributionTests(unittest.TestCase):
    def test_staged_and_installed_hosts_execute_identical_exchange_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            claude_cache = tmp / "claude-cache"
            codex_cache = tmp / "codex-cache"

            claude = run([
                "python3", str(SYNC), "--source", str(REPO_ROOT), "--host", "claude",
                "--cache", str(claude_cache), "--dirty",
                "--file", ".claude-plugin/plugin.json", "--file", "scripts/groundwork_exchange.py", "--json",
            ])
            self.assertEqual(claude.returncode, 0, msg=claude.stderr + claude.stdout)
            codex = run([
                "python3", str(SYNC), "--source", str(ARTIFACT), "--host", "codex",
                "--cache", str(codex_cache), "--dirty",
                "--file", ".codex-plugin/plugin.json", "--file", "scripts/groundwork_exchange.py", "--json",
            ])
            self.assertEqual(codex.returncode, 0, msg=codex.stderr + codex.stdout)

            copies = [
                ADAPTER,
                ARTIFACT / "scripts" / "groundwork_exchange.py",
                claude_cache / "scripts" / "groundwork_exchange.py",
                codex_cache / "scripts" / "groundwork_exchange.py",
            ]
            byte_sets = [path.read_bytes() for path in copies]
            self.assertTrue(all(payload == byte_sets[0] for payload in byte_sets[1:]))
            checksums = [gx.digest_file(path) for path in copies]
            self.assertEqual(len(set(checksums)), 1)

            workdir = tmp / "consumer"
            workdir.mkdir()
            git(workdir, "init", "-q")
            git(workdir, "config", "user.email", "test@example.com")
            git(workdir, "config", "user.name", "Test User")
            git(workdir, "config", "core.hooksPath", "/dev/null")
            git(workdir, "config", "commit.gpgsign", "false")
            (workdir / "src").mkdir()
            (workdir / "src" / "app.ts").write_text("export const ready = true;\n", encoding="utf-8")
            (workdir / ".build-loop" / "evidence").mkdir(parents=True)
            (workdir / ".build-loop" / "evidence" / "test.log").write_text("pass\n", encoding="utf-8")
            git(workdir, "add", "src/app.ts")
            git(workdir, "commit", "-q", "-m", "implement task")
            commit = git(workdir, "rev-parse", "HEAD")

            canonical_spec = {
                "id": "spec-parity", "schemaVersion": 3,
                "platformSurfaces": [{
                    "id": "surface-web", "platform": "web", "role": "primary", "name": "Web",
                    "interactionModes": ["pointer"], "featureIds": [],
                }],
                "screens": [{
                    "id": "screen-plan", "name": "Plan", "purpose": "Review the plan",
                    "featureIds": [], "states": ["reviewing"],
                }],
                "architecture": {
                    "components": [
                        {"id": "component-source", "name": "Source", "kind": "service", "featureIds": [], "owner": "app", "ownedFiles": ["src/app.ts"]},
                        {"id": "component-plan", "name": "Plan", "kind": "ui", "featureIds": [], "owner": "app"},
                    ],
                    "contracts": [{
                        "id": "contract-plan", "name": "Plan contract",
                        "provider": {"specId": "spec-parity", "kind": "component", "id": "component-source"},
                        "consumers": [{"specId": "spec-parity", "kind": "component", "id": "component-plan"}],
                        "ports": [{"id": "port-plan", "name": "Plan", "type": "json", "direction": "output"}],
                        "transport": "in-process", "failureModes": ["invalid"], "securityNotes": ["local"],
                        "ownedFiles": ["src/app.ts"],
                    }],
                    "relationships": [],
                    "flows": [{
                        "id": "flow-plan", "name": "Review plan", "trigger": "Open",
                        "exchanges": [{
                            "id": "exchange-plan", "order": 1,
                            "from": {"specId": "spec-parity", "kind": "component", "id": "component-source"},
                            "to": {"specId": "spec-parity", "kind": "component", "id": "component-plan"},
                            "contractRef": {"specId": "spec-parity", "kind": "contract", "id": "contract-plan"},
                            "inputRefs": [], "outputRefs": [],
                            "stateRefs": [{"screenId": "screen-plan", "state": "reviewing"}],
                            "failurePaths": ["invalid"],
                        }],
                    }],
                    "specDependencies": [],
                },
            }
            tasks = [{
                "id": "task-parity", "title": "Implement parity", "componentRefs": [],
                "contractRefs": [], "requirementIds": ["requirement-parity"],
                "dependsOn": [], "acceptanceCriterionIds": ["acceptance-parity"],
                "ownedFiles": ["src/app.ts"],
            }]
            request = {
                "contract": gx.BUILD_REQUEST_CONTRACT, "runId": "run-parity", "specId": "spec-parity",
                "specDigest": gx.digest_normalized(canonical_spec), "taskDigest": gx.digest_normalized(tasks),
                "platformSurfaces": canonical_spec["platformSurfaces"],
                "architecture": canonical_spec["architecture"],
                "tasks": tasks,
                "acceptanceCriteria": [{"id": "acceptance-parity", "statement": "WHEN built, THE SYSTEM SHALL pass."}],
                "manualActions": [],
                "returnVersions": {"implementationMap": [gx.IMPLEMENTATION_MAP_CONTRACT], "convergence": [gx.CONVERGENCE_CONTRACT]},
                "requestDigest": "sha256:" + "0" * 64, "createdAt": "2026-08-05T02:00:00Z",
            }
            request["requestDigest"] = gx.digest_normalized({key: value for key, value in request.items() if key != "requestDigest"})
            evidence = {
                "mappings": [{
                    "id": "mapping-task-parity", "kind": "task", "targetId": "task-parity", "status": "verified",
                    "fileRefs": ["src/app.ts"], "symbolRefs": ["ready"], "commitRefs": [commit],
                    "testEvidenceIds": ["evidence-parity"], "runtimeEvidenceIds": [],
                }],
                "evidence": [{
                    "id": "evidence-parity", "kind": "test", "command": "npm test", "outcome": "passed",
                    "artifactPath": ".build-loop/evidence/test.log", "recordedAt": "2026-08-05T02:01:00Z",
                }],
                "deviations": [],
            }
            request_path = workdir / "build-request.json"
            spec_path = workdir / "spec.json"
            evidence_path = workdir / "evidence.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            spec_path.write_text(json.dumps(canonical_spec), encoding="utf-8")
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

            outputs: list[bytes] = []
            for host, cache in (("claude", claude_cache), ("codex", codex_cache)):
                output = workdir / f"implementation-map-{host}.json"
                executed = run([
                    "python3", str(cache / "scripts" / "groundwork_exchange.py"), "emit-map",
                    "--request", str(request_path), "--spec", str(spec_path), "--evidence", str(evidence_path),
                    "--workdir", str(workdir), "--output", str(output), "--producer-version", "0.37.0",
                    "--created-at", "2026-08-05T02:02:00Z",
                    "--verified-evidence-id", "evidence-parity",
                ])
                self.assertEqual(executed.returncode, 0, msg=f"{host}: {executed.stderr}{executed.stdout}")
                outputs.append(output.read_bytes())
            self.assertEqual(outputs[0], outputs[1])
            mapped = json.loads(outputs[0])
            self.assertEqual(mapped["buildRequestDigest"], request["requestDigest"])
            self.assertEqual(mapped["mappings"][0]["status"], "verified")


if __name__ == "__main__":
    unittest.main(verbosity=2)
