---
name: plugin-tests
description: Static-analysis test harness for Claude Code plugins. Triggers on "test plugin", "validate plugin", "check skill resolution", "run plugin tests", "lint plugin", "verify manifest", "namesake collision", "MCP registration check". Runs Python stdlib pytest scripts that catch namesake collisions, manifest drift, MCP misregistration, trigger-phrase coverage gaps, and bridge pre-flight gaps. Routed as build-loop's 4th orchestrator mode (Build / Optimize / Research / Test).
version: 0.1.0
user-invocable: false
---

# Plugin Tests — Static-Analysis Harness

A pytest-stdlib test suite that validates a Claude Code plugin's structure, manifest, MCP wiring, skill descriptions, and bridge preflights. Designed to catch the bug classes that have actually shipped through build-loop's history (namesake collisions, manifest version drift, missing frontmatter on commands, bare MCP server names).

This is the **static** tier — pure text/JSON validation, zero runtime dependencies, runs in under a second. Runtime tests (live MCP round-trip, real bridge delegation) are out of scope here and live in the orchestrator's Phase 4 Review-B Validate.

## When this skill fires

- **User direct** — `/build-loop:test [test-name]` or any of: "test plugin", "validate plugin", "lint plugin", "verify manifest", "check skill resolution", "run plugin tests"
- **Orchestrator intent routing** — TEST mode (4th mode alongside Build / Optimize / Research) — `agents/build-orchestrator.md` §Intent Routing
- **Orchestrator auto-dispatch in Phase 4 Review-B Validate** (always on, since v0.7.3) — when Phase 3 Execute's diff touches any plugin metadata path: `*.claude-plugin/*.json`, `commands/*.md`, `skills/*/SKILL.md`, `agents/*.md`, `.mcp.json`, `hooks/hooks.json`, or any path referenced by `mcpServers`. Exit 1 routes into the memory-first gate as a test failure; exit 2 logs and continues without blocking.
- **Pre-publish gate** when bumping a plugin version (the human-driven equivalent of the auto-dispatch — run `/build-loop:test --strict` before pushing)

## What's tested

| Script | What it catches | Reference defects |
|---|---|---|
| `scripts/test_skill_resolution.py` | Namesake collisions (commands/X.md + skills/X/SKILL.md), frontmatter `name:` drift from dir name | `build-loop:build-loop` collision (shipped through 6 versions undetected before 0.4.1) |
| `scripts/test_plugin_manifest.py` | Required manifest fields, version sync between plugin.json and marketplace.json, MCP path resolution, every command has frontmatter, skill name uniqueness | 0.4.0/0.3.2 marketplace drift; missing frontmatter on `commands/debugger-detail.md` |
| `scripts/test_mcp_registration.py` | `.mcp.json` shape, referenced binaries exist, server-name hygiene (warns on bare names that collide across plugins) | The `debugger` server-name collision between bundled and standalone |
| `scripts/test_trigger_phrases.py` | Curated (skill, phrase) coverage in skill `description:` fields | Trigger-phrase gaps after the multi-provider auth audit |
| `scripts/test_bridge_preflight.py` | Every `*-bridge/SKILL.md` has an availability/absence check + `user-invocable: false` | api-registry-bridge missing `user-invocable: false`; bridges that hard-fail when their target plugin isn't installed |

## How to run

From the plugin repo root:

```bash
# Run all 5
for t in scripts/test_skill_resolution.py scripts/test_plugin_manifest.py \
         scripts/test_mcp_registration.py scripts/test_trigger_phrases.py \
         scripts/test_bridge_preflight.py; do
  echo "=== $t ==="
  python3 "$t"
done

# Or via the slash command
/build-loop:test
```

Each script is independent — running one without the others is fine. Each follows the build-loop pytest-stdlib convention from `scripts/test_plan_verify.py` (subprocess-against-sibling-script + tempfile fixtures + `unittest.TestCase`).

## Exit codes

- `0` — all tests pass
- `1` — at least one test failed (CI gate)
- `2` — runner error (test script itself crashed)

The orchestrator surfaces `1` as a Review-B Validate failure → routes to Iterate. `2` is treated as a verifier outage and logged but doesn't block.

## Adding new tests

When a new bug class ships through, add a test that would have caught it. Pattern:

1. Write `scripts/test_<bug_class>.py` matching the existing pattern (stdlib only, `unittest.TestCase`, subprocess against any helper script)
2. Add a row to the table in §"What's tested" above
3. Add the script to the runner in §"How to run"
4. Add a `(skill, phrase)` to `EXPECTED_TRIGGERS` in `test_trigger_phrases.py` if the new test covers a class that should be discoverable by user phrasing

Don't migrate to pytest, vitest, or Playwright. The stdlib pattern keeps the harness portable, zero-install, and CI-friendly. (See the testing survey at the head of `KNOWN-ISSUES.md` 2026-05-02 entry — IBR's vitest, atomize-ai's Jest+Playwright, prompt-test-lab's Playwright stratification all have their place; for plugin metadata validation specifically, stdlib Python wins.)

## What this skill does NOT do

- Runtime testing (live MCP calls, actual `Skill()` invocation) — that's Review-B Validate's job, executed by the orchestrator with the live runtime
- UI testing — for plugins that build UIs, dispatch `ibr:test` against the captured baseline (the `.ibr-test.json` declarative format)
- Performance / Lighthouse — separate concern, not a plugin metadata issue
- Cross-plugin integration — that's the bridge skills' job at runtime

## Cross-references

- `agents/build-orchestrator.md` §Intent Routing — TEST mode classification
- `commands/test.md` — slash-command surface (`/build-loop:test`)
- `scripts/collision_scan.py` — the static detector that `test_skill_resolution.py` wraps
- `KNOWN-ISSUES.md` 2026-05-02 entry — testing survey across 13 projects that informed this design
- IBR's `.ibr-test.json` — declarative format for runtime UI tests on plugin-built UIs

## History

- 2026-05-02 — initial release with 5 static-analysis scripts. Caught 2 real defects on first run (debugger-detail frontmatter, api-registry-bridge user-invocable). Pattern borrowed from build-loop's existing `scripts/test_plan_verify.py` + survey of test setups across 13 user projects.
