"""Regression checks for the bounded, read-only early risk probe."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import early_risk_probe as risk  # noqa: E402


def write(root: Path, rel: str, value: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


class EarlyRiskProbeTests(unittest.TestCase):
    def test_finds_predictable_deploy_delay_without_running_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write(root, "package.json", json.dumps({"scripts": {
                "deploy:db:gate": "node db-check.js",
                "deploy:preflight": "bash scripts/deploy-preflight.sh",
            }}))
            write(root, "scripts/deploy-preflight.sh",
                  "npm run build\nnpm run deploy:db:gate\n")
            write(root, "vercel.json", json.dumps({"git": {"deploymentEnabled": {"main": False}}}))
            write(root, ".github/workflows/stage.yml", "vercel deploy --prod --skip-domain")
            write(root, "docs/AGENT-ROUTING.md", "main auto-deploys. A push is a deploy.")
            write(root, ".navgator/architecture/index.json", json.dumps({"last_scan": 1_000}))
            result = risk.probe(root, now_ms=1_000_000_000)
            ids = {item["id"] for item in result["findings"]}
            self.assertEqual(ids, {
                "architecture_index_unpinned", "deployment_doc_conflict",
                "migration_gate_after_build",
            })
            self.assertIn("npm run deploy:db:gate", result["available_checks"])

    def test_clean_staged_workflow_does_not_warn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write(root, "package.json", json.dumps({"scripts": {
                "deploy:db:gate": "node db-check.js",
                "deploy:preflight": "bash scripts/deploy-preflight.sh",
            }}))
            write(root, "scripts/deploy-preflight.sh",
                  "npm run deploy:db:gate\nnpm run build\n")
            write(root, "vercel.json", json.dumps({"git": {"deploymentEnabled": {"main": False}}}))
            write(root, ".github/workflows/stage.yml", "vercel deploy --prod --skip-domain")
            write(root, "docs/AGENT-ROUTING.md", "main stages; promotion is explicit.")
            result = risk.probe(root, now_ms=200_000_000)
            self.assertEqual(result["status"], "clear")

    def test_external_next_modules_warns_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            outside = Path(temp) / "shared-modules"
            outside.mkdir()
            write(root, "package.json", json.dumps({"dependencies": {"next": "1.0.0"}}))
            (root / "node_modules").symlink_to(outside, target_is_directory=True)
            result = risk.probe(root)
            self.assertEqual([item["id"] for item in result["findings"]], ["external_node_modules"])

    def test_old_graph_at_same_clean_revision_needs_no_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write(root, ".navgator/architecture/index.json", json.dumps({"last_scan": 1_000}))
            write(root, ".navgator/architecture/freshness.json",
                  json.dumps({"commit_sha": "abc123", "dirty_count": 0}))
            with patch.object(risk, "_head", return_value="abc123def456"), \
                 patch.object(risk, "_source_worktree_changes", return_value=[]):
                result = risk.probe(root, now_ms=1_000_000_000)
            self.assertEqual(result["status"], "clear")

    def test_blank_graph_sha_is_not_a_valid_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write(root, ".navgator/architecture/index.json", json.dumps({"last_scan": 1_000}))
            write(root, ".navgator/architecture/freshness.json", json.dumps({"commit_sha": ""}))
            with patch.object(risk, "_head", return_value="abc123def456"):
                result = risk.probe(root, now_ms=1_000_000_000)
            self.assertEqual(result["findings"][0]["id"], "architecture_index_unpinned")

    def test_worktree_source_edits_invalidate_same_sha_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write(root, ".navgator/architecture/index.json", json.dumps({"last_scan": 1_000}))
            write(root, ".navgator/architecture/freshness.json",
                  json.dumps({"commit_sha": "abc123", "dirty_count": 0}))
            with patch.object(risk, "_head", return_value="abc123def456"), \
                 patch.object(risk, "_source_worktree_changes", return_value=["lib/api.ts"]):
                result = risk.probe(root, now_ms=1_000_000_000)
            self.assertEqual(result["findings"][0]["id"], "architecture_index_uncommitted_changes")

    def test_failed_git_status_cannot_make_graph_look_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write(root, ".navgator/architecture/index.json", json.dumps({"last_scan": 1_000}))
            write(root, ".navgator/architecture/freshness.json",
                  json.dumps({"commit_sha": "abc123", "dirty_count": 0}))
            with patch.object(risk, "_head", return_value="abc123def456"), \
                 patch.object(risk, "_source_worktree_changes", return_value=None):
                result = risk.probe(root, now_ms=1_000_000_000)
            self.assertEqual(result["findings"][0]["id"], "architecture_index_worktree_unchecked")

    def test_git_worktree_edits_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            write(root, "lib/api.ts", "export const value = 1;\n")
            subprocess.run(["git", "-C", str(root), "add", "lib/api.ts"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
            head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            write(root, ".navgator/architecture/index.json", json.dumps({"last_scan": 1_000}))
            write(root, ".navgator/architecture/freshness.json",
                  json.dumps({"commit_sha": head[:7], "dirty_count": 0}))
            write(root, "lib/api.ts", "export const value = 2;\n")
            result = risk.probe(root, now_ms=1_000_000_000)
            self.assertEqual(result["findings"][0]["id"], "architecture_index_uncommitted_changes")
            write(root, "lib/api.ts", "export const value = 1;\n")
            write(root, "src/new.ts", "export const added = true;\n")
            result = risk.probe(root, now_ms=1_000_000_000)
            self.assertEqual(result["findings"][0]["id"], "architecture_index_uncommitted_changes")

    def test_negative_deploy_doc_and_yaml_workflow_do_not_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write(root, "vercel.json", json.dumps({"git": {"deploymentEnabled": {"main": False}}}))
            write(root, ".github/workflows/stage.yaml", "vercel deploy --prod --skip-domain")
            write(root, "docs/AGENT-ROUTING.md", "main does not auto-deploy; promote the staged build.")
            self.assertEqual(risk.probe(root)["status"], "clear")
            write(root, "docs/AGENT-ROUTING.md", "main auto-deploys; no promotion needed.")
            self.assertEqual(risk.probe(root)["findings"][0]["id"], "deployment_doc_conflict")


if __name__ == "__main__":
    unittest.main()
