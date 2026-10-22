<!-- SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Working shared task-queue cards

Rules for any build-loop run that files, fixes or works a card on the shared task
queue (today the RossLabs Operations Center; Easy Terminal's `et task` store is the
intended replacement). The owner's queue must hold only what needs the owner:
production approvals, taste or product calls, credentials, and physical-device work.
A 60-card sample on 2026-10-22 found 90% of the owner's waiting cards did not need
him. Each rule below closes one of the observed causes.

## 1. Look up before you file or fix

Before filing a finding or starting a fix, check for an open card:

```bash
python3 scripts/task_queue_client.py lookup --repo <repo> --title "<one-line finding>" --json
```

A match returns the card id. Attach new evidence to that card; do not file a second
one. `scripts/file_to_operations_center.py` runs this lookup itself and attaches on a
match (`attached: true`, `task_id` = the existing card). The match is exact after
normalization (case, punctuation, `fix:` prefixes, worktree paths), so a finding with
a different title still files. Pass `--force-new` only for a distinct finding that
shares a title.

The filer refuses a card without `--spec` and `--repo` (exit 2): a card nobody can
work parks on the owner. It refuses `--handler direct-fable` unless the owner's
consent is on record (`--consent-direct-fable`). When the queue rejects intake as a
duplicate or full and names a card, the filer attaches there.

## 2. Close on fix: tag the commit

When a commit addresses a card, add the trailer `Task: <full-id>` (the legacy
`OC-Task: <id>` also parses). The queue's landing check closes the card when that
commit reaches the target repo's main. Get the id from the run context: the queue
runner's prompt carries `Task id: <id>`; a lookup returns it for a peer's card.
Resolve it to a full id and confirm the card is still open before you use it:

```bash
TRAILER="$(python3 scripts/task_queue_client.py trailer --task-id <id-or-prefix>)" \
  && git commit -m "<subject>" -m "<body>" --trailer "$TRAILER"
```

`trailer` exits 1 and prints nothing when no single open card matches. Then commit
without the trailer. A wrong trailer closes someone else's card.

## 3. Decide the default and inform; do not park for a choice

When the options include a recommended or clearly better one, take it, continue, and
state the choice and the reason in the receipt and the report. Park a card on the
owner only for: a production action (live user-facing deploy, production database,
App Store release, DNS/email/auth on a live tenant), an irreversible destructive
delete, a taste or product call with no defensible default, a missing credential,
or physical-device work. "Owner, pick A or B" with a recommendation attached is a
workflow violation. The decision ladder is `references/leadership.md` §"Decide-at-70%".

## 4. Pushing merged code and local installs are not production gates

`git push origin main` of code already merged and verified, local builds and
installs, TestFlight uploads, and preview or staging deploys proceed without asking.
The gate is user-facing impact: "does this change behavior for someone other than
the owner, today?" A repo whose main auto-deploys to production is the exception;
there the push is a production deploy. `scripts/deployment_policy.py` treats `main`,
`master` and `release` as protected by default. In a repo that does not deploy from
main, set `"deploymentPolicy": {"protectedBranches": []}` in its
`.build-loop/config.json` once, so the push stops routing to confirmation.

## 5. Review means delivered work

Move a card to `review` only when its change exists: committed, verified, and named
in the receipt (commit sha, tests run). A spec nobody has started stays `todo`. An
unfinished run reports what is left and stays in its working lane.

## 6. Commit before the session ends; wait for background subagents

A run that exits with uncommitted output parks as `needs_input` and blocks its own
retry. Before the final report:

1. Wait for every background subagent and background command you started; collect
   each result or record why it was abandoned.
2. Commit the verified work on the task branch (a WIP commit labeled unverified is
   better than a dirty worktree).
3. Confirm `git status --porcelain` is empty apart from tool state (`.build-loop/`,
   `.bookmark/`, `.rally/`, `.navgator/`, `.ibr/`).
