# Build-loop next steps — 2026-07-11 (for independent audit)

Status: proposed by Claude (session 11701681), pending adversarial audit.
Base: main @ 673a7fa (unreleased on top of v0.36.3 tag lineage).

## Context

Landed today (e5162fc..673a7fa, pushed): heredoc-aware git-command classifier
replacing the `*git*push*` substring hook trigger (6+ false-fires/day observed);
temporal-membership preflight so retro/audit tooling cannot attach another run's
transcript/verdict (fabrication incident 2026-07-11); worktree-isolation
doctrine + lint widened to commit-less file-editing writers (8h zombie-writer
incident 2026-07-11). All behind self-mod gate passes and an independent-auditor
`yay` after one correction round; 152 tests on the fix surface.

## Proposed steps, priority order

1. **Release 0.36.4.** Five behavior-changing commits are live only for
   checkout-tracking consumers. Notes + version bump + tag. Also delete the
   stale `.build-loop/release-pending.md` (references 0.33.0, staged
   2026-06-11 — bump long since landed; dead marker read by version advisor).
2. **Drain 10 open enforce-candidates, routed by owning repo.**
   - build-loop: crash-orphan run-record reconciliation (runs[] logged
     `outcome: fail` for a shipped, audited, merged run); retro signal-counting
     bounded to the run's commit window; Rally claims-refresh helper script
     (8+ manual calls per lane kickoff); lane-intent snapshot at kickoff;
     backlog `BUIL-SCRIPTS-kx9a4v240d4pz` (self_mod_verify full-scope glob
     misses nested-package tests).
   - agent-rally-point (not build-loop): claim-lease TTL vs multi-hour lanes;
     facts retention too short for next-day forensics.
   - ptyd (not build-loop): daemon activity-stamp fix.
   - Reject-with-reason the 4 typed-session candidates their own author flagged
     invalid (mined from a wrong transcript pre-fix).
3. **Triage June-era backlog items** (10+ CI/coordination/architecture entries
   predating several refactors): done / obsolete / still-real pass.
4. **Auto-trigger retrospectives for non-Claude-hosted runs.** The 2026-07-10
   Codex-hosted run closed with no retro; the day-late retro found the
   fabrication bug. With temporal-membership enforced, auto-fire at run close
   is safe; codifies lessons→improvements without a human asking.
5. **A/B verify today's mechanisms in the wild** over the next runs: zero hook
   false-fires expected (baseline ~6/day); wrong-run attachments must surface
   as explicit absence markers; isolation lint must flag commit-less background
   editors.
6. **Longer arc:** Advisor v2 (Phase 1 Assess through the dispatch ladder);
   remove deprecated navgator-bridge/debugger-bridge stubs after 0.36.4 ships
   (the promised one-release cycle).

## Audit questions for the reviewer

- Is the priority order right? (Release-first vs candidates-first.)
- Is anything mis-routed across the three repos?
- Are steps 4 and 5 sufficient to claim the lessons→improvements loop is
  closed, or is there a missing control?
- What's missing entirely?
