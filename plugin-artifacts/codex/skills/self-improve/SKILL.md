---
name: build-loop:self-improve
description: Run mandatory Phase 6 Learn, then dispatch only returned experimental-draft or review work orders. Also handles "scan recent runs" or "improve build-loop". Not for a deliberate whole-project retrospective (use `recursive-retrospective`).
version: 0.1.0
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Build-Loop Self-Improvement (Phase 6 Learn)

This skill runs after Review-G completes, or on demand. The deterministic runner detects recurring patterns and returns explicit work orders only when judgment is required.

**Principle:** auto-draft, notify, experiment, decide based on evidence. User can always remove. A/B comparison is small and focused — one metric, short sample, clear decision rule.

## When This Skill Runs

- Automatically at end of every build-loop run (Phase 6 Learn, after Review-G records the run)
- On demand via `/build-loop:self-improve`
- Accruing if `.build-loop/state.json.runs` has fewer than 3 entries; the phase still writes a receipt and mines toward the threshold

## Entry point

Every host uses `python3 scripts/learn/__main__.py run --workdir "$PWD" --run-id <recorded-run-id> --source manual --json`. On-demand scans first create `<recorded-run-id>` with `scripts/append_run.py`; they never invent a receipt detached from `runs[]`.

The command performs deterministic work and returns `work_orders[]` only when an agent role is needed. Dispatch the named role with the order payload, then record the result through `python3 scripts/learn/__main__.py attest --workdir "$PWD" --run-id <run-id> --work-order-id <id> --status complete [--artifact <path>] [--verdict <verdict>] --json`. The receipt must reach `status: complete`.

## Flow

```
┌──────────────────────────────────────────────────────────────┐
│ Phase 6 Learn: REVIEW (this skill)                                  │
├──────────────────────────────────────────────────────────────┤
│ 1. RUN      → deterministic Learn runner                      │
│              emits receipt + bounded work_orders[]            │
│ 2. FILTER   → keep only confidence:high or count >= threshold │
│ 3. DRAFT    → returned self-improvement work orders only      │
│              writes .build-loop/skills/experimental/<name>/   │
│ 4. SIGNOFF  → returned promotion-reviewer work order          │
│              records approve, revise, or discard              │
│ 5. TRACK    → record baseline in .build-loop/experiments/     │
│ 6. NOTIFY   → synthesize 3-5 line summary to user             │
│              (include removal command + A/B plan)             │
└──────────────────────────────────────────────────────────────┘
```

## Steps

### 1. Run and read the receipt

```
Input: `.build-loop/learn/<run-id>.json`
Output: deterministic stage results plus bounded `work_orders[]`
```

If `work_orders[]` is empty, emit `learn_line` and close. This path uses no LLM.

### 2. Trust the runner boundary

The runner owns detection, filtering, deduplication, and the two-pattern cap. Do not repeat these decisions in the caller.

### 3. Dispatch returned work

Dispatch only roles returned in `work_orders[]`, using the included payload. A `self-improvement-architect` may write `.build-loop/skills/experimental/<name>/SKILL.md` or `.build-loop/agents/experimental/<name>.md`. An `implementer` may realize a returned enforcement specification.

The architect agent includes an A/B Experiment section in every artifact it writes, and runs `python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/stamp_skill_frontmatter.py" --apply <written-path>` immediately after the write. A drafted skill is `user-invocable: false`; the harness computes `userInvocable ?? true`, so an unstamped draft would be publicly invocable the moment it lands somewhere loadable. If the architect returns without a `compliant`/`stamped` stamper status, re-run the command yourself before step 4.

### 4. Attest and review

Attach the repository-relative draft with `attest`. The runner then creates a `promotion-reviewer` order. That reviewer decides:

- **APPROVE** — artifact is coherent, pattern is real, A/B plan is measurable → proceed to track
- **REVISE** — core idea is right, execution needs tightening
- **DISCARD** — pattern is noise or artifact is unusable → delete the file, log to `.build-loop/experiments/discarded.jsonl` with reason

Attach the verdict with `attest`. Pending or failed orders keep the receipt open. The host's normal model resolver selects any agent model.

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

Emit exactly this format to the Review-G report tail:

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

### 7. Episodic memory consolidation (Phase 4 wiring)

After steps 1-6 complete, run the memory-consolidation pass. This is the
hook that wires recurring-pattern detection into the four-memory-types
framework — when the orchestrator sees the same root cause N times, it
becomes both an experimental skill (above) AND a procedural-memory
candidate (below).

Run in this order:

```bash
# Promote any pending semantic candidates into agent_memory.semantic_facts
# (no-op if .semantic/_candidates.jsonl is missing)
python3 scripts/consolidate_memory.py --workdir "$PWD"

# Surface recurring root_causes as procedural candidates
# (writes .procedural/_candidates.jsonl entries crossing the 3-incident threshold)
python3 scripts/procedural_governance.py --workdir "$PWD" --mode detect-patterns
```

Both are safe to re-run; both no-op when nothing qualifies. The first
fans out the auto-capture batch sweep results into the indexed
`semantic_facts` table; the second draws from the same
`state.json.runs[]` that step 1 just scanned, so the procedural
candidates align with the experimental skills drafted in steps 3-5.

Auto-drafting of procedures (`procedural_governance.py --mode auto-draft`)
remains gated until 5 hand-authored procedures exist in `.procedural/`
— the third phase of the procedural learning curve from design ref §14.

If consolidation surfaces a CONFLICT, the orchestrator surfaces the
count in the Phase 6 summary — never auto-resolves.

## Data Contracts

### `.build-loop/state.json.runs[]` extensions (writer: build-orchestrator during Review-G)

Review-G must append a run entry to `state.json.runs[]` before Phase 6 Learn runs. Schema:

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

### Promotion exposure statement (required in every promotion confirmation)

Promotion is the moment an experimental artifact stops being a scratch file: it becomes tracked in git AND loadable. Neither effect shows up in the artifact's own diff, so the confirmation has to say them out loud.

**Run the stamper on the DESTINATION path before asking the user** — every promotion target, no exceptions:

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/stamp_skill_frontmatter.py" --apply <destination>/SKILL.md
```

Destinations that require it: `.build-loop/skills/active/<name>/`, `~/.claude/skills/<name>/`, and `<plugin-repo>/skills/<name>/`. A non-zero exit blocks the promotion — never ask the user to confirm a move whose resulting surface is unknown.

**Then include this block verbatim in the `AskUserQuestion` body, the PushNotification body, and the `.build-loop/proposals/<name>.pending.md` marker:**

```
Exposure after promotion
  Destination:      <destination path>
  user-invocable:   <false | true>
  Directly invocable by you:
      <no — reached only through build-loop routing>
      <YES, as /<namespace>:<name> — because <the file's public-justification: line>>
  Loaded in:        <this project only | every session, every project>
  Git:              promotion moves the artifact out of the gitignored
                    `.build-loop/skills/experimental/**` tier, so it appears in
                    `git status` for the first time and becomes committable.
```

`user-invocable: true` is only answerable when the file carries a `public-justification:` field — without one the stamper has already refused the promotion, so the question never reaches the user.

### Promotion rules

When `autoPromote` is true AND `sample_size_target >= 8` AND the experiment's applied entries are all `confounded: false` (see §Confound tracking below):

| Delta vs baseline | Action | Location |
|---|---|---|
| Metric improves ≥ target (non-confounded) | **Auto-promote**: `git mv .build-loop/skills/experimental/<name> .build-loop/skills/active/<name>`, update SKILL.md frontmatter `experimental: false` + `promoted_at: <ISO>`, run `python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/stamp_skill_frontmatter.py" --apply .build-loop/skills/active/<name>/SKILL.md` (non-zero exit aborts the promotion and leaves the artifact in `experimental/`), emit the §Promotion exposure statement block for the user confirmation, then append `{event: "promoted", ...}` to the experiment's jsonl. The `git mv` is what makes the artifact tracked — `experimental/**` is gitignored, `active/**` is not. | `.build-loop/skills/active/<name>/` |
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

The command reads the experiment's track record across this and other projects (if global `~/.build-loop/experiments/` index exists), checks the artifact quality, stamps the destination SKILL.md, asks the user for confirmation carrying the §Promotion exposure statement block, and commits to the plugin repo on a feature branch for user review. Full protocol: `.agents/skills/source-command-promote-experiment/SKILL.md`.

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
- Will accrue when state.json has < 3 runs; deterministic Learn still runs and records its receipt
- Will not retry pattern detection more than once per run
- Will not write skills for patterns with confidence "low"

## Agent dispatch (this skill)

| Step | Dispatch rule |
|---|---|
| 1. Detect | Deterministic runner; no agent or LLM |
| 3. Draft | Dispatch only a returned `self-improvement-architect` work order |
| 4. Signoff | Dispatch only a returned `promotion-reviewer` work order |
| 6. Notify | Emit the receipt's deterministic `learn_line` |

The host resolves any returned agent role through its normal model policy. This protocol assigns no vendor-specific model.
