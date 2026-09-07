from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / "references/provider-catalogs"
SKILL_PATH = ROOT / "skills/model-tiering/SKILL.md"

PROVIDERS = {
    "openrouter": ("openrouter-models.json", "openrouter.md", "model_samples"),
    "fireworks_ai": ("fireworks-ai-models.json", "fireworks-ai.md", "models"),
    "together_ai": ("together-ai-models.json", "together-ai.md", "models"),
}


def load_catalog(filename: str) -> dict:
    return json.loads((CATALOG_DIR / filename).read_text(encoding="utf-8"))


def test_catalogs_have_dated_primary_provenance_and_live_freshness_contracts() -> None:
    for provider, (filename, _, _) in PROVIDERS.items():
        catalog = load_catalog(filename)
        assert catalog["provider"] == provider
        captured_at = date.fromisoformat(catalog["captured_at"])
        review_after = date.fromisoformat(catalog["review_after"])
        assert captured_at <= date.today() <= review_after
        assert catalog["source_quality"] == "T1_PRIMARY_SINGLE_VENDOR"
        assert catalog["coverage"]["exhaustive_models"] is False
        assert catalog["coverage"]["exhaustive_source"].startswith("https://")
        assert catalog["dynamic_fields"]
        assert catalog["sources"]
        assert all(source["tier"] == "T1" for source in catalog["sources"])
        assert all(source["captured_at"] == catalog["captured_at"] for source in catalog["sources"])
        assert all(source["url"].startswith("https://") for source in catalog["sources"])


def test_every_model_record_has_valid_source_references_and_unique_ids() -> None:
    for _, (filename, _, model_key) in PROVIDERS.items():
        catalog = load_catalog(filename)
        source_ids = {source["id"] for source in catalog["sources"]}
        records = catalog[model_key]
        ids = [record["id"] for record in records]
        assert len(ids) == len(set(ids))
        for record in records:
            assert record["sources"]
            assert set(record["sources"]) <= source_ids


def test_guides_cover_catalog_entries_and_dynamic_evidence_boundaries() -> None:
    required_phrases = {
        "openrouter.md": (
            "581 models",
            "not a recommendation",
            "require_parameters",
            "Recheck by 2026-09-21",
        ),
        "fireworks-ai.md": (
            "no uptime or latency SLA",
            "FIREWORKS_API_KEY",
            "2026-08-27",
            "Recheck by 2026-09-21",
        ),
        "together-ai.md": (
            "dynamic per organization and model",
            "TOGETHER_API_KEY",
            "2026-08-27",
            "Recheck by 2026-09-21",
        ),
    }
    for _, (filename, guide_name, model_key) in PROVIDERS.items():
        catalog = load_catalog(filename)
        guide = (CATALOG_DIR / guide_name).read_text(encoding="utf-8")
        for record in catalog[model_key]:
            assert f"`{record['id']}`" in guide
        for phrase in required_phrases[guide_name]:
            assert phrase in guide


def test_openrouter_snapshot_is_bounded_and_records_near_term_expirations() -> None:
    catalog = load_catalog("openrouter-models.json")
    snapshot = catalog["runtime_snapshot"]
    popular_snapshot = catalog["popular_snapshot"]
    assert snapshot["model_count"] >= 500
    assert re.fullmatch(r"[0-9a-f]{64}", snapshot["response_sha256"])
    assert snapshot["capability_counts"]["tools"] > 0
    assert snapshot["capability_counts"]["structured_outputs"] > 0
    expirations = {row["id"]: row["expiration_date"] for row in snapshot["actionable_expirations"]}
    # Actionable means "a real shutdown date", so every watched row must carry one
    # that is not the far-future sentinel, and none may already have passed
    # unnoticed — that is the staleness this watch list exists to surface.
    assert expirations
    sentinel = date.fromisoformat(snapshot["sentinel_expirations"]["value"])
    captured = date.fromisoformat(catalog["captured_at"])
    for model_id, value in expirations.items():
        expires = date.fromisoformat(value)
        assert expires != sentinel, f"{model_id} carries the sentinel, not a real date"
        assert expires >= captured, f"{model_id} expired before this capture was taken"
    assert snapshot["sentinel_expirations"]["value"] == "2098-12-31"
    assert len(catalog["model_samples"]) < snapshot["model_count"]
    assert popular_snapshot["request"] == "GET /api/v1/models?output_modalities=all&sort=most-popular"
    assert re.fullmatch(r"[0-9a-f]{64}", popular_snapshot["response_sha256"])
    assert popular_snapshot["model_count"] == snapshot["model_count"]
    assert [row["popular_rank"] for row in catalog["model_samples"]] == [1, 2, 3, 4, 8, 11]
    assert snapshot["access_mode"].startswith("unauthenticated")
    assert "lowest current route price" not in json.dumps(catalog)
    mimo = next(row for row in catalog["model_samples"] if row["id"] == "xiaomi/mimo-v2.5")
    assert mimo["capabilities"]["structured_outputs"] is None
    assert any("MiMo-V2.5" in note and "response_format" in note for note in catalog["uncertainties"])


def test_fireworks_deprecated_routes_have_current_replacements() -> None:
    catalog = load_catalog("fireworks-ai-models.json")
    current_ids = {model["id"] for model in catalog["models"]}
    deprecated_ids = {model["id"] for model in catalog["deprecations"]}
    assert current_ids.isdisjoint(deprecated_ids)
    migrations = {row["id"]: row for row in catalog["deprecations"]}
    assert migrations["accounts/fireworks/models/deepseek-v4-pro"]["replacement"] == (
        "accounts/fireworks/models/deepseek-v4-pro-0813"
    )
    assert migrations["accounts/fireworks/models/gpt-oss-20b"]["effective_at"] == "2026-08-27"
    assert catalog["runtime_verification"]["status"] == "not_run_missing_credential"


def test_together_removals_override_serverless_candidate_rows() -> None:
    catalog = load_catalog("together-ai-models.json")
    current_ids = {model["id"] for model in catalog["models"]}
    removed_ids = {model["id"] for model in catalog["recent_removals"]}
    assert current_ids.isdisjoint(removed_ids)
    assert "deepseek-ai/DeepSeek-V4-Pro-0813" in current_ids
    assert "Qwen/Qwen3.5-9B" in current_ids
    removal = next(row for row in catalog["recent_removals"] if row["id"] == "deepseek-ai/DeepSeek-V4-Pro")
    assert removal["replacement"] == "deepseek-ai/DeepSeek-V4-Pro-0813"
    assert catalog["runtime_verification"]["status"] == "not_run_missing_credential"
    guide = (CATALOG_DIR / "together-ai.md").read_text(encoding="utf-8")
    decision_summary = guide.split("## Decision summary", 1)[1].split("## Current serverless decision candidates", 1)[0]
    mentioned_candidates = set(re.findall(r"`([^`]+/[^`]+)`", decision_summary))
    # The summary may name a model Together has dated for removal, because
    # telling a reader to migrate OFF one is the opposite of recommending it.
    # It may never name a model that is already gone.
    scheduled_ids = {row["id"] for row in catalog["scheduled_removals"]}
    assert mentioned_candidates <= current_ids | scheduled_ids
    assert mentioned_candidates.isdisjoint(removed_ids)


def test_model_tiering_routes_provider_questions_without_enabling_models() -> None:
    skill = SKILL_PATH.read_text(encoding="utf-8")
    for filename, guide_name, _ in PROVIDERS.values():
        assert f"references/provider-catalogs/{guide_name}" in skill
        assert f"references/provider-catalogs/{filename}" in skill
    assert "does not become an available Build Loop subagent model" in skill
    assert "query the recorded live catalog source" in skill


def test_together_scheduled_removals_are_not_current_candidates() -> None:
    catalog = load_catalog("together-ai-models.json")
    current_ids = {model["id"] for model in catalog["models"]}
    scheduled = catalog["scheduled_removals"]
    assert scheduled
    scheduled_ids = {row["id"] for row in scheduled}
    assert current_ids.isdisjoint(scheduled_ids)
    for row in scheduled:
        assert date.fromisoformat(row["removal_date"]) >= date.fromisoformat(catalog["captured_at"])
        if "replacement" in row:
            assert row["replacement"] in current_ids
    guide = (CATALOG_DIR / "together-ai.md").read_text(encoding="utf-8")
    for row in scheduled:
        assert f"`{row['id']}`" in guide


def test_every_catalog_records_how_this_capture_was_reverified() -> None:
    """A refreshed snapshot has to say what it was checked against, not just when."""
    for provider, (filename, _, _) in PROVIDERS.items():
        catalog = load_catalog(filename)
        if provider == "openrouter":
            # OpenRouter's provenance is the recorded response digests themselves.
            assert catalog["runtime_snapshot"]["response_sha256"]
            continue
        note = catalog["provenance_note"]
        assert catalog["captured_at"] in note
        assert "https://" in note
