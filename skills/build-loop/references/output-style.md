# Output Style Contract — User-Facing Terminal Output

Final user-facing output (Phase 4 Review-G report, phase status lines printed to the operator's terminal) must be **clear, direct, concise, and free of internal jargon**. This contract is enforced — `scripts/report_lint.py` runs on the draft before the user sees it, and Review-G auto-revises on findings (warn-and-self-heal, never a hard halt).

It extends — does not duplicate — the existing guidance:

- `CLAUDE.md` § "Concise output" — say only what the user needs to decide or act
- `~/.claude/CLAUDE.md` § "Intentional word choice" — every line must transmit information the reader does not already have
- `~/.claude/CLAUDE.md` § "Reporting Work" — pyramid-principle headline + verification line + impact-by-size

Scope: **user-facing output only.** Internal agent-to-agent envelopes (subagent return JSON, judge-decisions, run records, MECE briefs) are structured data and stay as-is — they exist for machines, not the human.

## The rules

1. **Headline = one plain full sentence** stating what changed. First non-blank line. Not a noun phrase, not a telegraph fragment, not a heading.
2. **Outcome framing — lead with what changes for the user.** The headline and substance lead with what the user can now do, what stops failing, or what no longer needs a manual step — the *result*, not the implementation. Use before→after when it clarifies. Mechanism, file paths, and design detail still belong in the report — below the lead, in the progressive-disclosure detail (see rules 3–4), never in the headline. See "Outcome framing" below for the worked good/bad pair.
3. **Bulleted concrete artifacts.** Below the headline: commit hashes, file paths, issue paths. Concrete things the user can grep, open, or `git show`.
4. **Substance bullets.** What the change does for the user, in plain language (outcome-first per rule 2). Optional when the artifacts alone are self-explanatory.
5. **Validation line, explicit.** Name the exact command, method, or observer that verified the work, with a status marker:
   - `✅ Verified by <method>` — ran the script, passing test, curl response, IBR scan, demo
   - `⚠️ Untested — <what couldn't be verified and why>`
   - `❓ Uncertain — <what's assumed and what would close it>`
6. **Plain language, stated directly.** No jargon (see blocklist below). No contrastive pivot (`not X — it's Y`, `isn't X, it's Y`, `not just X but Y`) and no soft hedging — state the point directly, and state cause and effect directly (see "Precision and Brevity" below: shorter words, data over adjectives, direct cause→effect). The contrastive-pivot ban and the direct-cause-and-effect rule are the same instinct: say what happened and why, without negation or padding.
7. **Numbers only when supplied or verified.** Never invent a metric, date, percentage, or count to make a sentence concrete. This is the user-facing-style restatement of build-loop's existing "no false data, no unverified claims" guarantee — the fact-checker (Review-D) traces every rendered metric to its source; this rule keeps the report from adding one the fact-checker never saw. If you have no number, write the plain claim without one.

## Jargon blocklist (user-facing only)

These tokens are fine in internal envelopes; they must be translated to plain language in user-facing output:

| Internal token | Plain-language translation |
|---|---|
| `GAP-1`, `GAP-N` | Name the actual gap (e.g. "missing auditor dispatch") |
| `auditor_status: not-run:parent-must-dispatch` | "Auditor not run — parent owes it" |
| `auditor_status: ran:dispatched-agent` | "Auditor ran" or omit if obvious |
| `sub-step G`, `sub-step F` | "Final report step", "Auto-resolve step", or omit |
| `MECE` | "ownership split", "one owner per file" |
| `envelope` | "return data", "result" |
| `scope=build` | "full build review" or omit |
| `suggest_correction` / `yay` / `nay` / `look_again` | "approve", "reject", "needs change", "needs another look" |
| `Phase 4G` / `Phase 5` | "final review", "iterate step" |
| `state.json.runs[]` | "run record" |
| Internal phase codenames (C-HEAL, C-RCA, C-FLOW…) | Describe the behavior, not the code |

## Outcome framing — lead with the result, not the mechanism

The report describes what the change DOES FOR THE USER, not the feature or mechanism that delivers it. Lead with what the user can now do, what stops failing, or what no longer needs a manual step. Before→after where it clarifies. Plain language, minimal jargon. The mechanism — script names, normalization, TTLs, file paths — still appears, but in the progressive-disclosure detail below the lead, never in the headline or the first substance line.

This is judgment, not a deterministic check: `report_lint.py` does NOT grade outcome framing (a fuzzy "is this outcome-framed?" rule would false-green on disguised mechanism prose and false-block on terse-but-correct outcome reports). The rule is enforced by the Review-G one-pass self-heal — the orchestrator rewrites a mechanism-only lead into an outcome-first one before emitting.

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

## Precision and Brevity — sentence architecture

Clear, concise statements depend less on better words and more on sentence architecture. Strongest pattern: concrete noun + strong verb + specific object/outcome + cause/evidence when needed.

### 1. Core elements of a clear sentence
| Element | Job | Strong | Weak |
|---|---|---|---|
| Actor/subject | Who or what it is about | The team | There was |
| Verb | What happened/changed | launched, cut, missed, caused | had, was, experienced |
| Object/outcome | What the action affected | the onboarding flow | the process |
| Cause | Why it happened | because ownership was split | due to various issues |
| Metric/evidence | What proves it | 70% dropped at step 5 | many users struggled |
| Time/place | When/where | in Q2, at step 5 of 7 | recently, during the process |
| Implication | Why it matters | so the team should shorten setup | which is important |

Core sentence spine: [Actor] [verb] [object/outcome] [because cause].
Example: The team launched behind schedule because ownership was split across three groups.

### 2. Parts of speech: what to favor
- Nouns — concrete: name real things/people/roles/systems/outcomes. ("Users abandoned onboarding at step 5." not "There was friction in the user journey.")
- Verbs — carry the sentence: had an impact on→changed; was responsible for→caused; made improvements to→improved; experienced delays→slipped; provided support for→supported; made a decision→decided.
- Adjectives — only when they add precision: "missed the regulatory deadline" (why it matters) not "an important deadline" (asks for trust).
- Adverbs — usually replace with data: significantly increased→increased 42%; quickly resolved→resolved in two days; frequently failed→failed in 3 of 5 tests; strongly suggests→suggests; materially improved→improved conversion by 8 points.

### 3. Most useful sentence patterns
1. Outcome → Cause — [Actor] [outcome] because [cause]. ("The model gave inconsistent answers because the prompt lacked success criteria.")
2. Metric → Behavior → Location → Meaning — [Metric] of [group] [behavior] at [point], suggesting [meaning]. ("70% of users abandoned onboarding at step 5 of 7, suggesting the flow was too long.") Use "suggesting" when data implies but does not prove a cause.
3. Cause → Outcome — [Cause] caused [outcome]. ("No single owner caused the project to stall.")
4. Actor → Action → Object → Reason — [Actor] should [action] [object] because [reason]. ("The team should cut onboarding from seven steps to three because most users drop off before setup is complete.")
5. Decision → Rationale → Tradeoff — [Decision] works because [rationale], but [tradeoff]. ("A shorter onboarding flow should improve completion, but it may collect less user data upfront.")
6. If → Then → Because — If [condition], then [action/outcome] because [reason]. ("If users abandon step 5, then the team should test a shorter flow because the current sequence likely asks for too much upfront.")

### 4. Order of information
Default: (1) main point, (2) evidence or cause, (3) implication or next step.
Example: "Users are dropping out late in onboarding. 70% abandon at step 5 of 7, suggesting the setup asks for too much before users see value. The team should test a three-step version."

### 5. Practical rule set
1. Start with the real actor, not "there is" / "it is."
2. Use one strong verb instead of a weak verb plus extra words.
3. Prefer numbers over vague claims.
4. Put the main point before the explanation.
5. Use "because" for known causes.
6. Use "suggesting" for likely causes.
7. Cut adjectives unless they add specific meaning.
8. Replace adverbs with data when possible.
9. Keep one main idea per sentence.
10. Keep caveats close to the claim they qualify.
Do not invent data: numbers, dates, and evidence appear only when supplied or verified (this is the user-facing-style face of build-loop's no-false-data principle + the fact-checker, not a second check).

### Best default formula
[Specific actor] + [strong verb] + [specific outcome] + because + [specific cause].
Example: "Users abandoned onboarding because the seven-step flow asked for too much before showing value."

This section is JUDGMENT-enforced in the Review-G one-pass self-heal alongside outcome framing — `report_lint.py` does NOT grade "shorter words", sentence patterns, or "is this a causal sentence": a fuzzy check there would false-flag correct terse prose and over-block, against the deterministic-only-for-known-risks rule. The direct cause→effect guidance is the same instinct as rule 6's no-contrastive-pivot ban (state what happened and why, without negation or padding); the no-invented-data line is the user-facing-style face of rule 7 and of build-loop's standing "no false data, no unverified claims" principle, traced by the fact-checker in Review-D — not a second enforcement path. Apply these when rewriting the draft; do not add a lint for them.

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

## What stays internal (do not lint)

These are structured data for machines, not user-facing prose:

- Subagent return envelopes (JSON return values, `status: blocked | partial`, etc.)
- `state.json` writes
- `.build-loop/judge-decisions.json` entries
- MECE brief packets between orchestrator and implementers
- Rally Point post bodies on internal channels (peer-to-peer coordination)
- `auditor_status` and other machine fields embedded in run records

The lint targets ONLY the final user-facing report markdown + phase status lines printed to the operator's terminal. Inline backtick spans are NOT exempted — if an internal token appears in prose (even quoted), translate it; use a full fenced block only for verbatim code/trace the user should see literally.

## Enforcement summary

- `scripts/report_lint.py <draft.md> --json` runs on every Phase 4G draft
- Findings are WARN (advisory): the orchestrator self-heals by revising the draft once, then emits it
- Never hard-blocks a run — worst case is one revision pass
- The structural lint (`scripts/build_report_lint.py`) keeps running in parallel; it is orthogonal (parallel_batch / merge_plan / evidence triplet shape)
