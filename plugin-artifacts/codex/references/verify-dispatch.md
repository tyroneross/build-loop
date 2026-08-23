<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
<!-- PROVENANCE: folded from skills/verify-dispatch/SKILL.md (v0.1.0) on 2026-07-02 (pool-consolidation Inc 2). Reactive-selection trigger preserved in agents/build-orchestrator.md §"Verify every subagent" + skills/build-loop/SKILL.md; this reference holds the checklist body. -->

# Verify dispatch — post-dispatch verification checklist

Walk this after any dispatched Agent, Task, or orchestrator sub-agent (including background/`run_in_background` dispatches and headless build-loop orchestrators) claims commits landed and tests passed — especially when the safety classifier was unavailable or when you would say "classifier unavailable". Also applies when the user says "verify the subagent", "did the agent actually commit", or "confirm the dispatch worked". A solicited peer agreeing after you asked it to check is NOT independent verification — use this checklist instead.

## When this fires / Why

**Standing rule:** "verify every subagent." A dispatched agent's report is a claim, not evidence. Three recurring failure modes:

- The safety classifier is unavailable, so the agent's self-report goes unchecked.
- A solicited peer reviews your work after you asked it to — that's anchoring, not independent validation (memory: `feedback_solicited_peer_review_is_not_independent.md`).
- An auditor was supposed to run but was substituted by inline self-audit (memory: `feedback_buildloop_verify_auditor_ran.md`).

Ground truth comes from commands you run yourself, not from prose the agent returned.

## 6-Step Verification Checklist

Run these yourself. Do not echo the agent's report back as your own finding.

### 1 — Confirm the commit hashes exist on the claimed branch

```bash
git log --oneline -n 5
git rev-parse HEAD
git branch --contains <hash>
```

The hashes the agent named must appear in the log. The branch must be the expected one. If HEAD is on the wrong branch, flag it before reading anything else.

### 2 — Working tree is clean (modulo known runtime churn)

```bash
git status --short
```

Acceptable noise: `.rally/log/`, `.build-loop/state.json`, build artefacts declared in `.gitignore`. Anything else — unexpected staged files, leftover edits, index residue from a parallel agent — is a scope breach or index corruption; name it explicitly.

### 3 — Run the test suites yourself; do not trust the report

Pick the command that matches the repo:

```bash
# Rust
cargo test

# Python (prefer uv; system python may be broken)
uv run --with pytest python -m pytest

# Node / TypeScript
npx tsc --noEmit && npx jest --passWithNoTests
```

Capture and report real pass/fail counts and any error output. "The agent said tests passed" is not a verification; this step is.

### 4 — Confirm cross-repo parity fixtures are byte-identical

When the build involves copied or synced artifacts (e.g. native skill copies from a sibling repo, fixture pairs, generated schema files):

```bash
diff <canonical-source> <copy-in-this-repo>
# or
sha256sum <file-a> <file-b>
```

A hash or diff mismatch means the sync did not complete correctly even if the agent reported success.

### 5 — Report your findings with evidence

State the outcome in this form:

```
✅ verified by: git log (commit abc1234 on branch X), cargo test (47 passed, 0 failed), git status clean
⚠️ untested: <what you could not check and why>
```

Never emit "the agent confirmed it passed" as your own verification line. Name which commands you ran and what they returned. If a step was skipped, say why.

### 6 — Re-execute the literal command behind each headline verification claim

Steps 1–3 verify the *repo*. This step verifies the *claim*. A subagent's headline usually names the command it says it ran and the outcome it says it got — re-run that exact command and compare.

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/verification_claim_probe.py" \
    --report-file <the subagent's returned report> --markdown
```

The probe extracts command-shaped claims (a backticked command sitting next to a verification verb — *verified*, *confirmed*, *reproduced*, *ran*, *tested*, *proved*, *exit N*, *N passed*), re-runs each one, and labels it:

- **`executed:`** — we ran it and every stated expectation held.
- **`contradicted:`** — we ran it and an expectation failed. **Exit code 1.** This is a real finding; it goes in the report, not in a footnote.
- **`cited:`** — we did not run it. Either no expectation was stated to check against, or the command is not safely re-executable.

**Exit 2 = `nothing_executed`.** Zero claims extracted, or every claim refused, or every claim ran with no expectation to check — the probe verified nothing. Treat exit 2 as "unverified", never as "clean"; a run we could not observe is not a pass.

The safety layer is an **allowlist, not a deny-list**, because the commands come from LLM-authored report text and you cannot enumerate what a model might emit. Only verification-shaped heads run (`pytest`, `python3`, `node`, `npm test`, `cargo test`, `go test`, `swift test`, `ruff`, `mypy`, `tsc`, `eslint`, `jest`, `vitest`, `jq`, …), any shell metacharacter or redirection refuses outright, and a `git` command is delegated to `scripts/audit_git.py`'s classifier so the two scripts cannot drift on what counts as safe. Everything else is `cited`. Denied commands are **never executed** — the probe must not become the thing that writes to a live store.

Carry the `executed:` / `contradicted:` / `cited:` label into the report for every relayed claim. An unlabeled claim is `cited:` by default — you read it, you did not verify it.

**Why this step exists.** 2026-08-07: four subagent reports arrived with the safety classifier unavailable. One reported a security fix as *"Fixed — guard refuses the live store; verified by reproducing the auditor's exact attack (exit 2, store still 0)."* Running that exact command against the shipped code gave **exit 0 and 49 entries written into the user's live store**. The guard required its flag only on the branch where the path was omitted; naming the path explicitly walked straight past it. Steps 1–3 would all have passed — the commits existed, the tree was clean, the suite was green. Only re-running the claim's own command caught it.

### 7 — Confirm the run record landed in the workdir you dispatched into

A dispatched orchestrator that returns a polished report has not necessarily closed its run. Assert the `runs[]` mutation yourself, against the workdir you dispatched INTO (not your own session's cwd — they differ whenever you dispatch into a sibling repo, a plugin subdirectory, or a worktree):

```bash
# The envelope named a run_id — assert that exact record:
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_close_lint.py" \
  --workdir <dispatch-target-workdir> --run-id <run_id> --require-orchestrator --json

# No run_id in the envelope (the common shape when Review-G was skipped entirely):
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_close_lint.py" \
  --workdir <dispatch-target-workdir> --expect-recent-minutes --require-orchestrator --json
```

Exit 1 means the run is not closed and Phase 6 Learn cannot see it. Do not accept the completion: either re-dispatch the orchestrator's Review-G run-close step, or write the record yourself from the envelope's own contents using the printed `remediation` command. A `no_state` status is the loudest case — the target workdir has no `.build-loop/state.json` at all, so that run produced no durable artifact of any kind (check whether the orchestrator actually worked in the workdir you think it did).

Worked evidence (2026-07-16): six sequential dispatched `build-orchestrator` agents in `ObsidianVault/.obsidian/plugins/daily-planner` each completed with a high-quality report and wrote no `runs[]` entry, retrospective, milestone, or feedback line; the only rows in the vault's `state.json` came from Stop hooks. Every report read as success, so nothing surfaced until a retrospective counted the missing records a day later. Step 6 is a single command that would have caught the first one. The orchestrator-side half of this contract is the Review-G assertion in `references/phase-4-review.md` §"Run-close assertion" — that one catches a write that was attempted and failed; this one catches a run that never reached Review-G.

## Auditing verdict / classification claims (DONE · PASS · verified)

When a dispatched agent returns *verdicts* — "DONE", "PASS", "already implemented", "complete", "verified" — audit each verdict against the cited evidence, not against its title. An over-optimistic DONE hides a real gap far more often than a REJECT does; the failure mode is **claiming a nearby mechanism satisfies the requirement when it only partially does** ("adjacent" and "partial" read as DONE).

Run the audit as a **second, adversarial pass — a different model where possible** (e.g. Codex when the harness was Claude), prompted to REFUTE, not confirm:

- For every DONE / PASS, open the cited `path:line` and confirm the named control **actually satisfies the requirement** — not merely that a related file exists or a similar mechanism is nearby.
- Default to skepticism on DONE; a verdict carrying no `path:line` evidence is unverified by definition.
- Spend the adversarial budget on the DONE claims; sanity-check the rest (ADOPT genuinely not-yet-done, DEFER not actually adoptable).
- Report corrections as `<id> · claimed <verdict> · actually <truth> · correct <verdict>` with evidence `path:line`.

Worked evidence (2026-07-08, this repo): a Codex audit of a 13-item triage corrected **3 of 9 "DONE" verdicts to ADOPT** (partial/adjacent), each confirmed against `stop_closeout.py` / `session_end_retro_sweep.py`; a separate pre-push Codex audit caught **3 over-optimistic passes** (fire-scope over-firing on runs, a `tempfile` name the scratch-guard missed, a SessionEnd hook the plugin didn't actually ship). A single self-classification pass would have shipped all six.

## What this does NOT replace

- **runtime-parity-verification** — that skill cross-checks a running app's UI against backend state. This checklist covers the git/test layer only.
- **plan-verify** — that skill lints a plan's evidence claims before Phase 2 acceptance. This checklist fires after a dispatch reports completion.

Origin lessons: `feedback_solicited_peer_review_is_not_independent.md`, `feedback_buildloop_verify_auditor_ran.md`, `feedback_verify_running_app_not_compile_green.md`, `feedback_duplicate_claim_pivot_to_verifier.md`.
