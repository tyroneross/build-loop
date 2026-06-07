<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Push Readiness Checklist

Advisory checklist for recommending a push. This document is guidance, not a new gate: it does not replace `deployment_policy.py`, `autonomy_gate.py`, `push_hold.py`, protected-branch rules, or explicit operator confirmation.

Use it when a user asks whether a build is ready to push, when closing a build-loop run that accumulated local commits, or when a peer asks for a release-readiness readout.

## Recommendation language

Use one of these labels in the report:

- `Recommend push` — policy allows it, dry run succeeds, focused validation matches the changed surface, and no unresolved current coordination issue overlaps the push.
- `Pushable with notes` — mechanics are clean, but there are non-blocking caveats such as runtime Rally dirt, known unrelated full-suite failures, skipped optional checks, or stale advisory handoffs.
- `Hold recommended` — there is a concrete quality, accuracy, architecture, packaging, or coordination concern that should be resolved before push, even if Git would accept it.
- `Cannot recommend push` — an existing blocking mechanism says no, such as active push-hold, branch behind remote, failed dry run, protected production confirmation missing, or unresolved critical/high review finding.

Do not phrase advisory findings as hard blockers unless an existing blocking mechanism actually blocks them.

## Checklist

1. **Policy and push mechanics**
   - Run `git fetch origin <branch> --prune`.
   - Check `git status --short --branch` for ahead/behind and dirty state.
   - Run `python3 scripts/push_hold.py --status --workdir "$PWD" --json`.
   - Run `git push --dry-run origin <branch>`.
   - If the push target is production/protected/unknown, evaluate the exact command with `python3 scripts/deployment_policy.py --workdir "$PWD" --command "<push command>"`.

2. **Coordination**
   - Run `rally room --json` and `rally next --tool <tool> --json` when Rally is present.
   - Confirm active claims do not overlap files being changed for the push.
   - Separate stale handoffs or old inbox messages from current blockers.
   - If a peer verdict is required by the plan, cite the verdict event or say it is missing.

3. **Dirty tree classification**
   - Classify every dirty path as `source`, `test artifact`, `runtime coordination`, `archive bundle`, or `unknown`.
   - Commit source cleanup that belongs to the push.
   - Leave runtime coordination state and generated artifacts uncommitted unless the repo explicitly tracks that surface.
   - For archive bundles, prefer `.gitignore` or the documented archive location over deleting evidence.

4. **Quality validation**
   - Run `git diff --check`.
   - Run syntax/type/build checks for touched languages.
   - Run focused tests selected from changed files and adjacent contracts.
   - If the full suite has known unrelated failures, report the baseline and confirm the focused set covers this change. Do not normalize new failures as known failures.

5. **Accuracy and evidence**
   - Verify package/release claims with the actual package or release command in dry-run mode when available.
   - Verify memory/index/install claims with the repo's validator scripts.
   - For current external/API/package facts, cite the research packet or mark the claim unverified per `references/research-trigger-policy.md`.
   - State skipped checks explicitly with the reason.

6. **Architecture**
   - For cross-layer changes, include architecture-scout or equivalent impact-review evidence.
   - For stale architecture data, recommend refreshing the scan before making architecture claims.
   - Confirm new code follows the canonical script/skill/reference surface instead of adding a second ledger, duplicate protocol, or bypass path.

7. **Efficiency**
   - Prefer focused tests over exhaustive reruns when the changed surface is narrow.
   - Track any material package-size, runtime, or benchmark change when the diff touches performance-sensitive code.
   - Recommend deeper benchmarking only when the task had an explicit performance target or the diff changes hot-path behavior.

8. **Readout**
   - Start with the recommendation label.
   - Give the shortest useful evidence list: branch state, dry-run result, validation result, dirty-state classification, and caveats.
   - Name the exact command needed for the push only after the recommendation is clear.

## Example readout

```
Pushable with notes.

Evidence: main is ahead 34 and not behind; push-hold inactive; dry-run push succeeds; focused tests passed; npm pack dry-run includes the intended seed files. Dirty state is limited to Rally runtime files and archive bundles, plus one committed ignore cleanup. Caveat: full self-mod suite still has two known unrelated failures, so this recommendation relies on the focused validation set.

Command: git push origin main
```
