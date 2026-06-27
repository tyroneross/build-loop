---
name: auto-finding-capture
description: Project-scoped skill documenting build-loop's DEFAULT-ON auto-capture of clearly-identified findings/issues into the backlog, regardless of which terminal or agent surfaced them. Provides the detection contract, routing rules, dedup strategy, and the standing rule that agents/critics must NOT gate identified issues behind a user selection. Use when an agent, audit, or critic surfaces a concrete severity-labeled issue, or when reasoning about where findings persist.
user-invocable: false
when_to_use: |
  - Any agent/audit/critic states a concrete, severity-labeled issue in the session
  - A dispatched ad-hoc audit (Codex, NavGator, a security pass) returns findings in conversation
  - You are about to ask the user "which of these should I add to the backlog?" — DON'T; capture is automatic
  - You need to know where session findings persist and how they dedup
namespace: .build-loop/backlog/  (review-queue overflow: .build-loop/proposals/)
companion_scripts:
  - scripts/scan_findings/__main__.py — Stop-hook deterministic findings sweep
  - scripts/scan_findings/detect.py — detector (severity-label + structured-JSON extraction)
  - scripts/backlog.py — the single writer (new/sync/list); mirrors to build-loop-memory on sync
  - scripts/review_finding_gate.py — canonical severity taxonomy (SEVERITY_MAP), reused for normalization
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# auto-finding-capture — clearly-identified findings land in the backlog by DEFAULT

A clearly-identified finding is durable work. The user should never have to
remember to add it, and should never be asked to select which findings to keep.
Whenever ANY agent, audit, or critic surfaces a concrete issue in a session —
build-loop's own in-run reviewers OR an ad-hoc audit dispatched from a plain
terminal (Codex, NavGator, a one-off security pass) — that finding is captured
to the backlog automatically at session end.

## Standing rule (binding)

**Do not gate an identified issue behind a user selection.** If an agent or
critic has stated a concrete, severity-labeled finding, it is captured — full
stop. Asking "which of these should I add to the backlog?" is the workflow
violation this skill exists to remove. Surface findings in conversation AND let
them persist; the two are not in tension. The user can always drop a backlog
item later (`status: dropped` + `backlog.py sync`); they cannot recover a
finding that was never written down.

This mirrors the analogous DEFAULT-ON behavior for DECISIONS
(`auto-decision-capture` + `scan_transcript_for_decisions.py`) and for
CORRECTIONS/LESSONS (`scan_corrections`). Findings are the third lane.

## How it works — deterministic Stop-hook sweep

The Stop hook in `hooks/hooks.json` runs `python3 -m scan_findings` against
`$CLAUDE_TRANSCRIPT_PATH` at session end. It is **deterministic (zero-LLM)** —
pure regex + JSON parsing, no Ollama dependency — so it fails open with no model
installed. It scans the agent-authored surface of the transcript: host-assistant
text blocks AND `tool_result` blocks (where a dispatched sub-agent's condensed
return lands). It does NOT reach a sub-agent's isolated internal transcript —
only what surfaces in the session, which is exactly the gap this closes (ad-hoc
audits report their findings into the conversation).

### What counts as a clearly-identified finding (high precision over recall)

| Signal | Example | Route |
|---|---|---|
| **Structured findings JSON** (recognized severity) | `{"findings":[{"severity":"high","title":"Token logged in plaintext"}]}` | **backlog** |
| **Prose severity label** (UPPERCASE) + concrete clause | `HIGH: verify-install.yml interpolates dispatch input into shell — command injection` | **backlog** |
| **Explicit `severity:` field** + clause | `severity: medium — cache key collides across tenants` | **backlog** |
| Structured finding with **absent/unknown severity** | `{"severity":"banana","title":"..."}` | **review queue** |
| Prose **finding keyword / Bug:/Issue: prefix**, NO severity | `I suspect a race condition in the worker pool` | **review queue** |
| Anything else (questions, "low latency" prose, hedges) | `Is this high risk?` | ignored |

Routing is MECE: a **recognized severity** → backlog; a **finding signal without
a recognized severity** → `.build-loop/proposals/` for human triage; **neither**
→ ignored. A false backlog item is worse than a missed one, so anything short of
an asserted severity goes to the review queue, never straight to the backlog.

### Backlog mapping

Findings are written through `backlog.py new` (the single, host-agnostic writer
— never by hand), with:

- `--type fix`, `--area audit`
- `--priority` from severity: critical→P0, high→P1, medium→P2, low→P3
- `--provenance-source auto-finding-sweep[:<agent>]` — marks the auto-sweep and
  the originating agent where detectable (e.g. `auto-finding-sweep:security-reviewer`)
- `--provenance-ref finding-hash:<hash>` — the cross-session dedup key

### Dedup (idempotent)

Each finding carries a stable `finding-hash` = sha1 of its severity-stripped
normalized clause. Before writing, the sweep loads every existing backlog item
(active + archived) and review proposal and collects their hashes + normalized
titles. A candidate is skipped when its hash already exists OR its normalized
title equals an existing open item's title. Re-running the sweep on the same
transcript creates nothing new. The same finding re-stated at a different
severity (or re-swept on a later Stop) hashes identically and is deduped.

## Hook safety contract

Non-blocking, fail-open, identical guardrails to the decision sweep:

- any error logs and exits 0 (the hook backgrounds with `nohup … &; printf '{}'`)
- `.build-loop/.no-capture` (per-session opt-out) → clean exit 0
- single-flight `fcntl.flock` on `/tmp/build-loop-findings-scan.lock`
- wall-clock budget `SCAN_FINDINGS_BUDGET_S` (default 15s); partial completion is
  safe (each backlog write is its own atomic `backlog.py new` process)
- durable log at `${XDG_STATE_HOME:-~/.local/state}/build-loop/findings-scan.log`

## Manual run

```bash
# What the Stop hook runs (PYTHONPATH lets `-m scan_findings` resolve):
PYTHONPATH=scripts python3 -m scan_findings \
  --workdir "$PWD" --transcript "$CLAUDE_TRANSCRIPT_PATH" --print-json
```

`--print-json` reports `{candidates, backlog, review, skipped_dup}`. After a
sweep, `python3 scripts/backlog.py sync --repo .` refreshes the INDEX and mirrors
the new items into `build-loop-memory/projects/<slug>/backlog/`.

## Deferred (NOT built)

An LLM-judged extraction path (for findings stated without a severity label or a
finding keyword) is a possible future extension. v1 is deterministic-only by
design: it satisfies the acceptance contract, adds no dependency, and keeps
precision high. Add the LLM path only against a named, observed miss in this repo.
