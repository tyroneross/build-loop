# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Deterministic transcript scanner for user corrections + lessons.

Tier-1 of the three-tier capture stack (deterministic → optional Ollama →
host-agent refinement). This tier ALWAYS runs and uses NO LLM. It detects
high-signal correction + preference + lesson patterns in user turns and
writes raw candidates to a pending queue.

See `references/correction-aware-capture.md` for the full design.
"""

from scan_corrections.detect import (
    Candidate,
    CorrectionDetector,
    detect_candidates,
)

__all__ = ["Candidate", "CorrectionDetector", "detect_candidates"]
