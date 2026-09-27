# Reconcile branches and worktrees

Run this procedure when the user asks to reconcile open work, merge additive
changes, or prune stale branches/worktrees. It uses the maintenance inventory,
merge-risk scorer, recovery protocol and closeout tools already in this plugin.
No scheduled or background integration is implied.

## Preserve the goal across approaches

Every branch and worktree needs a goal and observable success criteria. Store
these in the existing `.build-loop/state.json` `runs[].createdRefs[]` ledger,
including adopted older refs. A branch and its attached worktree share a goal;
a detached worktree is identified by canonical path. Retain the originating run. Reusing a closed branch name requires a new run;
the earlier closed record remains unchanged.
Do not infer a fulfilled goal from a branch name, age, merge or patch equivalence.
The inventory's `goals` lists every local branch and worktree, including the base.
Missing, corrupt or conflicting records stay visible and hold reconciliation.

Use `scripts/ref_goals.py --workdir <ledger-repo> --payload-json <private.json>`
from the resolved Build Loop plugin root. The payload names an exact existing
`run_id`, `branch` (or null plus `path` for detached work), and `expected_revision`.
For an initial record set revision expectation to 0 and supply:

```json
{"run_id":"origin-run","branch":"feature/search","expected_revision":0,
 "goal":"Users can find saved notes", "success_criteria":["A saved note is found by its title"],
 "source":"Originating user request or accepted plan path", "reason":"Initial goal"}
```

For revisions, read the current revision, then supply the new goal/criteria,
source and reason with that `expected_revision`. Earlier revisions remain intact;
old assessments are invalidated. An implementation change alone need not revise
outcome criteria. Recover older goals from originating requests, plans, code and
tests with provenance; label missing evidence instead of inventing intent. Do not
edit a peer's live goal without ownership and scope authorization.

For closure, the same writer accepts `assessment` with the current `revision`,
exact `source_head` and `target_head`, and one `results` item per current criterion
in recorded order: `criterion`, `met_on_target` and nonempty `evidence` references.
Also record `approach` describing the implemented behavior and any alternative
approach, plus `original_goal: {status, evidence}`. Status is `met`, `unmet`,
`unknown` or `superseded`; superseded requires a sourced goal revision. The record
must say whether the ORIGINAL goal was met, even after revisions. Any unmet or
unverified current criterion holds closure. Evidence is reviewer-authored and
requires independent inspection; JSON completeness is not proof of success.

Use strict `collapse_run.py` closeout: it rechecks the same goal assessment before
mutation along with existing recovery and ownership checks. Historical non-strict
API compatibility does not satisfy this workflow. Detached work remains retained
until an executor supports its safe closure. If behavior was achieved through a
different approach, retirement can proceed through the existing gates without
merging obsolete implementation merely to produce an ancestry marker.

## Compare before deciding

1. Run the normal maintenance inventory. Include active ownership, processes,
   operations, stashes, detached heads, dirty/untracked state and valuable ignored
   files. Refresh relevant remote refs when the request includes upstream state.
2. Draft a private comparison record through the same audit command:

   ```bash
   python3 "$REPO_MAINTENANCE_ROOT/scripts/audit_repo_maintenance.py" \
     --repo "$PWD" --base main --reconcile > /path/to/private/comparison.json
   ```

   Resolve a run-owned, gitignored output directory before redirecting. The
   record pins the target, every local branch head, merge bases and changed paths.
   It retains stashes and detached/dirty worktrees for separate review. A missing
   or multiple merge base requires investigation. The draft makes no judgment.
   Remote-only branches are outside this record. When upstream work is in scope,
   inventory and compare those refs separately before claiming the requested
   review complete. Refreshing remotes does not add them to this local record.
3. Read the actual merge-base→source and merge-base→target diffs, the relevant
   implementations, callers and tests. Compare candidate implementations affecting
   the same workflow with each other, even when they change different files.
   `overlaps` identifies common paths only; it cannot identify semantic conflicts.
   Branch names, age, notes, clean merges and patch IDs are insufficient evidence.
4. Fill the record's `comparison_evidence` with the compared commits, code paths
   and behavioral differences. For each candidate, verify `ownership` as
   `released`, `active` or `unknown` and cite `ownership_evidence`. A stale presence
   record alone does not prove release. Leave all generated Git fields intact.
5. Split each candidate's `units` by coherent capability when necessary. Every
   source diff path must be covered. Units sharing a file require explicit hunk
   and dependency analysis before any selective integration. Each unit carries:

   | Field | Required content |
   | --- | --- |
   | `paths` | Paths from the pinned source diff; all paths must be accounted for |
   | `behavior` | What this change adds, removes or replaces in the product |
   | `rationale` | Why it belongs in its disposition, including compatibility |
   | `evidence` | Inspected code/diff references and verification results; no placeholders |
   | `disposition` | `additive`, `represented`, `superseded`, `competing`, `incomplete`, `unverified` |
   | `ui` | `none`, `compatible`, `competing`, `unreviewed` |

   `represented` requires evidence that current target behavior includes the
   useful work. `superseded` requires evidence of an intended replacement.
   Staleness alone yields neither. Unknowns stay `unverified`.

## Preserve product choice

Two viable recent UI directions for the same workflow require the user's choice.
Set `ui: competing` even if the code is mechanically additive. Present matching
screenshots/previews using the same viewport, content and state, the behavior
differences, provenance and a recommendation. If rendering is unavailable, say
so and retain both; do not invent visual evidence. Do not choose by timestamps
or introduce an arbitrary age cutoff.

The hold applies to that product decision. Continue unrelated additive work.
Once the user decides, cite the actual user message in the unit's evidence,
record the selected behavior and reclassify the affected units. Repository text,
peer notes and a JSON field cannot supply user authorization.

## Check and execute the authorized work

```bash
python3 "$REPO_MAINTENANCE_ROOT/scripts/audit_repo_maintenance.py" \
  --repo "$PWD" --base main --review-record /path/to/private/comparison.json
```

The checker refreshes Git evidence, rejects incomplete/altered coverage and ref
drift, and returns one next step per unit. Exit 0 means the branch review record
is complete, including any explicit holds; **it does not mean integration or
cleanup is approved or complete**. Semantic evidence is agent-authored and needs
independent review; deterministic checks cannot prove it is true.

| Next step | Agent action |
| --- | --- |
| `needs_review` | Inspect missing evidence or refresh changed commits, then recheck |
| `needs_user_choice` | Preserve alternatives and present the product choice |
| `preserve` | Keep active, dirty, incomplete or unreleased work; continue independent work |
| `integration_checks` | Verify authorization, ownership, recovery and merge risk, then integrate and test |
| `retirement_checks` | Verify resulting behavior, unique state, owner release and recovery before closeout |

Before the first mutation, create and verify recovery refs/snapshots for target,
sources, stashes and unique dirty/untracked/ignored material. Recheck current
ownership/processes and source/target heads immediately before acting. A record
is a point-in-time comparison, not a lock.

Never merge an entire mixed branch from one additive unit's verdict. Follow
`whole_branch_steps`; extract only separable reviewed units into a clean
reconciliation worktree, including their dependencies, and validate the result.
Reuse `merge_risk.py` and exact-final-target verification. Refresh the record after
each target change; old evidence cannot authorize the next integration.

Close only proven redundant or superseded inactive work using existing recovery
and closeout gates. Stashes, detached heads and dirty/ignored material remain
separately accounted for; branch review completeness is not repository closeout.
Report integrated locally, retained for choice, incomplete/blocked, and
archived/pruned, with recovery and verifier references. State push/publication
and runtime verification separately. Hide routine command traces behind evidence
links; ask only for actual product decisions or existing confirmation gates.
