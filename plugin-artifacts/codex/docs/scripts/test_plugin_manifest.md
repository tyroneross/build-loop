# test_plugin_manifest.py

**Purpose:** Sanity-check the plugin's manifest files (`plugin.json`, `marketplace.json`) and the structural invariants the Claude Code runtime expects from `commands/`, `skills/`, `agents/`, and `.mcp.json`.

## What problem does this solve?

Plugin distribution failures are usually shape failures, not logic failures. A typo in a manifest field, a marketplace entry that lags behind a plugin version bump, or a slash-command file that's missing frontmatter — none of these surface as Python errors at install time. They surface as silent feature gaps: the command doesn't appear, the skill doesn't load, the marketplace shows a stale version. The user has no diagnostic path other than "why isn't it working."

This test catches the categories of drift that have actually shipped and broken installations:

- **Marketplace drift** — `plugin.json` says version 0.4.1 but `.claude-plugin/marketplace.json` still lists 0.3.2. Build-loop shipped this twice (0.4.0 → 0.4.1 transition).
- **Frontmatter-missing slash-commands** — `commands/debugger-detail.md` shipped without YAML frontmatter; the runtime registered it but with no description, so it didn't appear in the `/help` listing.
- **MCP path drift** — `.mcp.json` references `dist/src/mcp/server.js`, but a `tsconfig` change moved the build output to `lib/`. The plugin loaded but the MCP server failed to start with no clear diagnostic.
- **Skill name uniqueness** — two skills with the same `name:` in frontmatter resolve nondeterministically. Catching this prevents a class of "the skill ran but did the wrong thing" bugs.

## How it works (algorithm)

The test module is composed of small classes, each guarding one invariant:

| Test class | Invariant |
|---|---|
| `PluginManifestShapeTests` | `plugin.json` exists, parses, has `name` + `version` + `description` fields, version is semver |
| `MarketplaceShapeTests` | `.claude-plugin/marketplace.json` exists, parses, contains an entry whose `name` matches `plugin.json.name` |
| `VersionShapeTests` | `plugin.json.version` is valid semver; `marketplace.json` entry's `version` matches `plugin.json.version` exactly (no drift) |
| `CommandFrontmatterTests` | every `commands/*.md` has YAML frontmatter with `description:` populated |
| `SkillNameUniquenessTests` | every `skills/<dir>/SKILL.md` has `name:` in frontmatter, and no two skills share the same `name:` |
| `McpServerShapeTests` | if `.mcp.json` exists and references a binary path, that path resolves under `${CLAUDE_PLUGIN_ROOT}` and the file actually exists on disk |

Each invariant is a separate `TestCase` so a failure in one doesn't cascade into the others. Tests use `unittest.TestCase.skipTest` for "this plugin doesn't ship that artifact" cases (e.g., a plugin without an MCP server skips the MCP shape tests), so a skip is informational rather than a failure.

## Inputs and outputs

- **Inputs:** the manifest files and component directories at the build-loop repo root (resolved via `HERE.parent` from the script location).
- **Outputs:**
  - stdout: standard unittest output, with skips clearly marked.
  - exit code: 0 if all pass or skip; non-zero on the first hard failure.

## Worked example

```bash
python3 scripts/test_plugin_manifest.py
```

Output:

```
test_plugin_json_exists (...) ... ok
test_plugin_json_has_required_fields (...) ... ok
test_marketplace_versions_match_plugin (...) ... ok
test_plugin_version_is_semver (...) ... ok
test_no_duplicate_skill_names (...) ... ok
... (6 more)

----------------------------------------------------------------------
Ran 11 tests in 0.005s

OK
```

When `marketplace.json` lags behind: `test_marketplace_versions_match_plugin` fails with:

```
AssertionError: marketplace.json lists version '0.7.4' but plugin.json declares '0.8.1' — bump them in lockstep
```

When a slash-command lacks frontmatter:

```
AssertionError: commands/debugger-detail.md has no YAML frontmatter; runtime will register it without a description
```

## Edge cases and known limits

- **Plugins without MCP servers:** the MCP shape tests use `skipTest` rather than `assertTrue` so plugins without `.mcp.json` don't fail.
- **Plugins without slash-commands:** ditto. The directory layout is optional in the runtime, so absence is silent.
- **Manifest schema evolution:** new required fields added to `plugin.json` by Claude Code itself are not currently checked here. The set of required fields is hardcoded based on the current runtime; if Claude Code starts requiring additional fields, this test must be updated.
- **`marketplace.json` array form:** the test handles both the single-plugin and multi-plugin `plugins[]` array forms.

## Verification / how do we know it works

The test was bootstrapped by running it against historical build-loop git revisions known to have shipped with broken manifests. Each known regression (0.3.2/0.4.0 marketplace drift, 0.6.x missing frontmatter on `commands/debugger-detail.md`) was reproduced as a failing test before the fix landed. After the fix, the tests pass.

## Related files

- `.claude-plugin/plugin.json` — the manifest under test
- `.claude-plugin/marketplace.json` — version-mirror under test
- `commands/`, `skills/`, `agents/`, `.mcp.json` — directories whose shape is asserted
- `skills/plugin-tests/SKILL.md` — describes when this test runs
- `agents/build-orchestrator.md` §Phase 4 Review-B — auto-dispatch
