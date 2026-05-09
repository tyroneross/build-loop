# Handoff: Orchestrator Line-Budget Refactor

When implementing F-01, read ADR-001 and satisfy T-01.

## Implementer notes

1. **Branch first.** Cut a feature branch from `origin/main`: `git checkout -b refactor/orchestrator-line-budget origin/main`. Do not work on `main` directly — concurrent-session squash risk per `feedback_buildloop_parallel_commit_race.md`.

2. **Commit 1 — Extract two protocols.**
   - Read `agents/build-orchestrator.md` lines 161-191 (Phase 3 commit step) and 192-250 (Phase 3 halt-and-ask branch). Capture them verbatim into the two new reference files.
   - Each new reference starts with: `# <Title>\n\n_Linked from `agents/build-orchestrator.md` §<section>._\n\n` then the lifted body.
   - DO NOT modify the agent file in this commit — extraction is additive only.

3. **Commit 2 — Extend phase-gate-checklist.**
   - Append a `## Phase 1 Assess detail (18-step)` section to `references/phase-gate-checklist.md` with the current Phase 1 detail from the agent file lines 91-127.
   - Keep numbering 1-18.

4. **Commit 3 — Compress agent to ≤200 lines.**
   - Replace lines 161-191 with: a 3-line bullet stating "single-writer git contract; full procedure in `references/single-writer-commit-protocol.md`."
   - Replace lines 192-250 with: a 3-line bullet stating "halt-and-ask backstop for architectural-class decisions; full procedure in `references/halt-and-ask-protocol.md`."
   - Replace the Phase 1 Assess body with: 6-7 bullets of the highest-level signals + a "Full 18-step protocol: `references/phase-gate-checklist.md`."
   - Replace Model Tiering & Escalation body with: 5-line table summary + "Full provider substitution table: `references/model-tier-mapping.md`."
   - Run `wc -l agents/build-orchestrator.md` after the edits — target ≤180 for headroom; ≤200 hard.

5. **Commit 4 — Update test.**
   - Edit `tests/test_orchestrator_skeleton.py` `REQUIRED_REFERENCES` list to include `halt-and-ask-protocol.md` and `single-writer-commit-protocol.md`.
   - Run `uv run pytest tests/test_orchestrator_skeleton.py -q` — must be 6/6 green.

6. **Final verification.**
   - `uv run pytest -q` — confirm only the 4 known pre-existing failures remain (line-budget no longer in the list, the other 3 unrelated).
   - `python3 scripts/plan_verify.py docs/plans/2026-05-09-orchestrator-line-budget-refactor.md --json` — confirm 0 BLOCKER.

## Rollback recipe

`git revert <merge-commit>` on `main`. The 2 new reference files are additive — they become orphans (re-linkable later). The agent file reverts to 376 lines, test fails again, but no behavior changes.

## Why this is safe

- All 4 commits are doc-only.
- Test suite locks every wiring point that matters.
- Reference-file pattern is already established (12 existing files).
- Concurrent-session risk mitigated by branching from `origin/main` and pushing promptly.
