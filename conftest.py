# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Root conftest.py — covers both scripts/ and tests/.

Implements the `live` marker auto-skip:
  - Tests marked `@pytest.mark.live` are skipped automatically when
    Ollama's port (127.0.0.1:11434) is not reachable via a fast TCP probe.
  - When Ollama IS reachable, `live`-marked tests run normally.

This is the load-bearing fix for the full-scope gate hang:
  scripts/test_stop_hook_integration.py::StopHookIntegrationTests::
    test_hook_command_runs_end_to_end_with_live_qwen
  ... and all similar tests that poll an external service.

The probe uses stdlib `socket` only (no deps).  Timeout is 0.5s.
"""
from __future__ import annotations

import socket

import pytest


def _ollama_reachable() -> bool:
    """Fast TCP probe: True if Ollama is listening on 127.0.0.1:11434."""
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=0.5):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Auto-skip `live`-marked tests when Ollama/qwen is unreachable.

    A single probe per collection run (cached here) keeps overhead at
    ~0.5s worst-case regardless of how many live-marked tests exist.
    """
    # Probe once per collection pass.
    reachable = _ollama_reachable()
    if reachable:
        return  # live service up — run all tests normally

    skip_marker = pytest.mark.skip(
        reason="live service (Ollama/qwen on 127.0.0.1:11434) unreachable"
    )
    for item in items:
        if item.get_closest_marker("live") is not None:
            item.add_marker(skip_marker, append=False)
