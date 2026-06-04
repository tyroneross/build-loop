# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""scripts/retrospective/ — post-push 9-section synthesis package.

Folder-per-capability:
  - locate.py      — find the transcript JSONL for the current cwd.
  - sections.py    — assemble the 9 named sections from transcript + state.
  - synthesize.py  — entry point; reads inputs, writes outputs, emits enforce-candidates.
  - write.py       — atomic active write + best-effort durable promotion.
  - __main__.py    — CLI (``python3 -m retrospective``).

Public API (re-exports for the agent + orchestrator):
  - locate.find_transcript_for_cwd
  - sections.build
  - synthesize.run
  - write.write_active / write.promote_durable
"""
from retrospective.locate import find_transcript_for_cwd
from retrospective.sections import build as build_sections
from retrospective.synthesize import run as synthesize_run
from retrospective.write import write_active, promote_durable

__all__ = [
    "find_transcript_for_cwd",
    "build_sections",
    "synthesize_run",
    "write_active",
    "promote_durable",
]
