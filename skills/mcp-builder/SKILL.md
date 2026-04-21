---
name: mcp-builder
description: This skill should be used when the user asks to "build an MCP server", "create an MCP", "ship a Model Context Protocol server", "add MCP tools to my plugin", "package an MCP server for Claude Code", "MCP server won't start", or needs guidance on MCP server implementation, transport, bundling, .mcp.json schema, or shipping MCP inside a Claude Code plugin. Use alongside plugin-builder when the plugin exposes MCP tools — plugin-builder covers the plugin wrapper, this skill covers the server itself.
---

# MCP Builder

Build, bundle, and ship Model Context Protocol servers for Claude Code. Companion to `plugin-builder` — use both when the plugin exposes MCP tools.

## Scope split with plugin-builder

| Concern | Skill |
|---|---|
| Plugin directory layout, `plugin.json`, marketplace publishing | `plugin-builder` |
| MCP server implementation (protocol, tools, transport) | **this skill** |
| `.mcp.json` schema inside a plugin | both — schema lives in `plugin-builder/references/plugin-hygiene-lessons.md` |
| Bundling strategy (`tsup` vs `tsc` vs committed `dist/`) | **this skill** |
| Standalone MCP debugging | **this skill** |

## When to build an MCP server vs a hook vs a skill

| Use case | Choose |
|---|---|
| Expose a local database, filesystem, or API to Claude as callable tools | **MCP server** |
| React to Claude events (post-tool, session-stop, etc.) without exposing tools | **Hook** |
| Guide Claude with instructions/knowledge, no tool exposure | **Skill** |
| Wrap an existing CLI so Claude can invoke it | **MCP server** (thin wrapper) or **plugin bin/** if the CLI is already stable |

Don't build an MCP server when a skill + bash would suffice. The overhead is a separate process per server, JSON-RPC framing, and a config schema that's easy to get wrong.

## Server scaffold (TypeScript + stdio)

Use the official SDK. Stdio transport is default for Claude Code plugins.

```ts
// src/mcp/server.ts
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';

const server = new Server(
  { name: 'my-plugin', version: '1.0.0' },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'echo',
      description: 'Echo a message back',
      inputSchema: {
        type: 'object',
        properties: { message: { type: 'string' } },
        required: ['message'],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  if (req.params.name === 'echo') {
    return { content: [{ type: 'text', text: String(req.params.arguments?.message) }] };
  }
  throw new Error(`Unknown tool: ${req.params.name}`);
});

const transport = new StdioServerTransport();
await server.connect(transport);
console.error('[my-plugin] MCP server started'); // stderr, not stdout
```

**Rules:**
- Log to `stderr` only. `stdout` is the JSON-RPC channel — anything else corrupts the protocol.
- Tool names: stable identifiers. Changing them is a breaking change for users.
- `inputSchema` is a JSON Schema. Be specific — vague schemas produce bad tool calls.
- Keep tool descriptions <500 chars; they're loaded into every session and cost tokens.

## Bundling: pick one (preferred → least preferred)

### 1. `tsup` — single-file bundle (strongly preferred)

```json
// package.json
{
  "scripts": { "build": "tsup src/mcp/server.ts --format=esm --target=node18 --bundle" },
  "devDependencies": { "tsup": "^8", "@modelcontextprotocol/sdk": "^1" }
}
```

Output: one self-contained `dist/mcp/server.js` that runs without `node_modules/`. Marketplaces can ship this without dependency resolution. IBR uses this pattern.

Commit `dist/` to git (don't gitignore) so the marketplace sync ships the bundle.

### 2. `tsc` + committed `dist/`

OK but fragile. Requires `node_modules/` at runtime, and marketplace sync skips `node_modules/`. Workarounds below.

### 3. `tsc` + SessionStart hook with `${CLAUDE_PLUGIN_DATA}`

Install deps into the persistent data dir the first time the plugin loads. Survives plugin updates.

```json
// hooks/hooks.json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "diff -q \"${CLAUDE_PLUGIN_ROOT}/package.json\" \"${CLAUDE_PLUGIN_DATA}/package.json\" >/dev/null 2>&1 || (cd \"${CLAUDE_PLUGIN_DATA}\" && cp \"${CLAUDE_PLUGIN_ROOT}/package.json\" . && npm install) || rm -f \"${CLAUDE_PLUGIN_DATA}/package.json\""
      }]
    }]
  }
}
```

Then point the MCP at the installed deps:

```json
// .mcp.json
{
  "mcpServers": {
    "my-plugin": {
      "command": "node",
      "args": ["${CLAUDE_PLUGIN_ROOT}/dist/mcp/server.js"],
      "env": {
        "NODE_PATH": "${CLAUDE_PLUGIN_DATA}/node_modules"
      }
    }
  }
}
```

Trade-off: first session is slow (installs deps); subsequent sessions are instant.

### 4. Pure-stdlib Python server

```python
# src/mcp/server.py — uses only the mcp package (can be vendored or single-file)
```

Works well when you want to avoid Node entirely. Harder to bundle single-file than TypeScript — usually needs `mcp-server-stdio` as a runtime dep.

## `.mcp.json` schema (the thing that breaks most often)

**CORRECT** — wrap in `"mcpServers"`:

```json
{
  "mcpServers": {
    "my-plugin": {
      "command": "node",
      "args": ["${CLAUDE_PLUGIN_ROOT}/dist/mcp/server.js"],
      "env": {
        "DEBUG": "1"
      }
    }
  }
}
```

**WRONG** — flat form. Silently passes `/doctor`, fails at MCP startup. Only `/mcp` surfaces the error.

```json
{
  "my-plugin": { "command": "node", "args": [...] }
}
```

Real incident (2026-04-21): `bookmark`, `claude-code-debugger` both shipped flat `.mcp.json`. Both showed "MCP · ✗ failed" in `/plugin` for weeks before diagnosis. See `plugin-builder/references/plugin-hygiene-lessons.md` § 7.

### Inline form (in `plugin.json`)

```json
{
  "name": "my-plugin",
  "mcpServers": {
    "my-plugin": { "command": "...", "args": [...] }
  }
}
```

Inline form drops the outer wrapper but uses the same inner structure.

## Standalone smoke test (before shipping)

Always verify the server responds to `initialize` before publishing. This isolates "server broken" from "Claude Code UI stale":

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | \
  CLAUDE_PLUGIN_ROOT=/path/to/your/plugin \
  node /path/to/your/plugin/dist/mcp/server.js
```

Healthy response (single line of JSON):
```json
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-11-25","serverInfo":{"name":"my-plugin","version":"1.0.0"},"capabilities":{"tools":{}}}}
```

If you see:
- nothing → server exited before the handshake; check `console.error` output
- `MODULE_NOT_FOUND` → bundling failed, `dist/` references a package that isn't bundled
- `EACCES` → file-permission issue, usually the script isn't executable (not required for `node` but often accompanies deeper path issues)
- `SyntaxError` → wrong target (e.g. ESM features in a CJS bundle, or vice versa)

The server should then block on stdin waiting for the next message. Send `Ctrl+D` (`echo ... | node ...` closes stdin after the echo, which is why the server exits cleanly).

## Debugging a failed server in Claude Code

1. Run the standalone smoke test above. If it fails, the server has a real problem.
2. If smoke test passes but `/plugin` still shows "failed", the UI status may be stale. Reload:
   ```
   /reload-plugins
   ```
3. Check `/mcp` (more detail than `/plugin`). Look for the actual launch error.
4. Check `claude --debug` output at startup — logs every MCP init attempt.
5. Verify `${CLAUDE_PLUGIN_ROOT}` resolves to the right cache dir, not a stale one. Look at `~/.claude/plugins/installed_plugins.json` for the `installPath` of your plugin.

Common failure modes after the schema fix:
- **No `dist/` in the cache** — bundle wasn't committed, or was gitignored.
- **`node_modules/` missing** — using `tsc` pattern without the SessionStart-install hook.
- **Wrong `installPath`** — `version` field says 1.0.1 but `installPath` ends in `/1.0.0/`. See `plugin-hygiene-lessons.md` § 10.

## Tool design rules (for the MCP server itself)

1. **Few strong tools beats many weak tools.** Claude's tool-picking degrades with each added tool. Aim for <10 tools per server unless you genuinely expose a large surface.
2. **Idempotent by default.** Tool calls can be retried. Mutating operations should specify idempotency.
3. **Explicit outcome in return value.** Include status + structured data. "Returned successfully" with no payload hides silent failures.
4. **No network at startup.** Don't validate credentials in the constructor; do it in `initialize` or lazily on first tool call. Startup-time network == long `/plugin` load times.
5. **Respect timeouts.** MCP has a default request timeout. Long-running tools should either stream progress or return quickly with a handle the client can poll.
6. **Error messages are for the LLM.** Write errors Claude can use to self-correct: `"input 'path' must be absolute, got: './foo'"` beats `"InvalidPath"`.

## References

- `plugin-builder/references/plugin-hygiene-lessons.md` — real incidents, `.mcp.json` schema war stories, packaging traps (sections 7, 8, 11, 13)
- `plugin-builder/references/authoritative-sources.md` — canonical Anthropic + MCP doc URLs
- https://code.claude.com/docs/en/plugins-reference#mcp-servers — Anthropic's MCP section
- https://modelcontextprotocol.io/ — protocol spec
- https://github.com/modelcontextprotocol/typescript-sdk — TypeScript reference SDK
- https://github.com/tyroneross/interface-built-right — `tsup`-bundled example

## Preflight checklist (MCP-specific, supplements plugin-builder checklist)

- [ ] `.mcp.json` wraps servers in `{"mcpServers": {...}}`
- [ ] Command uses `${CLAUDE_PLUGIN_ROOT}` for all plugin-relative paths
- [ ] Bundled with `tsup` (single file) OR `dist/` + `${CLAUDE_PLUGIN_DATA}` install hook OR `dist/` committed with pure-stdlib
- [ ] `dist/` is NOT gitignored
- [ ] Server logs only to stderr (stdout reserved for protocol)
- [ ] Standalone smoke test via `initialize` RPC passes
- [ ] Tool count is appropriate (<10 unless necessary)
- [ ] No startup-time network calls
- [ ] Tool descriptions are concise (<500 chars each)
- [ ] Versioning: bump `plugin.json` and the `Server(...)` constructor's `version` together
