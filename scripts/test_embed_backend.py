#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Tests for embed_backend.py.

Live-MLX + live-Ollama. Skips MLX paths if mlx_embeddings is not
importable (e.g. CI on Linux). Skips Ollama paths if 127.0.0.1:11434 is
unreachable.

Run: python3 scripts/test_embed_backend.py
"""
from __future__ import annotations

import http.client
import math
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _ollama_up() -> bool:
    try:
        c = http.client.HTTPConnection("127.0.0.1", 11434, timeout=2)
        c.request("GET", "/api/tags")
        resp = c.getresponse()
        ok = resp.status == 200
        resp.read()
        c.close()
        return ok
    except Exception:  # noqa: BLE001
        return False


def _mlx_importable() -> bool:
    try:
        import mlx_embeddings  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _l2(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def _fresh_module(env: dict | None = None):
    """Re-import embed_backend with env overrides applied."""
    import importlib
    if env:
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    import embed_backend  # type: ignore
    importlib.reload(embed_backend)
    return embed_backend


class EmbedBackendTests(unittest.TestCase):

    def setUp(self) -> None:
        # Capture and restore env to keep tests isolated.
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("EMBED_BACKEND", "EMBED_MODEL", "MLX_FORCE_FAIL")
        }
        for k in self._saved_env:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @unittest.skipUnless(_mlx_importable(), "mlx_embeddings not installed")
    def test_default_backend_is_mlx(self) -> None:
        eb = _fresh_module({"EMBED_BACKEND": None})
        v = eb.embed("hello world")
        self.assertEqual(len(v), 1024)
        self.assertIsInstance(v[0], float)
        self.assertEqual(eb.active_backend(), "mlx")
        self.assertEqual(eb.dimension(), 1024)

    @unittest.skipUnless(_ollama_up(), "ollama not reachable")
    def test_ollama_via_env(self) -> None:
        eb = _fresh_module({"EMBED_BACKEND": "ollama"})
        v = eb.embed("hello world")
        self.assertEqual(len(v), 1024)
        self.assertEqual(eb.active_backend(), "ollama")

    @unittest.skipUnless(_mlx_importable(), "mlx_embeddings not installed")
    def test_batched_returns_list_of_vectors(self) -> None:
        eb = _fresh_module({"EMBED_BACKEND": None})
        vs = eb.embed(["alpha", "beta", "gamma"])
        self.assertEqual(len(vs), 3)
        for v in vs:
            self.assertEqual(len(v), 1024)
            self.assertIsInstance(v[0], float)

    @unittest.skipUnless(_ollama_up(), "ollama not reachable")
    def test_fallback_when_mlx_forced_to_fail(self) -> None:
        eb = _fresh_module({"EMBED_BACKEND": "mlx", "MLX_FORCE_FAIL": "1"})
        v = eb.embed("hello world")
        self.assertEqual(len(v), 1024)
        self.assertEqual(eb.active_backend(), "ollama")
        self.assertIsNotNone(eb.fallback_reason())
        self.assertIn("MLX", eb.fallback_reason() or "")

    @unittest.skipUnless(_mlx_importable() and _ollama_up(), "need both backends")
    def test_cross_backend_cosine_above_threshold(self) -> None:
        """M-G: identical text under MLX vs Ollama yields cosine ≥ 0.95.

        Measured baseline ≈ 0.9664; threshold gives margin for any
        per-machine numerical drift.
        """
        sample = "Postgres with pgvector extension powers retrieval"

        eb_mlx = _fresh_module({"EMBED_BACKEND": "mlx"})
        v_mlx = eb_mlx.embed(sample)

        eb_oll = _fresh_module({"EMBED_BACKEND": "ollama"})
        v_oll = eb_oll.embed(sample)

        cos = _cos(_l2(v_mlx), _l2(v_oll))
        self.assertGreaterEqual(
            cos, 0.95,
            msg=f"cross-backend cosine {cos:.4f} below 0.95 threshold",
        )
        # Sanity: should also be < 1.0 (different code paths)
        self.assertLess(cos, 0.9999)

    @unittest.skipUnless(_mlx_importable(), "mlx_embeddings not installed")
    def test_singleton_caches_backend(self) -> None:
        """Second embed() call should not re-load the model.

        We can't directly assert "no reload"; instead we check that the
        module-level _BACKEND is the same object across calls.
        """
        eb = _fresh_module({"EMBED_BACKEND": "mlx"})
        eb.embed("first")
        first_backend_obj = eb._BACKEND
        eb.embed("second")
        self.assertIs(eb._BACKEND, first_backend_obj)

    @unittest.skipUnless(_mlx_importable(), "mlx_embeddings not installed")
    def test_empty_text_raises(self) -> None:
        eb = _fresh_module({"EMBED_BACKEND": "mlx"})
        with self.assertRaises(ValueError):
            eb.embed("")

    def test_invalid_arg_type_raises(self) -> None:
        # No env-dependent backend touched; should fail before backend init.
        eb = _fresh_module({"EMBED_BACKEND": "ollama"})  # safest backend init
        with self.assertRaises(TypeError):
            eb.embed(123)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main(verbosity=2)
