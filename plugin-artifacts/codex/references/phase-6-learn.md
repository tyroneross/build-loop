<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 6: Learn (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Contains the full Learn phase: pattern detection, experimental skill drafting, and sample review sweep.

## Phase 6: Learn — Cross-Build Pattern Detection (mandatory; always runs and always reports)

**One executable path:** `python3 scripts/learn/__main__.py run --workdir "$PWD" --run-id <recorded-run-id> --source review-g --json`. It writes `.build-loop/learn/<run-id>.json`, updates `runs[].learn`, and returns durable `work_orders[]` for agent-only drafting/review. Dispatch those roles and attach results with the CLI's `attest` command. `status: complete` is the only completed Phase 6 state.

**Goal**: detect recurring patterns across recent runs, auto-draft experimental skills/agents to address them, surface them for keep/remove decisions. Closes the loop between "build N times" and "build N+1 is faster because we learned."

**Load the `build-loop:self-improve` skill for the full protocol.** (Skill keeps its existing name for backward compatibility; this phase was named "Self-Improvement Review" in v0.2.0 — renamed here to avoid collision with Phase 4 Review.)

**Mandatory contract (v0.39.0+).** Every Phase 6 runs the deterministic detector and consolidation stages, persists their receipt, and emits its `learn_line` in Review-G. This path uses no LLM when nothing crosses threshold. Sonnet drafting and promotion review stay conditional and appear as explicit work orders. Also user-invokable via `/build-loop:self-improve` after recording a manual run id.

Quick flow:

1. **Run** — the executable runner reads four bounded sources: `runs[]`, retro enforce-candidates, learning objects, and tool traces. It consolidates memory, detects and deduplicates patterns, caps new work at two, and performs the sample sweep.
2. **Read** — no patterns means no agent dispatch. Agent-only work appears in `work_orders[]` with the exact role and payload.
3. **Dispatch** — call only returned roles. Architects draft experimental skills or agents. Implementers realize enforcement specifications. Promotion reviewers judge drafted or sample-eligible artifacts.
4. **Attest** — attach repository-relative artifacts and reviewer verdicts with the CLI. Pending or failed work keeps Phase 6 open.
5. **Close** — emit `learn_line`, enforce `run_close_lint.py --require-learn`, and leave promotion to explicit `/build-loop:promote-experiment` confirmation.

**Always-run + report gating (v0.30.0)**

Phase 6 has NO "skip entirely" condition. Three outcome states cover every run:

| State | Trigger | What runs | Review-G `## Learn` line |
|---|---|---|---|
| **Accruing** | `runs[] < 3` | Detector + consolidation only (no Sonnet draft) | `Learn: accruing (N/3 runs)` |
| **Deferred** | debug-only (`closeout: false` in dispatch envelope) OR budget-exhausted (`budget_check` envelope `action == "finalize_and_stop"` at Phase 6 entry) | Detector + consolidation; write `.build-loop/proposals/learn-deferred-<run-id>.md` marker with `{reason, runs_count, budget_action}`; skip Sonnet draft + Opus signoff | `Learn: deferred — <reason>` |
| **Full** | `runs[] >= 3` AND detector returned a pattern AND not deferred | Detector + consolidation + Sonnet draft + Opus signoff + sample sweep | `Learn: <N> patterns drafted` (or `Learn: 0 patterns above threshold (N runs scanned)` when detector returned nothing) |

**Deprecated escape hatch (migration no-op).** `.build-loop/config.json.autoSelfImprove: false` is no longer honored. It is read for migration safety: when present and `false`, the orchestrator appends a one-line `state.json.warnings[]` entry (`"autoSelfImprove: false is deprecated; ignored (migration no-op)"`) and proceeds as if the key were absent. Old user configs do not error. Remove the key at your convenience.

**User control (unchanged safety boundary)**:
- Remove any artifact: `rm -rf .build-loop/skills/experimental/<name>/` or `active/<name>/`
- Block re-promotion of a name: add it to `.build-loop/skills/.demoted`
- Inspect tracking: `cat .build-loop/experiments/<name>.jsonl`
- Promotion to `active/` STILL requires explicit `/build-loop:promote-experiment <name>` (decision-3 safety boundary preserved — auto-promote of unreviewed drafts never happens).
- Auto-promote defaults to OFF — set `"autoPromote": true` to enable (requires effective sample ≥ 8).

- Consumer default — learned drafts route to `~/.build-loop-extensions/pending/` via `scripts/extensions_route.py --name <ext-slug> --file <draft>`; they do not load until `scripts/extensions_approve.py` moves them into `plugin/`. (Maintainer routing: P2.)

**Retrospective finding capture — ownership (2026-08-29).** build-loop is the DEFAULT owner of the retrospective-finding-filing flow (`scripts/retrospective/file_findings.py`; see `AGENTS.md` §"Retrospective finding capture" for the exact `plan`/`apply`/`lint` commands and filing ladder). The `ai-assistant` and `ambient agent` projects may also invoke or guide this same flow, but only through those same `file_findings.py` commands — never a parallel reimplementation. This pointer lives in build-loop's own docs only: `ai-assistant` and `ambient agent` are NOT edited by this change.

**What this phase will NOT do**:
- Modify the build-loop plugin repo
- Promote artifacts cross-project without explicit `/build-loop:promote-experiment <name>`
- Run more than once per build
