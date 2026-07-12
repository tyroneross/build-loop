# Run-aware closeout implementation handoff

When implementing F-01, read ADR-01 in `docs/plans/2026-07-11-run-aware-closeout.md`, then satisfy T-01, T-05, T-06, T-07, and T-08 before any destructive path is considered complete.

When implementing F-02, preserve the rule that Stop records open ownership but never owner release; satisfy T-02, T-03, T-09, and T-10.

When implementing F-03, gate the canonical `run-closeout` phase post on live terminal receipt evidence and satisfy T-04/T-13/T-14. Direct Rally handoff resolution remains bypassable until backlog item `BUILDLOOP-COORD-001` adds native enforcement; document that narrower limitation without calling it enforced.

Recovery anchor: `.build-loop/bundles/pre-selfmod-20260712T011700.bundle` was created and verified before self-modification.

Final gate: targeted tests, acceptance rerun, `self_mod_verify.py --auto-revert`, and an independent audit must all pass before merge.
