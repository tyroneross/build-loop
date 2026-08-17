<!-- SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Public repository documentation boundary

Apply this policy whenever Build Loop assesses, changes, reviews, or publishes
documentation.

## 1. Resolve repository visibility first

Use verified repository metadata when available. A configured private repository
may retain both product documentation and internal development records. A public
repository must expose only material that helps users, contributors, or agents
install, operate, understand, trust, extend, or verify the current product.

If visibility cannot be verified, treat publication as unresolved. Do not delete
the only copy of an internal artifact and do not add it to a public commit.

## 2. What belongs in a public repository

- README, installation, onboarding, contribution, license, and changelog material.
- Current architecture and design rationale that match the implemented system.
- Current feature behavior, command semantics, protocols, schemas, integrations,
  handoff behavior, trust boundaries, and operational guidance.
- Agent-facing contracts needed to use or extend the product safely.
- Tests and verification instructions required to build, contribute to, or trust
  the shipped product.

An architecture or specification document is public only when it describes the
current system. A proposal or target architecture does not become current merely
because some pieces have landed.

## 3. What stays private

- Cockpit or maintainer dashboards, private operating boards, and internal status.
- Build plans, future plans, proposals, roadmaps, deferred-work lists, workstreams,
  and handoff notes for unfinished implementation.
- Root-cause analyses, incident reports, assessments, audit working papers,
  retrospectives, lessons-learned source documents, and response drafts.
- Future architecture considerations, option studies, migration plans, and
  superseded architecture notes.
- Maintainer-specific commit, push, identity, release, or deployment instructions.
- Performance captures, release rehearsals, deployment diagnostics, and test
  harness artifacts that are not required to use, build, or contribute to the
  product.
- Build Loop working state and review artifacts under `.build-loop/`.

Naming is evidence, not the decision. Review the content and audience. A file named
`SPEC` can still be a private future plan; a document describing implemented command
semantics can stay public.

## 4. Preserve internal material before public removal

For a public repository, write the full internal artifact to the private
`build-loop-memory` project lane before removing it from Git tracking. Use the
canonical writer so the private copy records source repository, source worktree,
run id, host, and original path. Preserve the original filename or path in the
memory record.

```bash
python3 <build-loop>/scripts/memory_writer.py \
  --scope project --project <project-slug> write \
  --file "projects/<project-slug>/raw/documents/<batch>/<source-path>.md" \
  --name <stable-name> \
  --description "Private project artifact; original path: <source-path>" \
  --type reference --run-id <run-id> --workdir <repo> --host <host> \
  --body-file <source-path> --json
```

Require a successful writer receipt before deleting the public-tree copy. Then add
an ignore rule for the internal artifact class so the next Build Loop run cannot
reintroduce it. Never put a link to a private memory repository in public user docs;
the private memory record carries provenance back to the public source instead.

Deleting a file from the current tree does not remove it from Git history. Treat a
history rewrite as a separate, destructive decision reserved for actual sensitive
data, not routine documentation cleanup.

## 5. Review result

Every documentation review for a public repository reports:

- `public_current`: current user, contributor, or agent documentation retained.
- `private_archived`: internal artifacts written to `build-loop-memory`, with receipts.
- `public_removed`: paths removed from public tracking after archival.
- `blocked`: artifacts that could not be archived or whose audience is unresolved.

A public-repository review cannot pass while an internal artifact remains staged or
while a removed artifact lacks a private-memory receipt.
