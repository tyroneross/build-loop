#!/usr/bin/env python3
"""Contextual Retrieval prepend for write-time context (Phase D).

Anthropic's Contextual Retrieval technique: before embedding+indexing a
chunk, prepend ~50–100 tokens of "this chunk comes from {context}" so
the embedded vector carries paraphrase coverage that pure subject/
predicate/object never had. Reduces top-20 retrieval failure 35% →
49% → 67% (embeddings → +BM25 → +rerank).

Local-only inference contract: Ollama models cycled in priority order;
Apple Foundation Models intentionally skipped — Python-from-Swift
bridging proved too brittle in prior work (see user memory
feedback_macos_keychain_signing.md). When no router target is
available the function returns the empty string and callers degrade
silently (no chunk_context prepended; the row still indexes via
subject/predicate/object alone).

Public API:
    available_router_target() -> str | None
    generate_chunk_context(decision_text, max_tokens=100) -> str

Env override:
    CHUNK_CONTEXT_MODEL  — pin a specific Ollama model id, bypassing
                           the priority cascade (useful for tests).
    CHUNK_CONTEXT_DISABLE — any truthy value forces empty return
                           (useful for fast unit tests).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from typing import Iterable

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

# Build-loop's local-LLM cascade. Spec listed `qwen3:4b` first; that
# model isn't pulled on the dev machine. Substituted with the closest
# available smaller-tier Qwen (`qwen3:8b-q4_K_M`) — documented divergence
# from the spec, validated against `ollama list` output during Phase 1
# Assess. Fallbacks remain in spec order.
PRIORITY_MODELS: tuple[str, ...] = (
    "qwen3:4b",                       # spec first choice (may be absent)
    "qwen3:8b-q4_K_M",                # build-loop substitute (present today)
    "qwen2.5-coder:7b-instruct",      # spec fallback (may be absent)
    "qwen2.5-coder:32b-instruct-q5_K_M",  # build-loop substitute
    "gpt-oss:20b",                    # spec final fallback
)

OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11434
OLLAMA_TIMEOUT_S = 30
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "24h")

# Approx-tokens-to-chars conversion. Tokenizer-free — same shorthand
# the spec calls for. ~4 chars/token across English.
TOKEN_CHAR_FACTOR = 4

PROMPT_TEMPLATE = (
    "Summarize the context this decision/lesson comes from in <=80 tokens. "
    "Include: subject domain, the problem it addresses, and any specific "
    "entities/files/concepts referenced. Output a single dense sentence; "
    "no preamble, no quotes.\n\n"
    "Decision: {text}\n\n"
    "Context:"
)


def _log(msg: str) -> None:
    print(f"[contextual_prepend] {msg}", file=sys.stderr, flush=True)


def _ollama_list_models() -> list[str]:
    """Return the set of locally-pulled Ollama model ids.

    Calls `ollama list` once. Empty list on any failure (binary missing,
    daemon down, parse error). The cascade caller treats missing models
    as "skip and try next."
    """
    try:
        proc = subprocess.run(
            ["ollama", "list"],
            check=False, capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        _log(f"ollama unavailable: {e}")
        return []
    if proc.returncode != 0:
        return []
    out: list[str] = []
    for line in proc.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if parts:
            out.append(parts[0])  # NAME column (e.g. 'qwen3:8b-q4_K_M')
    return out


_AVAILABLE_CACHE: dict[str, list[str]] = {}


def _available_models(refresh: bool = False) -> list[str]:
    """Cached `ollama list` lookup. Tests call with refresh=True."""
    if refresh or "models" not in _AVAILABLE_CACHE:
        _AVAILABLE_CACHE["models"] = _ollama_list_models()
    return _AVAILABLE_CACHE["models"]


def available_router_target(*, refresh: bool = False) -> str | None:
    """Return the first PRIORITY_MODELS entry currently pulled, or None.

    `$CHUNK_CONTEXT_MODEL` overrides the cascade entirely (test hook
    and a way for advanced users to pin a specific model).
    """
    pinned = os.environ.get("CHUNK_CONTEXT_MODEL")
    if pinned:
        return pinned

    if os.environ.get("CHUNK_CONTEXT_DISABLE"):
        return None

    available = set(_available_models(refresh=refresh))
    if not available:
        return None

    for candidate in PRIORITY_MODELS:
        # Allow either exact match OR `name` matching `name:tag` ignoring
        # the tag (so "qwen3:4b" can match a `qwen3:4b-q4_K_M` if the
        # user pulled a quantized variant).
        if candidate in available:
            return candidate
        bare = candidate.split(":", 1)[0]
        for a in available:
            if a == bare or a.startswith(f"{bare}:"):
                return a
    return None


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _ollama_generate(model: str, prompt: str, *, max_tokens: int) -> str:
    """POST /api/generate (non-streaming). Returns the generated text.

    Raises on HTTP errors so the caller can decide policy. Output is
    truncated to `max_tokens * TOKEN_CHAR_FACTOR` chars before returning
    — a tokenizer-free length cap that matches the spec's "100 tokens
    via tokenizer-free char approximation" call.
    """
    body_obj = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        # Disable model "thinking" output for reasoning-aware models
        # (qwen3:*, gpt-oss:*). Ollama 0.5+ supports `think: false` —
        # without it, the response field can come back empty because
        # the model spent num_predict tokens on the hidden chain of
        # thought instead of the final answer.
        "think": False,
        "options": {
            # Generous ceiling — qwen3 etc. may emit a chain of thought
            # block before the answer; we post-truncate by chars to
            # the spec's tokenizer-free 100-token target.
            "num_predict": max(512, max_tokens * 8),
            "temperature": 0.2,  # dense, reproducible summary
        },
    }
    body = json.dumps(body_obj).encode("utf-8")
    req = urllib.request.Request(
        f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_S) as resp:
        if resp.status != 200:
            raise RuntimeError(f"ollama HTTP {resp.status}")
        payload = resp.read()
    data = json.loads(payload)
    text = (data.get("response") or "").strip()
    return text[: max_tokens * TOKEN_CHAR_FACTOR]


def _clean_response(text: str) -> str:
    """Strip surrounding quotes / leading 'Context:' template echoes.

    Centralised here (vs inlined in `_ollama_generate`) so callers that
    mock `_ollama_generate` still get the cleanup applied. Idempotent —
    safe to call on already-clean text.
    """
    if not text:
        return text
    text = text.strip()
    for prefix in ("Context:", "context:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    if len(text) >= 2 and text[0] in ("'", '"') and text[-1] in ("'", '"'):
        text = text[1:-1].strip()
    return text


def generate_chunk_context(
    decision_text: str,
    *,
    max_tokens: int = 100,
    model: str | None = None,
) -> str:
    """Produce a dense context summary for `decision_text`.

    Returns "" when no router target is available, when the daemon
    fails, or when `$CHUNK_CONTEXT_DISABLE` is set. Callers must
    tolerate the empty case — chunk_context is *additive*; an empty
    value just means today's pre-Phase-D embedding behaviour.

    Args:
      decision_text: the body the chunk_context will summarise. Pass
                     subject + predicate + object joined with spaces,
                     or the full decision body — the prompt is the
                     same.
      max_tokens:    tokenizer-free length cap (spec calls 100 tokens).
      model:         override the router cascade (test hook).
    """
    if not decision_text or not decision_text.strip():
        return ""
    if os.environ.get("CHUNK_CONTEXT_DISABLE"):
        return ""

    target = model or available_router_target()
    if not target:
        return ""

    prompt = PROMPT_TEMPLATE.format(text=decision_text.strip())
    try:
        raw = _ollama_generate(target, prompt, max_tokens=max_tokens)
    except Exception as e:  # noqa: BLE001
        _log(f"generate_chunk_context skipped (model={target!r}): {e}")
        return ""
    return _clean_response(raw)


# ---------------------------------------------------------------------------
# CLI (smoke / debug)
# ---------------------------------------------------------------------------

def main(argv: Iterable[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: contextual_prepend.py <text> [--model MODEL]", file=sys.stderr)
        return 1
    model = None
    text_parts: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--model" and i + 1 < len(args):
            model = args[i + 1]
            i += 2
        else:
            text_parts.append(args[i])
            i += 1
    text = " ".join(text_parts)
    target = model or available_router_target()
    if not target:
        print("# no router target available", file=sys.stderr)
        return 0
    print(f"# router target: {target}", file=sys.stderr)
    out = generate_chunk_context(text, model=model)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
