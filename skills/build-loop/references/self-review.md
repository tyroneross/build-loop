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
  "digest_path": ".build-loop/self-review/2026-05-29-deep.md",
  "queued": [".build-loop/proposals/self-review-foo.md"],
  "errors": [],
  "dry_run": false
}
```

Deep mode digests include an `## Apply plan` section that separates SAFE-to-auto-apply items from RISKY-to-surface items.

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

## APPLY PROMPT

This is the exact prompt text fed to `claude -p` by `scripts/self_review_run.sh` for deep auto-apply. Do not paraphrase — the wrapper reads this file and extracts the block between the `BEGIN_APPLY_PROMPT` and `END_APPLY_PROMPT` markers.

<!-- BEGIN_APPLY_PROMPT -->
You are running headlessly as part of a scheduled build-loop self-review. Your job is to apply safe improvements identified by the periodic self-review.

Step 1 — Read the context:
- Read `.build-loop/self-review/last-deep.json` (the data-gatherer output).
- For each path listed in `queued[]`, read the proposal file. Take only proposals where `classify_hint: SAFE`.
- Skip any proposal where `classify_hint` is `RISKY` or `DECISION` — leave those files untouched.

Step 2 — Apply each SAFE proposal:
For each SAFE proposal, route it through `/build-loop:run` with a goal derived from the proposal's title and body. Build-loop handles apply + commit + collapse. Its own autonomy gate and deployment policy govern what lands — do not bypass them.

Step 3 — Determine push behavior:
Read `.build-loop/config.json` key `selfReview.autonomy` (default `apply_push`).
- If `apply_push`: after all SAFE proposals have been routed and landed, run `git push origin main` for the accumulated commits. Build-loop's deployment_policy still gates this; if the push is blocked by policy, log the block and exit 0 — do not force.
- If `apply_local`: do not push. Commits land locally; the user pushes manually.

Step 4 — Report:
Print a short summary: how many SAFE proposals were processed, how many RISKY/DECISION items were left queued, and the push status (pushed / local-only / skipped by policy / n/a).

Constraints:
- Never apply a RISKY or DECISION proposal autonomously.
- Never bypass build-loop's commit auditor or autonomy gate.
- If build-loop is not available as a slash command, log the unavailability and exit 0 — the queue stays intact.
- This is a local developer tool; there are no users to protect other than the repo owner.
<!-- END_APPLY_PROMPT -->
