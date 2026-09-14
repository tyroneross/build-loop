<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Risk-targeted acceptance

Build Loop validates the changed behavior with the smallest test set that covers
the affected paths, acceptance criteria, and named risks. A green narrow test is
credible only when the selection record shows complete coverage.

## Manifest

Write or reuse `.build-loop/runs/<run_id>/acceptance-lanes.json` during Plan.
The matching SHA-bound plan is `.build-loop/runs/<run_id>/plan.md`; the selector
rejects substitute paths.

```json
{
  "schema": "build-loop.acceptance-lanes.v1",
  "minimum_confidence": 0.8,
  "ignore_paths": ["docs/**"],
  "lanes": [
    {
      "id": "sync-authority-unit",
      "command": ["xcodebuild", "test", "-only-testing:AmbientPhoneTests/PhoneSyncCoordinatorTests"],
      "covers": {
        "paths": ["PhoneApp/Sources/Sync/**"],
        "risks": ["authority", "async-state"],
        "criteria": ["PHONE-CAPABILITIES"]
      },
      "cost": {"seconds": 45, "tokens": 0, "dollars": 0},
      "confidence": 1.0
    },
    {
      "id": "full-acceptance",
      "command": ["bash", "scripts/acceptance.sh"],
      "covers": {"all": true},
      "cost": {"seconds": 600, "tokens": 0, "dollars": 0},
      "full_suite": true
    }
  ]
}
```

Each lane declares one executable argv array and the evidence surfaces it covers.
Execute argv directly without a shell. `covers.all` is valid only on the one
`full_suite` lane; a compile or smoke lane must name the paths, risks, and
criteria it actually proves.
Use stable criterion IDs from `goal.md`. Name concrete risks such as `authority`,
`privacy`, `persistence`, `protocol`, `migration`, `concurrency`, or
`accessibility`; avoid generic `high` or `important` tags. Cost estimates may be
measured or conservatively estimated. Missing estimates remain `null` in savings
output and must not become invented efficiency claims.

`ignore_paths` applies only to paths that have no runtime or contract effect,
such as generated evidence. Never ignore tests, schemas, protocols, security
policy, release metadata, or executable configuration merely to avoid a fallback.
Ignoring every changed path is an incomplete plan, never a zero-test pass.

## Selection

Add exactly one acceptance context block to the SHA-bound goal or plan file:

```acceptance_context
{"boundary":"local","risks":["authority","persistence"],"force_full":false}
```

This block is the authority for the run boundary, complete ordered risk list,
and explicit full-suite policy. Selection arguments must match it exactly.
Receipt verification reloads the digest-bound block and recomputes the lanes,
so editing the generated selection cannot downgrade a release, omit a risk, or
remove a forced full-suite run.

Run the deterministic planner with the exact expected change surface:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance_selector.py" \
  --manifest ".build-loop/runs/<run_id>/acceptance-lanes.json" \
  --workdir "$PWD" --run-id "<run_id>" --goal ".build-loop/runs/<run_id>/plan.md" \
  --changed-files <files...> \
  --risks <risk-ids...> \
  --criteria <criterion-ids...> \
  --boundary local \
  --compact --output .build-loop/acceptance-selection.json
```

The selector does not execute manifest commands. It chooses risk and criterion
coverage ahead of path-only coverage, rejects lanes below `minimum_confidence`,
and chooses the exact best cover for ordinary manifests by dollars, tokens,
seconds, coverage confidence, and lane count. The confidence floor protects
correctness before optimization; cost ranks lanes that clear it. Manifests with more than 18 narrow candidates
use a bounded greedy fallback and report `selection_method: greedy_fallback`.

- `verdict: complete` means the selected lanes cover every non-ignored target.
- `verdict: incomplete` means a target is uncovered or the selected set exceeds
  an explicit `--max-seconds` budget. Preserve coverage and revise the manifest
  or budget; never drop a risk to make the plan green.
- An uncovered target selects the declared full-suite lane as a fail-safe. If no
  full-suite lane exists, selection remains incomplete.
- Exit 2 (missing/invalid manifest or goal-criterion mismatch) blocks Plan and
  Review-B. It never falls through to legacy or ad hoc acceptance commands.

## Full-suite rule

Run a full suite only when the selection record names one of these reasons:

1. `release_boundary` — distribution or production evidence needs repository-wide proof.
2. `force_full` — a governing project contract explicitly requires it.
3. `required_on:<boundary>` — the lane manifest carries a standing boundary rule.
4. `fallback_for_uncovered:<targets>` — the manifest has no narrower trustworthy cover.

Do not add a full suite after targeted tests pass merely for reassurance. If a
new failure or dependency appears, add its changed path or risk, rerun the
selector, and execute the newly selected lane.

`merge` remains targeted by default. A repository that requires a broad merge
gate declares `required_on: ["merge"]` on that lane. `release` always requires a
declared `full_suite` lane and returns incomplete when none exists.

## Execution receipt

After executing each selected argv array, write
`.build-loop/acceptance-results.json`:

```json
{
  "schema": "build-loop.acceptance-results.v1",
  "run_id": "<run_id>",
  "selection_sha256": "<sha256 of the exact acceptance-selection.json bytes>",
  "lanes": [
    {
      "id": "sync-authority-unit",
      "status": "passed",
      "exit_code": 0,
      "command": ["xcodebuild", "test", "-only-testing:AmbientPhoneTests/PhoneSyncCoordinatorTests"],
      "evidence": ".build-loop/evidence/sync-authority-unit.log",
      "evidence_sha256": "<sha256 of evidence bytes>"
    }
  ]
}
```

Every selected lane must appear exactly once, no unselected lane may appear,
every status and exit code must be successful, and the argv must equal the
selection. Evidence must be a fresh file under `.build-loop/evidence/` whose
digest matches the receipt. The selector also binds the manifest, goal criteria,
acceptance context, changed-file list and content, and run ID; edits after
selection make the receipt stale. Verify before Report:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/acceptance_selector.py" \
  --workdir "$PWD" --run-id "<run_id>" --verify-results --compact
```

When `acceptance-selection.json` exists, `run_close_lint.py` runs the same check
and returns `acceptance_incomplete` until the bound receipts pass. Runs created
before this contract remain compatible because the close gate activates only
when a selection file exists.

## Token and cost rule

Use deterministic tests before LLM graders. When every criterion has a strong
machine-checkable boundary oracle and no criterion requires qualitative judgment,
record LLM-as-judge as `not_applicable: deterministic_oracles_complete`. The
independent code audit remains governed by Review-A and is not replaced by tests.

Report selected versus run-all seconds, tokens, and dollars only when the
manifest supplies the corresponding metric for every lane. Retain the compact
selection JSON as the evidence for why unselected lanes did not run.
