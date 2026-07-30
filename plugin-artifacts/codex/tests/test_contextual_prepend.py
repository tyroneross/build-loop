"""Tests for scripts/contextual_prepend.py.

Unit-level coverage uses mocks so the suite passes without an Ollama
daemon. One integration test (gated on a live `ollama list` returning
≥1 model) verifies the end-to-end generate path.

Acceptance integration for Phase D — verifying that a paraphrased
query surfaces a decision via chunk_context that pure-cosine misses —
lives in `tests/test_recall_acceptance.py` (existing file, extended).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import contextual_prepend as cp  # noqa: E402


# ---------------------------------------------------------------------------
# available_router_target — selection priority
# ---------------------------------------------------------------------------

def test_router_picks_first_available_in_priority_order(monkeypatch):
    # Pretend only `gpt-oss:20b` is pulled — must skip the higher-priority
    # entries that aren't present.
    monkeypatch.setattr(cp, "_available_models", lambda refresh=False: ["gpt-oss:20b"])
    monkeypatch.delenv("CHUNK_CONTEXT_MODEL", raising=False)
    monkeypatch.delenv("CHUNK_CONTEXT_DISABLE", raising=False)
    assert cp.available_router_target(refresh=True) == "gpt-oss:20b"


def test_router_returns_none_when_no_models_pulled(monkeypatch):
    monkeypatch.setattr(cp, "_available_models", lambda refresh=False: [])
    monkeypatch.delenv("CHUNK_CONTEXT_MODEL", raising=False)
    monkeypatch.delenv("CHUNK_CONTEXT_DISABLE", raising=False)
    assert cp.available_router_target(refresh=True) is None


def test_router_env_pin_overrides_cascade(monkeypatch):
    monkeypatch.setattr(cp, "_available_models", lambda refresh=False: ["qwen3:4b"])
    monkeypatch.setenv("CHUNK_CONTEXT_MODEL", "custom-model:latest")
    assert cp.available_router_target(refresh=True) == "custom-model:latest"


def test_router_env_disable_returns_none(monkeypatch):
    monkeypatch.setattr(cp, "_available_models", lambda refresh=False: ["qwen3:4b"])
    monkeypatch.setenv("CHUNK_CONTEXT_DISABLE", "1")
    monkeypatch.delenv("CHUNK_CONTEXT_MODEL", raising=False)
    assert cp.available_router_target(refresh=True) is None


def test_router_matches_quantized_variant(monkeypatch):
    """qwen3:4b spec entry should match a pulled `qwen3:4b-q4_K_M`."""
    monkeypatch.setattr(
        cp, "_available_models",
        lambda refresh=False: ["qwen3:4b-q4_K_M"],
    )
    monkeypatch.delenv("CHUNK_CONTEXT_MODEL", raising=False)
    monkeypatch.delenv("CHUNK_CONTEXT_DISABLE", raising=False)
    target = cp.available_router_target(refresh=True)
    assert target is not None
    assert target.startswith("qwen3:")


# ---------------------------------------------------------------------------
# generate_chunk_context — graceful skip + length cap
# ---------------------------------------------------------------------------

def test_generate_returns_empty_when_no_router(monkeypatch):
    monkeypatch.setattr(cp, "available_router_target", lambda **kw: None)
    monkeypatch.delenv("CHUNK_CONTEXT_DISABLE", raising=False)
    out = cp.generate_chunk_context("anything")
    assert out == ""


def test_generate_returns_empty_for_blank_input(monkeypatch):
    monkeypatch.setattr(cp, "available_router_target", lambda **kw: "any-model")
    assert cp.generate_chunk_context("") == ""
    assert cp.generate_chunk_context("   \n\t  ") == ""


def test_generate_truncates_to_char_budget(monkeypatch):
    """100 tokens × 4 chars/token = 400-char ceiling."""
    monkeypatch.setattr(cp, "available_router_target", lambda **kw: "any-model")
    long_response = "x" * 5000
    monkeypatch.setattr(cp, "_ollama_generate",
                        lambda model, prompt, *, max_tokens: long_response[: max_tokens * cp.TOKEN_CHAR_FACTOR])
    out = cp.generate_chunk_context("Some decision body.", max_tokens=100)
    assert len(out) == 400  # 100 * 4


def test_generate_swallows_ollama_failure(monkeypatch):
    """A daemon error must NOT propagate — return "" so the caller
    silently falls through to the pre-Phase-D path."""
    monkeypatch.setattr(cp, "available_router_target", lambda **kw: "any-model")

    def _boom(*a, **kw):
        raise RuntimeError("simulated ollama 500")

    monkeypatch.setattr(cp, "_ollama_generate", _boom)
    out = cp.generate_chunk_context("Some decision body.")
    assert out == ""


def test_generate_strips_template_prefix_and_quotes(monkeypatch):
    """Defensive cleanup of common LLM artifacts — leading 'Context:'
    repetition or wrapping quotes."""
    monkeypatch.setattr(cp, "available_router_target", lambda **kw: "any-model")
    monkeypatch.setattr(
        cp, "_ollama_generate",
        lambda model, prompt, *, max_tokens: 'Context: "A dense summary."'
    )
    out = cp.generate_chunk_context("body", max_tokens=20)
    assert out == "A dense summary."


def test_generate_passes_explicit_model_override(monkeypatch):
    """Caller may pin model directly without env vars."""
    captured = {}

    def _record(model, prompt, *, max_tokens):
        captured["model"] = model
        return "ok"

    monkeypatch.setattr(cp, "_ollama_generate", _record)
    cp.generate_chunk_context("body", model="user-pinned-model")
    assert captured["model"] == "user-pinned-model"


# ---------------------------------------------------------------------------
# Integration — only runs when an Ollama model is actually pulled
# ---------------------------------------------------------------------------

def _ollama_has_any_model() -> bool:
    try:
        return bool(cp._ollama_list_models())
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _ollama_has_any_model(),
                    reason="No Ollama models pulled locally")
def test_integration_generate_returns_nonempty_on_real_ollama():
    out = cp.generate_chunk_context(
        "Phase A shipped hybrid recall over agent_memory: "
        "vector + sparse + RRF + cross-encoder rerank.",
        max_tokens=80,
    )
    assert out, "real Ollama call should return a non-empty summary"
    assert len(out) <= 100 * cp.TOKEN_CHAR_FACTOR
