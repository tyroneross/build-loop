---
name: build-loop:self-improve
description: Phase 9 REVIEW — scan recent build-loop runs for recurring patterns, auto-draft experimental skills/agents with A/B tracking, notify user. Use after Phase 8 REPORT, or user-invokable with `/build-loop:self-improve` to trigger a scan outside a build.
version: 0.1.0
user-invocable: true
---

# Build-Loop Self-Improvement (Phase 9 REVIEW)

This skill runs after Phase 8 REPORT completes, or on demand. It detects recurring patterns across recent build-loop runs, drafts experimental skills/agents to address them, and notifies the user for keep/remove decisions.

**Principle:** auto-draft, notify, experiment, decide based on evidence. User can always remove. A/B comparison is small and focused — one metric, short sample, clear decision rule.

## When This Skill Runs

- Automatically at end of every build-loop run (Phase 9, after Phase 8 REPORT)
- On demand via `/build-loop:self-improve`
- Skipped if `.build-loop/state.json.runs` has fewer than 3 entries — not enough signal

## Flow

```
┌──────────────────────────────────────────────────────────────┐
│ Phase 9: REVIEW (this skill)                                  │
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
{"event": "created", "date": "2026-04-19T14:22:00Z", "artifact": "experimental-middleware-typegen", "baseline_metric": "Phase 5 pass rate on middleware edits", "baseline_value": 0.6, "target_value": 0.9, "sample_size_target": 5}
```

The experimental skill's description triggers it on matching runs. Each subsequent run that matches the skill's trigger appends to this file:

```jsonl
{"event": "applied", "date": "...", "run_date": "2026-04-20", "triggered": true, "metric_value": 1.0, "outcome": "phase_5_pass"}
```

After `sample_size_target` applied entries, Phase 9 computes delta and emits a decision recommendation (promote / remove / extend sample).

### 6. Notify user (concise synthesis)

Emit exactly this format to the Phase 8 report tail:

```
## Phase 9: Self-Improvement Review

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
## Phase 9: Self-Improvement Review
Scanned N runs. No recurring patterns crossed confidence threshold. Nothing created.
```

## Data Contracts

### `.build-loop/state.json.runs[]` extensions (writer: build-orchestrator during Phase 8)

Phase 8 REPORT must now append a run entry to `state.json.runs[]` before Phase 9 runs. Schema:

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

Append-only log per experimental artifact. One `{"event": "created", ...}` entry, then `{"event": "applied", ...}` for each triggered run. Decision entry `{"event": "decision", "verdict": "promote|remove|extend", ...}` when sample complete.

## Promotion Rule (after sample complete)

| Delta | Action |
|---|---|
| Metric improves ≥ target | Propose promote: move from `.build-loop/skills/experimental/` to `.build-loop/skills/active/` (user confirms via AskUserQuestion) |
| Metric regresses | Remove automatically; log to `discarded.jsonl` with evidence |
| Metric flat (±10%) | Extend sample to 2N before deciding |

Promotion to the plugin repo (cross-project) is a separate step requiring explicit user request. `/build-loop:promote-experiment <name>` is the entry point (not yet implemented — tracked in follow-up).

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
