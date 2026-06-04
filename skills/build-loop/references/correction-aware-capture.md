<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Correction-aware lesson capture — three-tier design

## Why

Two gaps in the prior capture stack:

1. **`scan_transcript_for_decisions.py` no-ops entirely without Ollama.**
   The script gates on `shutil.which("ollama")`; absent the binary, an
   entire session's worth of decisions is silently dropped.
2. **No lessons / feedback lane from live conversation.** The decisions
   scanner captures decisions only. A user correcting the assistant's
   just-taken action — the highest-signal lesson event in a session —
   had no trigger and no destination.

The three-tier design closes both gaps without disrupting the existing
decisions pipeline.

## Three tiers

### Tier 1 — Deterministic Stop-hook (always runs, no LLM)

**Script:** `scripts/scan_corrections/`
**Hook:** `hooks/hooks.json` Stop entry, fires alongside (not instead of)
the decisions scanner.

Detects three classes of high-signal patterns in USER turns:

| Class | Patterns | Confidence |
|---|---|---|
| Correction | `revert that`, `don't X`, `undo`, `stop X`, `back that out`, `wrong approach` | `confirmed` (extra: `prior_assistant_acted: true` when assistant just used tools) |
| Preference | `always X`, `never X`, `must X`, `default to X`, `we use X for Y`, `prefer X` | `confirmed` |
| Tradeoff | `X instead of Y`, `actually X not Y`, `X over Y because Z` | `confirmed` |

Scope routing: keywords like `across projects`, `globally`, `for all projects`,
`as a rule`, `standing rule` flag the candidate as `scope: global` (otherwise
`project`). Anti-false-positive: wh-question turns ending in `?` are skipped
by hard-skip patterns; greetings/thanks/ok are skipped.

Writes one `.build-loop/pending-lessons/<ts>-<kind>-<id>.md` per candidate
with YAML frontmatter:

```yaml
---
id: <16-char sha1>
kind: correction|preference|tradeoff
signal_type: <named pattern>
confidence: confirmed
scope: project|global
turn_index: <int>
captured_chars: <int>
tier: 1-deterministic
source: stop-hook
captured_at: <iso8601>
extras:
  prior_assistant_acted: true|false
---

## Quote
> <verbatim user span>

## Context (±200 chars)
```

Idempotent: `id_hash` is a SHA-1 of (kind, signal_type, normalized quote),
so re-running the scanner on the same transcript does not duplicate files.
Promoted/discarded subdirs are also checked for dedup.

Fail-open contract: any exception logs to stderr and exits 0. The
`.build-loop/.no-capture` opt-out short-circuits before any work.
`SCAN_CORRECTIONS_BUDGET_S` (default 10s) caps wall-clock.

### Tier 2 — Optional Ollama accelerator (existing path, unchanged)

**Script:** `scripts/scan_transcript_for_decisions.py` (the existing
decisions scanner, untouched). When Ollama is installed AND the
transcript is large enough to benefit from clustering, this path
distills/dedups decisions and writes to
`build-loop-memory/projects/<slug>/decisions/` (or `_review/` for
quarantine).

Tier 2 is **strictly optional**. The user installed Ollama on this
machine, so it runs; on a fresh machine without Ollama, tier 1 alone
guarantees capture and the session continues uninterrupted.

### Tier 3 — Host-agent refinement (the primary intelligence)

**Script:** `scripts/surface_pending_lessons.py`
**Consumed by:** the host coding agent (Claude Code in this build,
Codex on a Codex host, etc.) at SessionStart.

Per the user's standing "host-agent-is-the-LLM" rule, the host coding
agent is the primary refinement layer. Each session, the host reads
`.build-loop/pending-lessons/` (and optionally
`build-loop-memory/projects/<slug>/decisions/_review/`), classifies each
candidate, and promotes via:

- `scripts/memory_writer.py` — for `kind=lesson|feedback|preference`
  (routes to `build-loop-memory/lessons/` for `scope=global` or
  `build-loop-memory/projects/<slug>/lessons/` for `scope=project`)
- `scripts/write_decision/__main__.py` — for `kind=decision`

Discarded candidates move into
`.build-loop/pending-lessons/discarded/` (any file there is silently
skipped on re-runs, so the discard is durable).

The same surface also exposes the existing Ollama `_review/` quarantine
when `--include-decisions-review` is set, so the host agent has one
queue to drain.

## Store bridge (harness ↔ build-loop-memory)

**Script:** `scripts/bridge_lesson_to_harness.py`

Once a lesson lands in build-loop-memory (via tier-3 promotion or any
other path), it can be mirrored into the harness auto-memory store the
host coding agent auto-loads at session start
(`~/.claude/projects/-Users-<u>/memory/`). The bridge:

- Resolves a deterministic target basename `<kind>_<slug>.md` matching
  the harness convention
- Augments the bridged copy's frontmatter with `bridged_from`,
  `bridged_at`, `source_store: build-loop-memory`
- Appends a one-line entry to harness `MEMORY.md` under
  `## Bridged from build-loop-memory` (creates the section if absent;
  preserves other sections; dedup'd on target basename)

Idempotent and reversible. Only `lesson | feedback | preference | convention | gotcha`
types bridge; decisions stay in their own canonical store.

## Triggers — what fires when

| Event | Tier | Effect |
|---|---|---|
| Session ends (Stop hook) | 1 | tier-1 scanner writes raw candidates to `.build-loop/pending-lessons/` |
| Session ends (Stop hook) | 2 | tier-2 scanner (Ollama, if present) writes distilled decisions |
| Session starts (host agent reads context-bootstrap) | 3 | pending-lessons queue count surfaces in queue summary; host agent runs `surface_pending_lessons.py` to refine |
| Lesson promoted to build-loop-memory | — | `bridge_lesson_to_harness.py` mirrors to harness auto-memory |

## File layout summary

```
scripts/
  scan_corrections/
    __init__.py
    detect.py             — patterns + Candidate dataclass + JSONL parser
    __main__.py           — Stop-hook CLI (tier 1)
    test_detect.py        — 26 unit tests
    test_cli.py           — 8 CLI integration tests
  surface_pending_lessons.py     — tier 3 host-agent surface
  test_surface_pending_lessons.py — 10 tests
  bridge_lesson_to_harness.py    — store bridge
  test_bridge_lesson_to_harness.py — 11 tests
  scan_transcript_for_decisions.py — UNCHANGED (tier 2)

hooks/hooks.json — Stop hook now runs both scanners in parallel

scripts/context_bootstrap.py — QUEUE_NAMES extended with "pending-lessons"

.build-loop/pending-lessons/   — tier-1 candidate queue (this run)
                  /promoted/    — host-agent promoted (silenced)
                  /discarded/   — host-agent discarded (silenced)
```

## Non-goals + tradeoffs

- **Not a replacement for `auto-decision-capture`.** That skill is for
  the host agent's in-session reasoning — explicit captures during the
  conversation. Tier-1 + tier-3 fire at session boundaries; the skill
  fires inline. They compose.
- **Tier 1 is intentionally narrow.** Adding fuzzy patterns would
  produce false positives. The host agent (tier 3) is where ambiguous
  cases get judged — that's the design intent of "host-agent-is-the-LLM."
- **Bridge runs on demand, not automatically.** A future enhancement
  could fire it via a post-promotion hook; the durable lesson lives in
  build-loop-memory either way, and the bridge is reversible.
