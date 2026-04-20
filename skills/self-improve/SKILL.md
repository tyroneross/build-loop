---
name: build-loop:self-improve
description: Phase 6 Learn — scan recent build-loop runs for recurring patterns, auto-draft experimental skills/agents with A/B tracking, notify user. Use after Review sub-step F (Report), or user-invokable with `/build-loop:self-improve` to trigger a scan outside a build.
version: 0.1.0
user-invocable: true
---

# Build-Loop Self-Improvement (Phase 6 Learn)

This skill runs after Review sub-step F (Report) completes, or on demand. It detects recurring patterns across recent build-loop runs, drafts experimental skills/agents to address them, and notifies the user for keep/remove decisions.

**Principle:** auto-draft, notify, experiment, decide based on evidence. User can always remove. A/B comparison is small and focused — one metric, short sample, clear decision rule.

## When This Skill Runs

- Automatically at end of every build-loop run (Phase 6 Learn, after Review sub-step F (Report))
- On demand via `/build-loop:self-improve`
- Skipped if `.build-loop/state.json.runs` has fewer than 3 entries — not enough signal

## Flow

```
┌──────────────────────────────────────────────────────────────┐
│ Phase 6 Learn: REVIEW (this skill)                                  │
├──────────────────────────────────────────────────────────────┤
│ 1. DETECT   → recurring-pattern-detector (Haiku)              │
│              emits patterns[] JSON                            │
│ 2. FILTER   → keep only confidence:high or count >= threshold │
│ 3. DRAFT    → for each kept pattern:                          │
│              self-improvement-architect (Sonnet)              │
│              writes .build-loop/skills/experimental/<name>/   │
│ 4. SIGNOFF  → build-orchestrator (Opus 4.7) reviews each:     │
│              approve, revise, or discard                      │
│ 5. TRACK    → record baseline in .build-loop/experiments/     │
│ 6. NOTIFY   → synthesize 3-5 line summary to user             │
│              (include removal command + A/B plan)             │
└──────────────────────────────────────────────────────────────┘
```

## Steps

### 1. Detect recurring patterns

```
Agent: recurring-pattern-detector (haiku)
Input: read .build-loop/state.json
Output: {scannedRuns, patterns: [...]}
```

If `patterns.length === 0`, skip to step 6 (notify with "no patterns detected, N runs scanned"). End.

### 2. Filter

Keep patterns matching any of:
- `confidence === "high"`
- `count >= 4` regardless of confidence
- `type === "manual_intervention"` (user time is expensive; lower threshold)

Drop the rest. Log skipped patterns in `.build-loop/experiments/skipped.jsonl` with date + reason — lets us tune thresholds later without losing signal.

### 3. Draft experimental artifacts

For each kept pattern, dispatch:

```
Agent: self-improvement-architect (sonnet)
Input: the pattern object + target type (skill or agent)
Output:
  - writes .build-loop/skills/experimental/<name>/SKILL.md (or agents/experimental/<name>.md)
  - returns concise 3-4 line synthesis
```

The architect agent includes an A/B Experiment section in every artifact it writes.

### 4. Opus 4.7 signoff

Build-orchestrator (Opus 4.7) reads each drafted artifact and decides:

- **APPROVE** — artifact is coherent, pattern is real, A/B plan is measurable → proceed to track
- **REVISE** — core idea is right, execution needs tightening → re-dispatch architect with specific feedback, max 1 revision pass
- **DISCARD** — pattern is noise or artifact is unusable → delete the file, log to `.build-loop/experiments/discarded.jsonl` with reason

Opus signoff is the quality gate. Sonnet drafts fast; Opus ensures no garbage ships into `.build-loop/skills/experimental/`.

### 5. Track baseline

For each APPROVED artifact, write to `.build-loop/experiments/<name>.jsonl`:

```jsonl
{"event": "created", "date": "2026-04-19T14:22:00Z", "artifact": "experimental-middleware-typegen", "baseline_metric": "Review-B pass rate on middleware edits", "baseline_value": 0.6, "target_value": 0.9, "sample_size_target": 5}
```

The experimental skill's description triggers it on matching runs. Each subsequent run that matches the skill's trigger appends to this file:

```jsonl
{"event": "applied", "date": "...", "run_date": "2026-04-20", "triggered": true, "metric_value": 1.0, "outcome": "phase_5_pass"}
```

After `sample_size_target` applied entries, Phase 6 Learn computes delta and emits a decision recommendation (promote / remove / extend sample).

### 6. Notify user (concise synthesis)

Emit exactly this format to the Review sub-step F report tail:

```
## Phase 6 Learn: Self-Improvement Review

Scanned: N runs over last M days
Detected: X high-confidence patterns, Y filtered out (low signal)

Created experimental artifacts (all in .build-loop/, easy to remove):
  • <name-1>     — <one-line purpose>     — A/B on: <metric>
  • <name-2>     — <one-line purpose>     — A/B on: <metric>

Monitor: `cat .build-loop/experiments/<name>.jsonl`
Remove: `rm -rf .build-loop/skills/experimental/<name>/`
```

If nothing was created, emit:

```
## Phase 6 Learn: Self-Improvement Review
Scanned N runs. No recurring patterns crossed confidence threshold. Nothing created.
```

## Data Contracts

### `.build-loop/state.json.runs[]` extensions (writer: build-orchestrator during Review sub-step F)

Review sub-step F (Report) must now append a run entry to `state.json.runs[]` before Phase 6 Learn runs. Schema:

```json
{
  "date": "ISO-8601 UTC",
  "goal": "short goal text",
  "outcome": "pass" | "fail" | "partial",
  "phases": {
    "1": { "status": "pass|fail", "duration_s": number, "root_cause": "string?" },
    "...": "..."
  },
  "diagnosticCommands": ["shell commands run during build"],
  "filesTouched": ["absolute paths edited"],
  "manualInterventions": [
    { "phase": number, "note": "short description" }
  ]
}
```

The orchestrator is responsible for capturing `diagnosticCommands` (hook or transcript review), `filesTouched` (git diff after build), and `manualInterventions` (any AskUserQuestion response that overrode default flow).

### `.build-loop/experiments/<name>.jsonl`

Append-only log per experimental artifact. Schema:

```jsonl
{"event": "created", "date": "ISO", "artifact": "name", "baseline_metric": "...", "baseline_value": N, "target_value": N, "sample_size_target": 8}
{"event": "applied", "date": "ISO", "run_id": "run_YYYYMMDDTHHMMSSZ_hash8", "triggered": true, "metric_value": N, "outcome": "pass|fail", "co_applied_experimental_artifacts": ["other-name"], "confounded": true}
{"event": "applied", "date": "ISO", "run_id": "...", "triggered": true, "metric_value": N, "outcome": "pass", "co_applied_experimental_artifacts": [], "confounded": false}
{"event": "decision", "date": "ISO", "verdict": "promote|remove|extend", ...}
```

`applied` rows with `confounded: true` are preserved for audit but excluded from the effective sample count. The effective sample is `count(rows where confounded == false)`. A sample only passes the floor when effective count >= 8 (or the user's custom `sample_size_target`, whichever is larger).

## Promotion decisions (after sample complete)

**Default behavior is opt-in.** `autoPromote` defaults to **`false`** in `.build-loop/config.json` — Phase 6 Learn drafts experimental artifacts and tracks A/B evidence but stops at "propose promote" until the user enables the auto mode. This changed after an adversarial review flagged that small-sample auto-promotion can flip on flake and delete good work during temporary regressions.

### Enabling auto-promote

To opt in, create `.build-loop/config.json` with:

```json
{ "autoPromote": true }
```

Even with the flag on, auto-promotion requires **`sample_size_target >= 8`** and passing the promotion rules below. Below the floor, Phase 6 Learn always writes a proposal to `.build-loop/proposals/<name>.md` and blocks promotion until the user reviews. The architect agent initializes new experiments with `sample_size_target: 8` by default; only raise it when noise demands more power.

### Promotion rules

When `autoPromote` is true AND `sample_size_target >= 8` AND the experiment's applied entries are all `confounded: false` (see §Confound tracking below):

| Delta vs baseline | Action | Location |
|---|---|---|
| Metric improves ≥ target (non-confounded) | **Auto-promote**: `git mv .build-loop/skills/experimental/<name> .build-loop/skills/active/<name>`, update SKILL.md frontmatter `experimental: false` + `promoted_at: <ISO>`, append `{event: "promoted", ...}` to the experiment's jsonl | `.build-loop/skills/active/<name>/` |
| Metric improves < target (partial win) | **Extend sample** to 2N; re-evaluate after additional runs | unchanged |
| Metric flat (±10% of baseline) | **Extend sample** to 2N; re-evaluate | unchanged |
| Metric regresses | **Write proposal** to `.build-loop/proposals/<name>-remove.md` with evidence. Removal requires user confirmation via `AskUserQuestion` in the next Phase 6 Learn run (not immediate `rm -rf`). Avoids single-build regressions deleting useful skills. | experimental (intact) |
| Sample at 2N still flat | **Write proposal** to `.build-loop/proposals/<name>-inconclusive.md`; same user confirmation gate for removal | experimental (intact) |

If the opt-in flag is off, every row above becomes "write proposal, no file moves/deletes." Proposals accumulate in `.build-loop/proposals/` for manual review.

**Below the sample-size floor** (`applied_count < 8`): Phase 6 Learn records evidence but never acts. The architect may still author new experimental artifacts in this state — the floor only gates promotion/removal decisions.

### Confound tracking

Every Review-B applied-run log line MUST include:
- `run_id` — a canonical identifier for the build run (the orchestrator generates it at Review-F, e.g. `run_20260419T143022Z_<goalHash8>`)
- `co_applied_experimental_artifacts[]` — full list of experimental artifact names that also triggered on this run

**Rule**: a run with `co_applied_experimental_artifacts.length > 0` is **confounded** — no single artifact can claim credit for the metric delta. Phase 6 Learn marks all such runs with `confounded: true` and **excludes them from promotion math**. The confound state is sticky: removing an entry from the jsonl does not uncontaminate it.

**Enforcement**: at most one experimental artifact should trigger per build by design. If two fire (because their descriptions both matched the goal), log both measurements with the confound flag and continue the build, but the A/B accounting discounts all co-applied rows. Extending the sample to 2N must count only `confounded: false` rows toward the new target.

**Why we keep both artifacts active rather than disabling one**: silently disabling a co-applied artifact alters future behavior without user awareness. Keeping them both on + marking runs confounded produces honest (if slower) evidence and a surfacable signal that the skills are overlapping and should be merged or one retired.

**A note on precedent**: Karpathy's autoresearch auto-accepts metric wins *within a single optimization run*, not across runs. Cross-run auto-promotion is a new layer. The adversarial review correctly pointed out that small-sample cross-run promotion without isolation is not the same claim of rigor — so the default is opt-in, the floor is 8, and confounded runs don't count.

### Decision log format

Write to `.build-loop/experiments/decisions.jsonl` (append-only) one entry per auto-decision:

```jsonl
{"event": "auto_promote", "date": "ISO", "name": "middleware-typegen", "baseline": 0.6, "observed": 0.94, "delta": "+56%", "target": 0.9, "sample_size": 5, "artifacts_moved": 2}
{"event": "auto_remove", "date": "ISO", "name": "aggressive-dedup", "reason": "regression", "baseline": 3.2, "observed": 4.1, "delta": "+28% (worse)", "sample_size": 5}
{"event": "extend_sample", "date": "ISO", "name": "memo-scope", "reason": "flat", "baseline": 0.72, "observed": 0.75, "delta": "+4%", "new_target_size": 10}
{"event": "auto_remove", "date": "ISO", "name": "eager-typegen", "reason": "inconclusive", "sample_size": 10, "note": "flat after 2N"}
```

### User override / reversal

- **Stop an auto-promote**: if the user disagrees with an auto-promotion, `git mv .build-loop/skills/active/<name> .build-loop/skills/experimental/<name>` or `rm -rf .build-loop/skills/active/<name>/`. Phase 6 Learn will not re-promote a name listed in `.build-loop/skills/.demoted` (one name per line — create this file to block re-promotion).
- **Restore a removed artifact**: logs preserve the original SKILL.md content in `discarded.jsonl` under `{artifact_content: "..."}`. Restoration is manual (grab the content, write back). Only the last 30 discards are preserved; older entries keep metadata only.
- **Auto-promote is OFF by default**. To enable: `.build-loop/config.json` → `{"autoPromote": true}`. Even when on, promotion requires effective non-confounded sample >= 8 and non-regression. Below the floor or with confounded-only evidence, proposals accumulate in `.build-loop/proposals/` for manual review regardless of the flag.

## Cross-Project Promotion

Auto-promote stays inside the project. Moving an experimental or active artifact into the build-loop plugin repo — where it affects every user on every project — requires explicit invocation:

```
/build-loop:promote-experiment <name>
```

The command reads the experiment's track record across this and other projects (if global `~/.build-loop/experiments/` index exists), checks the artifact quality, asks the user for confirmation, and commits to the plugin repo on a feature branch for user review. See `commands/promote-experiment.md` for the full protocol.

## Removal

Users can remove any experimental artifact at any time:

```bash
rm -rf .build-loop/skills/experimental/<name>/
rm .build-loop/experiments/<name>.jsonl    # optional, keeps history
```

The skill stops triggering immediately (no orchestrator restart needed).

## What This Skill Will NOT Do

- Will not modify the build-loop plugin repo
- Will not promote skills across projects without explicit user approval
- Will not run if state.json has < 3 runs — insufficient signal
- Will not retry pattern detection more than once per run
- Will not write skills for patterns with confidence "low"

## Model Tiering (this skill)

| Step | Agent / Model |
|---|---|
| 1. Detect | recurring-pattern-detector (haiku) |
| 3. Draft | self-improvement-architect (sonnet) |
| 4. Signoff | build-orchestrator (opus 4.7) |
| 6. Notify | inline, no model |

Haiku detect is the floor — scanning JSON for counts. Sonnet drafts because authoring SKILL.md needs judgment about trigger phrases and structure. Opus 4.7 signs off because a bad experimental skill silently contaminates future runs; wrong spec is catastrophic.
