# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""scan_findings — deterministic Stop-hook sweep that auto-captures
clearly-identified findings/issues from ANY agent in a session into the backlog.

Sibling of ``scan_corrections`` (corrections/lessons) and
``scan_transcript_for_decisions`` (decisions). This package owns the
findings lane: an audit/critic/agent that states a concrete, severity-labeled
issue should have it persisted to ``.build-loop/backlog/`` automatically —
zero user discipline, regardless of which terminal or agent surfaced it.
"""
