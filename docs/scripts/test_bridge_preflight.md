# test_bridge_preflight.py

**Purpose:** Assert that every `*-bridge` skill ships with a runtime preflight check (so it no-ops gracefully when its target plugin isn't installed) and is marked as not user-invocable (so it isn't surfaced in the user's autocomplete).

## What problem does this solve?

Bridges are escalation hops that delegate to optional supporting plugins (e.g., `debugger-bridge` delegates to the standalone `claude-code-debugger` plugin for cross-project memory; `navgator-bridge` delegates to NavGator for blast-radius analysis). Bridges are useful precisely because they're conditional — when the supporting plugin is installed, the bridge does extended-capability work; when it isn't, the bridge should silently no-op so the build doesn't fail.

The failure mode this test guards: a bridge ships without an availability check, hard-fails when the supporting plugin isn't installed, and breaks every build run on every machine that doesn't have the optional plugin. This shipped at least once in build-loop history (`api-registry-bridge` had no `availablePlugins.apiRegistry` guard for a release before being caught).

A second issue: bridges shouldn't appear in the user-facing skill autocomplete. They're internal coordination skills the orchestrator invokes; users shouldn't be able to invoke them directly. Without a `user-invocable: false` flag, autocomplete pollutes with bridge names that look invokable but don't do useful standalone work.

## How it works (algorithm)

For every directory matching `skills/*-bridge/`, the test:

1. **Loads `SKILL.md`** and parses frontmatter.
2. **Asserts frontmatter exists** — bridges without YAML frontmatter at all are clearly broken.
3. **Asserts a preflight signal is present in the body.** The signal is a phrase from a small lexicon that indicates an availability check: `availablePlugins`, `if available`, `optional`, `gracefully no-op`, `skip if not installed`, etc. The lexicon is hand-curated based on actual build-loop bridges.
4. **Asserts `user-invocable: false` is in frontmatter.** A small allowlist (`USER_INVOCABLE_EXCEPTIONS`) covers bridges that are legitimately user-invocable; an exception there must be justified by a comment.

The test is deliberately permissive about *how* the preflight check is implemented — different bridges use different patterns (frontmatter trigger, body text, runtime `Skill()` call). The test only requires that *some* phrase from the lexicon appears, and trusts that the orchestrator-level dispatch logic will honor the bridge's actual signaling.

## Inputs and outputs

- **Inputs:** every `skills/*-bridge/SKILL.md` in the repo.
- **Outputs:**
  - stdout: unittest output naming each bridge and whether it passed.
  - exit code: 0 on full pass; non-zero on first failure.

## Worked example

```bash
python3 scripts/test_bridge_preflight.py
```

Output:

```
test_every_bridge_has_frontmatter (...) ... ok
test_every_bridge_has_preflight (...) ... ok
test_bridges_are_not_user_invocable (...) ... ok

----------------------------------------------------------------------
Ran 3 tests in 0.002s

OK
```

When `api-registry-bridge` shipped without the preflight signal:

```
AssertionError: bridge skills/api-registry-bridge/SKILL.md has no recognizable availability check. Add a phrase like 'if availablePlugins.apiRegistry' or 'optional — no-ops when api-registry plugin not installed' so the dispatch contract is explicit.
```

When `mcp-builder-bridge` (hypothetical) was missing the user-invocable flag:

```
AssertionError: bridge skills/mcp-builder-bridge/SKILL.md missing 'user-invocable: false' in frontmatter. Bridges are orchestrator-internal; surfacing them in user autocomplete creates confusion.
```

## Edge cases and known limits

- **Lexicon coverage:** if a future bridge invents a new way to express its preflight contract (e.g., a structured `availability:` frontmatter field), the lexicon needs to be updated. The current lexicon was derived from existing bridges' actual phrasing.
- **Allowlist for user-invocable bridges:** `USER_INVOCABLE_EXCEPTIONS` is empty by default in build-loop. Other plugins copying this test must populate the allowlist if they have legitimately user-invocable bridges.
- **Naming convention:** the test discovers bridges by directory pattern (`*-bridge`). A bridge that didn't follow the naming convention would be missed. This is a deliberate trade-off; enforcing the naming convention is itself part of the bridge contract.

## Verification / how do we know it works

The test caught two regressions:
1. `api-registry-bridge` shipped with no `availablePlugins.apiRegistry` guard. The test added the missing line to the body and the bridge now correctly no-ops on machines without the api-registry plugin installed.
2. A copy-paste error left `user-invocable: true` in `logging-tracer-bridge`'s frontmatter (a holdover from when the user-facing slash command was being designed). The test failed before merge.

## Related files

- `skills/*-bridge/SKILL.md` — the files under test
- `skills/plugin-tests/SKILL.md` — describes when this test runs
- `agents/build-orchestrator.md` §Capability Routing — describes how the orchestrator dispatches bridges based on `availablePlugins`
- `skills/build-loop/detect-plugins.mjs` — populates `availablePlugins` at Assess
