---
name: handoff
description: "Compose a complete, durable build-loop handoff document from the current run state, and optionally launch a fresh session with it injected. Use when crossing a context boundary (context limit, planned restart, worktree GC). Triggers: 'hand off', 'handoff', 'new session', 'context limit', 'restart', 'fresh session', '/build-loop:handoff'."
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

## What it composes

The handoff doc has eight fixed sections (always the same order; absent data renders as "n/a"):

| # | Section | Source |
|---|---------|--------|
| 1 | North Star (intent) | `.build-loop/intent.md` |
| 2 | Current Goal | `.build-loop/goal.md` |
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

The command surface (`/build-loop:handoff --launch`) handles this. The skill provides
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
CLI path uses the host's own CLI, isolated in `commands/handoff.md`'s conditional
block — the skill logic is identical across Claude Code, Codex, and future hosts.
