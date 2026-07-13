# Repository Maintenance Safety Protocol

Use this protocol for source consolidation, artifact cleanup, structural changes, and closeout.

## 1. Establish authority and ownership

- Read repository instructions before commands.
- Separate authorization for local integration, generated-artifact cleanup, sibling retirement, push, remote deletion, deployment, force-push, and recovery-tag deletion.
- Inspect live peer claims and files in flight. Continue read-only analysis when ownership overlaps.

## 2. Inventory live truth

Capture:

- canonical repo root, local `main`, upstream, and ahead/behind counts;
- all linked worktrees, branches, detached heads, locks, dirt, and prune markers;
- stashes including base, untracked parent, message, and changed paths;
- open merge, rebase, cherry-pick, revert, or bisect state;
- generated/build roots, sizes, ages, ignore status, and active process references;
- sibling repositories and the exact in-tree prefixes or consumers they overlap;
- canonical build, test, bundling, and parity gates.

Review code and trees directly. Notes, branch names, and old source-of-truth documents can be stale.

## 3. Decide repository boundaries

Use product coupling as the primary criterion:

| Signal | Prefer one repo | Prefer separate repos |
| --- | --- | --- |
| Shipping | only bundled with parent | independently released |
| Consumers | one parent product | multiple independent consumers |
| Verification | same required gate | independent gates and compatibility matrix |
| Change cadence | normally changes together | independently versioned |
| Ownership | same owner/security boundary | distinct owners or security/availability boundary |

For a consolidation, prove history and content separately:

```bash
git merge-base --is-ancestor <source-head> main
git log --format=fuller --merges --all
git ls-tree -r main -- path/to/in-tree/source
git range-diff <candidate-range-a> <candidate-range-b>
```

An imported source head plus a newer in-tree implementation means the sibling is historical. Do not merge it back over the evolved canonical tree. Audit its branches, stashes, and dirt for unique work first.

## 4. Protect recoverability

Create annotated tags under `archive/pre-closeout-YYYY-MM-DD/` for:

- pre-integration `main`;
- every branch head considered for deletion;
- every stash commit considered for removal;
- any superseded source head that will lose an active checkout.

After tagging a stash, inspect its parents. A stash with untracked files normally has a third parent. Reflogs are not retention policy.

## 5. Classify build and generated roots

Treat isolated build roots as intentional concurrency infrastructure until proven otherwise. A cleanup candidate must be:

- reproducible from tracked source and declared dependencies;
- ignored by Git;
- unused by an active process or current worktree;
- outside protected coordinator/final-build and canonical cache paths;
- older than the repository's retention threshold.

Do not delete Xcode, IDE, package-manager, or shared caches outside the repository unless explicitly included in scope. After pruning in-repo DerivedData, refresh any `buildServer.json`, compilation database, index, or generated path pointer that names the removed root.

## 6. Integrate and evolve safely

- Prefer a clean reconciliation worktree and fast-forward integration.
- Enumerate consumers before changing a repository boundary, protocol, package path, or generated artifact.
- Keep unrelated dirty material out of product commits.
- Run targeted verification after each risky change.
- Run canonical verification on the final integration tip and again on exact final `main` after the last mutation.
- Verify bundled or vendored outputs against canonical source after regeneration.

## 7. Clean in dependency order

1. verified clean auxiliary worktrees;
2. branches contained by final `main`;
3. patch-equivalent branches with recovery refs and recorded proof;
4. archived stashes;
5. stale worktree metadata;
6. authorized ignored artifact roots;
7. obsolete sibling checkout only after unique-state audit.

Avoid `git branch -D` until normal deletion fails and equivalence proof exists. Never use `git reset --hard` to clean user-owned state.

## 8. Verify final truth

Re-run the maintenance audit and report distinct states:

- canonical source: identified/not identified;
- structural consolidation: complete/not needed/blocked;
- committed: yes/no;
- integrated into local `main`: yes/no;
- canonical `main` clean: yes/no;
- worktrees/branches/stashes closed: yes/no;
- generated-artifact policy satisfied: yes/no;
- pushed: yes/no;
- deployed or runtime-tested: yes/no/not applicable.

Keep recovery tags until a separate retention review explicitly removes them.
