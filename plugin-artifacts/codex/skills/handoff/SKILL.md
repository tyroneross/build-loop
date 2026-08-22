---
name: handoff
description: "Compose a complete, durable build-loop handoff document from the current run state, and optionally launch a fresh session with it injected. Use when crossing a context boundary (context limit, planned restart, worktree GC). Triggers: 'hand off', 'handoff', 'new session', 'context limit', 'restart', 'fresh session', '/build-loop:compose-handoff'."
user-invocable: false
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

## Recall and confidence are different failures with different fixes

This is the finding that mattered most, and it is not obvious: **an agent can score full
marks on recall and still refuse to act.**

Measured on one document across four rounds. Haiku answered 10/10 factual questions
correctly while rating its own confidence **2/5** — it had every fact and would not touch
the repo. Adding more facts would not have helped, because facts were never the gap.

| Failure | Caused by | Symptom | Fix |
|---|---|---|---|
| **Recall** | Omission — truncation, silent caps, missing orientation | "NOT IN DOC", wrong counts, guessing the tech stack | Put the content in (classes 1–5) |
| **Confidence** | Unresolved implication — a flag with no interpretation | Correct answers, low self-rating, "I'd have to read the code first" | State what each flag *means for the reader* (classes 6–7) |

Concretely, what moved Haiku from 2/5 to 5/5 was not new information. It was:

- Queue items carrying `judgment_verdict` / `classify` / `owed_layers`, so "Judgment owed —
  bl-…-codex-299759" became something a reader could triage instead of fear.
- The open decision gaining an **owner** and a **criterion for deciding**, so it read as
  assigned rather than abandoned.
- The override gaining an explicit **cost** ("runs zero tests") and **bound** (what it does
  and does not invalidate), so it read as disclosed rather than alarming.

**Rule: every warning in a handoff must carry its own interpretation.** A flag without a
"so what" transfers anxiety, not information — and a cautious reader responds by doing
nothing, which is the exact outcome the handoff exists to prevent.

## How to test a handoff (the cold-read protocol)

Do not self-assess a handoff. Dispatch a fresh agent whose ONLY artifact is the document
path, tell it to open nothing else, and score it. Brief it and the test is worthless.

Run the same fixed question set at **two model tiers**, because they detect different
defects:

| Tier | Detects | Why |
|---|---|---|
| **Weakest available** (Haiku) | Unresolved implications | It will not infer past an ambiguity; low confidence with correct answers pinpoints class 6/7 gaps |
| **Strongest available** (Opus) | Internal contradictions | It cross-references sections and finds claims that cannot both be true |

Ask for `CONFIDENCE (1-5)`, `GAPS`, and `CONTRADICTIONS` explicitly, and instruct the
agent to answer "NOT IN DOC" rather than guess — otherwise general knowledge silently
fills holes and the doc scores better than it deserves. Tell it to be harsh; a comfortable
review is a useless one.

Question set that surfaced every defect found (adapt the nouns, keep the shapes): what is
this product and for whom · what platforms and storage · what was just done and is it
finished · what builds/tests it and what must you never run · what is <the largest open
item> and is it done · name three forbidden things · how many queue items and which are
audit debt · did this ship through a normal gate · what decision is owed, who owns it,
what decides it · which field proves no new run started and which must you not read · why
is this record untrustworthy · what is the trap when searching for callers · are you
authorized to start · what must you run after adding a file · who wrote this record and
when relative to the work.

**Expect the strong tier to challenge your evidence, not just your prose.** On the final
round Opus accepted every fact and still flagged that a "three platforms build clean"
claim was overstated — the third platform's target excluded the directory the changed
files lived in, so its green proved the change could not break it, not that it was
verified. That correction came from the review, not from the author.

## What it composes

Nine fixed sections (always the same order; absent data renders as "n/a"):

| # | Section | Source |
|---|---------|--------|
| 1 | North Star (intent) — incl. Orientation + glossary | `.build-loop/intent.md` (inlined WHOLE) |
| 2 | Current Goal — incl. open decisions | `.build-loop/goal.md` (inlined WHOLE) |
| 3 | Phase + Live Checklist | `.build-loop/state.json` (execution + runs[]) |
| 4 | Git State | `git status` + `git log` (working-tree listing capped — see below) |
| 5 | Queues | `followup/`, `backlog/`, `ux-queue/`, `issues/` |
| 6 | Gotchas / Lessons | `.build-loop/feedback.md` |
| 7 | Last Run Summary | `state.json.runs[-1]` |
| 8 | Landmines | detected (stale `.current-run-id`, `.push-hold`, crash marker, …) |
| 9 | Resume Instructions | generated (workdir, phase context) |

### §4 working-tree listing is capped at 40 paths

Above 40 dirty files, §4 lists the paths in the **smallest** top-level groups and
collapses the rest into a count-by-top-level-path table, then prints a
`git add -A` warning. Reproduced 2026-07-25 on atomize-ai: ~2,981 pre-existing
dirty tooling files (`.navgator/`, `.build-loop/`, `.bookmark/`, `.rally/`) made §4
2,999 of the document's 3,214 lines — 93% — burying North Star, Goal, Landmines,
and Resume Instructions past 3,000 lines of cache paths. A handoff exists for when
context is scarce, so paying 3,000 lines for it inverts the tool's purpose.

The cap is on **enumeration only**: every dirty file is still counted in the table.
Smallest-group-first is what keeps it useful — `git status` sorts by path, so a
head-40 would have emitted 40 `.bookmark/` cache paths and cut every source edit.

`--full-git` restores the raw per-file listing when it is genuinely wanted.

## Delivery is not the same as writing it

A handoff that exists only on disk has not been handed off. Two things are required
every time, and neither is optional because the file was committed.

**1. State the absolute path in the reply.** Not "handoff written", not a repo-relative
path. The person picking this up is in a different terminal, often a different repo, and
frequently is not a person. Print the full path:

```
/Users/<user>/dev/git-folder/<repo>/HANDOFF.md
```

**2. Post it to Rally Point.** A handoff invisible to the coordination substrate cannot be
picked up by a peer agent, which is the case it most exists for.

```bash
rally say fact --tool "<your-tool-id>" \
  --subject "handoff: <one line, what state the work is in>" \
  --evidence "file:<absolute path>" --json
```

The same applies to a retrospective. Those land in the memory store rather than the repo,
so they are unfindable from the working directory unless the path is stated.

**Why this is a rule and not a preference.** The seven content classes below exist so a
cold reader can resume without the author. All of that work is wasted if the reader cannot
locate the document. The most common failure is not a thin handoff; it is a good handoff
nobody found.

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

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handoff --workdir "$PWD" --full-git
```

Lists every dirty path in §4 instead of capping at 40 and summarizing the rest.
Combines with any of the flags above.

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
orchestrator already writes. Tests: `scripts/handoff/test_handoff.py`.

## Host-agnostic design

The skill provides **structured data + instructions**. The host coding agent's LLM
interprets and acts on the handoff doc. No vendor-specific API calls. The `--launch`
CLI path uses the host's own CLI, isolated in `commands/compose-handoff.md`'s conditional
block — the skill logic is identical across Claude Code, Codex, and future hosts.
