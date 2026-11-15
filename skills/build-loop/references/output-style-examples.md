# Worked style examples

Read these examples when a concrete formatting example is needed. The rules
remain in [output style](output-style.md) and [status format](status-output-format.md).
Examples are historical illustrations, not current product or provider facts.

### Good — outcome-framed (the user-approved target style)

```
When you run build-loop and a model it depends on goes down, the work now keeps
running on a backup instead of stopping and waiting for you to step in — and when
that model comes back, it returns to it on its own. You stop having to be the
manual fallback.

- No more stalled runs during an outage — if the preferred model is unavailable,
  agents automatically continue on the next-best one, instead of erroring out and
  needing a restart.
- Self-recovery — once the model is back, the system returns to it within about
  half an hour by itself, rather than staying on the backup until someone resets it.
- Quality holds during fallback — it won't quietly drop verification to a weaker
  model, and won't pick a model the setup can't run.
- Works as you add or change models — the same behavior applies no matter which
  vendor a model comes from.
```

Why this is good: the headline says what changes for the user ("the work now keeps running … you stop having to be the manual fallback"), each bullet leads with the user-visible outcome (no stalled runs, self-recovery, quality holds, works as you change models), and the before→after contrast ("instead of stopping and waiting", "rather than staying on the backup") makes the gain concrete. The mechanism that delivers it (the fallback script, the recovery window, the tier floor, vendor-agnostic resolution) is implied through outcomes, not enumerated.

### Bad — mechanism-only (same change, tells the user nothing)

```
Added dispatch_fallback.py with canonical-id normalization and a host-provider
filter; outages persist to model-availability.json with a TTL.
```

Why this is bad: it is the exact same change, but described as the feature/implementation. The reader learns what files exist, not that their runs stop stalling during an outage or that quality holds on the backup. It leads with the mechanism (`dispatch_fallback.py`, `canonical-id normalization`, `host-provider filter`, `model-availability.json`, `TTL`) and never states the user outcome. The fix is the rewrite above: keep this detail, but move it below an outcome-first lead.

## Good — the exemplar (Codex output, captured 2026-06-04)

```
- ad1ac79a memory(build-loop): capture native ax ui follow-up
- Issue: dev/git-folder/build-loop-memory/projects/build-loop/issues/bl-native-ax-light-ui-gap-check.md
- Backlog row added to .../backlog.md

It captures:
- skills/native-ax-driver/ as the home.
- IBR as the primary/full verifier, native AX as the lighter gap/fill layer.
- Blockers: clean main/fresh worktree and recovered AX/screencapture.

Validation passed: python3 scripts/validate_memory_store.py --strict.
```

Why this is good: plain-sentence headline implied by the commit subject, bulleted concrete artifacts with paths and a commit hash, an "It captures:" substance block, and an explicit validation line naming the exact command. Scannable, direct, no jargon.

## Good — exemplar B (scoped-edit narration, captured 2026-06-06)

```
Claim is recorded. I'm keeping the edit scoped: selector scoring plus one regression
test that reproduces "old handoff masks newer targeted inbox/review request."
[visible actions: git switch -c …, Explored: read/search …]
The edit is small: add a typed helper for requires_ack evidence, score direct targeted
artifacts as inbox-doorbell work, and cover it with a regression where a stale handoff
used to hide the newer message.
```

Why it's good: declarative state → scope stated plainly with the bug in plain words → the change in one informed sentence.

## Good — exemplar C (closeout note, captured 2026-06-06)

```
Commit 4392166 is created on fix/actionable-inbox-next. I'm posting the closeout to
Rally and releasing the two file claims now.
[visible actions: rally say release …, rally say artifact …]
```

Why it's good: states the landed artifact (commit + branch) → then the action-in-progress in one plain line.

## Bad — verbose + jargon (rewritten Good)

**Bad (jargon + filler):**

```
Phase 4G Sub-step G completed for the auditor dispatch ladder. The auditor_status
field was changed to ran:dispatched-agent based on the GAP-1 contract; the build
scope=build review-completeness gate passed. We've now wired the MECE envelope
through the build-orchestrator so it propagates to the runs[] entry. Suggested
correction verdict from independent-auditor: yay. Want me to push?
```

**Good (rewrite):**

```
Auditor now runs on every build commit; the gap where nested orchestrators
skipped it is closed.

- Commit: 7e54621 fix(audit): close nested-orchestrator audit gap
- Files: agents/build-orchestrator.md, scripts/audit_before_commit.py
- Run record: .build-loop/state.json runs[] entry build-2026-06-07-...

It captures:
- Nested orchestrators now hand the audit back to the dispatching parent.
- Auditor's approve verdict recorded in .build-loop/judge-decisions.json.

✅ Verified by python3 scripts/test_audit_before_commit.py — 14 passed.
```

## 7. Worked progression — why the near-misses fail

Real sequence from the session that produced this file. Each attempt fixed a
different level and the sentence stayed unusable, because the defect was in message
logic the whole time.

**Original (jargon):** *"The resolvers disagree on T3/T4."*
Project-internal vocabulary; meaningless outside this codebase.

**Attempt 1 — FAILS:** *"Two lookups pick different models."*
Plainer, and no more useful. No actor, no target, no modality. The reader asks "why
would they?" and has nothing to do next.

**Attempt 2 — ALSO FAILS:** *"Ask for the same tier two different ways and you get
two different models back — one path sorts by release date, the other by capability
rank."*
Accurate, and still wrong. Diagnosis-first: it explains the internals, so the reader
now understands the bug and still has to work out what to do about it.

**PASSES:**
```
We need one code path per model tier
Today we have 2+ paths, with inconsistent results
```
Actor (`we`), specific object (`one code path per model tier`), modality (`need` =
requirement). Target state first, gap second. Longer than attempt 1 — length was
never the goal.

## 8. Worked example — a full block

```
2 Groq models shut down on 2026-08-16
Anything still pointing at them fails outright, and the catalog lists them as
live, so the next person to read it picks a dead model.

 - llama-3.1-8b-instant — shut down 2026-08-16, replacement openai/gpt-oss-20b
 - llama-3.3-70b-versatile — shut down 2026-08-16, replacements
   openai/gpt-oss-120b and qwen/qwen3.6-27b
 - Both were free + developer plan only; enterprise spend unaffected
 - To fix: re-read Groq's official docs, update all 15 entries, mark those 2
   retired, and move the re-check date forward
 - I need your approval — this needs live doc lookups, and I will not move the
   date without doing them
```
