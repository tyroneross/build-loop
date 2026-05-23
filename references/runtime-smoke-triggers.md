<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross | SPDX-License-Identifier: Apache-2.0 -->

# Runtime Smoke Triggers

## What triggers a runtime smoke and why

A runtime smoke test fires when a build changes a file that is directly responsible for rendering a user-facing route, handling an API request, or booting a server. The test catches the class of failure that static analysis misses: a component that imports cleanly but throws at mount time, a page that returns HTTP 200 with an Application Error body, or a dev-server that refuses to start after a middleware change. It is placed **after** code-based graders (type-check, lint, unit tests) and **before** LLM judges, so it acts as a cheap binary gate on the most common live-render failure mode.

## Trigger patterns

| Pattern | Match | Reason |
|---|---|---|
| `app/**/page.{tsx,jsx,ts,js}` | App Router page | Renders to user — verify mount |
| `app/**/route.{ts,js}` | App Router API handler | Verify handler responds |
| `pages/**/*.{tsx,jsx,ts,js}` | Pages Router page | Same as App Router page |
| `app/**/middleware.{ts,js}` and `middleware.{ts,js}` | Routing middleware | Edge runtime; verify no middleware crash |
| `**/server.{ts,js}` | Custom Express/Fastify/Node server entry | Verify boot |
| `**/sse-*.{ts,js}` and any file containing `EventSource` or `text/event-stream` | SSE producer/consumer | Verify event taxonomy parses |
| `app/**/layout.{tsx,jsx,ts,js}` | Layout component | Affects all child routes — verify hydration |
| Files containing `'use client'` directive on line 1 | Client component | Verify hydration |

**Note on content-based patterns**: the `'use client'` and `EventSource`/`text/event-stream` patterns require reading file contents, not just matching paths. The current implementation in `scripts/runtime_smoke.py` handles path-based patterns only; content-based detection is deferred to the adapter layer or a future enhancement.

## Future adapter slots

| Adapter | Status | Triggered by | Maintainer notes |
|---|---|---|---|
| `nextjs` | shipped | App/Pages Router files, root layout, root middleware | `scripts/runtime_smoke_adapters/nextjs.py` |
| `sse_consumer` | **shipped 2026-05-09** | `state.json.triggers.runtimeServer == true` AND `runtimeServerInfo.sse_route` non-null AND diff touches `server_module` / `embedded_ui_module` | `scripts/runtime_smoke_adapters/sse_consumer.py`. Implements the 5-step procedure (restart → wait → curl 5s → parse handlers → fail on missing arm). Closes silent-server / ignored-client bug class observed in example-app 2026-05-08. Stack-agnostic — uses `runtimeServerInfo.start_command` if present, else `uv run <package> --serve --port` from pyproject, else `python3 -m <module>`. |
| `fastapi` | TODO | `app/main.py` with `FastAPI()` import; route decorators (`@app.get`, etc.) | Skip cleanly when uvicorn unavailable; return `status: skipped, reason: uvicorn_not_installed` |
| `express` | TODO | `package.json` has `express`; `**/server.{ts,js}` pattern | Detect port from server source or default to 3000; verify `/` returns non-5xx |

## When to add an adapter

An adapter is worth shipping when all of the following hold:

1. The project stack hits a runtime-smoke trigger at least 3 times across build history (i.e., the gate would have fired repeatedly for this stack).
2. The adapter can detect the live-render failure class that static analysis misses for that stack.
3. A clean skip path exists for environments where the server tooling is unavailable (no hard failure on missing binaries).
4. The user explicitly requests coverage for this stack, OR the build history shows a repeated "tests passed, page broken" failure pattern for it.

A new adapter should return `status: skipped` with a descriptive `reason` when its required runtime is missing — it must never return `status: fail` solely because the adapter's own tooling is absent.
