---
name: verify-dispatch
description: Use after any dispatched Agent, Task, or orchestrator sub-agent (including background/run_in_background dispatches and headless build-loop orchestrators) claims commits landed and tests passed — especially when the safety classifier was unavailable or when you would say "classifier unavailable". Also fires when the user says "verify the subagent", "did the agent actually commit", or "confirm the dispatch worked". A solicited peer agreeing after you asked it to check is NOT independent verification — use this checklist instead.
version: 0.1.0
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# verify-dispatch

## When this fires / Why

**Standing rule:** "verify every subagent." A dispatched agent's report is a claim, not evidence. Three recurring failure modes:

- The safety classifier is unavailable, so the agent's self-report goes unchecked.
- A solicited peer reviews your work after you asked it to — that's anchoring, not independent validation (memory: `feedback_solicited_peer_review_is_not_independent.md`).
- An auditor was supposed to run but was substituted by inline self-audit (memory: `feedback_buildloop_verify_auditor_ran.md`).

Ground truth comes from commands you run yourself, not from prose the agent returned.

## 5-Step Verification Checklist

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

## What this does NOT replace

- **runtime-parity-verification** — that skill cross-checks a running app's UI against backend state. This skill covers the git/test layer only.
- **plan-verify** — that skill lints a plan's evidence claims before Phase 2 acceptance. This skill fires after a dispatch reports completion.

Origin lessons: `feedback_solicited_peer_review_is_not_independent.md`, `feedback_buildloop_verify_auditor_ran.md`, `feedback_verify_running_app_not_compile_green.md`.
