# test_skill_resolution.py

**Purpose:** Repo-level guard test that wraps `collision_scan.py` and asserts no unaccepted slash-command/skill name collisions ship in the build-loop plugin.

## What problem does this solve?

Knowing the bug pattern (see `collision_scan.md`) is not enough. Without an automated check at the plugin level, a future PR could reintroduce a collision the same way the original `build-loop:build-loop` collision shipped through six versions undetected. This script is the always-on guard.

It runs the static collision detector on the build-loop repo, compares the result against an explicit `ACCEPTED_SIBLINGS` allowlist, and fails the test if anything outside that allowlist appears. The allowlist exists because three command/skill pairs (`optimize`, `research`, `plan-verify`) intentionally share names — they were inspected by hand and judged not to misbehave in practice. The allowlist is a list of "we looked at these and they're fine" decisions, not a list of unfixed bugs.

A second guard ensures items in the allowlist still actually exist on disk; if a developer renames or deletes one of the accepted siblings, the test fails so the allowlist gets updated rather than going stale.

## How it works (algorithm)

The test class composes three independent assertions:

1. **`ZeroNewCollisionsTests.test_only_accepted_siblings_present`** — invokes `collision_scan.scan_plugin(REPO_ROOT)` and computes the set of qualified names returned. Asserts that this set is a subset of `ACCEPTED_SIBLINGS`. Any new collision fails the test with a message naming the unaccepted shapes.
2. **`ZeroNewCollisionsTests.test_accepted_siblings_still_exist`** — for each entry in the allowlist, asserts that both `commands/<name>.md` and `skills/<name>/SKILL.md` exist on disk. Catches drift.
3. **`StrictExitCodeTests.test_strict_exits_1_with_accepted_siblings`** — runs `python3 collision_scan.py --strict --path <repo>` as a subprocess and checks that exit code 1 fires when collisions exist. Catches regressions in the scanner's strict mode (which is what plugin authors actually use as a CI gate).

The accepted-siblings allowlist for build-loop:

| Qualified name | Reason accepted |
|---|---|
| `build-loop:optimize` | Slash-command and skill share intent; user has not observed misbehavior. Same shape as the buggy case but apparently resolves correctly in this specific runtime. Watched. |
| `build-loop:research` | Same. |
| `build-loop:plan-verify` | Same. |

If any of these begin to misbehave, the documented fix is the same as for `build-loop:build-loop`: rename the slash-command file (e.g. `commands/optimize.md` → `commands/optimize-run.md`), keep the skill name unchanged so all `Skill()` callers continue to resolve.

## Inputs and outputs

- **Inputs:** none. The script is a unittest module that finds the repo root via its own location (`HERE.parent`).
- **Outputs:**
  - stdout: standard unittest output (test names + OK/FAIL).
  - exit code: 0 if all pass, non-zero otherwise.
  - No filesystem side effects.

## Worked example

Standard run from the build-loop repo root:

```bash
python3 scripts/test_skill_resolution.py
```

Output:

```
test_strict_exits_1_with_accepted_siblings (...) ... ok
test_accepted_siblings_still_exist (...) ... ok
test_only_accepted_siblings_present (...) ... ok
... (5 more in related test classes)

----------------------------------------------------------------------
Ran 8 tests in 0.286s

OK
```

If a developer adds `commands/foo.md` and `skills/foo/SKILL.md`, the test fails with:

```
AssertionError: Unaccepted collision: build-loop:foo
```

The fix is either to rename one of the two files or, if the developer has reviewed it and judged it acceptable, to add the qualified name to `ACCEPTED_SIBLINGS` with a one-line rationale comment.

## Edge cases and known limits

- **Allowlist drift:** the second test asserts that allowlist entries still exist; this catches the case where a developer renames a file but forgets to update the test.
- **Cross-plugin collisions:** the test only scans the build-loop repo, not the user's installed plugins. Cross-plugin collisions are detected by running `collision_scan.py` with multiple `--path` arguments — that's a separate workflow not covered by this test.
- **Subprocess timeout:** the strict-mode subprocess call has no explicit timeout; if `collision_scan.py` hung, the test would hang. In practice the scanner runs in milliseconds.

## Verification / how do we know it works

The test was first written when the original `build-loop:build-loop` collision was being remediated. Running the test against pre-fix HEAD reproduced the failure (catching the collision); running against post-fix HEAD passed. The strict-exit-code test was added after a separate regression in `collision_scan.py` (a flag-parsing bug that always returned 0) was found in code review.

## Related files

- `scripts/collision_scan.py` — the static detector wrapped by this test
- `KNOWN-ISSUES.md` — original bug discovery
- `skills/plugin-tests/SKILL.md` — when this test runs as part of the plugin-tests suite
- `agents/build-orchestrator.md` §Phase 4 Review-B — auto-dispatch
