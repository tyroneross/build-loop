---
name: repo-closeout
description: "Audit and evolve repository structure safely across the full maintenance lifecycle: repository topology and scope, application and build-system profiles, module and folder boundaries, canonical source-of-truth and monorepo decisions, sibling consolidation, generated/build artifact retention, worktree and branch hygiene, local-versus-remote state, and verified closeout into local main. Use when asked how any kind of repo should be structured or maintained, what application/build profile it has, why it has duplicate source or many build directories, whether repositories or modules should split or merge, to clean generated artifacts, to review open commits/worktrees/branches/stashes, or to merge and close completed local work. Never push, delete unique source, or remove recovery refs unless explicitly authorized."
user-invocable: true
---

# Repository Maintenance and Closeout

Optimize the repository for the product's real boundaries, then keep it recoverable as worktrees, builds, generated state, and branches evolve. Treat closeout as one phase of maintenance, not the whole workflow.

Resolve `REPO_CLOSEOUT_ROOT` to the directory containing this `SKILL.md` before running bundled scripts. Under Claude Code this is normally `${CLAUDE_PLUGIN_ROOT}/skills/repo-closeout`; under Codex or another host, derive it from the loaded skill path.

## Establish the product and repository contract

1. Read applicable `AGENTS.md`, architecture, build, coordination, and document-lifecycle guidance.
2. Check live coordination before editing shared files or refs. Treat notes as provenance, not code proof.
3. Identify the product, shipped artifacts, consumers, release boundary, owners, and canonical build/test commands.
4. Resolve the canonical Git root, local `main`, upstream, linked worktrees, and nested or sibling repositories.
5. Run the read-only baseline:

```bash
python3 "$REPO_CLOSEOUT_ROOT/scripts/audit_repo_closeout.py" \
  --repo "$PWD" --base main --json
```

Read [references/safety-protocol.md](references/safety-protocol.md) before changing structure, refs, worktrees, stashes, or generated directories.

For repository-boundary, module, or folder-layout work, read [references/repository-taxonomy.md](references/repository-taxonomy.md). For application-specific, build-system, cache, or generated-layout work, read [references/stack-profiles.md](references/stack-profiles.md). Classify signals before recommending a target structure.

## Classify the repository without forcing one label

1. Record observed application, language, build-system, workspace, runtime, ownership, and release signals.
2. Classify portfolio strategy, repository scope, composition, release coupling, runtime deployment, internal organization, and physical layout independently.
3. Label heuristic results as inferred with confidence. Directory names and manifests are not product-boundary proof.
4. Separate current state from target state and name the smallest transition that addresses measured cost.
5. Preserve framework conventions unless changing them has a concrete product, ownership, security, release, build, or navigation benefit.

Do not flatten `workspace`, `product monorepo`, `service polyrepo`, and `orchestration repo` into one topology enum. They describe different dimensions. Do not treat agent configuration such as `.claude/` or `.codex/` as product architecture.

## Decide the source of truth from product boundaries

Prefer one repository when a component:

- ships only with the parent product;
- changes and verifies in the same release gate;
- has no independent consumers, version, or ownership boundary;
- must stay code-identical to a bundled or generated artifact.

Keep a separate repository when it has an independent public contract, release cadence, consumers, operational boundary, or ownership/security boundary. Do not preserve a repository split merely because history started that way.

Compare a sibling source repository to its proposed in-tree prefix:

```bash
python3 "$REPO_CLOSEOUT_ROOT/scripts/audit_repo_closeout.py" \
  --repo "$PWD" --base main \
  --compare-repo /path/to/source-repo \
  --compare-prefix path/inside/current/repo --json
```

If the sibling head is already an ancestor of `main` and the in-tree prefix has since diverged, the usual disposition is `retire-sibling`, not another merge. Treat comparison output as source-tree evidence only; it is never sufficient retirement authorization. Audit sibling branches, worktrees, stashes, operations, and dirty paths separately before retirement. Preserve unique state under recovery refs.

## Keep structure navigable without ceremonial refactors

- Give each shipped capability one canonical source and a thin stable public boundary.
- Organize internal files by capability or bounded context; use small, well-named modules behind the stable boundary.
- Keep generated, vendored, cached, and source-owned paths distinguishable.
- Verify that every generated or bundled artifact has one reproducible writer and a parity gate.
- Use revisit/churn and agent cost as structure signals. Do not claim that generic cleanup alone improves correctness.
- Require a concrete product, ownership, dependency, or verification reason for deep-module or repository-boundary changes.
- Split modules only when a boundary enforces a public contract, dependency direction, ownership/security rule, independent test/release, real reuse, or measured build benefit. Merge them back when scaffolding and coordinated edits dominate.

## Control build and cache accumulation

Per-worktree or per-agent build roots are valid isolation. Accumulation without retention is repository hygiene debt. Inventory before deleting:

```bash
python3 "$REPO_CLOSEOUT_ROOT/scripts/audit_repo_closeout.py" \
  --repo "$PWD" --base main --artifacts --stale-days 7 \
  --protect-artifact build --protect-artifact build-rust --json
```

Classify each artifact root as `protected`, `active`, `recent-cache`, `cleanup-candidate`, or `review-tracked-or-unignored`. Treat the script's `cleanup-candidate` result as a stale ignored review candidate; the script does not prove reproducibility. Its current inventory covers top-level roots, so inspect declared nested caches separately. Remove only reproducible, ignored, inactive artifacts within the user's authorized scope. Protect the coordinator/final build, active worktree builds, canonical dependency caches, and anything required for current verification.

Add a repository retention policy when isolation creates named build roots: naming convention, protected roots, active-process test, age threshold, cleanup owner, and the command that refreshes build-server metadata after pruning.

## Evolve work safely

1. Use an isolated worktree for material changes when shared-checkout collision is possible.
2. Map the change to its capability boundary and enumerate cross-repo consumers before changing contracts or paths.
3. Run the narrow verifier during implementation and the canonical verifier on the final integration tip.
4. Regenerate bundled outputs only from canonical source; verify code or artifact parity afterward.
5. Collapse completed work back to one protected local `main`; remove temporary worktrees and branches only after ancestry or patch-equivalence proof.

## Close completed local work

Give every worktree, branch, stash, dirty path set, sibling source, and artifact root exactly one disposition:

- `integrate` — unique completed work with review and verification.
- `redundant` — ancestor of `main` or proven patch-equivalent.
- `retire-sibling` — imported source whose in-tree canonical copy has evolved.
- `cleanup-candidate` — reproducible, ignored, inactive generated state past retention.
- `preserve-only` — incomplete, unrelated, user-owned, or historical material.
- `blocked` — active ownership, failed verification, conflicts, or unclear provenance.

Before mutation, create dated annotated recovery tags for pre-integration `main`, every branch head, and every stash commit under `archive/pre-closeout-YYYY-MM-DD/`. Preserve dirty state including untracked files. Prefer fast-forward integration; re-run the canonical verifier on exact final `main` after the last mutation.

Clean in dependency order: auxiliary worktrees, contained branches, patch-equivalent branches with recovery proof, archived stashes, stale worktree metadata, then authorized generated artifacts. Never use `git reset --hard` or force-delete unique state as cleanup.

## Report distinct truths

Lead with the disposition, then state:

1. canonical source and repository-boundary decision;
2. final local `main` commit and verifier evidence;
3. worktrees, branches, stashes, sibling repos, and artifacts integrated, retained, or removed;
4. recovery namespace and residual risks;
5. local-versus-upstream divergence;
6. committed, merged locally, pushed, deployed, and runtime-tested status separately.

Do not report “merged” for work that exists only on another branch. Do not report “clean” while unique sibling, stash, dirty, or generated state remains unclassified.
