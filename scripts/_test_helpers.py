#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Shared test helpers for Phase C isolation.

Memory-store cutover: write_decision.py routes file writes to
``$AGENT_MEMORY_ROOT/projects/<project>/decisions/`` rather than the legacy
``<workdir>/.episodic/decisions/`` path.  Tests that invoke
``write_decision.py`` as a subprocess must:

  1. Override ``AGENT_MEMORY_ROOT`` to point at a temporary directory so
     they never touch ``~/dev/git-folder/build-loop-memory/``.
  2. Use ``_decisions_dir(project)`` (not the workdir path) when asserting
     on files that ``write_decision.py`` wrote.
  3. Events and history that legacy scripts (revoke, supersede, validate,
     regenerate-index, sync) operate on remain at
     ``<workdir>/.episodic/decisions/``.  When a test seeds a file for
     those scripts to consume, use ``write_legacy_madr()`` to place it in
     the legacy path directly without going through ``write_decision.py``.

Usage
-----
Mix ``MemIsolationMixin`` into any ``unittest.TestCase`` subclass that
invokes ``write_decision.py`` as a subprocess:

    class MyTests(MemIsolationMixin, unittest.TestCase):
        def setUp(self):
            super().setUp()
            ...

The mixin's ``setUp`` is safe to call alongside your own ``setUp`` as long
as you call ``super().setUp()`` first.

``MemIsolationMixin`` provides:
  - ``self._memroot``            – ``tempfile.TemporaryDirectory`` object
  - ``self._decisions_dir(project)``  – ``Path`` under the tmp memroot
  - ``self._events_path()``      – ``workdir / .build-loop / events.jsonl``

Note: ``self.workdir`` and ``self.tmp`` must be set by the subclass *before*
``_events_path()`` is called.  Only ``setUp`` ordering guarantees this;
``_events_path()`` is therefore a method, not a property.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

_TAXONOMY_DEFAULT = """---
type: taxonomy
schema_version: 1
---

# Vocab

## 1. Decision tags

- `architecture`
- `data`
- `ui`
- `infra`
- `tooling`
- `process`
- `security`
- `performance`
- `testing`

## 3. Confidence levels

`assumed < inferred < confirmed < explicit`

## 6. Source attribution

- `manual`
- `auto-explicit`
- `auto-confirmed`
- `auto-inferred`
- `auto-assumed`
- `migration`
- `orchestrator`
"""


class MemIsolationMixin:
    """Mixin that overrides ``AGENT_MEMORY_ROOT`` for each test method.

    Works with both ``setUp`` / ``tearDown`` and ``setUpClass`` /
    ``tearDownClass`` usage patterns; the per-test (instance-level)
    form is the canonical one used by the test files in this directory.
    """

    def setUp(self) -> None:  # type: ignore[override]
        super().setUp()  # type: ignore[misc]
        self._memroot = tempfile.TemporaryDirectory()
        self._prev_env: dict[str, str | None] = {
            "BUILD_LOOP_MEMORY_STORE_ROOT": os.environ.get("BUILD_LOOP_MEMORY_STORE_ROOT"),
            "BUILD_LOOP_MEMORY_ROOT": os.environ.get("BUILD_LOOP_MEMORY_ROOT"),
            "AGENT_MEMORY_ROOT": os.environ.get("AGENT_MEMORY_ROOT"),
        }
        os.environ.pop("BUILD_LOOP_MEMORY_STORE_ROOT", None)
        os.environ.pop("BUILD_LOOP_MEMORY_ROOT", None)
        os.environ["AGENT_MEMORY_ROOT"] = self._memroot.name

    def tearDown(self) -> None:  # type: ignore[override]
        for key, val in self._prev_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._memroot.cleanup()
        super().tearDown()  # type: ignore[misc]

    def _decisions_dir(self, project: str = "_unscoped") -> Path:
        """Return the canonical location for a project's decision files."""
        return Path(self._memroot.name) / "projects" / project / "decisions"

    def _events_path(self) -> Path:
        """Return the events.jsonl path (stays local to workdir)."""
        return self.workdir / ".build-loop" / "events.jsonl"  # type: ignore[attr-defined]


def write_legacy_madr(
    workdir: Path,
    decision_id: str,
    date: str,
    title: str,
    entity: str,
    primary_tag: str,
    confidence: str = "explicit",
    source: str = "manual",
    extra_fm: dict | None = None,
) -> Path:
    """Write a minimal MADR directly into ``workdir/.episodic/decisions/``.

    Used by tests whose subject script (revoke, supersede, validate,
    regenerate-index, sync) reads from the legacy path.  Bypasses
    ``write_decision.py`` intentionally — these tests exercise the
    downstream script, not the writer.
    """
    from write_decision import slugify  # type: ignore

    slug = slugify(title)
    fm_lines = [
        "---",
        f"id: '{decision_id}'",
        f"slug: {slug}",
        f"title: {title}",
        "type: decision",
        "status: accepted",
        f"confidence: {confidence}",
        f"date: '{date}'",
        f"tags: [{primary_tag}]",
        f"primary_tag: {primary_tag}",
        f"entity: {entity}",
        f"source: {source}",
        "project: test-default",
        "tool: manual",
        "model: claude-opus-4-7",
        "task_category: unknown",
        "author: test",
        "last_validated: null",
        "last_accessed: null",
        "files_touched: []",
        "closing_commit: null",
        "related_runs: []",
        "related_decisions: []",
        "supersedes: null",
        "superseded_by: null",
        "bookmark_snapshot_id: null",
        "captured_turn_excerpt: null",
        "confidence_source: user_statement",
        "confirmation_count: 0",
        "valid_until: null",
        "causal_parent_id: null",
        "embedding_model_version: mxbai-embed-large-v1",
        "domain: unknown",
        "goal: unknown",
    ]
    if extra_fm:
        for k, v in extra_fm.items():
            if v is None:
                fm_lines.append(f"{k}: null")
            elif isinstance(v, str):
                fm_lines.append(f"{k}: '{v}'")
            else:
                fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    body = "\n".join(fm_lines) + f"\n\n# {title}\n\nbody.\n"

    decisions_dir = workdir / ".episodic" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    (decisions_dir / "_history").mkdir(parents=True, exist_ok=True)
    filename = f"{decision_id}-{date}-{slug}.md"
    path = decisions_dir / filename
    path.write_text(body)
    return path


def run_write_decision(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    """Run ``write_decision.py`` with the given args.

    If ``env`` is None the current process environment (already patched by
    ``MemIsolationMixin.setUp``) is inherited.
    """
    script = HERE / "write_decision" / "__main__.py"
    return subprocess.run(
        [sys.executable, str(script)] + args,
        capture_output=True,
        text=True,
        env=env,
    )
