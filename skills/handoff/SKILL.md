---
name: handoff
description: "Compose a complete, durable build-loop handoff document from the current run state, and optionally launch a fresh session with it injected. Use when crossing a context boundary (context limit, planned restart, worktree GC). Triggers: 'hand off', 'handoff', 'new session', 'context limit', 'restart', 'fresh session', '/build-loop:compose-handoff'."
user-invocable: true
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Build-Loop Handoff

Compose a complete, durable handoff document from the current build-loop run state,
then (optionally) launch a fresh session in the STABLE checkout with that document injected.

## When to use

- Context window approaching limit mid-build.
- Planned restart at a session boundary (e.g. deploying a plugin update requires a restart).
- A worktree about to be GC'd — extract state before the folder disappears.
- Handing off work to a peer agent or co-developer.

## The seven content classes a handoff must carry

Derived empirically (2026-07-26): a generated handoff was cold-read by fresh agents at
three model tiers, scored, repaired, and re-tested. Every failure fell into one of seven
classes. A handoff missing any one of them produces an agent that can *restate* the work
but cannot *act* on it. Ranked by how much damage the omission causes.

| # | Class | The question it answers | Failure when missing |
|---|-------|------------------------|----------------------|
| 1 | **Orientation** | What is this product, for whom, on what stack? | Reviewer inferred "Apple app" only from `xcodebuild` in a gotcha. Everything downstream is guesswork. |
| 2 | **Constraints / non-goals** | What must I NOT do? | The single most-cited gap. This is the class truncation kills first, because non-goals sit at the END of an intent file. |
| 3 | **Landmines** | What will bite me on my FIRST action? | Stale run-id markers, set push-holds, emptied state blocks. Each fires before any real work begins. |
| 4 | **Authorization** | Am I allowed to just start? | Absent this, an agent picks the most visible queue item — which may be another run's unpaid audit debt. |
| 5 | **Verification recipe** | How do I build/test, and what must I never run? | Without it an agent reaches for the obvious tool and gets a false green. |
| 6 | **Open decisions** | What is genuinely undecided, who owns it, what decides it? | A dangling "may have been wrong" with no owner and no criterion is unresolvable by the next agent. |
| 7 | **Provenance** | How trustworthy is this record itself? | A reconstructed or overridden record read as ground truth is worse than no record. |

Two cross-cutting rules learned the same way:

- **Never truncate a class-2 or class-6 section.** Both live at the end of their source
  files, so any line cap removes exactly the text that carries the constraint.
- **Counts are claims.** A queue count that silently caps (or counts a derived `INDEX.md`
  instead of `items/`) understates open work. Titles alone are not enough either — carry
  the frontmatter fields that decide whether an item is safe to pick up
  (`status`, `classify`, `judgment_verdict`, `owed_layers`, `blocked_by`).

Measured effect: fixing classes 1–7 moved cold-read accuracy from partial to full across
Opus, Sonnet and Haiku. Confidence tracks model tier — a weaker model reads disclosed
provenance ("reconstructed", "override") as danger rather than as context, so state those
plainly and say what they do and do not imply.

## What it composes

Nine fixed sections (always the same order; absent data renders as "n/a"):

| # | Section | Source |
|---|---------|--------|
| 1 | North Star (intent) — incl. Orientation + glossary | `.build-loop/intent.md` (inlined WHOLE) |
| 2 | Current Goal — incl. open decisions | `.build-loop/goal.md` (inlined WHOLE) |
| 3 | Phase + Live Checklist | `.build-loop/state.json` (execution + runs[]) |
| 4 | Git State | `git status` + `git log` |
| 5 | Queues | `followup/`, `backlog/`, `ux-queue/`, `issues/` |
| 6 | Gotchas / Lessons | `.build-loop/feedback.md` |
| 7 | Last Run Summary | `state.json.runs[-1]` |
| 8 | Resume Instructions | generated (workdir, phase context) |

## Usage — no flag (emit doc)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handoff --workdir "$PWD"
```

Prints the handoff doc to stdout. Pipe to a file or share directly.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handoff --workdir "$PWD" --output handoff.md
```

Writes to a file instead.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handoff --workdir "$PWD" --json
```

Emits a JSON envelope `{document, sources, errors, ts}` for programmatic use.

## Usage — `--launch` (fresh session)

The command surface (`/build-loop:compose-handoff --launch`) handles this. The skill provides
the doc; the command layer handles host-specific launch.

**What `--launch` does:**
1. Compose the handoff doc from the CURRENT run state.
2. Write it to `.build-loop/handoff-latest.md` in the STABLE checkout.
3. Start a fresh host session at the STABLE checkout root (not the worktree).
4. Inject the handoff doc so the new session opens with full context.

**Host behavior:**

| Host | Launch method | Handoff injection |
|------|--------------|-------------------|
| Claude Code | `claude --print` with doc as initial prompt prefix | Inline in opening message |
| Codex | `codex` with `--context` flag or stdin | Depends on Codex version |
| Unknown / unsupported | Emit doc + print instructions, exit 0 | Manual paste |

The `--launch` path always writes `.build-loop/handoff-latest.md` regardless of host
support — the doc is the primary deliverable; launch is a convenience.

**Important:** launch always targets the STABLE checkout (`git worktree list` → the
`[bare]` or main entry), not the current worktree. Worktrees may be GC'd before the
new session starts.

## KISS/DRY note

`scripts/handoff/__main__.py` reads `.build-loop/` using only `json`, `pathlib`,
and `subprocess` from the standard library — no new dependencies. It does NOT
re-implement state parsing; it reads `state.json` directly at the same paths the
orchestrator already writes. Tests: `scripts/handoff/test_handoff.py` (13 tests).

## Host-agnostic design

The skill provides **structured data + instructions**. The host coding agent's LLM
interprets and acts on the handoff doc. No vendor-specific API calls. The `--launch`
CLI path uses the host's own CLI, isolated in `commands/compose-handoff.md`'s conditional
block — the skill logic is identical across Claude Code, Codex, and future hosts.
