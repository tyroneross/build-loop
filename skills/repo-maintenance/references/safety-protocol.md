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

### 7b. Worktree removal: archive first, the guard enforces it

`git status --porcelain -uall` omits git-IGNORED files, and every removal path deletes them: `git worktree remove` (with or without `--force`), `rm -rf`, `trash`, `safe-del`. On 2026-10-23 an agent removed 83 "merged, clean" Easy Terminal worktrees after that check and destroyed unrecoverable `.build-loop/`, `.bookmark/`, `.designdoc/` and `.ibr/` state in 55 of them, including a Groundwork project a live server was serving.

Before removing any worktree, run:

```
python3 <build-loop>/scripts/worktree_archive.py --path <worktree> [--path <worktree> ...]
```

It tars tracked changes, untracked files and every non-cache ignored entry into `<repo>.worktrees/_preserved/<name>-<UTC>.tar.gz` (`--dest` overrides), lists pure caches (`node_modules/`, `target/`, `.build/`, `__pycache__/`, build output) without archiving them, and writes a receipt (path, HEAD, branch, file list with sizes, archive sha256) to `~/.build-loop/worktree-removal-receipts/<sha1(path)>.json` plus a copy beside the archive. Restore with `tar -xzf <archive> -C <parent>`. Use `scripts/worktree_inventory.py --path <wt>` first when you only need to read what removal would delete.

`scripts/hooks/worktree_removal_guard.py` denies `git worktree remove`, and recursive `rm` / `trash` / `safe-del` whose target is a registered worktree or an ancestor of one, unless a receipt for that exact path is under 30 minutes old and matches the current HEAD. It also denies deleting a directory that holds a receipt's own archive. The deny names the archive command. `git worktree prune` is allowed: it only drops metadata for directories already gone. The guard fails open, with a visible warning, when git cannot read HEAD or a target is unresolvable before the shell runs (`$VAR`, command substitution); a confident match without a receipt always fails closed. It runs in every repository, opted in or not, and respects the global `BUILD_LOOP_HOOKS=off` switch.

Host coverage:

- **Claude Code:** wired into the PreToolUse:Bash dispatcher (`scripts/hooks/pre_bash_dispatch.sh`) registered in `hooks/hooks.json`.
- **Codex:** the same plugin `hooks/hooks.json` loads under Codex (it sets `PLUGIN_ROOT` and `CLAUDE_*` aliases). Codex runs a plugin hook only after the user trusts and enables it in `/hooks`; this change leaves the dispatcher entry's command and timeout unchanged, so its trust hash still matches and no re-trust is needed where it was already trusted. Where it is disabled, the guard does not run under Codex. Codex shell argv (`["bash","-lc","…"]`) is unwrapped.
- **Cursor:** no build-loop hook ships for Cursor. Add a `beforeShellExecution` entry to `~/.cursor/hooks.json` running `python3 <build-loop>/scripts/hooks/worktree_removal_guard.py --host cursor` (emits `{"permission":"deny",…}`), with an explicit timeout. Until then this section is the only control.
- **Muse Code and other hosts without a hook surface:** this section is the only control; run the archive command by hand.
- **Programmatic removals bypass the guard**, because it sees shell commands only: `scripts/collapse_run.py` (`_remove_worktree`) and `scripts/artifact_guard.py` call `git worktree remove` directly. Their worktrees are build-loop run lanes and temporary audit trees, but any ignored state inside them is deleted unarchived.

### 7a. Unmerged branches: resolve what git can prove, file only real decisions

Run `python3 skills/repo-maintenance/scripts/branch_disposition.py --repo <repo>` (read-only), then again with `--apply`. It deletes `merged` branches and archives then deletes `content-on-main` and `patch-equivalent` ones (annotated tag `archive/pre-closeout-<date>/<branch>`, verified before the delete; the delete is one ref transaction that locks the branch at its classified commit and re-checks worktrees while locked; a checkout that lands mid-delete gets the ref restored). It never touches a checked-out, protected or task-queue (`oc/*`) branch, or remote refs.

For each `needs-decision` branch, read its commits and diff before filing anything:

- Main already implements the change another way (`git log -S '<key identifier>' main`), or the branch is an abandoned experiment whose card is closed: archive it with an annotated tag whose message names the evidence, then delete it.
- The branch holds a product or taste choice main does not make (UI direction, behavior change, scope call): file one card, "decide branch <b>", with the commits, diffstat and your recommendation, through `scripts/file_to_operations_center.py` (it attaches to last week's open card instead of duplicating it).
- Anything else with a clear default (finish and merge, or archive): do it and record it in the receipt. Do not ask the owner to pick.

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
