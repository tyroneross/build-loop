# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Test isolation for the retrospective capability.

``promote_durable`` writes a durable copy to the build-loop-memory root
resolved by ``_paths.build_loop_memory_root()`` (env-overridable, default
``~/dev/git-folder/build-loop-memory``). Without isolation, any test that
exercises durable promotion writes the user's REAL memory repo — observed
2026-06-04 when ``test_synthesize`` leaked ``projects/{myproj,proj,empty}``
fixtures into build-loop-memory.

This autouse fixture redirects the resolver at an isolated per-test tmp dir
via the same env the resolver honors, so the production code path runs
unchanged but writes land in a throwaway directory. unittest.TestCase-based
tests pick this up automatically (autouse fixtures apply to unittest tests).
"""
from __future__ import annotations

import os
import tempfile

import pytest

_ROOT_ENV_KEYS = (
    "BUILD_LOOP_MEMORY_STORE_ROOT",
    "BUILD_LOOP_MEMORY_ROOT",
    "AGENT_MEMORY_ROOT",
)


@pytest.fixture(autouse=True)
def _isolate_build_loop_memory_root():
    """Point durable-promotion writes at an isolated tmp build-loop-memory."""
    prev = {k: os.environ.get(k) for k in _ROOT_ENV_KEYS}
    tmp = tempfile.TemporaryDirectory(prefix="bl-mem-isolated-")
    # Existing dir → the real write path runs (isolated), not silently skipped.
    os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = tmp.name
    os.environ["BUILD_LOOP_MEMORY_ROOT"] = tmp.name
    os.environ.pop("AGENT_MEMORY_ROOT", None)
    try:
        yield tmp.name
    finally:
        tmp.cleanup()
        for key, val in prev.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
