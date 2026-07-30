# test_mcp_registration.py

**Purpose:** Validate that the plugin's `.mcp.json` is well-formed, references binaries that resolve on disk, and uses server names that won't collide with other installed plugins.

## What problem does this solve?

Claude Code installs MCP servers per-plugin. When two plugins each register a server with the same bare name (e.g., `debugger`, `memory`, `search`), only one wins at runtime; the other is silently shadowed. There's no error message — the second plugin's MCP tools just don't appear under the qualified name the second plugin's authors thought they did.

Build-loop previously registered a bundled debugger MCP server, while the standalone debugger plugin also registered a server named `debugger`. Anyone who installed both got nondeterministic resolution. Build-loop now avoids that entire class by not shipping an MCP server; this test remains useful for plugins that do expose MCP servers.

The script also catches the broader category of "manifest looks fine but the binary it points at doesn't actually exist." A typical failure mode: someone changes a TypeScript build output path, or forgets to run `npm run build` before tagging a release. The plugin installs cleanly, but on first MCP call the runtime spawns `node` with a missing path, and the server crashes silently.

## How it works (algorithm)

The test module loads `.claude-plugin/plugin.json`, follows its `mcpServers` field — which can be either an inline object or a string path to a separate `.mcp.json` — and runs four assertions:

1. **`ConfigShapeTests`** — the manifest declares `mcpServers` (or skips the test if not present); the referenced JSON parses; the parsed object has the expected `mcpServers` key with a dict value.
2. **`ServerNamingHygieneTests`** — for each server name, check it against a set of common collision-prone bare names (`debugger`, `memory`, `search`, `auth`, `logger`, `tracer`). If the plugin name is not a substring of the server name, the test "skips" with a hint. Skips are informational; many plugins use bare names today and the runtime doesn't block them. The skip is the warning.
3. **`CommandResolvesTests`** — for each server, expand `${CLAUDE_PLUGIN_ROOT}` in its `args` array against the actual repo root, then assert any path-shaped argument (anything with a file extension) resolves to an existing file. Catches the missing-build-output failure mode.
4. **`NoDuplicateNamesTests`** — within this plugin's own `.mcp.json`, every server name is unique. JSON dict keys are inherently unique so this is a smoke check, but it's cheap insurance against a hand-edited config.

## Inputs and outputs

- **Inputs:** `.claude-plugin/plugin.json` and the `.mcp.json` it references.
- **Outputs:**
  - stdout: unittest output, including skips for absent artifacts.
  - exit code: 0 on pass-or-skip; non-zero on hard failure.
  - No filesystem side effects.

## Worked example

```bash
python3 scripts/test_mcp_registration.py
```

After the 0.8.2 rename, output:

```
test_command_args_resolve (...) ... ok
test_plugin_declares_mcp_or_skips (...) ... ok
test_referenced_mcp_file_valid_json (...) ... ok
test_unique_within_plugin (...) ... ok
test_server_names_avoid_common_unprefixed_names (...) ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.001s

OK
```

Before the rename, `test_server_names_avoid_common_unprefixed_names` would skip with:

```
SKIP: server name(s) ['debugger'] are not plugin-prefixed and may collide with another plugin registering the same name. Consider renaming to e.g. 'my-plugin-debugger'. Non-blocking — many plugins use bare names today.
```

If a future change moved the server binary without updating `.mcp.json`:

```
AssertionError: server 'my-plugin-debugger' references '${CLAUDE_PLUGIN_ROOT}/dist/mcp/server.js' which resolves to /Users/.../dist/mcp/server.js — not present (run `npm run build` if TS source?)
```

## Edge cases and known limits

- **Inline vs file-form `mcpServers`:** both shapes are supported. The string form is the common one; the inline-object form (the manifest itself contains an `mcpServers` block) is occasionally used by smaller plugins.
- **Non-`node` commands:** the path-resolution check only fires when an arg looks like a path (has a file extension). A pure `python3 -m foo` invocation won't be checked.
- **Naming hygiene scope:** the bare-name list is intentionally short — only commonly-collisional generic names. Plugin-specific names like `bookmark` or `context7` aren't on the list because they're already plugin-uniqueish.
- **Skip vs fail:** ServerNamingHygiene uses `skipTest` rather than `assertTrue` because retrofitting every existing plugin to plugin-prefix server names is out of scope for this test. The skip is the warning. CI gates that want a blocking check should grep test output for `SKIP` or use the structured JSON output of the orchestrator's plugin-tests dispatch.

## Verification / how do we know it works

The original build-loop MCP collision fix was bootstrapped against this test. After Build Loop removed its MCP server, the same test now skips cleanly for Build Loop and remains available for plugins that declare `mcpServers`. The path-resolution check was verified by deliberately introducing a typo in `.mcp.json` (changing `dist/mcp` to `dst/mcp`) — the test failed with the expected error message, and once corrected, passed.

## Related files

- `.mcp.json` — the file under test when a plugin ships MCP servers
- `.claude-plugin/plugin.json` — declares which `.mcp.json` to load when present
- `KNOWN-ISSUES.md` — documents historical bundled-vs-standalone collisions
- `skills/plugin-tests/SKILL.md` — describes when this test runs
- `agents/build-orchestrator.md` §Phase 4 Review-B — auto-dispatch
