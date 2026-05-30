<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Self-Review — Periodic Autonomous Self-Improvement

Single source of truth for the build-loop self-review subsystem: purpose, light/deep model, config schema, launchd schedule, graceful degradation, and the headless apply prompt.

---

## Purpose

Self-review mines recent build-loop runs for recurring issues and efficiency signals, produces a human-readable digest, and (in deep mode) automatically applies safe improvements via the normal build-loop pipeline. It closes the loop between observations captured during builds and durable fixes that land in the repo — without requiring the user to manually schedule or trigger a review.

Two cadences balance cost against coverage:

| Mode | Default cadence | Scope | Auto-apply |
|---|---|---|---|
| **light** | Daily (09:00) | Issues in the last 7 days; proposal cap 10 | No — digest + queue only |
| **deep** | Weekly (Sunday 03:00) | Issues in the last 30 days; uncapped | Yes — SAFE proposals auto-routed through build-loop |

---

## Config schema

Add a `selfReview` block to `.build-loop/config.json` (all fields optional; defaults shown):

```json
{
  "selfReview": {
    "enabled": true,
    "autonomy": "apply_push",
    "light": "daily",
    "deep": "weekly"
  }
}
```

| Field | Values | Meaning |
|---|---|---|
| `enabled` | `true` / `false` | `false` disables the launchd jobs and is a no-op for the installer |
| `autonomy` | `apply_push` \| `apply_local` \| `propose` | `apply_push` — apply SAFE items and push main; `apply_local` — apply but do not push; `propose` — queue only, no auto-apply |
| `light` | `daily` \| `weekly` \| `disabled` | Cadence for the light job |
| `deep` | `daily` \| `weekly` \| `disabled` | Cadence for the deep job |

Cadence values map to launchd `StartCalendarInterval`. `daily` → every day at the job's configured hour. `weekly` → once per week on the configured weekday. `disabled` skips installing that job.

---

## How it works

### Data-gathering layer (scripts/self_review.py — frozen)

`python3 scripts/self_review.py --mode {light|deep} [--workdir <repo>] [--days N] [--dry-run] --json`

Always exits 0. Writes:
- `.build-loop/self-review/<UTCdate>-<mode>.md` — human digest
- `.build-loop/proposals/self-review-*.md` — one file per candidate improvement, each with `classify_hint: SAFE|RISKY|DECISION` frontmatter

Returns JSON to stdout:
```json
{
  "mode": "light|deep",
  "window_days": 7,
  "mined": {"corrections": [], "rituals": [], "sequences": []},
  "efficiency_findings": [],
  "self_simplification": [],
  "digest_path": ".build-loop/self-review/2026-05-29-deep.md",
  "queued": [".build-loop/proposals/self-review-foo.md"],
  "errors": [],
  "dry_run": false
}
```

**`self_simplification[]`** (deep mode, self-recursive only): a list of proactive simplification findings for build-loop's own code. Each entry has the shape:

```json
{
  "target": "self",
  "file": "<relative path>",
  "finding": "<one-line description>",
  "classify_hint": "SAFE|RISKY|DECISION",
  "proposed_action": "<what to do>"
}
```

`target: self` marks a proposal as targeting build-loop's own code (plugin repo or `build-loop-memory`). These proposals are subject to the SELF-MODIFICATION SAFETY GATE (see §"Self-modification of the restricted repo" below) — they are never processed by the standard reactive-fix path.

Deep mode digests include an `## Apply plan` section that separates SAFE-to-auto-apply items from RISKY-to-surface items. When `self_simplification[]` is non-empty, the digest also includes a `## Self-simplification proposals` section listing each `target: self` finding.

### Scheduling layer (launchd)

`python3 scripts/install_self_review.py install` writes two plists to `~/Library/LaunchAgents/`:

- `com.tyroneross.buildloop.selfreview-light.plist` — daily at 09:00, invokes `scripts/self_review_run.sh light`
- `com.tyroneross.buildloop.selfreview-deep.plist` — weekly Sunday at 03:00, invokes `scripts/self_review_run.sh deep`

Both jobs write output to `.build-loop/self-review/launchd-{light,deep}.log`.

### Wrapper layer (scripts/self_review_run.sh)

Invoked by launchd (or manually). Runs the gatherer, saves the JSON snapshot to `.build-loop/self-review/last-<mode>.json`, appends a timestamped line to `.build-loop/self-review/run.log`, and (for deep mode with `autonomy` in `{apply_push, apply_local}`) invokes the headless apply prompt via `claude -p`.

---

## Install / uninstall / status

```bash
# Install both launchd jobs
python3 scripts/install_self_review.py install

# Remove launchd jobs and plists
python3 scripts/install_self_review.py uninstall

# Check loaded/not-loaded
python3 scripts/install_self_review.py status

# JSON output for any subcommand
python3 scripts/install_self_review.py status --json
```

Or via the slash command: `/build-loop:self-review --install` / `--uninstall` / `--status`.

---

## Graceful degradation

If the `claude` CLI is not installed or not on PATH when the deep wrapper fires, the wrapper logs the skip reason and exits 0. The digest and queued proposals are still produced — the user can process them manually via `/build-loop:run` or `/build-loop:self-review deep`.

The queue accumulates across skipped deep runs. On the next successful headless invocation, all queued SAFE proposals are processed.

---

---

## Self-modification of the restricted repo

The self-review/self-heal loop is authorized to write to the restricted repo — build-loop's own plugin repo and the `build-loop-memory` durable repo (lessons and skills). This is normally a guarded action because it edits the running runtime.

**The SELF-MODIFICATION SAFETY GATE is MANDATORY and non-negotiable for ANY change to build-loop's own code.** It is the load-bearing safety for this authorization.

### Gate protocol (every self-modification commit must pass all steps)

**Step 1 — Bundle first (reversibility):**
```bash
git bundle create .build-loop/bundles/pre-selfmod-$(date +%Y%m%dT%H%M%S).bundle --all
```
Always bundle before any self-modification. This is the rollback point.

**Step 2 — Self-recursive / per-commit mode:**
Self-modifications use the existing per-commit mode machinery (one commit at a time, reviewed before the next). `selfRecursive.enabled` is `true` when the working directory IS build-loop's own repo. Do not batch multiple self-modification commits without a gate pass between each.

**Step 3 — Verify gate (MANDATORY before commit):**
```bash
python3 scripts/self_mod_verify.py \
  --workdir "$PWD" \
  --scope auto \
  --changed-files <space-separated file list> \
  --auto-revert \
  --json
```
`--scope auto` runs mapped tests for small changes and broader tests for multi-file/core changes (practical default). Use `--scope full` for the slow exhaustive option (900s, parallel when pytest-xdist is present).

`verdict: pass` → the self-modification may commit.
`verdict: fail` → the gate AUTO-REVERTS the change and writes the finding to `.build-loop/proposals/needs-human/`. The change is NEVER committed. Never retry a failed gate verdict without human review.
`verdict: needs_human` / `meta_modification: true` → see §"Meta-modifications (always human-gated)" immediately below. STOP — queue to `.build-loop/proposals/needs-human/`, never auto-apply, even if tests passed.

**Step 4 — SAFE-only auto-apply:**
Only changes that `classify_action.py` classifies as SAFE auto-apply through this path. Structural or architectural self-modifications — new phase, changed dispatch contract, agent-role change, new mandatory gate — surface as DECISION and are never auto-applied. They are queued to `.build-loop/proposals/needs-human/` for explicit user action.

### Meta-modifications (always human-gated)

**Meta-modifications are always human-gated.** A self-modification that touches the gate's own inputs or control surfaces — test files, `self_mod_verify.py`, `classify_action.py`, `autonomy_gate.py`, `self_review.py`, the self-improvement loop scripts, or deploy/permission logic — is a META-MODIFICATION. The gate returns `verdict: needs_human`; it is surfaced as DECISION for human review and NEVER SAFE-auto-applied, regardless of test passage. Rationale: a self-improver that can edit the tests or gate that measure it can fabricate a green result (observed in Darwin-Gödel Machine; the reward-tampering failure mode in Anthropic's "Sycophancy to Subterfuge"). The eval bar scales with blast radius: `--scope auto` runs mapped tests for small changes, broader tests for multi-file/core changes; `full` is bounded (900s, parallel when pytest-xdist is present).

The deep-run host agent MUST treat `verdict: needs_human` / `meta_modification: true` as a STOP — queue to `.build-loop/proposals/needs-human/`, never auto-apply, even if the tests passed.

### Authorized targets

- Build-loop's own plugin repo (files under the build-loop working directory when `selfRecursive.enabled`)
- `build-loop-memory` durable repo: lessons, skills, and project-scoped memory entries written via `scripts/memory_writer.py`

### What is NOT authorized without user confirmation

- Promotion of project-local skills to `~/.claude/skills/` (global scope — confirm first)
- Changes to `agents/build-orchestrator.md` phase contracts or MECE ownership rules (structural — DECISION)
- META-MODIFICATIONS: any change to `scripts/self_mod_verify.py`, `scripts/classify_action.py`, `scripts/autonomy_gate.py`, `scripts/self_review.py`, the self-improvement loop scripts, test files for any of these, or deploy/permission logic — the gate returns `verdict: needs_human` for these regardless of test passage (see §"Meta-modifications (always human-gated)")

---

## APPLY PROMPT

This is the exact prompt text fed to `claude -p` by `scripts/self_review_run.sh` for deep auto-apply. Do not paraphrase — the wrapper reads this file and extracts the block between the `BEGIN_APPLY_PROMPT` and `END_APPLY_PROMPT` markers.

<!-- BEGIN_APPLY_PROMPT -->
You are running headlessly as part of a scheduled build-loop self-review. Your job is to apply safe improvements identified by the periodic self-review.

Step 1 — Read the context:
- Read `.build-loop/self-review/last-deep.json` (the data-gatherer output).
- For each path listed in `queued[]`, read the proposal file. Take only proposals where `classify_hint: SAFE`.
- Skip any proposal where `classify_hint` is `RISKY` or `DECISION` — leave those files untouched.
- Separately, collect every entry in `self_simplification[]` where `classify_hint: SAFE` AND `target: self`. These are proactive self-simplifications of build-loop's own code and require the SELF-MODIFICATION SAFETY GATE in Step 2b.

Step 2a — Apply each SAFE non-self proposal:
For each SAFE proposal without `target: self`, route it through `/build-loop:run` with a goal derived from the proposal's title and body. Build-loop handles apply + commit + collapse. Its own autonomy gate and deployment policy govern what lands — do not bypass them.

Step 2b — Apply SAFE `target: self` proposals (self-modification path):
For each SAFE `target: self` proposal, apply the change, then — BEFORE committing — run the MANDATORY SAFETY GATE:
```bash
python3 scripts/self_mod_verify.py \
  --workdir "$PWD" \
  --scope auto \
  --changed-files <the files you changed> \
  --auto-revert \
  --json
```
- `verdict: pass` → commit the change via the normal per-commit mode (one commit per self-modification; do not batch).
- `verdict: fail` → the gate has already auto-reverted the change. Move the proposal to `.build-loop/proposals/needs-human/` and continue with the next proposal. NEVER commit a failed-gate self-modification.
- `verdict: needs_human` / `meta_modification: true` → the changed files touch the gate's own inputs or control surfaces. STOP — do not commit, do not retry. Move the proposal to `.build-loop/proposals/needs-human/`. This is true even if all tests passed — fabricated passes are the exact failure mode this gate prevents.
- Skip any `target: self` proposal that `classify_action.py` does not classify as SAFE (RISKY/DECISION → queue to `.build-loop/proposals/needs-human/`, do not apply).

Step 3 — Determine push behavior:
Read `.build-loop/config.json` key `selfReview.autonomy` (default `apply_push`).
- If `apply_push`: after all SAFE proposals have been routed and landed, run `git push origin main` for the accumulated commits. Build-loop's deployment_policy still gates this; if the push is blocked by policy, log the block and exit 0 — do not force.
- If `apply_local`: do not push. Commits land locally; the user pushes manually.

Step 4 — Report:
Print a short summary: how many SAFE proposals were processed (split: standard vs `target: self`), how many RISKY/DECISION items were left queued, how many `target: self` proposals were gated/reverted, and the push status (pushed / local-only / skipped by policy / n/a).

Constraints:
- Never apply a RISKY or DECISION proposal autonomously.
- Never apply a `target: self` proposal without running `self_mod_verify.py` first.
- Never commit a change that `self_mod_verify.py` returned `verdict: fail` for.
- Never auto-apply when `self_mod_verify.py` returns `verdict: needs_human` or `meta_modification: true` — queue to `.build-loop/proposals/needs-human/`, even if tests passed.
- Never bypass build-loop's commit auditor or autonomy gate.
- If build-loop is not available as a slash command, log the unavailability and exit 0 — the queue stays intact.
- This is a local developer tool; there are no users to protect other than the repo owner.
<!-- END_APPLY_PROMPT -->
