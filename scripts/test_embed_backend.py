#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for embed_backend.py.

Selection tests run offline. Live-Ollama tests skip if 127.0.0.1:11434
is unreachable.

Run: python3 scripts/test_embed_backend.py
"""
from __future__ import annotations

import http.client
import json
import math
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
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
            for k in ("EMBED_BACKEND", "EMBED_MODEL", "MLX_FORCE_FAIL", "EMBED_FORCE_INPROCESS")
        }
        for k in self._saved_env:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_model_matches_migration_target(self) -> None:
        """The default query path must embed in the same space the store is
        migrated to. Replaces the MLX-vs-Ollama cosine guard (backlog
        BUIL-MEMORY-m0hcg9vzf60y895rj3vt9): mxbai (MLX) vs bge-m3 (Ollama)
        measured -0.0487 cosine on identical text, so a default that drifts
        from DEFAULT_TARGET silently turns recall into noise."""
        from migrate_reembed_to_bgem3 import DEFAULT_TARGET
        eb = _fresh_module({"EMBED_FORCE_INPROCESS": "1"})
        self.assertEqual(eb.active_backend(), "ollama")
        self.assertEqual(eb.active_model(), DEFAULT_TARGET)

    def test_daemon_serving_other_model_is_ignored(self) -> None:
        eb = _fresh_module()
        eb._probe_daemon = lambda: eb.DaemonBackend(
            backend_name="mlx", model="mlx-community/mxbai-embed-large-v1"
        )
        self.assertEqual(eb.active_backend(), "ollama")
        self.assertEqual(eb.active_model(), eb.OLLAMA_DEFAULT_MODEL)

    def test_daemon_serving_default_model_is_used(self) -> None:
        eb = _fresh_module()
        daemon = eb.DaemonBackend(backend_name="ollama", model=eb.OLLAMA_DEFAULT_MODEL)
        eb._probe_daemon = lambda: daemon
        eb.active_model()
        self.assertIs(eb._BACKEND, daemon)

    def test_mlx_without_model_raises(self) -> None:
        eb = _fresh_module({"EMBED_BACKEND": "mlx"})
        with self.assertRaises(RuntimeError):
            eb.active_backend()

    def test_mlx_failure_raises_instead_of_falling_back(self) -> None:
        eb = _fresh_module({"EMBED_BACKEND": "mlx", "EMBED_MODEL": "any", "MLX_FORCE_FAIL": "1"})
        with self.assertRaises(RuntimeError):
            eb.active_backend()
        self.assertIsNone(eb._BACKEND)

    def test_unknown_backend_raises(self) -> None:
        eb = _fresh_module({"EMBED_BACKEND": "nope"})
        with self.assertRaises(RuntimeError):
            eb.active_backend()

    @unittest.skipUnless(_ollama_up(), "ollama not reachable")
    def test_ollama_via_env(self) -> None:
        eb = _fresh_module({"EMBED_BACKEND": "ollama"})
        v = eb.embed("hello world")
        self.assertEqual(len(v), 1024)
        self.assertIsInstance(v[0], float)
        self.assertEqual(eb.active_backend(), "ollama")
        self.assertEqual(eb.dimension(), 1024)

    @unittest.skipUnless(_ollama_up(), "ollama not reachable")
    def test_batched_returns_list_of_vectors(self) -> None:
        eb = _fresh_module({"EMBED_BACKEND": "ollama"})
        vs = eb.embed(["alpha", "beta", "gamma"])
        self.assertEqual(len(vs), 3)
        for v in vs:
            self.assertEqual(len(v), 1024)
            self.assertIsInstance(v[0], float)

    @unittest.skipUnless(_ollama_up(), "ollama not reachable")
    def test_singleton_caches_backend(self) -> None:
        eb = _fresh_module({"EMBED_BACKEND": "ollama"})
        eb.embed("first")
        first_backend_obj = eb._BACKEND
        eb.embed("second")
        self.assertIs(eb._BACKEND, first_backend_obj)

    def test_invalid_arg_type_raises(self) -> None:
        eb = _fresh_module({"EMBED_BACKEND": "ollama"})
        with self.assertRaises(TypeError):
            eb.embed(123)  # type: ignore[arg-type]

class OllamaTransportTests(unittest.TestCase):
    def backend(self, payload=None, status=200, raw=None):
        import embed_backend as eb
        backend = eb.OllamaBackend()
        connection = Mock()
        response = Mock(status=status)
        response.read.return_value = raw if raw is not None else json.dumps(payload).encode()
        connection.getresponse.return_value = response
        backend._conn = connection
        return backend, connection

    def valid(self, count=1):
        return {"model": "bge-m3:latest", "embeddings": [[float(i + 1)] * 1024 for i in range(count)]}

    def test_batch_uses_one_request_and_preserves_order(self):
        backend, conn = self.backend(self.valid(3))
        output = backend.embed_batch(["one", "two", "three"])
        self.assertEqual([row[0] for row in output], [1.0, 2.0, 3.0])
        self.assertEqual(conn.request.call_count, 1)
        self.assertEqual(conn.request.call_args.args[:2], ("POST", "/api/embed"))
        body = json.loads(conn.request.call_args.kwargs["body"])
        self.assertEqual(body["input"], ["one", "two", "three"])
        self.assertIs(body["truncate"], False)

    def test_single_uses_the_same_batch_transport(self):
        backend, conn = self.backend(self.valid())
        self.assertEqual(len(backend.embed("one")), 1024)
        self.assertEqual(json.loads(conn.request.call_args.kwargs["body"])["input"], ["one"])

    def test_bad_responses_fail_once_without_retry(self):
        cases = [None, {}, {"model": None}, {"model": "mxbai-embed-large", "embeddings": [[1] * 1024]},
                 {"model": "bge-m3", "embeddings": []}, {"model": "bge-m3", "embeddings": [[1]]}]
        for value in [True, "1", None, float("nan"), float("inf"), 10 ** 1000]:
            cases.append({"model": "bge-m3", "embeddings": [[value] + [1] * 1023]})
        for payload in cases:
            with self.subTest(payload_type=type(payload).__name__):
                backend, conn = self.backend(payload)
                with self.assertRaises(RuntimeError):
                    backend.embed("one")
                self.assertEqual(conn.request.call_count, 1)
        for raw, status in [(b"not-json", 200), (b"{}", 400), (b"{}", 503)]:
            backend, conn = self.backend(status=status, raw=raw)
            with self.assertRaises(RuntimeError):
                backend.embed("one")
            self.assertEqual(conn.request.call_count, 1)

    def test_connection_failure_reconnects_once(self):
        backend, broken = self.backend(self.valid())
        broken.getresponse.side_effect = ConnectionResetError("lost connection")
        _, healthy = self.backend(self.valid())
        with patch("http.client.HTTPConnection", return_value=healthy):
            self.assertEqual(len(backend.embed("one")), 1024)
        self.assertEqual(broken.request.call_count, 1)
        self.assertEqual(healthy.request.call_count, 1)
        broken.close.assert_called_once()

    def test_invalid_or_empty_batch_does_not_call_http(self):
        backend, conn = self.backend(self.valid())
        self.assertEqual(backend.embed_batch([]), [])
        for texts, error in [([""], ValueError), (["valid", 5], TypeError)]:
            with self.assertRaises(error):
                backend.embed_batch(texts)
        conn.request.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
