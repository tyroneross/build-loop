# Build Loop worktrees

This directory holds temporary Git checkouts for work on:

`{{repository}}`

## Why it exists

Build Loop uses separate checkouts so agents can work on independent tasks without editing the same working files. Each child directory is a working checkout, usually on its own branch. Git history and refs are shared with the original repository; working files are separate. This is development workspace, not another installed copy of the application.

Git worktrees are a standard Git feature. The container name is a local convention. Build Loop's current helper creates them under `<repository>/.build-loop/worktrees/`. A sibling `<repository>.worktrees/` directory comes from manual or older setup; use the current helper for new work.

## For users

List the registered checkouts and their branches:

```sh
git -C {{repository_command}} worktree list
```

A directory containing only this README has no child workspaces. It can remain for future work. A child checkout may contain unfinished changes, so its presence alone does not mean it is safe to remove. Build Loop closes owned worktrees after review and integration, preserving required evidence under the original repository's ignored `.build-loop/` directory.

## For agents

- Read the original repository's `AGENTS.md` and check current Git state and ownership before using a checkout. A folder name is not proof of ownership or completed work.
- Create new checkouts through Build Loop's `scripts/worktree_guard.py`. It uses the canonical location and creates this README without replacing existing user documentation.
- Record the task, branch, owner, and closeout criteria in the run's `createdRefs`. Work only within the assigned checkout and file scope.
- Preserve unfinished changes and peer work. Use Build Loop's `scripts/collapse_run.py` after review, integration, and positive owner release; do not remove checkout folders with a recursive delete.
- Keep branch bundles and other local recovery evidence Git-ignored. Keep this README when closing individual child worktrees.
- If an explicitly authorized custom container is necessary, populate its README from Build Loop's `templates/worktrees-README.md`, resolving the repository placeholders. Do not replace an existing README.
