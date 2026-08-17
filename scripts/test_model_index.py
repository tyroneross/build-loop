#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for model_index.py — the host-neutral read surface over the model index.

The load-bearing property is AGREEMENT: model_index is a thin shell, so every
answer it gives must equal the answer the existing resolver gives for the same
question. A test that only asserts model_index's own output would certify a
private opinion. So the agreement tests run BOTH CLIs as subprocesses and
compare, rather than re-deriving an expected model id here (which would encode
today's routing into the test and break on every legitimate index edit).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
INDEX = HERE / "model_index.py"
OVERRIDES = HERE / "model_overrides.py"
RESOLVER = HERE / "model_resolver.py"
AGENT_RESOLVER = HERE / "resolve_agent_model.py"
TAXONOMY_PATH = REPO_ROOT / "references" / "model-taxonomy.json"

sys.path.insert(0, str(HERE))
import model_index  # noqa: E402
import model_taxonomy  # noqa: E402


def run(script: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def run_index(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return run(INDEX, *args, cwd=cwd)


def index_json(*args: str, cwd: Path | None = None) -> dict:
    proc = run_index(*args, "--json", cwd=cwd)
    assert proc.returncode in (0, 1), f"unexpected rc={proc.returncode}: {proc.stderr}"
    return json.loads(proc.stdout)


class AgreementTests(unittest.TestCase):
    """model_index must never disagree with the resolver it wraps."""

    def test_resolve_tier_matches_model_resolver_ambient(self):
        # Ambient (no --host): both share the SAME host detection, so the ids
        # must match on whatever host the suite runs on.
        for tier in ("frontier", "thinking", "code", "pattern"):
            with self.subTest(tier=tier):
                mine = index_json("resolve", "--tier", tier)["model"]
                theirs = json.loads(
                    run(RESOLVER, "--tier", tier, "--json").stdout
                )["model"]
                self.assertEqual(mine, theirs)

    def test_resolve_frontier_matches_model_overrides(self):
        # model_overrides has no host filter; pin the host to its Anthropic
        # default mapping so the comparison is apples-to-apples.
        mine = index_json("resolve", "--tier", "frontier", "--host", "anthropic")["model"]
        theirs = json.loads(run(OVERRIDES, "--tier", "frontier", "--json").stdout)["model"]
        self.assertEqual(mine, theirs)

    def test_resolve_code_matches_model_overrides(self):
        mine = index_json("resolve", "--tier", "code", "--host", "anthropic")["model"]
        theirs = json.loads(run(OVERRIDES, "--tier", "code", "--json").stdout)["model"]
        self.assertEqual(mine, theirs)

    def test_role_resolve_matches_resolve_agent_model_for_advisor(self):
        agent = json.loads(run(AGENT_RESOLVER, "advisor", "--json").stdout)
        mine = index_json(
            "resolve", "--tier", agent["tier"], "--segment", agent["segment"]
        )
        self.assertEqual(mine["model"], agent["model"])

    def test_agent_subcommand_matches_resolve_agent_model(self):
        for name in ("advisor", "implementer"):
            with self.subTest(agent=name):
                theirs = json.loads(run(AGENT_RESOLVER, name, "--json").stdout)
                mine = index_json("agent", name)
                self.assertEqual(mine["model"], theirs["model"])
                self.assertEqual(mine["source"], theirs["source"])

    def test_export_map_matches_per_tier_resolve(self):
        exported = index_json("export")["models"]
        for tier, model in exported.items():
            with self.subTest(tier=tier):
                one = index_json("resolve", "--tier", tier)["model"]
                self.assertEqual(one, model)

    def test_tiers_match_the_taxonomy_loader(self):
        payload = index_json("tiers")
        self.assertEqual(payload["tier_ladder"], list(model_taxonomy.tier_ladder()))
        self.assertEqual(payload["legacy_aliases"], model_taxonomy.legacy_aliases())
        self.assertEqual(
            {t["tier"] for t in payload["tiers"]}, set(model_taxonomy.tier_ladder())
        )

    def test_segments_match_the_taxonomy_loader(self):
        payload = index_json("segments")
        self.assertEqual(
            {s["segment"] for s in payload["segments"]},
            set(model_taxonomy.segments()),
        )
        self.assertEqual(payload["active_segments"], model_taxonomy.active_segments())


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_uses_the_canonical_json_algorithm(self):
        raw = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        expected = hashlib.sha256(
            json.dumps(
                raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()[:16]
        self.assertEqual(model_index.fingerprint(), expected)

    def test_fingerprint_is_insensitive_to_reformatting(self):
        # Reformatting the file must NOT read as a routing change — that is the
        # reason the fingerprint covers the parsed value, not the bytes.
        raw = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        reordered = dict(reversed(list(raw.items())))
        self.assertEqual(
            model_index.fingerprint(raw), model_index.fingerprint(reordered)
        )

    def test_fingerprint_changes_when_routing_changes(self):
        raw = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        mutated = json.loads(json.dumps(raw))
        mutated["preferred"]["generative_reasoning"]["T1"] = ["__not-a-model__"]
        self.assertNotEqual(
            model_index.fingerprint(raw), model_index.fingerprint(mutated)
        )

    def test_every_json_payload_carries_the_staleness_contract(self):
        commands = (
            ("resolve", "--tier", "frontier"),
            ("tiers",),
            ("segments",),
            ("models",),
            ("export",),
            ("agent", "advisor"),
        )
        for cmd in commands:
            with self.subTest(cmd=cmd[0]):
                payload = index_json(*cmd)
                self.assertEqual(payload["schema_version"], model_taxonomy.taxonomy()["schema_version"])
                self.assertEqual(payload["fingerprint"], model_index.fingerprint())
                self.assertEqual(payload["command"], cmd[0])
                self.assertTrue(payload["taxonomy_path"].endswith("model-taxonomy.json"))

    def test_envelope_refuses_to_let_a_payload_shadow_the_contract(self):
        """Regression: `agent`'s resolver envelope carries its own `source` key,
        which silently overwrote the contract field before the rename + guard."""
        with self.assertRaises(RuntimeError):
            model_index.envelope("agent", fingerprint="spoofed")
        # And the real agent payload keeps BOTH meanings distinct.
        payload = index_json("agent", "advisor")
        self.assertTrue(payload["taxonomy_path"].endswith("model-taxonomy.json"))
        self.assertEqual(payload["source"], "role-preferred")

    @unittest.skipUnless(
        (Path.home() / "dev/git-folder/RossLabs-AI-Assistant/registry/sync.py").is_file(),
        "RossLabs-AI-Assistant checkout not present",
    )
    def test_agrees_with_ai_assistant_registry_fingerprint(self):
        """The two indexes must reach the SAME staleness verdict.

        A consumer that caches from one and validates against the other would
        thrash forever if the algorithms differed by so much as a separator.
        """
        import importlib.util

        sync_path = Path.home() / "dev/git-folder/RossLabs-AI-Assistant/registry/sync.py"
        spec = importlib.util.spec_from_file_location("_rl_registry_sync", sync_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        raw = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(model_index.fingerprint(), mod._json_fingerprint(raw))


class ExitCodeTests(unittest.TestCase):
    def test_success_is_zero(self):
        self.assertEqual(run_index("tiers").returncode, 0)
        self.assertEqual(run_index("resolve", "--tier", "code").returncode, 0)

    def test_unknown_tier_is_not_found(self):
        proc = run_index("resolve", "--tier", "not-a-tier", "--json")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stderr)["kind"], "not-found")

    def test_unknown_segment_is_not_found(self):
        proc = run_index("resolve", "--tier", "code", "--segment", "nope", "--json")
        self.assertEqual(proc.returncode, 1)

    def test_unknown_agent_is_not_found(self):
        proc = run_index("agent", "__no-such-agent__", "--json")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stderr)["kind"], "not-found")

    def test_unresolvable_role_is_not_found_not_a_crash(self):
        # T5's preferred list is entirely non-Anthropic, so an Anthropic-only
        # host has no reachable candidate: exit 1 with model=None, never a
        # traceback and never a silently-substituted model.
        proc = run_index("resolve", "--tier", "T5", "--host", "anthropic", "--json")
        self.assertEqual(proc.returncode, 1)
        self.assertIsNone(json.loads(proc.stdout)["model"])

    def test_no_models_match_filter_is_not_found(self):
        proc = run_index("models", "--provider", "__nobody__", "--json")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["count"], 0)


class HostNeutralityTests(unittest.TestCase):
    def test_runs_from_an_unrelated_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_index("tiers", "--json", cwd=Path(tmp))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                json.loads(proc.stdout)["tier_ladder"],
                list(model_taxonomy.tier_ladder()),
            )

    def test_answer_is_identical_from_any_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            here = index_json("resolve", "--tier", "frontier", "--host", "anthropic", "--workdir", tmp)
            there = index_json(
                "resolve", "--tier", "frontier", "--host", "anthropic", "--workdir", tmp,
                cwd=Path(tmp),
            )
            self.assertEqual(here["model"], there["model"])

    def test_host_alias_maps_to_provider(self):
        self.assertEqual(model_index.parse_host("codex"), {"openai"})
        self.assertEqual(model_index.parse_host("claude"), {"anthropic"})
        self.assertEqual(model_index.parse_host("gemini"), {"google"})
        self.assertEqual(model_index.parse_host("anthropic,openai"), {"anthropic", "openai"})
        self.assertIsNone(model_index.parse_host(None))

    def test_local_runner_aliases_fold_to_the_taxonomy_provider_token(self):
        # The taxonomy spells locally-run models `provider: local`. A consumer
        # types the runner name; if the alias mapped to "ollama" the filter would
        # match nothing and the local host would look empty.
        for alias in ("local", "ollama", "lmstudio", "mlx"):
            self.assertEqual(model_index.parse_host(alias), {"local"})
        payload = index_json("resolve", "--tier", "T3", "--host", "ollama")
        self.assertEqual(payload["provider"], "local")

    def test_openai_host_resolves_to_an_openai_model(self):
        payload = index_json("resolve", "--tier", "frontier", "--host", "codex")
        self.assertEqual(payload["provider"], "openai")

    def test_host_any_disables_the_filter(self):
        payload = index_json("resolve", "--tier", "frontier", "--host", "any")
        self.assertIsNone(payload["host_providers"])
        self.assertIsNotNone(payload["model"])

    def test_stdlib_only_no_third_party_imports(self):
        """The contract is 'stdlib only, no network' — enforce it structurally."""
        tree = ast.parse(INDEX.read_text(encoding="utf-8"))
        first_party = {p.stem for p in HERE.glob("*.py")}
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
        outside = roots - set(sys.stdlib_module_names) - first_party
        self.assertEqual(outside, set(), f"non-stdlib imports: {outside}")


class ExportFormatTests(unittest.TestCase):
    def _env_lines(self) -> dict[str, str]:
        proc = run_index("export", "--format", "env")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = {}
        for line in proc.stdout.splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k] = v
        return out

    def test_env_emits_prefixed_tier_variables(self):
        env = self._env_lines()
        self.assertEqual(env["BUILDLOOP_MODEL_SCHEMA_VERSION"], model_taxonomy.taxonomy()["schema_version"])
        self.assertEqual(env["BUILDLOOP_MODEL_FINGERPRINT"], model_index.fingerprint())
        for legacy in ("FRONTIER", "THINKING", "CODE", "PATTERN"):
            self.assertIn(f"BUILDLOOP_MODEL_{legacy}", env)

    def test_env_values_match_the_json_export(self):
        env = self._env_lines()
        exported = index_json("export")["models"]
        for tier, model in exported.items():
            key = model_index.env_var_name(tier)
            if model is None:
                self.assertNotIn(key, env)
            else:
                self.assertEqual(env[key], model)

    def test_env_is_shell_sourceable(self):
        """A shell/Codex profile must be able to `source` the output verbatim."""
        with tempfile.TemporaryDirectory() as tmp:
            envfile = Path(tmp) / "models.env"
            envfile.write_text(run_index("export", "--format", "env").stdout, encoding="utf-8")
            proc = subprocess.run(
                ["sh", "-c", f'. "{envfile}"; printf "%s" "$BUILDLOOP_MODEL_CODE"'],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, index_json("resolve", "--tier", "code")["model"])

    def test_env_var_naming_is_shell_safe(self):
        # T-S would be an invalid shell identifier; it must become T_S.
        self.assertEqual(model_index.env_var_name("T-S"), "BUILDLOOP_MODEL_T_S")
        self.assertEqual(model_index.env_var_name("frontier"), "BUILDLOOP_MODEL_FRONTIER")

    def test_toml_parses_and_matches_the_json_export(self):
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - py<3.11
            self.skipTest("tomllib requires Python 3.11+")
        proc = run_index("export", "--format", "toml")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        parsed = tomllib.loads(proc.stdout)
        self.assertEqual(parsed["fingerprint"], model_index.fingerprint())
        exported = index_json("export")["models"]
        for tier, model in exported.items():
            if model is not None:
                self.assertEqual(parsed["models"][tier.replace("-", "_")], model)

    def test_json_flag_overrides_format(self):
        proc = run_index("export", "--format", "env", "--json")
        self.assertEqual(json.loads(proc.stdout)["format"], "json")


class ResolveEnvelopeTests(unittest.TestCase):
    def test_chain_is_ordered_and_marks_the_selection(self):
        payload = index_json("resolve", "--tier", "frontier", "--host", "anthropic")
        chain = payload["fallback_chain"]
        self.assertTrue(chain)
        selected = [c for c in chain if c["selected"]]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["model"], payload["model"])
        self.assertEqual(chain[0]["model"], payload["model"])

    def test_chain_reports_why_a_candidate_was_passed_over(self):
        payload = index_json("resolve", "--tier", "frontier", "--host", "anthropic")
        unreachable = [c for c in payload["fallback_chain"] if not c["host_reachable"]]
        self.assertTrue(unreachable, "expected cross-provider candidates to be flagged")
        for entry in unreachable:
            self.assertNotEqual(entry["provider"], "anthropic")

    def test_frontier_chain_never_descends_below_thinking(self):
        """The hard floor invariant must be visible in the published chain."""
        payload = index_json("resolve", "--tier", "frontier", "--host", "any")
        tiers_in_chain = {c["tier"] for c in payload["fallback_chain"]}
        self.assertFalse(tiers_in_chain & {"code", "pattern"}, tiers_in_chain)

    def test_legacy_and_ladder_tokens_resolve_identically(self):
        for legacy, rung in model_taxonomy.legacy_aliases().items():
            with self.subTest(legacy=legacy):
                a = index_json("resolve", "--tier", legacy, "--host", "anthropic")
                b = index_json("resolve", "--tier", rung, "--host", "anthropic")
                self.assertEqual(a["model"], b["model"])
                self.assertEqual(a["tier"], b["tier"])

    def test_prompting_profile_rides_along(self):
        payload = index_json("resolve", "--tier", "frontier")
        self.assertEqual(
            payload["prompting_profile"], model_taxonomy.prompting_profile("frontier")
        )

    def test_workdir_availability_is_honored(self):
        """A declared outage in the consumer project must change the answer."""
        import time

        with tempfile.TemporaryDirectory() as tmp:
            bl = Path(tmp) / ".build-loop"
            bl.mkdir()
            baseline = index_json("resolve", "--tier", "frontier", "--host", "anthropic", "--workdir", tmp)
            (bl / "model-availability.json").write_text(
                json.dumps(
                    {"unavailable": [{"id": baseline["model"], "recorded_at": time.time(), "ttl": 3600}]}
                ),
                encoding="utf-8",
            )
            after = index_json("resolve", "--tier", "frontier", "--host", "anthropic", "--workdir", tmp)
            self.assertNotEqual(after["model"], baseline["model"])
            skipped = [c for c in after["fallback_chain"] if not c["available"]]
            self.assertTrue(any(c["model"] == baseline["model"] for c in skipped))


class ModelsFilterTests(unittest.TestCase):
    def test_unfiltered_lists_every_registered_model(self):
        payload = index_json("models")
        expected = {
            k for k, v in model_taxonomy.taxonomy()["models"].items()
            if not k.startswith("_") and isinstance(v, dict)
        }
        self.assertEqual({m["id"] for m in payload["models"]}, expected)

    def test_tier_filter_accepts_both_vocabularies(self):
        by_rung = index_json("models", "--tier", "T1")
        by_legacy = index_json("models", "--tier", "frontier")
        self.assertEqual(
            [m["id"] for m in by_rung["models"]], [m["id"] for m in by_legacy["models"]]
        )
        self.assertTrue(all(m["tier"] == "T1" for m in by_rung["models"]))

    def test_provider_and_segment_filters_compose(self):
        payload = index_json("models", "--provider", "anthropic", "--segment", "generative_reasoning")
        self.assertTrue(payload["models"])
        for m in payload["models"]:
            self.assertEqual(m["provider"], "anthropic")
            self.assertEqual(m["segment"], "generative_reasoning")

    def test_status_filter(self):
        payload = index_json("models", "--status", "local")
        self.assertTrue(payload["models"])
        self.assertTrue(all(m["status"] == "local" for m in payload["models"]))


class HumanOutputTests(unittest.TestCase):
    def test_default_output_is_human_readable_not_json(self):
        for cmd in (("tiers",), ("segments",), ("models",), ("resolve", "--tier", "code")):
            with self.subTest(cmd=cmd[0]):
                out = run_index(*cmd).stdout
                self.assertFalse(out.lstrip().startswith("{"), out[:80])
                self.assertTrue(out.strip())

    def test_human_resolve_names_the_model_and_the_reason(self):
        out = run_index("resolve", "--tier", "code", "--host", "anthropic").stdout
        model = index_json("resolve", "--tier", "code", "--host", "anthropic")["model"]
        self.assertIn(model, out)
        self.assertIn("why:", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
