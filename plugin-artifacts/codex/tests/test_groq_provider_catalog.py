from __future__ import annotations

import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "references/provider-catalogs/groq-models.json"
GUIDE_PATH = ROOT / "references/provider-catalogs/groq.md"
SKILL_PATH = ROOT / "skills/model-tiering/SKILL.md"


def load_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def models_by_id(catalog: dict) -> dict[str, dict]:
    return {model["id"]: model for model in catalog["models"]}


def test_catalog_has_dated_primary_source_provenance() -> None:
    catalog = load_catalog()
    assert catalog["provider"] == "groq"
    assert catalog["captured_at"] == "2026-08-07"
    assert catalog["source_quality"] == "T1_PRIMARY_SINGLE_VENDOR"
    assert catalog["source_precedence"][0] == "deprecations"

    required_sources = {
        "models",
        "deprecations",
        "structured_outputs",
        "tool_use",
        "reasoning",
        "responses_api",
        "vision",
        "speech_to_text",
        "text_to_speech",
        "rate_limits",
        "prompt_caching",
        "flex",
        "batch",
        "latency",
        "production_checklist",
    }
    sources = {source["id"]: source for source in catalog["sources"]}
    assert required_sources <= sources.keys()
    assert all(source["tier"] == "T1" for source in sources.values())
    assert all(source["url"].startswith("https://console.groq.com/docs/") for source in sources.values())


def test_catalog_covers_models_page_snapshot_without_duplicate_ids() -> None:
    catalog = load_catalog()
    ids = [model["id"] for model in catalog["models"]]
    expected_ids = {
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "whisper-large-v3",
        "whisper-large-v3-turbo",
        "groq/compound",
        "groq/compound-mini",
        "canopylabs/orpheus-arabic-saudi",
        "canopylabs/orpheus-v1-english",
        "meta-llama/llama-prompt-guard-2-22m",
        "meta-llama/llama-prompt-guard-2-86m",
        "minimaxai/minimax-m2.7",
        "openai/gpt-oss-safeguard-20b",
        "qwen/qwen3.6-27b",
    }
    assert len(ids) == len(set(ids)) == 15
    assert set(ids) == expected_ids
    source_ids = {source["id"] for source in catalog["sources"]}
    assert all(model["sources"] and set(model["sources"]) <= source_ids for model in catalog["models"])


def test_deprecation_schedule_overrides_models_page_badge() -> None:
    models = models_by_id(load_catalog())
    expected = {
        "llama-3.1-8b-instant": ["openai/gpt-oss-20b"],
        "llama-3.3-70b-versatile": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b"],
    }
    for model_id, replacements in expected.items():
        model = models[model_id]
        assert model["lifecycle"] == "production_deprecated"
        assert model["deprecation"]["shutdown_at"] == "2026-08-16"
        assert model["deprecation"]["plans"] == ["free", "developer"]
        assert model["deprecation"]["replacements"] == replacements
        assert "deprecations" in model["sources"]


def test_strict_structured_outputs_are_not_overclaimed() -> None:
    models = models_by_id(load_catalog())
    strict_ids = {
        model_id
        for model_id, model in models.items()
        if model.get("capabilities", {}).get("strict_structured_outputs") is True
    }
    assert strict_ids == {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}


def test_reasoning_controls_are_model_family_specific() -> None:
    models = models_by_id(load_catalog())
    for model_id in ("openai/gpt-oss-20b", "openai/gpt-oss-120b"):
        capabilities = models[model_id]["capabilities"]
        assert capabilities["reasoning_interface"] == "include_reasoning"
        assert capabilities["reasoning_format_supported"] is False
    for model_id in ("qwen/qwen3.6-27b", "minimaxai/minimax-m2.7"):
        capabilities = models[model_id]["capabilities"]
        assert capabilities["reasoning_interface"] == "reasoning_format"
        assert capabilities["reasoning_format_supported"] is True


def test_dynamic_fields_are_real_catalog_paths() -> None:
    catalog = load_catalog()
    for path in catalog["dynamic_fields"]:
        if path.startswith("models[]."):
            field = path.removeprefix("models[].")
            assert any(field in model for model in catalog["models"]), path
            continue
        value: object = catalog
        for part in path.split("."):
            assert isinstance(value, dict) and part in value, path
            value = value[part]
    assert catalog["provider_features"]["responses_api"]["lifecycle"] == "beta"


def test_preview_modalities_are_not_presented_as_stable_production() -> None:
    models = models_by_id(load_catalog())
    assert models["qwen/qwen3.6-27b"]["lifecycle"] == "preview"
    assert models["qwen/qwen3.6-27b"]["capabilities"]["vision"] is True
    assert models["canopylabs/orpheus-v1-english"]["lifecycle"] == "preview"
    assert models["canopylabs/orpheus-arabic-saudi"]["lifecycle"] == "preview"


def test_review_deadline_has_not_passed() -> None:
    review_after = date.fromisoformat(load_catalog()["review_after"])
    assert date.today() <= review_after, (
        f"Groq catalog expired on {review_after}; refresh official docs and advance review_after"
    )


def test_guide_covers_every_catalog_entry_and_dynamic_caveats() -> None:
    catalog = load_catalog()
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    for model in catalog["models"]:
        assert f"`{model['id']}`" in guide
    for phrase in (
        "vendor benchmarks",
        "not end-to-end latency or an SLA",
        "organization level",
        "before billing decisions",
        "2026-08-16",
        "do **not** support `reasoning_format`",
        "labels it **beta**",
    ):
        assert phrase in guide


def test_model_tiering_routes_groq_questions_to_catalog_without_enabling_models() -> None:
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "references/provider-catalogs/groq.md" in skill
    assert "references/provider-catalogs/groq-models.json" in skill
    assert "does not become an available Build Loop subagent model" in skill
