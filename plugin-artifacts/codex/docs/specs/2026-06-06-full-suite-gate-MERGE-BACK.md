<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Merge-back ledger — full-suite gate (2026-06-06)

Written at closeout (anti-sprawl). One canonical folder, one branch, collapsed to `main` as the
single final decision. Companion to `docs/specs/2026-06-06-full-suite-gate-design.md`.

## Parent principle
- **Canonical checkout:** `~/dev/git-folder/build-loop` (local). On `main`, fast-forwarded from
  `e08655f` → `29a6458` (v0.30.1). No push.
- **Single integration target:** `main`. No per-approach / long-lived branches after the run.

## Plan
1. Spec already committed to `main` at `e08655f` (the build-loop run brief is the authoritative
   intent file).
2. Run-worktree `worktree-agent-ae7d5197be2e80aaf` at `.claude/worktrees/agent-ae7d5197be2e80aaf/`
   carried the three implementation commits.
3. Fast-forward merge to `main` (linear history, no merge commit needed — three commits land in
   order on top of `e08655f`).
4. Remove the harness-isolated worktree folder + delete its branch ref.
5. **No deploy / no push** — local main only.

## Ref status

| Ref / worktree | Purpose | Merge target | Status | Closed |
|---|---|---|---|---|
| `worktree-agent-ae7d5197be2e80aaf` (harness-isolated run branch) | Part B export + Part C gate + version bump (3 commits: `15e49cd`, `b154a71`, `29a6458`) | `main` | **merged via fast-forward** | 2026-06-06 |
| `.claude/worktrees/agent-ae7d5197be2e80aaf/` (harness worktree) | container for the run | (removed at closeout) | **removed** | 2026-06-06 |

## Closeout summary

- 3 commits landed on the harness-isolated branch and were fast-forwarded to `main`:
  - `15e49cd` — fix(write_run_entry): export EXECUTION_SCHEMA_VERSION + valid sets from package
  - `b154a71` — feat(review): pytest-collection gate at Review-B (closes full-suite blindspot)
  - `29a6458` — chore(version): bump to 0.30.1 — full-suite collection fix + Review-B gate
- Worktree removed; branch ref deleted.
- Final `git worktree list` shows only the canonical checkout.
- **NO push to `origin/main`** (per brief). User decides marketplace deploy as a separate restart-boundary step.

## Verification recap (against spec acceptance)

- **Part B:** `env -u PYTHONPATH .venv/bin/python -m pytest scripts/ tests/ --collect-only` → exit 0,
  0 collection errors (down from 1 ImportError at `tests/test_run_entry_execution_state.py:19`).
- **Part C:** `scripts/pytest_collect_gate.py` exists, wired into Review-B (both
  `agents/build-orchestrator.md` and `skills/build-loop/references/phase-4-review.md`).
  Regression test `scripts/test_pytest_collect_gate.py` has a deliberately-broken-import sandbox
  asserting exit 1 + status="fail" + finding points at the broken module; clean tree asserts
  exit 0 + status="pass". 9/9 tests pass.
- **Version bump:** 6 manifests + 1 test pin (`tests/test_phase_6_gating_docs.py::EXPECTED_VERSION`)
  all in sync at `0.30.1`; manifest-parity test green.
