# Independent audit — 2026-07-11

> Historical audit packet. Retained for provenance; verify current release state from live manifests, tests, and git history.

Verdict: revise-with-changes

**1. Release-first is acceptable only after a release-state reconciliation gate; the plan's stated base is false and its release recipe is incomplete.**

Evidence: the plan calls `673a7fa` "unreleased on top of v0.36.3 tag lineage" and proposes notes, a version bump, and a `0.36.4` tag (`docs/plans/2026-07-11-next-steps-plan.md:4`, `:18-21`). Live remote evidence shows tags/releases only through `v0.36.1` (`git ls-remote --tags origin 'refs/tags/v0.36*'`; `gh release list`), while `e5162fc` is merely a commit titled `release: 0.36.3`. The checked-in surfaces also disagree: `.claude-plugin/plugin.json:3` is `0.36.3`, `package.json:3` is `0.36.1`, and `pyproject.toml:7` is `0.12.16`. The repository's own verifier requires `package.json`, both plugin manifests, marketplace metadata, and the Codex artifact to match one target (`scripts/verify_release_surface.py:115-147`), and the publish workflow rejects a tag that differs from `package.json` (`.github/workflows/publish-npm.yml:35-41`). Revise step 1 to: establish whether `0.36.2/0.36.3` were intentionally plugin-only or failed releases; choose the canonical next version; update every enforced surface and lockfile; run the release verifier; then tag/release. Deleting `.build-loop/release-pending.md` is valid cleanup because it still names `0.33.0` (`.build-loop/release-pending.md:1-6`), but deletion belongs after the replacement release record is verified.

**2. The candidate count is correct, but the plan conflates disposition work with implementation and needs explicit ownership packets.**

Evidence: Easy Terminal contains 12 candidate files; two are already implemented (`audit-fixes-2026-07-10-01.md:41-50`, `typed-session-integration-20260710-05.md:17-31`), leaving 10 open. Four transcript-mined candidates are content-free sequence patterns (`typed-session-integration-20260710-01.md:5-13` through `-04.md:5-13`) and should be rejected with the shared wrong-transcript reason. Routing is otherwise substantively correct: claim TTL is Rally infrastructure (`audit-fixes-2026-07-10-02.md:7-22`); the daemon activity stamp is explicitly a ptyd change requiring Rust test, rebundle, coordination, and E2E verification (`audit-fixes-2026-07-10-03.md:7-21`); lane intent/run capture and transcript-window bounds are build-loop retrospective controls (`audit-fixes-2026-07-10-04.md:7-20`, `bl-20260711T050542Z-codex-575901-02.md:5-7`). Revise step 2 into ten rows with candidate path, owning repo, disposition, acceptance evidence, and owner. Keep `BUIL-SCRIPTS-kx9a4v240d4pz` in the backlog lane rather than counting it as an enforce-candidate; it is a separate open P2 item (`.build-loop/backlog/items/BUIL-SCRIPTS-kx9a4v240d4pz.md:1-25`).

**3. Steps 4-5 do not close the lessons-to-improvements loop because they cover capture and observation, not enforced disposition and outcome attribution.**

Evidence: the current design already generates retrospective candidates for human review and explicitly never auto-promotes them (`skills/build-loop/SKILL.md:225`); Phase 6 only treats recurring candidate signatures from at least two run IDs as signals (`skills/build-loop/references/phase-6-learn.md:19`). Auto-triggering a retrospective (`docs/plans/2026-07-11-next-steps-plan.md:36-39`) closes the capture gap. Watching subsequent runs (`:40-43`) provides observations. Neither step guarantees every candidate reaches adopt/reject/defer, assigns an owner/deadline, verifies the adopted control on the real failing input, or records whether the change improved its named metric. Add a durable candidate lifecycle gate: generated -> triaged -> owned -> implemented/rejected with reason -> counterfactual regression verified -> post-change metric attributed. The gate should flag aged undisposed candidates and failed/missing verification; otherwise this same set can accumulate indefinitely while the plan claims closure.

**4. The A/B step lacks executable measurements and mixes three different validation designs.**

Evidence: step 5 names expected outcomes but gives no sample size, observation window, event source, denominator, owner, or stop/rollback threshold (`docs/plans/2026-07-11-next-steps-plan.md:40-43`). "Zero false-fires" is a rate comparison against the claimed `~6/day` baseline; wrong-run attachment is a negative-control fixture that should be deterministically regression-tested; isolation lint is a positive fixture whose exact commit-less editor input must trip the lint. Specify these separately. Record telemetry for real-world false-fire rate, retain deterministic real-input fixtures for the two safety controls, and require a defined number of runs/days before accepting the field result. Without this, "A/B" is not reproducible and cannot support a closure claim.

**5. Backlog triage is correctly prioritized after release safety, but the stated scope omits active P1 work and a release-blocking decision surface.**

Evidence: step 3 narrows attention to "June-era" CI/coordination/architecture items (`docs/plans/2026-07-11-next-steps-plan.md:34-35`). The live backlog also has an open P1 observability item created July 6 (`.build-loop/backlog/items/BUIL-OBSERVABILITY-kwv7emf5fwt2x.md:1-24`), an open P1 claim-reaper item (`BUIL-COORDINATION-kwbmd87dagzfk.md:1-24`), and a gated branch disposition decision (`BUIL-PLUGIN-EXTENSIONS-kwbmd89xfxmnn.md:1-24`). Revise the triage criterion from age/category to all active items ordered by priority, gate, and evidence freshness. Also regenerate the derived backlog index after dispositions so stale open counts do not survive the pass.

**6. The plan omits verification of its strongest contextual claims.**

Evidence: the five commits in `git log e5162fc..673a7fa` do support the classifier, temporal-membership, and isolation themes (`422a5c1`, `54268bd`, `e45cfa6`, `93fe6c8`, `673a7fa`). The plan additionally asserts `6+ false-fires/day`, an eight-hour incident, self-mod gate passes, an independent-auditor `yay`, and 152 tests (`docs/plans/2026-07-11-next-steps-plan.md:8-14`) without naming durable artifacts for those numbers/verdicts. Add exact run IDs, judge-decision paths/Rally artifact IDs, gate outputs, and test commands. Commit titles establish that code landed; they do not establish those quantitative or review claims.

## Required revised order

1. Reconcile release/version truth and run the complete pre-release verifier.
2. Release the chosen canonical version if every gate passes.
3. Materialize the ten candidate dispositions as owned cross-repo work packets; reject the four invalid candidates immediately.
4. Triage all active backlog items by priority/gate/evidence freshness.
5. Implement host-neutral retrospective triggering plus the candidate lifecycle/aging gate.
6. Run deterministic real-input regression fixtures and the separately specified field-measurement window.
7. Proceed to Advisor v2 and bridge removal only after the actual shipped release satisfies the promised-cycle condition.
